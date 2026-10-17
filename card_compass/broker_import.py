"""Generic trades and Vanguard BUY/SELL imports with persistent deduplication."""
import csv
import json
import re
from copy import deepcopy
from datetime import datetime, timezone
from decimal import Decimal
from hashlib import sha256
from io import StringIO
from pathlib import Path
import uuid
from .investments import symbol, number, position, validate_workspace, include_purchased_symbols
from .planning import valid_day

TRADE_COLUMNS = ['account', 'transaction_id', 'date', 'ticker', 'side', 'quantity', 'price', 'fees', 'currency']
POSITION_COLUMNS = ['account', 'as_of', 'ticker', 'shares', 'currency']
ACTIVITY_COLUMNS = ['Settlement date', 'Trade date', 'Symbol', 'Name', 'Type', 'Account type',
                    'Quantity', 'Price', 'Commission & fees**', 'Amount']


def activity_number(value, *, positive=False, fees=False):
    value = value.strip()
    if fees and value.casefold() in ('', 'free'):
        return Decimal(0)
    # Dollar signs and correctly grouped thousands are used in Vanguard exports.
    if not re.fullmatch(r'\$?(?:\d+|\d{1,3}(?:,\d{3})+)(?:\.\d+)?', value):
        raise ValueError('Use a finite non-negative dollar value or share quantity.')
    result = number(value.replace('$', '').replace(',', ''), positive)
    if result > Decimal('1e9'):
        raise ValueError('Trade values must be at most 1 billion.')
    return result


def parse_trade_file(path):
    """Detect generic trades or the Vanguard custom activity report by its header."""
    path = Path(path)
    if path.stat().st_size > 10_000_000:
        raise ValueError('Import files must be at most 10 MB.')
    try:
        records = list(csv.reader(StringIO(path.read_bytes().decode('utf-8-sig')), strict=True))
    except csv.Error as exc:
        raise ValueError('Invalid CSV: ' + str(exc)) from exc
    required = {name.casefold() for name in ACTIVITY_COLUMNS}
    header_index = next((i for i, row in enumerate(records)
                         if required <= {cell.strip().casefold() for cell in row}), None)
    if header_index is None:
        return dict(rows=_parse_generic_trades(path), format='generic', ignored=0)
    fields = [cell.strip().casefold() for cell in records[header_index]]
    if len(fields) != len(set(fields)):
        raise ValueError('Activity report column names must be unique.')
    rows, ignored, count = [], 0, 0
    for line, cells in enumerate(records[header_index + 1:], header_index + 2):
        if not any(cell.strip() for cell in cells):
            continue
        if cells[0].strip().casefold() == 'disclosures':
            break
        count += 1
        if count > 10000:
            raise ValueError('Import at most 10,000 rows at once.')
        if len(cells) != len(fields):
            raise ValueError(f'Line {line}: incorrect number of CSV fields.')
        source = dict(zip(fields, (cell.strip() for cell in cells)))
        side = source['type'].casefold()
        if side not in ('buy', 'sell'):
            ignored += 1
            continue
        try:
            day = datetime.strptime(source['trade date'], '%m/%d/%Y').date().isoformat()
            valid_day(day, future=False)
            ticker = symbol(source['symbol'])
            account_type = source['account type'].upper()
            if not account_type or len(account_type) > 100:
                raise ValueError('A valid account type is required.')
            # Vanguard reports shares sold as negative; the ledger stores a
            # positive quantity and uses side to apply the reduction.
            quantity = source['quantity']
            if side == 'sell' and quantity.startswith('-'):
                quantity = quantity[1:]
            values = dict(quantity=activity_number(quantity, positive=True),
                          price=activity_number(source['price']),
                          fees=activity_number(source['commission & fees**'], fees=True))
            if side == 'sell' and values['fees'] > values['quantity'] * values['price']:
                raise ValueError('Sale fees cannot exceed proceeds.')
            # No execution IDs are supplied. Ignore report dates, row order, company
            # names and numeric formatting so overlapping exports keep the same ID.
            identity = [account_type, day, ticker,
                        *[format(values[key].normalize(), 'f') for key in ('quantity', 'price', 'fees')]]
            # Keep existing BUY IDs compatible with earlier uploads. The side
            # prefix distinguishes a buy and sell with otherwise equal values.
            external_id = 'vanguard_' + side + '_' + sha256(json.dumps(identity).encode()).hexdigest()
            rows.append(dict(account='Vanguard ' + account_type, transaction_id=external_id,
                             date=day, ticker=ticker, side=side, currency='USD', _line=line,
                             **{key: str(value) for key, value in values.items()}))
        except ValueError as exc:
            raise ValueError(f'Line {line}: {exc}') from exc
    if not rows:
        raise ValueError('The activity report contains no BUY or SELL rows to import.')
    return dict(rows=rows, format='vanguard_activity', ignored=ignored)


