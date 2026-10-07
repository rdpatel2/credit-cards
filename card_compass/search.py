"""Human-friendly catalog search independent of source IDs and word order."""
import re
from .engine import label

ISSUER_ALIASES = {'bank-of-america': ['bofa', 'boa', 'b of a'],
                  'american-express': ['amex'], 'u-s-bank': ['us bank', 'usbank']}


def words(value):
    return re.findall(r'[^\W_]+', value.casefold(), flags=re.UNICODE)


def matches_card(card, query):
    fields = [card['name'], card['id'], card['issuerId'], 'credit card']
    fields.extend(ISSUER_ALIASES.get(card['issuerId'], []))
    for benefit in card.get('benefits', []):
        fields.extend(str(benefit.get(k, '')) for k in ('name', 'description', 'terms', 'category'))
        options = benefit.get('bonusCategoryDetail', {}).get('categorySelection', {}).get('options', [])
        fields.extend(label(c) for c in options)
    for rate in card.get('earningRates', []):
        fields.append(rate.get('terms', ''))
        fields.extend(label(c) for c in rate.get('eligibleCategories', []))
    haystack = ' '.join(words(' '.join(fields)))
    return all(token in haystack for token in words(query))
