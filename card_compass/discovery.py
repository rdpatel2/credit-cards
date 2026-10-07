"""Overlapping discovery groups based on published earning rates and benefits."""
GROUPS = (
    ('cashback', 'Cash back', 'Earn money back on everyday purchases.', 'wallet'),
    ('points', 'Points', 'Explore flexible and loyalty reward points.', 'matches'),
    ('miles', 'Miles', 'Earn miles toward your next trip.', 'compass'),
    ('travel', 'Travel', 'Rewards and benefits for getting away.', 'explore'),
    ('dining', 'Dining', 'Find rewards for restaurants and takeout.', 'spending'),
    ('groceries', 'Groceries', 'Make your supermarket spending count.', 'coverage'),
    ('no-fee', 'No annual fee', 'Explore cards without a yearly fee.', 'wallet'),
    ('business', 'Business', 'Cards designed for business spending.', 'statement'),
)


def discoverable(card):
    return card.get('status') in ('active', 'custom')


def card_groups(card):
    if not discoverable(card):
        return set()
    rates = card.get('earningRates', [])
    units = {r.get('rewardUnit') for r in rates}
    groups = set()
    if 'cashback_percent' in units:
        groups.add('cashback')
    groups.update(units & {'points', 'miles'})
    categories = {c for r in rates for c in r.get('eligibleCategories', [])}
    categories.update(b.get('category', '') for b in card.get('benefits', []))
    if any(any(token in c for token in ('travel', 'airfare', 'airline', 'flight', 'hotel', 'rental_car', 'vacation')) for c in categories):
        groups.add('travel')
    if any(any(token in c for token in ('dining', 'restaurant')) for c in categories):
        groups.add('dining')
    if any(any(token in c for token in ('grocer', 'supermarket')) for c in categories):
        groups.add('groceries')
    if card.get('annualFee', {}).get('amount') == 0:
        groups.add('no-fee')
    if card.get('variant') == 'business':
        groups.add('business')
    return groups