def parse_trades(path):
    return parse_trade_file(path)['rows']


def read_csv(path, columns):
    path = Path(path)
    if path.stat().st_size > 10_000_000:
        raise ValueError('Import files must be at most 10 MB.')
    raw = path.read_bytes().decode('utf-8-sig')
    reader = csv.DictReader(StringIO(raw))
    fields = reader.fieldnames or []
    if len(fields) != len(set(fields)) or any(key not in fields for key in columns):
        raise ValueError('Required, unique column names: ' + ', '.join(columns))
    rows = []
    for line, row in enumerate(reader, 2):
        if len(rows) >= 10000:
            raise ValueError('Import at most 10,000 rows at once.')
        if None in row or any(row.get(key) is None for key in columns):
            raise ValueError(f'Line {line}: incorrect number of CSV fields.')
        row = {key: row[key].strip() for key in columns}
        if not row['account'] or len(row['account']) > 120:
            raise ValueError(f'Line {line}: account must be 1–120 characters.')
        if row['currency'] != 'USD':
            raise ValueError(f'Line {line}: only USD investment accounts are supported; no conversion is performed.')
        row['_line'] = line
        rows.append(row)
    if not rows:
        raise ValueError('The file contains no data rows.')
    return rows


def _parse_generic_trades(path):
    rows = read_csv(path, TRADE_COLUMNS)
    seen = set()
    for row in rows:
        try:
            valid_day(row['date'], future=False)
            row['ticker'] = symbol(row['ticker'])
            row['side'] = row['side'].lower()
            if row['side'] not in ('buy', 'sell'):
                raise ValueError('Use buy or sell. Dividends, transfers and corporate actions need separate records.')
            if not 1 <= len(row['transaction_id']) <= 120:
                raise ValueError('A broker transaction_id of 1–120 characters is required for reliable deduplication.')
            key = row['account'], row['transaction_id']
            if key in seen:
                raise ValueError('Repeated transaction_id in the same account. Separate executions need separate IDs.')
            seen.add(key)
            for field in ('quantity', 'price', 'fees'):
                value = number(row[field], field == 'quantity')
                if value > Decimal('1e9'):
                    raise ValueError('Trade values must be at most 1 billion.')
                row[field] = str(value)
            if row['side'] == 'sell' and Decimal(row['fees']) > Decimal(row['quantity']) * Decimal(row['price']):
                raise ValueError('Sale fees cannot exceed proceeds.')
        except ValueError as exc:
            raise ValueError(f"Line {row['_line']}: {exc}") from exc
    return rows


def parse_positions(path):
    rows = read_csv(path, POSITION_COLUMNS)
    seen = set()
    for row in rows:
        try:
            valid_day(row['as_of'], future=False)
            row['ticker'] = symbol(row['ticker'])
            row['shares'] = str(number(row['shares']))
            key = row['account'], row['ticker']
            if key in seen:
                raise ValueError('Use one position snapshot per account and ticker.')
            seen.add(key)
        except ValueError as exc:
            raise ValueError(f"Line {row['_line']}: {exc}") from exc
    return rows


def economic_key(ticker, row):
    return (ticker, row.get('accountId', 'legacy'), row['date'], row['side'],
            *[number(row[key]) for key in ('quantity', 'price', 'fees')])


