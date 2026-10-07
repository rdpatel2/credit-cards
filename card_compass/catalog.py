"""Public source fetching and atomic local storage (no GUI dependency)."""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import sys
from urllib.request import Request, urlopen

BASE = "https://raw.githubusercontent.com/fuermosi777/rewards/main/data"
SOURCE = "https://github.com/fuermosi777/rewards"
SNAPSHOT = Path(__file__).parent / "data" / "snapshot.json"


def data_dir() -> Path:
    if sys.platform == "win32":
        root = Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData/Local"))
    elif sys.platform == "darwin":
        root = Path.home() / "Library/Application Support"
    else:
        root = Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local/share"))
    return root / "CardCompass"


def read_json(path: Path, default=None):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return default


def write_json(path: Path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(".tmp")
    temp.write_text(json.dumps(value, indent=2, ensure_ascii=False), encoding="utf-8")
    temp.replace(path)


def fetch_json(url: str):
    request = Request(url, headers={"User-Agent": "CardCompass/0.1", "Cache-Control": "no-cache"})
    with urlopen(request, timeout=15) as response:
        raw = response.read(4_000_001)
    if len(raw) > 4_000_000:
        raise ValueError("Source response too large")
    return json.loads(raw)


def validate_catalog(value):
    if not isinstance(value, dict) or not isinstance(value.get("cards"), list) or not value["cards"]:
        raise ValueError("Empty or malformed card catalog")
    ids = set()
    for card in value["cards"]:
        if not isinstance(card, dict) or not all(isinstance(card.get(k), str) and card[k] for k in ("id", "name", "issuerId")):
            raise ValueError("Invalid card identity")
        if card["id"] in ids:
            raise ValueError("Duplicate card identity")
        ids.add(card["id"])
        fee = card.get("annualFee", {})
        if not isinstance(fee, dict) or not isinstance(fee.get("amount"), (int, float)) or fee["amount"] < 0:
            raise ValueError("Invalid annual fee")
        for key in ("earningRates", "benefits", "offers"):
            if not isinstance(card.get(key, []), list):
                raise ValueError(f"Invalid {key}")
    return value


class CatalogStore:
    def __init__(self, directory: Path | None = None, fetcher=fetch_json):
        self.directory = directory or data_dir()
        self.fetcher = fetcher

    def load(self):
        for path in (self.directory / "catalog.json", SNAPSHOT):
            try:
                return validate_catalog(read_json(path))
            except ValueError:
                pass
        return {"cards": [], "fetched_at": None, "source": SOURCE}

    def refresh(self, progress=lambda done, total: None):
        index = self.fetcher(f"{BASE}/index.json")
        entries = index.get("cards", [])
        if not entries or len(entries) > 1000:
            raise ValueError("Source index is empty or too large")
        # All candidates are needed for wallet-wide comparisons; download only JSON,
        # never card artwork. No partial refresh replaces a working cache.
        def get_card(entry):
            ident = entry["id"]
            if not ident or any(c not in "abcdefghijklmnopqrstuvwxyz0123456789-" for c in ident):
                raise ValueError("Invalid source card ID")
            card = self.fetcher(f"{BASE}/cards/{ident}.json")
            bonus = self.fetcher(f"{BASE}/bonuses/{ident}.json")
            if card.get("id") != ident or bonus.get("cardId") != ident:
                raise ValueError("Mismatched source identity")
            return {**card, "offers": bonus.get("currentOffers", []), "offerSourceMeta": bonus.get("sourceMeta", {})}

        cards = []
        with ThreadPoolExecutor(max_workers=8) as pool:
            futures = [pool.submit(get_card, entry) for entry in entries]
            for future in as_completed(futures):
                cards.append(future.result())
                progress(len(cards), len(entries))
        result = validate_catalog({
            "cards": sorted(cards, key=lambda c: (c["issuerId"], c["name"])),
            "fetched_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "source": SOURCE,
        })
        write_json(self.directory / "catalog.json", result)
        return result


def default_profile():
    return {"wallet": [], "spend": {}, "coverage": {}, "target_rate": 2.0,
            "point_value": 1.0, "card_point_values": {}, "max_fee": 1000,
            "include_business": False, "eligible_bonus": False, "sort": "Ongoing value"}


def load_profile(directory: Path):
    value = read_json(directory / "profile.json", {})
    return {**default_profile(), **value} if isinstance(value, dict) else default_profile()
