"""Reward estimates using a spend allocation optimizer with shared cap constraints."""
from __future__ import annotations
from datetime import date
from dataclasses import dataclass, field
import math
from scipy.optimize import linprog
from scipy.sparse import lil_matrix

DEFAULT_CATEGORIES = {
    "dining": "Dining", "groceries": "Groceries (US supermarkets)",
    "gas_stations": "Gas stations", "transit": "Transit", "travel": "General travel",
    "airfare": "Airfare", "hotels": "Hotels", "streaming": "Streaming",
    "entertainment": "Entertainment", "drugstores": "Drugstores", "online_retail": "Online retail",
    "utilities": "Utilities", "wireless": "Wireless", "everything_else": "Other spending",
}
ALIASES = {
    "grocery_stores": "groceries", "us_supermarkets": "groceries", "us_gas_stations": "gas_stations",
    "us_restaurants": "dining", "everywhere_else": "everything_else", "pharmacy": "drugstores",
    "drug_stores": "drugstores", "airline_tickets": "airfare", "air_travel": "airfare",
    "airlines": "airfare", "flights": "airfare", "hotel": "hotels",
    "local_transit": "transit", "local_transit_and_commuting": "transit",
}


def canonical(category):
    return ALIASES.get(category, category)


def label(category):
    return DEFAULT_CATEGORIES.get(category, category.replace("_", " ").title())


def point_value(card, profile):
    return float(profile.get("card_point_values", {}).get(card["id"], profile.get("point_value", 1.0)))


def cap_per_year(cap):
    if not cap:
        return None
    period = cap.get("period", {})
    factor = {"month": 12, "quarter": 4, "year": 1}.get(period.get("unit"))
    if factor is None or float(period.get("count", 1)) <= 0:
        raise ValueError("Unsupported cap period")
    return float(cap["amount"]) * factor / float(period.get("count", 1))


@dataclass
class Rule:
    card_id: str
    categories: set[str]
    rate: float
    cap: float | None = None
    cap_group: str | None = None
    reward_cap: bool = False


