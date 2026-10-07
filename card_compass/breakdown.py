"""Summaries of actual imported purchases and refunds, using integer cents."""
from collections import defaultdict
from datetime import date


def spending_breakdown(rows, month=None, card_id=None):
    if month is not None:
        date.fromisoformat(month + '-01')
    included = [row for row in rows if row['kind'] in ('purchase', 'refund')
                and (not month or row['date'].startswith(month + '-'))
                and (not card_id or row['card_id'] == card_id)]
    groups = {key: defaultdict(lambda: {'purchases_cents': 0, 'refunds_cents': 0, 'count': 0})
              for key in ('categories', 'merchants', 'cards', 'months')}
    purchases = sum(max(0, row['amount_cents']) for row in included)
    refunds = sum(max(0, -row['amount_cents']) for row in included)
    merchant_labels = {}
    for row in included:
        merchant = row.get('merchant_group', row['merchant_key'])
        merchant_labels[merchant] = row.get('merchant_name', merchant.title())
        keys = {'categories': row.get('category') or 'uncategorized', 'merchants': merchant,
                'cards': row['card_id'], 'months': row['date'][:7]}
        for grouping, key in keys.items():
            group = groups[grouping][key]
            group['purchases_cents'] += max(0, row['amount_cents'])
            group['refunds_cents'] += max(0, -row['amount_cents'])
            group['count'] += 1
    result = {'purchases_cents': purchases, 'refunds_cents': refunds,
              'net_cents': purchases - refunds, 'count': len(included)}
    for grouping, grouped in groups.items():
        items = []
        for key, values in grouped.items():
            items.append({'key': key, **values,
                          'name': merchant_labels[key] if grouping == 'merchants' else key,
                          'net_cents': values['purchases_cents'] - values['refunds_cents'],
                          'purchase_share': values['purchases_cents'] / purchases if purchases else 0})
        result[grouping] = sorted(items, key=(lambda item: item['key']) if grouping == 'months'
                                  else lambda item: (-item['net_cents'], -item['purchases_cents'], item['key']))
    return result
