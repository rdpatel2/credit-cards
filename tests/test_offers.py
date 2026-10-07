from copy import deepcopy
import pytest
from card_compass.offers import (VENTURE_ID, VENTURE_URL, parse_venture_offer,
                                 refresh_issuer_offers, limited_time)
from card_compass.catalog import CatalogStore, validate_catalog, write_json
from card_compass.engine import available_offers, recommend

MILES = "earn 75,000 bonus miles once you spend $4,000 on purchases within the first 3 months from account opening"
COMPOUND = ("<h3>Limited-Time Offer</h3><p>Enjoy $300 to use on hotel and vacation rental bookings "
            "through Capital One Travel in your first cardholder year, plus " + MILES + "</p>"
            "<p>Eligible holders receive a one-time $300 Capital One Travel Stays credit. "
            "Credits will expire one year after account opening.</p>")


def venture():
    return {"id": VENTURE_ID, "name": "Venture", "issuerId": "capital-one", "status": "active",
            "annualFee": {"amount": 95}, "earningRates": [],
            "offers": [{"offerType": "public", "rewardAmount": 75000, "rewardUnit": "points"}]}


def test_parse_full_offer_and_changed_amounts():
    offer = parse_venture_offer(COMPOUND)
    assert offer["limitedTime"] is True
    assert offer["spendRequirement"] == 4000
    assert offer["timeframeDays"] == 90
    assert [(c["rewardAmount"], c["rewardUnit"]) for c in offer["rewardComponents"]] == [(75000, "miles"), (300, "travel_credit_usd")]
    changed = parse_venture_offer(COMPOUND.replace("$300", "$250").replace("75,000", "90,000"))
    assert [c["rewardAmount"] for c in changed["rewardComponents"]] == [90000, 250]


def test_ended_promotion_removes_credit_and_badge():
    previous = [venture()]
    refresh_issuer_offers(previous, [], lambda url: COMPOUND)
    cards = [venture()]
    refresh_issuer_offers(cards, previous, lambda url: "<p>" + MILES + "</p>")
    offer = cards[0]["offers"][0]
    assert len(offer["rewardComponents"]) == 1
    assert offer["limitedTime"] is False
    assert cards[0]["issuerOfferCheck"]["status"] == "verified"


@pytest.mark.parametrize("html", ["Access denied", COMPOUND.replace("vacation rental bookings", "holiday stays"),
                                  COMPOUND.replace("one year", "six months")])
def test_unrecognized_layout_does_not_silently_drop_credit(html):
    with pytest.raises(ValueError):
        parse_venture_offer(html)


def test_script_and_unrelated_labels_do_not_create_limited_offer():
    html = '<script>' + COMPOUND + '</script><h2>Limited-Time Offer</h2><p>Other card details. ' + MILES + '</p>'
    offer = parse_venture_offer(html)
    assert offer["limitedTime"] is False
    assert len(offer["rewardComponents"]) == 1
    assert limited_time({"terms": "A limited time promotion."})


def test_failure_retains_checked_offer_and_marks_it_unconfirmed():
    previous = [venture()]
    refresh_issuer_offers(previous, [], lambda url: COMPOUND)
    cards = [venture()]
    refresh_issuer_offers(cards, previous, lambda url: "Access denied")
    assert cards[0]["offers"] == previous[0]["offers"]
    assert cards[0]["issuerOfferCheck"]["status"] == "unconfirmed"
    assert cards[0]["issuerOfferCheck"]["checkedAt"] == previous[0]["issuerOfferCheck"]["checkedAt"]
    assert cards[0]["issuerOfferCheck"]["sourceUrl"] == VENTURE_URL


def test_failure_without_prior_check_keeps_feed_offer():
    cards = [venture()]
    original = deepcopy(cards[0]["offers"])
    def offline(url):
        raise OSError("offline")
    refresh_issuer_offers(cards, [], offline)
    assert cards[0]["offers"] == original
    assert cards[0]["issuerOfferCheck"]["status"] == "unconfirmed"


def test_compound_value_does_not_count_restricted_credit_or_sum_alternatives():
    from tests.test_engine import card, rate
    c = card(rates=[rate(1)], offers=[parse_venture_offer(COMPOUND),
        {"offerType": "public", "rewardAmount": 800, "rewardUnit": "cashback_usd", "spendRequirement": 1, "timeframeDays": 90}])
    profile = {"spend": {"dining": 2000}, "eligible_bonus": True}
    offers = available_offers(c, profile)
    assert offers[0]["value"] == 750
    assert offers[0]["excludedComponents"][0]["rewardAmount"] == 300
    assert recommend([c], profile)[0][0]["bonus"] == 800
    c["offers"] = [{"offerType": "public", "rewardAmount": 300, "rewardUnit": "statement_credit_usd", "spendRequirement": 1, "timeframeDays": 90}]
    assert available_offers(c, profile)[0]["counted"] is False


def test_catalog_refresh_checks_issuer_and_persists_components(tmp_path):
    calls = []
    def feed(url):
        if url.endswith("index.json"):
            return {"cards": [{"id": VENTURE_ID}]}
        if "/bonuses/" in url:
            return {"cardId": VENTURE_ID, "currentOffers": venture()["offers"]}
        return venture()
    def issuer(url):
        calls.append(url)
        return COMPOUND
    store = CatalogStore(tmp_path, feed, issuer)
    result = store.refresh()
    assert len(result["cards"][0]["offers"][0]["rewardComponents"]) == 2
    assert store.load() == result
    store.refresh()
    assert calls == [VENTURE_URL, VENTURE_URL]


@pytest.mark.parametrize("components", [[{"rewardAmount": -1, "rewardUnit": "miles"}],
                                         [{"rewardAmount": float("nan"), "rewardUnit": "miles"}], ["bad"]])
def test_invalid_components_rejected(components):
    c = venture()
    c["offers"][0]["rewardComponents"] = components
    with pytest.raises(ValueError):
        validate_catalog({"cards": [c]})
