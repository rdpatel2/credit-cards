import json
import pytest
from card_compass.catalog import BASE, CatalogStore, write_json, load_profile


def fake_fetch(url):
    if url.endswith("index.json"):
        return {"cards": [{"id": "sample"}]}
    if "/bonuses/" in url:
        return {"cardId": "sample", "currentOffers": [{"rewardAmount": 200}]}
    return {"id": "sample", "name": "Sample", "issuerId": "issuer", "annualFee": {"amount": 0}, "earningRates": []}


def test_refresh_persists_and_loads(tmp_path):
    store = CatalogStore(tmp_path, fake_fetch)
    progress = []
    result = store.refresh(lambda a, b: progress.append((a, b)))
    assert store.load() == result
    assert result["cards"][0]["offers"][0]["rewardAmount"] == 200
    assert progress == [(1, 1)]


def test_failed_refresh_preserves_previous_cache(tmp_path):
    store = CatalogStore(tmp_path, fake_fetch)
    previous = store.refresh()
    def fail(url):
        if "/bonuses/" in url:
            raise OSError("offline")
        return fake_fetch(url)
    store.fetcher = fail
    with pytest.raises(OSError):
        store.refresh()
    assert store.load() == previous


def test_invalid_catalog_does_not_replace_cache(tmp_path):
    store = CatalogStore(tmp_path, fake_fetch)
    previous = store.refresh()
    store.fetcher = lambda url: {"cards": []}
    with pytest.raises(ValueError):
        store.refresh()
    assert store.load() == previous


def test_profile_defaults_and_corrupt_file(tmp_path):
    write_json(tmp_path / "profile.json", {"wallet": ["sample"]})
    assert load_profile(tmp_path)["wallet"] == ["sample"]
    (tmp_path / "profile.json").write_text("broken")
    assert load_profile(tmp_path)["wallet"] == []
