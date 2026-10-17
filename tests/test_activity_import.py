import csv
from copy import deepcopy
from decimal import Decimal

import pytest

from card_compass.broker_import import (
    ACTIVITY_COLUMNS, build_preview, commit_preview, new_account, parse_trade_file, parse_trades,
)
from card_compass.investments import InvestmentStore


def write_activity(path, rows, columns=ACTIVITY_COLUMNS):
    with path.open('w', encoding='utf-8-sig', newline='') as stream:
        writer = csv.writer(stream)
        writer.writerow(['Custom report created on: 10/15/2026.'] + [''] * 9)
        writer.writerow(['This report only includes settled transactions.'] + [''] * 9)
        writer.writerow([''] * 10)
        writer.writerow(columns)
        writer.writerows(rows)
        writer.writerow([''] * 10)
        writer.writerow(['DISCLOSURES'] + [''] * 9)
        writer.writerow(['Disclosure text, not transaction data.'])
    return path


def buy(**changes):
    row = dict(zip(ACTIVITY_COLUMNS, ['1/6/2025', '1/3/2025', 'VOO', 'Example, Inc.',
                                    'Buy', 'CASH', '1.2500', '$100.0000', 'Free', '-$125.00']))
    row.update(changes)
    return [row[key] for key in ACTIVITY_COLUMNS]


def sell(**changes):
    return buy(**{'Type': 'Sell', 'Settlement date': '1/13/2025', 'Trade date': '1/10/2025',
                  'Quantity': '-0.5000', 'Price': '$120.0000', 'Commission & fees**': '$1.00',
                  'Amount': '$59.00', **changes})


def test_activity_imports_buys_and_sells_with_trade_dates_fractional_shares_and_fees(tmp_path):
    path = write_activity(tmp_path/'activity.csv', [
        buy(), buy(**{'Symbol': 'VIG', 'Price': '$1,234.5000', 'Commission & fees**': '$1.25'}),
        sell(),
        *[buy(**{'Type': kind, 'Quantity': '', 'Price': ''})
          for kind in ('Reinvestment', 'Dividend', 'Stock split', 'Sweep in')],
    ])
    result = parse_trade_file(path)
    assert result['format'] == 'vanguard_activity' and result['ignored'] == 4
    assert len(result['rows']) == 3
    first, second, sale = result['rows']
    assert first['date'] == '2025-01-03' and first['side'] == 'buy'
    assert first['account'] == 'Vanguard CASH' and first['currency'] == 'USD'
    assert Decimal(first['quantity']) == Decimal('1.25') and first['fees'] == '0'
    assert Decimal(second['price']) == Decimal('1234.5') and second['fees'] == '1.25'
    assert sale['side'] == 'sell' and sale['date'] == '2025-01-10'
    assert Decimal(sale['quantity']) == Decimal('0.5')
    store = InvestmentStore(tmp_path)
    preview = build_preview(store.state, result['rows'], {'Vanguard CASH': 'legacy'})
    assert not store.state['trades']
    commit_preview(store, preview)
    holdings = store.portfolio()['holdings']
    assert holdings['VOO']['quantity'] == Decimal('0.75') and holdings['VOO']['basis'] == 75
    assert holdings['VOO']['realizedGain'] == 9
    assert holdings['VIG']['basis'] == Decimal('1544.375')
    assert any('VIG' in listing['symbols'] for listing in store.state['watchlists'])


def test_overlapping_reports_skip_duplicates_after_reload_and_workspace_restore(tmp_path):
    path = write_activity(tmp_path/'original.csv', [buy(), buy()])
    store = InvestmentStore(tmp_path)
    mapping = {'Vanguard CASH': 'legacy'}
    first = build_preview(store.state, parse_trades(path), mapping)
    assert len(first['added']) == 1 and len(first['skipped']) == 1
    commit_preview(store, first)
    store = InvestmentStore(tmp_path)
    original = store.path.read_bytes()
    # New filename, report range, order, company label, date and numeric formatting.
    path = write_activity(tmp_path/'newer.csv', [
        buy(**{'Trade date': '1/10/2025', 'Settlement date': '1/13/2025', 'Quantity': '2'}),
        buy(**{'Trade date': '01/03/2025', 'Quantity': '1.25', 'Price': '100',
               'Commission & fees**': '$0.0000', 'Name': 'Renamed security', 'Type': 'BUY'}),
    ])
    preview = build_preview(store.state, parse_trades(path), mapping)
    assert len(preview['added']) == len(preview['skipped']) == 1 and not preview['possible']
    assert store.path.read_bytes() == original
    commit_preview(store, preview)
    assert store.portfolio()['holdings']['VOO']['quantity'] == Decimal('3.25')
    restored = InvestmentStore(tmp_path/'restored')
    restored.path.parent.mkdir()
    restored.save(deepcopy(store.state))
    preview = build_preview(restored.state, parse_trades(path), mapping)
    assert not preview['added'] and len(preview['skipped']) == 2
    commit_preview(restored, preview)
    assert restored.portfolio()['holdings']['VOO']['quantity'] == Decimal('3.25')
    assert len(restored.state['brokerImports']) == 2  # No extra history for a no-op.


def test_activity_identity_is_scoped_to_destination_account(tmp_path):
    rows = parse_trades(write_activity(tmp_path/'activity.csv', [buy()]))
    store = InvestmentStore(tmp_path)
    commit_preview(store, build_preview(store.state, rows, {'Vanguard CASH': 'legacy'}))
    account = new_account('Another broker account')
    preview = build_preview(store.state, rows, {'Vanguard CASH': account['id']}, [account])
    assert len(preview['added']) == 1 and not preview['skipped'] and not preview['possible']
    commit_preview(store, preview)
    assert store.portfolio()['holdings']['VOO']['quantity'] == Decimal('2.5')