def rules_for(card, profile, today=None):
    today = today or date.today()
    settings = profile.get("card_settings", {}).get(card["id"], {})
    warnings, rules = [], []
    # The issuer's standing Customized Cash category needs selection, not activation.
    # Its introductory extra 3% is not represented by the feed's recurring rates.
    if card['id'] == 'bank-of-america-customized-cash-rewards':
        warnings.append('First-year 6% choice-category promotion (issuer check 2026-10-07) is excluded from numeric estimates; see Sources')
    rates = list(card.get("earningRates", []))
    for benefit in card.get("benefits", []):
        if benefit.get("benefitType") != "bonus_category":
            continue
        rate = dict(benefit.get("bonusCategoryDetail", {}))
        if card['id'] == 'bank-of-america-customized-cash-rewards' and benefit.get('id') == 'boa-ccr-choice-category-bonus':
            rate['requiresActivation'] = False
        selection = rate.get("categorySelection")
        if selection:
            mode = selection.get("mode")
            if mode == "issuer_rotating":
                cycle = f"{today.year}-Q{(today.month - 1) // 3 + 1}"
                if selection.get("currentCycle") != cycle:
                    warnings.append(f"{benefit['name']}: current rotating categories unresolved")
                    continue
                rate["eligibleCategories"] = selection.get("options", [])
            else:
                selected = settings.get("selections", {}).get(benefit["id"], [])
                if not selected or len(selected) > selection.get("selectCount", 1) or not set(selected) <= set(selection.get("options", [])):
                    warnings.append(f"{benefit['name']}: select category in Card settings")
                    continue
                rate["eligibleCategories"] = selected
                if mode == "automatic_top_category":
                    warnings.append(f"{benefit['name']}: assumes selected categories are highest on this card each cycle")
        if rate.get("requiresActivation") and not settings.get("activated", False):
            warnings.append(f"{benefit['name']}: activation not confirmed")
            continue
        # A fixed cycle cannot be extrapolated across an entire year.
        if selection and selection.get("mode") == "issuer_rotating":
            warnings.append(f"{benefit['name']}: seasonal benefit shown in details; excluded from annual estimate")
            continue
        rates.append(rate)
    for rate in rates:
        if rate.get("requiresActivation") and not settings.get("activated", False):
            warnings.append("An earning rate needs activation")
            continue
        unit = rate.get("rewardUnit")
        if unit not in ("cashback_percent", "points", "miles"):
            warnings.append("An earning rate has an unsupported reward unit")
            continue
        cats = {"*" if canonical(c) == "everything_else" else canonical(c) for c in rate.get("eligibleCategories", [])}
        if not cats or cats & {"choice_category", "top_spend_category", "second_highest_spend_category"}:
            warnings.append("A category rate needs an explicit selection")
            continue
        try:
            cap = cap_per_year(rate.get("spendCap"))
            amount = float(rate["multiplier"]) / 100 * (1 if unit == "cashback_percent" else point_value(card, profile))
            if not math.isfinite(amount) or amount < 0 or (cap is not None and (not math.isfinite(cap) or cap < 0)):
                raise ValueError("Invalid rate")
        except (ValueError, KeyError, TypeError):
            warnings.append("An earning rate could not be calculated")
            continue
        group = None
        # These products share a cap across multiple rate records. Other equal
        # numeric caps (e.g. Blue Cash Everyday) are genuinely independent.
        if cap is not None and card["id"] in {
            "bank-of-america-customized-cash-rewards", "citi-costco-anywhere-visa",
            "bank-of-america-business-advantage-customized-cash",
        }:
            group = f"{card['id']}:combined"
        if card["id"] == "comenity-aaa-daily-advantage" and cap == 500:
            # The upstream schema puts a REWARD cap into spendCap. Its terms
            # identify the capped subset; other categories remain uncapped.
            capped_cats = cats & {"groceries", "gas_stations", "wholesale_clubs"}
            if capped_cats:
                rules.append(Rule(card["id"], capped_cats, amount, 500, f"{card['id']}:rewards", True))
            uncapped = cats - capped_cats
            if uncapped:
                rules.append(Rule(card["id"], uncapped, amount))
            warnings.append("AAA combined $500 annual cashback cap normalized from source terms")
        else:
            rules.append(Rule(card["id"], cats, amount, cap, group))
    if not rules:
        warnings.append("No usable earning rates; estimate unavailable")
    return rules, list(dict.fromkeys(warnings))


@dataclass
class Allocation:
    total: float = 0
    category_rewards: dict = field(default_factory=dict)
    category_cards: dict = field(default_factory=dict)


def allocate(rules, spend):
    """Maximize rewards; each purchase goes to one rule, each cap is shared."""
    annual = {k: float(v) * 12 for k, v in spend.items() if float(v) > 0}
    cats = list(annual)
    variables = [(i, cat) for i, rule in enumerate(rules) for cat in cats
                 if cat in rule.categories or "*" in rule.categories]
    if not variables:
        return Allocation(category_rewards={c: 0 for c in cats}, category_cards={c: [] for c in cats})
    groups = {}
    for i, rule in enumerate(rules):
        if rule.cap is not None:
            key = rule.cap_group or f"rule:{i}"
            groups.setdefault(key, []).append(i)
    capped = list(groups)
    matrix = lil_matrix((len(cats) + len(capped), len(variables)))
    rows = {c: i for i, c in enumerate(cats)}
    cap_rows = {r: len(cats) + i for i, r in enumerate(capped)}
    for col, (rule_id, cat) in enumerate(variables):
        matrix[rows[cat], col] = 1
        rule = rules[rule_id]
        key = rule.cap_group or f"rule:{rule_id}"
        if key in cap_rows:
            matrix[cap_rows[key], col] = rule.rate if rule.reward_cap else 1
    result = linprog([-rules[i].rate for i, _ in variables], A_ub=matrix.tocsr(),
                     b_ub=[annual[c] for c in cats] + [min(rules[i].cap for i in groups[g]) for g in capped],
                     bounds=(0, None), method="highs")
    if not result.success:
        raise ValueError("Unable to allocate spending")
    allocation = Allocation(category_rewards={c: 0 for c in cats}, category_cards={c: [] for c in cats})
    for amount, (rule_id, cat) in zip(result.x, variables):
        reward = amount * rules[rule_id].rate
        allocation.total += reward
        allocation.category_rewards[cat] += reward
        if amount > .01 and rules[rule_id].card_id not in allocation.category_cards[cat]:
            allocation.category_cards[cat].append(rules[rule_id].card_id)
    return allocation