def build_preview(state, rows, mapping, new_accounts=(), snapshots=(), source_names=()):
    """Immutable preview; validate the entire ledger before offering a commit."""
    candidate = deepcopy(state)
    candidate.setdefault('investmentAccounts', [])
    candidate['investmentAccounts'].extend(deepcopy(list(new_accounts)))
    account_ids = {a['id'] for a in candidate['investmentAccounts']}
    if any(row['account'] not in mapping or mapping[row['account']] not in account_ids for row in [*rows, *snapshots]):
        raise ValueError('Map each CSV account to an investment account before previewing.')
    existing = {}
    economic = set()
    for ticker, items in candidate['trades'].items():
        for row in items:
            economic.add(economic_key(ticker, row))
            if row.get('externalId'):
                key = row.get('accountId', 'legacy'), row['externalId']
                if key in existing:
                    raise ValueError('Existing broker IDs are ambiguous; resolve duplicate records before importing.')
                existing[key] = (ticker, row)
    added, skipped, possible = [], [], []
    for source in rows:
        account_id, ticker = mapping[source['account']], source['ticker']
        key = account_id, source['transaction_id']
        ident = sha256((account_id + '\0' + source['transaction_id']).encode()).hexdigest()
        row = dict(id='broker_' + ident[:40], externalId=source['transaction_id'], accountId=account_id,
                   **{name: source[name] for name in ('date','side','quantity','price','fees')})
        if key in existing:
            old_ticker, old_row = existing[key]
            if economic_key(old_ticker, old_row) != economic_key(ticker, row):
                raise ValueError(f"Line {source['_line']}: transaction ID already exists with different values. No records have been changed.")
            skipped.append((ticker, row))
            continue
        if economic_key(ticker, row) in economic:
            possible.append((ticker, row))
        candidate['trades'].setdefault(ticker, []).append(row)
        existing[key] = (ticker, row)
        economic.add(economic_key(ticker, row))
        added.append((ticker, row))
    include_purchased_symbols(candidate)
    candidate = validate_workspace(candidate)
    reconciled = []
    for source in snapshots:
        account_id, ticker = mapping[source['account']], source['ticker']
        items = [row for row in candidate['trades'].get(ticker, []) if row.get('accountId','legacy') == account_id and row['date'] <= source['as_of']]
        actual = position(items)['quantity']
        expected = number(source['shares'])
        reconciled.append(dict(accountId=account_id, ticker=ticker, as_of=source['as_of'], recorded=actual,
                               broker=expected, difference=actual-expected))
    return dict(state=candidate, added=added, skipped=skipped, possible=possible, reconciliation=reconciled,
                base=deepcopy(state), source_names=[Path(name).name for name in source_names])


def commit_preview(store, preview, acknowledge_duplicates=False, acknowledge_variances=False):
    if store.state != preview['base']:
        raise ValueError('Investment records changed after preview. Preview again before importing.')
    if preview['possible'] and not acknowledge_duplicates:
        raise ValueError('Review possible duplicate executions before confirming the import.')
    if any(row['difference'] for row in preview['reconciliation']) and not acknowledge_variances:
        raise ValueError('Review reconciliation differences before confirming the import.')
    candidate=deepcopy(preview['state'])
    history=candidate.setdefault('brokerImports',[])
    if preview['added'] or preview['reconciliation']:
        if len(history)>=200:
            raise ValueError('Import history is full (200 runs). Export and archive the workspace before another import.')
        history.append(dict(id=uuid.uuid4().hex, importedAt=datetime.now(timezone.utc).isoformat(),
                            files=preview['source_names'], added=len(preview['added']), skipped=len(preview['skipped']),
                            possibleDuplicates=len(preview['possible']), duplicatesAcknowledged=bool(acknowledge_duplicates),
                            variancesAcknowledged=bool(acknowledge_variances),
                            reconciliation=[{key:str(value) for key,value in row.items()} for row in preview['reconciliation']]))
    store.save(candidate)


def new_account(name, kind='unknown', owner=''):
    return dict(id=uuid.uuid4().hex, name=name.strip(), kind=kind, owner=owner.strip(), currency='USD')