def test_adding_sales_to_previous_buy_only_import_preserves_ids_and_skips_overlaps(tmp_path):
    path = write_activity(tmp_path/'activity.csv', [buy()])
    rows = parse_trades(path)
    # Stable ID from the previous BUY-only importer must remain recognized.
    assert rows[0]['transaction_id'] == 'vanguard_buy_aad1af7f8b299a5d59f8a92832de57f7fd1bbb94c4e8a51f13fc0f327c93cab1'
    store = InvestmentStore(tmp_path)
    mapping = {'Vanguard CASH': 'legacy'}
    commit_preview(store, build_preview(store.state, rows, mapping))
    write_activity(path, [sell(), buy(), sell()])
    preview = build_preview(store.state, parse_trades(path), mapping)
    assert len(preview['added']) == 1 and preview['added'][0][1]['side'] == 'sell'
    assert len(preview['skipped']) == 2 and not preview['possible']
    commit_preview(store, preview)
    store = InvestmentStore(tmp_path)
    # A positive sale quantity and changed numeric formatting describe the same execution.
    write_activity(path, [sell(**{'Type': 'SELL', 'Quantity': '0.5', 'Price': '120'}), buy()])
    preview = build_preview(store.state, parse_trades(path), mapping)
    assert not preview['added'] and len(preview['skipped']) == 2
    assert store.portfolio()['holdings']['VOO']['quantity'] == Decimal('0.75')
    assert store.portfolio()['holdings']['VOO']['realizedGain'] == 9


def test_buy_and_sell_with_identical_economics_have_separate_ids(tmp_path):
    path = write_activity(tmp_path/'activity.csv', [buy(), buy(**{'Type': 'sElL', 'Quantity': '-1.25'})])
    rows = parse_trades(path)
    assert rows[0]['transaction_id'] != rows[1]['transaction_id']
    store = InvestmentStore(tmp_path)
    preview = build_preview(store.state, rows, {'Vanguard CASH': 'legacy'})
    assert len(preview['added']) == 2 and not preview['skipped']
    commit_preview(store, preview)
    holding = store.portfolio()['holdings']['VOO']
    assert holding['quantity'] == holding['basis'] == holding['realizedGain'] == 0


def test_sell_only_report_uses_existing_purchases_and_blocks_account_oversales_atomically(tmp_path):
    path = write_activity(tmp_path/'activity.csv', [sell()])
    rows = parse_trades(path)
    store = InvestmentStore(tmp_path)
    store.save(store.state)
    before = store.path.read_bytes()
    with pytest.raises(ValueError, match='exceeds shares held'):
        build_preview(store.state, rows, {'Vanguard CASH': 'legacy'})
    assert not store.state['trades'] and store.path.read_bytes() == before
    store.trade('VOO', dict(id='earlier', accountId='legacy', date='2025-01-03',
                           side='buy', quantity=1, price=100, fees=0))
    other = new_account('Separate account')
    with pytest.raises(ValueError, match='exceeds shares held'):
        build_preview(store.state, rows, {'Vanguard CASH': other['id']}, [other])
    assert len(store.state['investmentAccounts']) == 1
    commit_preview(store, build_preview(store.state, rows, {'Vanguard CASH': 'legacy'}))
    assert store.portfolio()['holdings']['VOO']['quantity'] == Decimal('0.5')


@pytest.mark.parametrize('changes', [
    {'Quantity': '-0'}, {'Quantity': '-NaN'}, {'Quantity': '--1'},
    {'Price': '-1'}, {'Commission & fees**': '$61'}, {'Trade date': '2/30/2025'},
])
def test_invalid_sell_is_rejected_with_line_number(tmp_path, changes):
    path = write_activity(tmp_path/'bad.csv', [buy(), sell(**changes)])
    with pytest.raises(ValueError, match='Line 6:'):
        parse_trades(path)


@pytest.mark.parametrize('changes', [
    {'Trade date': '2/30/2025'}, {'Trade date': '1/1/2999'}, {'Symbol': ''},
    {'Quantity': '0'}, {'Quantity': '-1'}, {'Quantity': 'NaN'}, {'Quantity': ''},
    {'Price': '$-1'}, {'Price': '$1,00.0'}, {'Price': 'Infinity'},
    {'Commission & fees**': '-1'}, {'Account type': ''}, {'Price': '1000000001'},
])
def test_invalid_buy_prevents_import_without_modifying_saved_records(tmp_path, changes):
    store = InvestmentStore(tmp_path)
    store.save(store.state)
    before = store.path.read_bytes()
    path = write_activity(tmp_path/'bad.csv', [buy(), buy(**changes)])
    with pytest.raises(ValueError, match='Line 6:'):
        parse_trades(path)
    assert store.path.read_bytes() == before and not store.state['trades']


def test_activity_rejects_missing_or_duplicate_headers_and_truncated_rows(tmp_path):
    path = tmp_path/'bad.csv'
    for columns in (ACTIVITY_COLUMNS[:-1], [*ACTIVITY_COLUMNS, 'Symbol']):
        write_activity(path, [buy()], columns)
        with pytest.raises(ValueError, match='column'):
            parse_trades(path)
    write_activity(path, [buy()[:-1]])
    with pytest.raises(ValueError, match='incorrect number'):
        parse_trades(path)
    write_activity(path, [buy(**{'Type': 'Dividend'})])
    with pytest.raises(ValueError, match='no BUY or SELL rows'):
        parse_trades(path)
