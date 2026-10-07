from card_compass.discovery import card_groups, discoverable


def test_groups_follow_reward_units_and_category_terms():
    card = {'status': 'active', 'annualFee': {'amount': 0}, 'variant': 'business',
            'earningRates': [
                {'rewardUnit': 'cashback_percent', 'eligibleCategories': ['dining', 'groceries']},
                {'rewardUnit': 'miles', 'eligibleCategories': ['capital_one_travel_hotels']}],
            'benefits': []}
    assert card_groups(card) == {'cashback', 'miles', 'travel', 'dining', 'groceries', 'no-fee', 'business'}
    card['status'] = 'discontinued'
    assert card_groups(card) == set()
    assert not discoverable(card)


def test_points_and_custom_cards_and_unknown_status():
    card = {'status': 'custom', 'annualFee': {'amount': 95},
            'earningRates': [{'rewardUnit': 'points', 'eligibleCategories': ['everything_else']}],
            'benefits': [{'category': 'travel', 'benefitType': 'lounge_access'}]}
    assert discoverable(card)
    assert card_groups(card) == {'points', 'travel'}
    card['status'] = 'unknown'
    assert not discoverable(card)


def test_cashback_card_with_generic_rewards_does_not_imply_travel():
    card = {'status': 'active', 'annualFee': {'amount': 95},
            'earningRates': [{'rewardUnit': 'cashback_percent', 'eligibleCategories': ['everything_else']}]}
    assert card_groups(card) == {'cashback'}
    card['benefits'] = [{'category': 'travel', 'benefitType': 'insurance'}]
    assert card_groups(card) == {'cashback'}
