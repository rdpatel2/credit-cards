"""Compound signup rewards and a conservative adapter for Venture's issuer page."""
from datetime import datetime, timezone
from html.parser import HTMLParser
import re
from urllib.request import Request, urlopen

VENTURE_ID = "capital-one-venture-rewards"
VENTURE_URL = "https://www.capitalone.com/credit-cards/venture/"


class VisibleText(HTMLParser):
    def __init__(self):
        super().__init__()
        self.hidden = 0
        self.parts = []

    def handle_starttag(self, tag, attrs):
        if tag in ("script", "style"):
            self.hidden += 1

    def handle_endtag(self, tag):
        if tag in ("script", "style"):
            self.hidden = max(0, self.hidden - 1)

    def handle_data(self, data):
        if not self.hidden:
            self.parts.append(data)


def fetch_issuer_html(url):
    request = Request(url, headers={"User-Agent": "Mozilla/5.0 CardCompass/0.1", "Cache-Control": "no-cache"})
    with urlopen(request, timeout=15) as response:
        raw = response.read(4_000_001)
    if len(raw) > 4_000_000:
        raise ValueError("Issuer response too large")
    return raw.decode("utf-8")


def parse_venture_offer(html):
    parser = VisibleText()
    parser.feed(html)
    text = " ".join(" ".join(parser.parts).split())
    # Match the complete marketing sentence, not calculator values or annual benefits.
    match = re.search(
        r"(?:Enjoy \$(?P<credit>[\d,]+) to use on hotel and vacation rental bookings "
        r"through Capital One Travel in your first cardholder year, plus )?"
        r"earn (?P<miles>[\d,]+) bonus miles once you spend \$(?P<spend>[\d,]+) "
        r"on purchases within the first (?P<months>\d+) months from account opening",
        text, re.IGNORECASE)
    if not match:
        raise ValueError("Venture offer wording not recognized; current offer requires review")
    if not match["credit"] and ("plus" in text[max(0, match.start() - 10):match.start()].lower() or
                                re.search(r"one-time \$[\d,]+ Capital One Travel Stays credit", text, re.I)):
        raise ValueError("Venture compound offer wording not recognized")
    number = lambda key: int(match[key].replace(",", ""))
    components = [{"rewardAmount": number("miles"), "rewardUnit": "miles"}]
    if match["credit"]:
        # Require corroborating credit terms before assigning its restrictions/expiry.
        if not re.search(r"one-time \$" + re.escape(match["credit"]) + r" Capital One Travel Stays credit", text, re.I) or "Credits will expire one year after account opening" not in text:
            raise ValueError("Venture credit terms not recognized")
        components.append({"rewardAmount": number("credit"), "rewardUnit": "travel_credit_usd",
                           "label": "Capital One Travel Stays credit",
                           "terms": "One-time credit for hotels and vacation rentals booked through Capital One Travel. Expires one year after account opening."})
    # The label must immediately precede the offer, not appear elsewhere on the page.
    prefix = text[max(0, match.start() - 80):match.start()]
    limited = bool(re.search(r"limited[\s-]+time offer\s*$", prefix, re.I))
    return {"offerType": "public", "rewardAmount": number("miles"), "rewardUnit": "miles",
            "rewardComponents": components, "spendRequirement": number("spend"),
            "timeframeDays": number("months") * 30, "limitedTime": limited,
            "terms": match[0] + ". See issuer terms for eligibility and exclusions.",
            "sourceUrl": VENTURE_URL}


def refresh_issuer_offers(cards, previous_cards, fetcher=fetch_issuer_html):
    """Keep last checked terms on failure, visibly marked as unconfirmed this refresh."""
    card = next((c for c in cards if c["id"] == VENTURE_ID), None)
    if card is None:
        return
    try:
        offer = parse_venture_offer(fetcher(VENTURE_URL))
    except (OSError, ValueError, UnicodeError) as error:
        previous = next((c for c in previous_cards if c["id"] == VENTURE_ID), {})
        checked = previous.get("issuerOfferCheck", {})
        if checked.get("checkedAt"):
            card["offers"] = previous.get("offers", [])
        card["issuerOfferCheck"] = {**checked, "status": "unconfirmed", "sourceUrl": VENTURE_URL,
                                    "message": "Issuer check failed; showing last available terms. " + str(error)}
        return
    card["offers"] = [offer] + [o for o in card.get("offers", []) if o.get("offerType") != "public"]
    card["issuerOfferCheck"] = {"status": "verified", "checkedAt": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                               "sourceUrl": VENTURE_URL}


def reward_components(offer):
    return offer.get("rewardComponents") or [offer]


def limited_time(offer):
    return offer.get("limitedTime") is True or bool(re.search(
        r"\blimited[\s-]+time\b", offer.get("terms", ""), re.I))


def reward_text(component):
    amount = component.get("rewardAmount", 0)
    unit = component.get("rewardUnit", "")
    labels = {"cashback_usd": "cash back", "statement_credit_usd": "statement credit",
              "travel_credit_usd": "travel credit"}
    if unit in labels:
        return f"${amount:,.0f} " + component.get("label", labels[unit])
    return f"{amount:,.0f} " + component.get("label", unit.replace("_", " "))
