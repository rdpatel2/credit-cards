import pytest
from card_compass.csv_statements import csv_table, suggested_columns, parse_csv_text, read_csv_text
from card_compass.ledger import Ledger

BOFA = '''Posted Date,Reference Number,Payee,Address,Amount
09/10/2026,5032,PAYMENT - THANK YOU,,-255.00
09/11/2026,1111,FOOD LION #1573,RALEIGH NC,34.39
09/15/2026,2222,FOOD LION #1573,RALEIGH NC,-3.00
'''


def parse(text=BOFA, convention='positive', **kwargs):
    columns, _ = csv_table(text)
    return parse_csv_text(text, suggested_columns(columns), convention, **kwargs)


def test_csv_purchases_refunds_payments_and_merchant_memory(tmp_path):
    statement = parse(issuer='bofa', closing_date='2026-09-17', account_ending='0001')
    assert [(t.amount_cents, t.kind) for t in statement.transactions] == [(-25500, 'payment'), (3439, 'purchase'), (-300, 'refund')]
    assert statement.purchases_cents == 3139
    ledger = Ledger(tmp_path)
    rows = ledger.preview(statement, 'card')
    assert ledger.commit(statement, 'card', rows, {'FOOD LION': {'name': 'Food Lion', 'category': 'groceries'}}) == 3
    second = ledger.preview(statement, 'card')
    assert all(row['duplicate'] for row in second)
    assert second[1]['category'] == 'groceries'
    assert ledger.monthly_spending()[0] == {'groceries': 31.39}


def test_negative_purchase_convention_and_quoted_values():
    text = 'Date,Description,Amount\n2026-09-10,"SHOP, EXAMPLE","-1,234.56"\n2026-09-11,PAYMENT THANK YOU,2000.00\n2026-09-12,SHOP REFUND,4.00\n'
    statement = parse(text, 'negative')
    assert [t.amount_cents for t in statement.transactions] == [123456, -200000, -400]
    assert [t.kind for t in statement.transactions] == ['purchase', 'payment', 'refund']
    assert not statement.date_confirmed


def test_semicolon_debit_credit_and_bank_category_hint():
    text = 'Date;Merchant;Debit;Credit;Category;Type\n09/01/26;SHOP;20.12;;Retail;Sale\n09/02/26;SHOP;;2.10;Retail;Return\n09/03/26;CARD INTEREST;3.00;;;Interest\n'
    statement = parse(text, 'debit_credit')
    assert [t.amount_cents for t in statement.transactions] == [2012, -210, 300]
    assert [t.kind for t in statement.transactions] == ['purchase', 'refund', 'interest']
    assert statement.transactions[0].issuer_category == 'Retail'


def test_preamble_and_amex_account_ending(tmp_path):
    text = 'Account summary,Example\nBalance,100\nDate,Description,Amount,Account Number\n09/01/2026,SHOP,10.00,XXXX-1-00002\n'
    path = tmp_path / 'statement.csv'
    path.write_text(text, encoding='utf-8-sig')
    statement = parse(read_csv_text(path), issuer='amex')
    assert statement.account_ending == '00002'
    assert statement.transactions[0].account_ending == '00002'


@pytest.mark.parametrize('text,convention', [
    ('Date,Description,Amount\n09/10/26,SHOP,1.234\n', 'positive'),
    ('Date,Description,Amount\n09/10/26,SHOP,NaN\n', 'positive'),
    ('Date,Description,Amount\ninvalid,SHOP,1.00\n', 'positive'),
    ('Date,Description,Amount\n09/10/26,SHOP,\n', 'positive'),
    ('Date,Description,Amount\n09/10/26,SHOP,1.00,unexpected\n', 'positive'),
    ('Date,Description,Debit,Credit\n09/10/26,SHOP,1,2\n', 'debit_credit'),
    ('Date,Description,Debit,Credit\n09/10/26,SHOP,,\n', 'debit_credit'),
])
def test_invalid_rows_fail_without_silently_dropping_purchases(text, convention):
    with pytest.raises(ValueError, match='CSV data row 1'):
        parse(text, convention)


def test_multiple_accounts_are_rejected():
    text = 'Date,Description,Amount,Account\n09/01/26,SHOP,10,0001\n09/02/26,SHOP,10,0002\n'
    with pytest.raises(ValueError, match='multiple accounts'):
        parse(text)


def test_custom_columns_can_be_mapped():
    statement = parse_csv_text('When,Vendor,USD\n2026-09-01,SHOP,10\n',
                               {'date': 'When', 'description': 'Vendor', 'amount': 'USD'}, 'positive')
    assert statement.transactions[0].amount_cents == 1000
