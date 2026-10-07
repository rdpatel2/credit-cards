from datetime import date
import pytest
import pymupdf

from card_compass.statements import parse_statement_text, parse_pdf, merchant_key
from tests.statement_examples import BOFA, AMEX


def test_bofa_totals_and_transaction_types():
    statement = parse_statement_text(BOFA)
    assert statement.issuer == 'bofa'
    assert statement.account_ending == '0001'
    assert len(statement.transactions) == 13
    assert statement.purchases_cents == 19197
    assert not statement.warnings
    assert statement.transactions[0].kind == 'payment'
    assert statement.transactions[0].amount_cents == -25500
    assert statement.transactions[-1].kind == 'interest'
    assert statement.transactions[1].date == '2026-08-21'
    assert statement.transactions[1].posting_date == '2026-08-22'


def test_amex_multiline_itinerary_and_page_headers():
    statement = parse_statement_text(AMEX)
    assert len(statement.transactions) == 13
    assert statement.purchases_cents == 75413
    assert statement.account_ending == '00002'
    assert statement.transactions[0].issuer_category == 'GROCERY STORE'
    flight = statement.transactions[-1]
    assert flight.date == '2026-09-19'
    assert flight.amount_cents == 58680
    assert flight.description == 'AplPay DELTA AIR LINES ATLANTA'
    assert all('Account Ending' not in t.description for t in statement.transactions)


def test_bofa_missing_year_requires_explicit_closing_date():
    text = BOFA.replace('Statement Closing Date 09/17/2026', '')
    with pytest.raises(ValueError, match='year is missing'):
        parse_statement_text(text)
    result = parse_statement_text(text, closing_date='2026-09-17')
    assert result.transactions[1].date == '2026-08-21'


def test_year_rollover_and_refund():
    text = '''Bank of America\nStatement Closing Date 01/17/2027
Purchases and Adjustments
12/31 01/02 SHOP EXAMPLE NC 1234 0001 -10.00
TOTAL PURCHASES AND ADJUSTMENTS FOR THIS PERIOD -$10.00'''
    result = parse_statement_text(text)
    assert result.transactions[0].date == '2026-12-31'
    assert result.transactions[0].posting_date == '2027-01-02'
    assert result.transactions[0].kind == 'refund'
    assert not result.warnings


def test_reconciliation_detects_missing_purchases():
    statement = parse_statement_text(BOFA.replace('09/10 09/11 KNIGHTS PLAY LLC APEX NC 8457 0001 20.00\n', ''))
    assert any('mismatch' in warning for warning in statement.warnings)


def test_normalization_remembers_branches_without_assigning_categories():
    assert merchant_key('CHIPOTLE 2485 CARY NC') == merchant_key('CHIPOTLE 9999 RALEIGH NC') == 'CHIPOTLE'
    assert merchant_key('AplPay FOOD LION #1573 RALEIGH NC') == 'FOOD LION'
    assert merchant_key('TST*TAQUERIA BY KATSUJI Durham NC').startswith('TAQUERIA')
    assert merchant_key('UNFAMILIAR CAFE RALEIGH NC') != merchant_key('UNRELATED CAFE RALEIGH NC')
    assert merchant_key('STUDIO 123 RALEIGH NC') != merchant_key('STUDIO 456 RALEIGH NC')


def test_pdf_geometry_preserves_right_hand_amounts(tmp_path):
    path = tmp_path / 'sample.pdf'
    with pymupdf.open() as doc:
        page = doc.new_page(width=850, height=900)
        page.insert_text((35, 30), 'American Express Closing Date 09/21/26', fontsize=10)
        page.insert_text((35, 50), 'Account Ending 1-00002', fontsize=10)
        page.insert_text((35, 90), '08/22/26 FOOD LION #1573 RALEIGH NC', fontsize=10)
        page.insert_text((700, 90), '$34.39', fontsize=10)
        page.insert_text((35, 110), 'GROCERY STORE', fontsize=10)
        page.insert_text((35, 150), '09/19/26 AplPay DELTA AIR LINES ATLANTA', fontsize=10)
        page.insert_text((700, 150), '$586.80', fontsize=10)
        page.insert_text((35, 170), 'Ticket Number: 00000000000001 Date of Departure: 06/02', fontsize=10)
        doc.save(path)
    statement = parse_pdf(path)
    assert statement.purchases_cents == 62119
    assert len(statement.transactions) == 2
    assert statement.source_hash


def test_scanned_pdf_gives_actionable_error(tmp_path):
    path = tmp_path / 'empty.pdf'
    with pymupdf.open() as doc:
        doc.new_page()
        doc.save(path)
    with pytest.raises(ValueError, match='selectable text'):
        parse_pdf(path)


@pytest.mark.parametrize('formatted', ['-$20.00', '$-20.00', '($20.00)', '$20.00 CR'])
def test_amex_refund_formats(formatted):
    statement = parse_statement_text('American Express Closing Date 09/21/26\n09/07/26 SHOP EXAMPLE ' + formatted)
    assert statement.transactions[0].amount_cents == -2000
    assert statement.transactions[0].kind == 'refund'
    assert statement.transactions[0].description == 'SHOP EXAMPLE'


def test_bofa_generated_pdf_preserves_rows_and_total(tmp_path):
    path = tmp_path / 'bofa.pdf'
    with pymupdf.open() as doc:
        page = doc.new_page(width=1100, height=1200)
        for index, line in enumerate(BOFA.splitlines()):
            page.insert_text((30, 30 + index * 18), line, fontsize=10)
        doc.save(path)
    result = parse_pdf(path)
    assert result.purchases_cents == 19197
    assert len(result.transactions) == 13
    assert not result.warnings
