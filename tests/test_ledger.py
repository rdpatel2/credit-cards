import pytest
from card_compass.ledger import Ledger
from card_compass.statements import parse_statement_text
from tests.statement_examples import BOFA, AMEX


def choices(rows, category='dining'):
    return {row['merchant_key']: {'name': row['merchant_name'], 'category': category, 'scope': 'global'}
            for row in rows if row['kind'] in ('purchase', 'refund') and not row['category']}


def test_first_import_and_reimport_skip_duplicates(tmp_path):
    ledger = Ledger(tmp_path)
    statement = parse_statement_text(BOFA)
    rows = ledger.preview(statement, 'bofa-card')
    assert sum(row['merchant_key'] == 'CHIPOTLE' for row in rows) == 2
    assert ledger.commit(statement, 'bofa-card', rows, choices(rows)) == 13
    remembered = ledger.remembered_card(statement)
    assert remembered == 'bofa-card'
    second = Ledger(tmp_path).preview(statement, 'bofa-card')
    assert all(row['duplicate'] and not row['include'] for row in second)
    assert all(row['category'] for row in second if row['kind'] == 'purchase')
    assert ledger.commit(statement, 'bofa-card', second, {}) == 0
    assert ledger.monthly_spending()[0]['dining'] == 191.97


def test_identical_same_day_purchases_preserved(tmp_path):
    ledger = Ledger(tmp_path)
    text = '''American Express Closing Date 09/21/26
Account Ending 1-00002
09/06/26 CHIPOTLE 2485 CARY NC $10.12
09/06/26 CHIPOTLE 2485 CARY NC $10.12'''
    statement = parse_statement_text(text)
    rows = ledger.preview(statement, 'amex-card')
    assert ledger.commit(statement, 'amex-card', rows, choices(rows)) == 2
    assert len(ledger.transactions()) == 2
    assert all(row['duplicate'] for row in ledger.preview(statement, 'amex-card'))


def test_rules_are_reused_across_cards_and_scopes(tmp_path):
    ledger = Ledger(tmp_path)
    first = parse_statement_text(BOFA)
    rows = ledger.preview(first, 'bofa-card')
    ledger.commit(first, 'bofa-card', rows, choices(rows))
    second = parse_statement_text('American Express Closing Date 10/21/26\nAccount Ending 1-00002\n10/06/26 CHIPOTLE 9999 RALEIGH NC $12.00')
    rows = ledger.preview(second, 'amex-card')
    assert rows[0]['category'] == 'dining'
    ledger.commit(second, 'amex-card', rows, {'CHIPOTLE': {'name': 'Chipotle', 'category': 'everything_else', 'scope': 'card:amex-card'}})
    assert ledger.preview(second, 'amex-card')[0]['category'] == 'everything_else'
    assert next(row for row in ledger.preview(first, 'bofa-card') if row['merchant_key'] == 'CHIPOTLE')['category'] == 'dining'


def test_unresolved_merchants_roll_back_all_changes(tmp_path):
    ledger = Ledger(tmp_path)
    statement = parse_statement_text(BOFA)
    rows = ledger.preview(statement, 'card')
    with pytest.raises(ValueError, match='Choose a category'):
        ledger.commit(statement, 'card', rows, {})
    assert ledger.transactions() == []
    assert ledger.remembered_card(statement) is None


def test_rule_changes_preserve_one_time_overrides(tmp_path):
    ledger = Ledger(tmp_path)
    statement = parse_statement_text(BOFA)
    rows = ledger.preview(statement, 'card')
    ledger.commit(statement, 'card', rows, choices(rows))
    chips = [r for r in ledger.transactions() if r['merchant_key'] == 'CHIPOTLE']
    ledger.override_transaction(chips[0]['id'], 'everything_else')
    ledger.update_rule('global', 'CHIPOTLE', 'entertainment', apply_history=True)
    chips = [r for r in ledger.transactions() if r['merchant_key'] == 'CHIPOTLE']
    assert {row['category'] for row in chips} == {'everything_else', 'entertainment'}
    assert ledger.preview(statement, 'card')[4]['category'] is not None


def test_average_per_card_including_multiple_months(tmp_path):
    ledger = Ledger(tmp_path)
    for month, value in [('09', '100.00'), ('10', '200.00')]:
        statement = parse_statement_text(f'American Express Closing Date {month}/21/26\nAccount Ending 1-00002\n{month}/06/26 SHOP EXAMPLE ${value}')
        rows = ledger.preview(statement, 'card')
        ledger.commit(statement, 'card', rows, choices(rows, 'groceries'))
    spending, months = ledger.monthly_spending()
    assert spending['groceries'] == 150
    assert months['card'] == 2


def test_alias_link_reuses_saved_merchant(tmp_path):
    ledger = Ledger(tmp_path)
    first = parse_statement_text(BOFA)
    rows = ledger.preview(first, 'card')
    ledger.commit(first, 'card', rows, choices(rows))
    second = parse_statement_text('American Express Closing Date 10/21/26\nAccount Ending 1-00002\n10/06/26 RESTAURANT BILLING LLC $12.00')
    rows = ledger.preview(second, 'amex-card')
    ledger.commit(second, 'amex-card', rows, {rows[0]['merchant_key']: {'name': 'Chipotle', 'category': 'dining'}})
    assert ledger.preview(second, 'amex-card')[0]['merchant_name'] == 'Chipotle'


def test_refunds_reduce_spend_and_payments_do_not(tmp_path):
    ledger = Ledger(tmp_path)
    statement = parse_statement_text('''American Express Closing Date 09/21/26
Account Ending 1-00002
09/06/26 SHOP EXAMPLE $100.00
09/07/26 SHOP EXAMPLE -$20.00
09/08/26 PAYMENT THANK YOU -$80.00''')
    rows = ledger.preview(statement, 'card')
    ledger.commit(statement, 'card', rows, choices(rows, 'online_retail'))
    assert ledger.monthly_spending()[0]['online_retail'] == 80


def test_account_link_can_be_corrected_without_losing_history(tmp_path):
    ledger = Ledger(tmp_path)
    statement = parse_statement_text(BOFA)
    rows = ledger.preview(statement, 'wrong-card')
    ledger.commit(statement, 'wrong-card', rows, choices(rows))
    account = ledger.accounts()[0]
    ledger.reassign_account(account['account_key'], 'correct-card')
    assert ledger.remembered_card(statement) == 'correct-card'
    assert all(row['card_id'] == 'correct-card' for row in ledger.transactions())
    assert len(ledger.transactions()) == 13


def test_import_can_be_removed_and_reimported_with_merchant_memory(tmp_path):
    ledger = Ledger(tmp_path)
    statement = parse_statement_text(BOFA)
    rows = ledger.preview(statement, 'card')
    ledger.commit(statement, 'card', rows, choices(rows))
    saved = ledger.imports()[0]
    ledger.remove_import(saved['id'])
    assert ledger.transactions() == []
    assert ledger.monthly_spending() == ({}, {})
    rows = ledger.preview(statement, 'card')
    assert all(not r['duplicate'] for r in rows)
    assert all(r['category'] for r in rows if r['kind'] == 'purchase')
