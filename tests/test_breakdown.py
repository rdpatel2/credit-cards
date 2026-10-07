import pytest
from card_compass.breakdown import spending_breakdown
from card_compass.ledger import Ledger
from card_compass.statements import Statement, Transaction


def row(amount, category='dining', kind='purchase', day='2026-09-01', card='first', merchant='SHOP'):
    return {'amount_cents': amount, 'category': category, 'kind': kind, 'date': day,
            'card_id': card, 'merchant_key': merchant}


def test_rankings_refunds_and_excluded_card_payments():
    data = spending_breakdown([row(12000), row(-2000, kind='refund'),
                              row(5000, 'groceries', merchant='FOOD LION'),
                              row(-50000, kind='payment'), row(500, kind='interest'), row(2000, kind='fee')])
    assert data['purchases_cents'] == 17000
    assert data['refunds_cents'] == 2000
    assert data['net_cents'] == 15000
    assert data['count'] == 3
    assert [(item['key'], item['net_cents']) for item in data['categories']] == [('dining', 10000), ('groceries', 5000)]
    assert sum(item['purchase_share'] for item in data['categories']) == pytest.approx(1)


def test_month_and_card_filters_use_transaction_date():
    rows = [row(1000), row(2000, day='2026-10-01'), row(3000, card='second')]
    data = spending_breakdown(rows, month='2026-09', card_id='first')
    assert data['net_cents'] == 1000
    assert [item['key'] for item in data['months']] == ['2026-09']
    assert spending_breakdown(rows)['net_cents'] == 6000


def test_refund_only_and_uncategorized_data_are_not_hidden():
    data = spending_breakdown([row(-2500, category=None, kind='refund')])
    assert data['net_cents'] == -2500
    assert data['categories'][0]['key'] == 'uncategorized'
    assert data['categories'][0]['purchase_share'] == 0
    assert spending_breakdown([])['categories'] == []


def test_aliases_group_merchants_without_overwriting_transaction_overrides(tmp_path):
    ledger = Ledger(tmp_path)
    statement = Statement('bofa', '0001', '2026-09-17', [
        Transaction('2026-09-01', '2026-09-02', 'LOCAL CAFE BRANCH ONE', 1000),
        Transaction('2026-09-02', '2026-09-03', 'LOCAL CAFE BRANCH TWO', 2000)])
    rows = ledger.preview(statement, 'card')
    choices = {r['merchant_key']: {'name': 'Local Cafe', 'category': 'dining'} for r in rows}
    ledger.commit(statement, 'card', rows, choices)
    saved = ledger.transactions()
    ledger.override_transaction(saved[0]['id'], 'recreation')
    data = spending_breakdown(ledger.breakdown_rows())
    assert len(data['merchants']) == 1
    assert data['merchants'][0]['name'] == 'Local Cafe'
    assert data['merchants'][0]['net_cents'] == 3000
    assert len(data['categories']) == 2