def wallet_rules(cards, profile):
    rules, warnings = [], []
    for card in cards:
        if card["id"] in profile.get("wallet", []):
            card_rules, issues = rules_for(card, profile)
            rules.extend(card_rules)
            warnings.extend(f"{card['name']}: {w}" for w in issues)
    for category, percent in profile.get("coverage", {}).items():
        if percent is not None:
            # An explicit override replaces inferred coverage for that category.
            expanded = set(profile.get("spend", {})) | set(DEFAULT_CATEGORIES)
            for rule in rules:
                if "*" in rule.categories:
                    rule.categories = expanded
                rule.categories.discard(category)
            rules.append(Rule("manual", {category}, float(percent) / 100))
    return rules, warnings


def normalized_spend(profile):
    return profile.get("spend", {})


def available_offers(card, profile, today=None):
    today = today or date.today()
    result = []
    monthly = sum(float(v) for v in profile.get("spend", {}).values())
    for offer in card.get("offers", []):
        expiration = offer.get("expirationDate")
        if expiration:
            try:
                if date.fromisoformat(expiration) < today:
                    continue
            except ValueError:
                continue
        amount = float(offer.get("rewardAmount", 0))
        unit = offer.get("rewardUnit")
        value = amount if unit in ("cashback_usd", "statement_credit_usd") else amount * point_value(card, profile) / 100 if unit in ("points", "miles") else None
        days = offer.get("timeframeDays")
        required = offer.get("spendRequirement")
        feasible = days is not None and required is not None and monthly * float(days) / 30 >= float(required)
        public = offer.get("offerType") == "public"
        result.append({**offer, "value": value, "feasible": feasible,
                       "counted": bool(feasible and public and value is not None and profile.get("eligible_bonus", False))})
    return result


def recommend(cards, profile):
    base_rules, warnings = wallet_rules(cards, profile)
    spend = normalized_spend(profile)
    base = allocate(base_rules, spend)
    results = []
    for card in cards:
        if card["id"] in profile.get("wallet", []) or card.get("status") != "active":
            continue
        if card.get("variant") == "business" and not profile.get("include_business", False):
            continue
        fee = float(card["annualFee"]["amount"])
        if fee > float(profile.get("max_fee", 1000)):
            continue
        rules, issues = rules_for(card, profile)
        if not rules:
            continue
        combined = allocate(base_rules + rules, spend)
        gains = {k: combined.category_rewards.get(k, 0) - base.category_rewards.get(k, 0) for k in spend}
        gain = combined.total - base.total
        bonus = max((o["value"] for o in available_offers(card, profile) if o["counted"]), default=0)
        first_fee = 0 if card["annualFee"].get("waivedFirstYear") else fee
        results.append({"card": card, "gain": gain, "ongoing": gain - fee,
                        "first_year": gain - first_fee + bonus, "bonus": bonus,
                        "category_gains": gains, "warnings": issues})
    key = "first_year" if profile.get("sort") == "First-year value" else "ongoing"
    return sorted(results, key=lambda r: (-r[key], r["card"]["name"])), base, warnings
