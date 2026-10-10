import pytest
from card_compass.finance import FinanceStore, cents


def test_recurring_cash_flow_is_posted_per_month_and_persists(tmp_path):
    store = FinanceStore(tmp_path)
    income = store.save_entry('Salary', 'income', 'salary', 5000, '2026-10', True)
    rent = store.save_entry('Rent', 'expense', 'rent', 1400, '2026-10', True)
    store.set_target('2026-10', 'rent', 1600)
    store.post(income, '2026-10', True)
    store.post(rent, '2026-10', True)
    s = store.summary('2026-10', {'groceries': 300})
    assert (s['income'], s['planned'], s['remaining'], s['cash_flow']) == (500000, 190000, 310000, 360000)
    assert store.summary('2026-11')['received'] == 0
    assert store.summary('2026-09')['income'] == 0
    assert FinanceStore(tmp_path).summary('2026-10')['received'] == 500000
    store.post(rent, '2026-10', False)
    assert store.summary('2026-10')['actual'] == 0
    store.save_entry('Rent', 'expense', 'rent', 1400, '2026-10', True, rent, '2026-11')
    assert store.summary('2026-12')['planned'] == 0
    store.delete_entry(income)
    assert store.summary('2026-10')['received'] == 0


def test_targets_and_scheduled_bills_are_not_added_twice(tmp_path):
    store = FinanceStore(tmp_path)
    store.save_entry('Rent', 'expense', 'rent', 1500, '2026-10')
    store.set_target('2026-10', 'rent', 1000)
    store.set_target('2026-10', 'groceries', 0)
    assert store.summary('2026-10', {'groceries': 500})['planned'] == 150000
    store.copy_targets('2026-10', '2026-11')
    assert store.summary('2026-11')['planned'] == 100000
    assert store.summary('2026-12')['planned'] == 0


def test_imported_actuals_include_refunds_and_one_offs_not_payments(tmp_path):
    store = FinanceStore(tmp_path)
    # Use the ledger's actual schema, avoiding invented mock totals.
    with store.connection() as db:
        db.execute("INSERT INTO accounts VALUES ('a','test','1234','card')")
        db.execute("INSERT INTO statements VALUES (1,'hash','file','a','2026-10-30')")
        for ident, kind, value, day in [(1, 'purchase', 20000, '2026-10-01'), (2, 'refund', -5000, '2026-10-02'), (3, 'payment', -15000, '2026-10-03'), (4, 'purchase', 10000, '2026-09-01')]:
            db.execute('''INSERT INTO transactions(id,statement_id,account_key,fingerprint,occurrence,date,posting_date,description,merchant_key,amount_cents,kind,reference,issuer_category,page,category,one_off)
                VALUES (?,1,'a',?,1,?,?,'Shop','shop',?,?,'','',1,'groceries',1)''', (ident, str(ident), day, day, value, kind))
    store.save_entry('Electricity', 'expense', 'utilities', 100, '2026-10')
    s = store.summary('2026-10')
    assert s['actual'] == s['card_total'] == 15000
    assert s['categories']['groceries']['actual'] == 15000
    assert s['planned'] == 10000


def test_recurring_edit_prunes_inapplicable_postings(tmp_path):
    store = FinanceStore(tmp_path)
    ident = store.save_entry('Pay', 'income', 'salary', 100, '2026-10', True)
    store.post(ident, '2026-11', True)
    store.save_entry('Pay', 'income', 'salary', 100, '2026-10', False, ident)
    store.save_entry('Pay', 'income', 'salary', 100, '2026-10', True, ident)
    assert not store.entries('2026-11')[0]['posted']


@pytest.mark.parametrize('value', ['NaN', 'Infinity', -1, 'invalid'])
def test_invalid_money_is_rejected(value):
    with pytest.raises(ValueError):
        cents(value)


def test_invalid_dates_and_postings_rejected(tmp_path):
    store = FinanceStore(tmp_path)
    with pytest.raises(ValueError):
        store.summary('2026-13')
    with pytest.raises(ValueError):
        store.post(999, '2026-10', True)
    with pytest.raises(ValueError):
        store.save_entry('Rent', 'expense', 'rent', 100, '2026-10', True, end_month='2026-09')
