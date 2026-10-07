from datetime import date
import pytest
from card_compass.engine import Rule, allocate, recommend, rules_for, available_offers
from card_compass.catalog import default_profile


def card(ident="new", rates=None, fee=0, **extra):
    return {"id": ident, "name": ident, "issuerId": "bank", "status": "active", "variant": "personal",
            "annualFee": {"amount": fee}, "earningRates": rates or [], **extra}


def rate(amount, categories=("everything_else",), **extra):
    return {"multiplier": amount, "rewardUnit": "cashback_percent", "eligibleCategories": list(categories), **extra}


def test_shared_cap_routes_overflow_to_existing_card():
    result = allocate([Rule("old", {"*"}, .02), Rule("new", {"groceries", "dining"}, .06, 6000)],
                      {"groceries": 1000, "dining": 1000})
    assert result.total == pytest.approx(6000 * .06 + 18000 * .02)


def test_optimizer_avoids_greedy_category_allocation():
    result = allocate([Rule("a", {"groceries", "dining"}, .06, 6000), Rule("b", {"groceries"}, .05, 6000)],
                      {"groceries": 500, "dining": 500})
    assert result.total == pytest.approx(660)


def test_incremental_value_and_fees():
    cards = [card("old", [rate(2)]), card("new", [rate(3)], fee=95)]
    profile = {**default_profile(), "wallet": ["old"], "spend": {"dining": 1000}}
    results, base, _ = recommend(cards, profile)
    assert base.total == 240
    assert len(results) == 1
    assert results[0]["ongoing"] == pytest.approx(25)


def test_manual_zero_replaces_wallet_coverage():
    cards = [card("old", [rate(2), rate(4, ["dining"])])]
    profile = {**default_profile(), "wallet": ["old"], "spend": {"dining": 100, "everything_else": 100}, "coverage": {"dining": 0}}
    _, baseline, _ = recommend(cards, profile)
    assert baseline.category_rewards["dining"] == 0
    assert baseline.total == 24


def test_point_values_are_not_raw_multipliers():
    rates = [{"multiplier": 5, "rewardUnit": "points", "eligibleCategories": ["dining"]}]
    rules, _ = rules_for(card(rates=rates), {"point_value": .5})
    assert rules[0].rate == .025


def test_rotating_categories_and_activation_are_unresolved():
    benefit = {"id": "rotating", "name": "Rotating bonus", "benefitType": "bonus_category", "bonusCategoryDetail": {
        "multiplier": 5, "rewardUnit": "cashback_percent", "requiresActivation": True,
        "categorySelection": {"mode": "issuer_rotating", "currentCycle": "2026-Q3", "options": ["groceries"]}}}
    rules, warnings = rules_for(card(rates=[rate(1)], benefits=[benefit]), {}, date(2026, 10, 6))
    assert len(rules) == 1
    assert any("unresolved" in w for w in warnings)


def test_choice_menu_does_not_credit_every_option():
    benefit = {"id": "choice", "name": "Pick one", "benefitType": "bonus_category", "bonusCategoryDetail": {
        "multiplier": 3, "rewardUnit": "cashback_percent", "categorySelection": {"mode": "user_choice", "options": ["dining", "groceries"]}}}
    c = card(rates=[rate(1)], benefits=[benefit])
    rules, warnings = rules_for(c, {})
    assert len(rules) == 1 and warnings
    rules, _ = rules_for(c, {"card_settings": {"new": {"selections": {"choice": ["dining"]}}}})
    assert rules[-1].categories == {"dining"}


def test_expired_targeted_and_unaffordable_offers_not_counted():
    c = card(rates=[rate(1)], offers=[
        {"offerType": "public", "rewardAmount": 200, "rewardUnit": "cashback_usd", "spendRequirement": 500, "timeframeDays": 90},
        {"offerType": "targeted", "rewardAmount": 1000, "rewardUnit": "cashback_usd", "spendRequirement": 1, "timeframeDays": 90},
        {"offerType": "public", "rewardAmount": 500, "rewardUnit": "cashback_usd", "spendRequirement": 5000, "timeframeDays": 90},
        {"offerType": "public", "rewardAmount": 900, "rewardUnit": "cashback_usd", "spendRequirement": 1, "timeframeDays": 90, "expirationDate": "2020-01-01"}])
    profile = {**default_profile(), "spend": {"dining": 200}, "eligible_bonus": True}
    offers = available_offers(c, profile)
    assert len(offers) == 3
    assert sum(o["counted"] for o in offers) == 1
    results, _, _ = recommend([c], profile)
    assert results[0]["bonus"] == 200


def test_first_year_fee_waiver_and_no_bonus_by_default():
    c = card(rates=[rate(2)], annualFee={"amount": 95, "waivedFirstYear": True})
    results, _, _ = recommend([c], {**default_profile(), "spend": {"dining": 100}})
    assert results[0]["first_year"] == 24
    assert results[0]["ongoing"] == -71


def test_discontinued_and_business_cards_excluded():
    c = [card("a", [rate(1)], status="discontinued"), card("b", [rate(1)], variant="business")]
    assert recommend(c, default_profile())[0] == []


def test_shared_cap_across_different_rate_records():
    c = card("citi-costco-anywhere-visa", [
        rate(5, ["costco_gas"], spendCap={"amount": 7000, "period": {"unit": "year"}}),
        rate(4, ["gas_stations"], spendCap={"amount": 7000, "period": {"unit": "year"}}), rate(1)])
    rules, _ = rules_for(c, {})
    result = allocate(rules, {"costco_gas": 500, "gas_stations": 500})
    assert result.total == pytest.approx(6000 * .05 + 1000 * .04 + 5000 * .01)


def test_reward_cap_and_uncapped_subset():
    c = card("comenity-aaa-daily-advantage", [
        rate(5, ["groceries"], spendCap={"amount": 500, "period": {"unit": "year"}}),
        rate(3, ["gas_stations", "streaming"], spendCap={"amount": 500, "period": {"unit": "year"}}), rate(1)])
    rules, _ = rules_for(c, {})
    result = allocate(rules, {"groceries": 1000, "gas_stations": 1000, "streaming": 100})
    assert result.total == pytest.approx(500 + 14000 * .01 + 1200 * .03)
