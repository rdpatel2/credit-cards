"""Recorded dividend cash and user-reviewed classification, separate from trades."""
from copy import deepcopy
from datetime import date
from decimal import Decimal
import re
import uuid
from .investments import symbol, position
from .planning import valid_day

CLASSIFICATIONS = {'unknown': 'Not yet classified', 'ordinary': 'Entirely nonqualified ordinary',
                   'qualified': 'Entirely qualified', 'mixed': 'Ordinary with a qualified portion'}


def validate_events(events, accounts):
    if not isinstance(events, list) or len(events) > 10000:
        raise ValueError('Use at most 10,000 dividend records.')
    ids = set()
    for row in events:
        if not isinstance(row, dict) or not isinstance(row.get('id'), str) or not re.fullmatch(r'[a-f0-9]{32}', row['id']) or row['id'] in ids:
            raise ValueError('Dividend records need unique IDs.')
        ids.add(row['id'])
        if row.get('accountId') not in accounts:
            raise ValueError('A dividend refers to a missing investment account.')
        if not isinstance(row.get('symbol'), str) or symbol(row['symbol']) != row['symbol']:
            raise ValueError('Use an uppercase dividend symbol.')
        valid_day(row.get('paid'), future=False)
        if row.get('exDate'):
            valid_day(row['exDate'], future=False)
            if row['exDate'] > row['paid']:
                raise ValueError('Ex-dividend date must not follow the payment date.')
        if row.get('currency') != 'USD':
            raise ValueError('Dividend records currently support USD only.')
        for field in ('grossCents', 'qualifiedCents', 'withheldCents'):
            if type(row.get(field)) is not int or not 0 <= row[field] <= 100_000_000_000_000:
                raise ValueError('Use non-negative dividend amounts in cents.')
        if row['grossCents'] <= 0 or row['qualifiedCents'] > row['grossCents'] or row['withheldCents'] > row['grossCents']:
            raise ValueError('Gross dividends must be positive; qualified and withheld amounts cannot exceed gross dividends.')
        classification = row.get('classification')
        qualified = row['qualifiedCents']
        if classification not in CLASSIFICATIONS or (classification in ('unknown', 'ordinary') and qualified != 0) or (classification == 'qualified' and qualified != row['grossCents']) or (classification == 'mixed' and not 0 < qualified < row['grossCents']):
            raise ValueError('Qualified amount must match the selected classification; it is part of gross dividends, not extra income.')
        for field, limit, required in [('source', 120, True), ('notes', 1000, False)]:
            value = row.get(field)
            if not isinstance(value, str) or len(value) > limit or required and not value.strip():
                raise ValueError('Identify the dividend source and keep notes within the supported limit.')
    return events


def save_event(state, event, acknowledge_duplicate=False):
    candidate = deepcopy(state)
    events = candidate.setdefault('dividendEvents', [])
    accounts = {r['id'] for r in candidate['investmentAccounts']}
    validate_events([event], accounts)
    existing = next((r for r in events if r['id'] == event['id']), None)
    duplicate = any(r['id'] != event['id'] and (r['accountId'], r['symbol'], r['paid'], r['grossCents']) ==
                    (event['accountId'], event['symbol'], event['paid'], event['grossCents']) for r in events)
    if duplicate and not acknowledge_duplicate:
        raise ValueError('A matching account/symbol/date/gross amount already exists. Review and acknowledge a separate payment before saving.')
    if existing:
        events[events.index(existing)] = deepcopy(event)
    else:
        events.append(deepcopy(event))
    validate_events(events, accounts)
    return candidate


def event_values(account, ticker, paid, gross, classification='unknown', qualified=0,
                 withheld=0, source='Manual statement', notes='', ex_date='', ident=None):
    def cents(value):
        amount = Decimal(str(value))
        if not amount.is_finite() or amount < 0 or amount*100 != (amount*100).to_integral_value():
            raise ValueError('Dividend money needs non-negative amounts with at most two decimals.')
        return int(amount*100)
    return dict(id=ident or uuid.uuid4().hex, accountId=account, symbol=symbol(ticker), paid=paid,
                grossCents=cents(gross), qualifiedCents=cents(qualified), withheldCents=cents(withheld),
                classification=classification, currency='USD', source=source.strip(), notes=notes.strip(), exDate=ex_date)


def year_review(state, year, account_id=None):
    if type(year) is not int or not 1900 <= year <= 2200:
        raise ValueError('Choose a valid review year.')
    accounts = {r['id']: r for r in state['investmentAccounts']}
    if account_id is not None and account_id not in accounts:
        raise ValueError('Choose an existing investment account.')
    events = state.get('dividendEvents', [])
    validate_events(events, accounts)
    result = dict(year=year, taxable=dict(grossCents=0, qualifiedCents=0, nonqualifiedCents=0,
        unclassifiedCents=0, withheldCents=0), excluded=dict(grossCents=0, count=0), events=[], disposals=[], warnings=[])
    for event in events:
        if event['paid'][:4] != str(year) or account_id and event['accountId'] != account_id:
            continue
        result['events'].append(event)
        if accounts[event['accountId']]['kind'] == 'brokerage':
            totals = result['taxable']
            totals['grossCents'] += event['grossCents']
            totals['withheldCents'] += event['withheldCents']
            if event['classification'] == 'unknown':
                totals['unclassifiedCents'] += event['grossCents']
            else:
                totals['qualifiedCents'] += event['qualifiedCents']
                totals['nonqualifiedCents'] += event['grossCents']-event['qualifiedCents']
        else:
            result['excluded']['grossCents'] += event['grossCents']
            result['excluded']['count'] += 1
    cutoff = min(date.today().isoformat(), f'{year:04d}-12-31')
    future_count = 0
    for ticker, rows in state['trades'].items():
        future_count += sum(r['date'] > date.today().isoformat() and (account_id is None or r.get('accountId','legacy') == account_id) for r in rows)
        for disposal in position([r for r in rows if r['date'] <= cutoff])['disposals']:
            if disposal['sold'][:4] == str(year) and (account_id is None or disposal['accountId'] == account_id):
                result['disposals'].append(dict(disposal, symbol=ticker, accountKind=accounts[disposal['accountId']]['kind']))
    if future_count:
        result['warnings'].append(f'{future_count} future-dated trade records are excluded from this historical review; inspect them separately.')
    if result['taxable']['unclassifiedCents']:
        result['warnings'].append('Some taxable-account dividends are unclassified. They are excluded from qualified/nonqualified subtotals until reviewed.')
    if result['excluded']['count']:
        result['warnings'].append('Retirement, HSA, unknown and other account types remain separate; their dividends are not assumed taxable distributions.')
    result['warnings'].append('This is a recorded-data review, not a tax filing package or proof of completeness. Qualified portions are user-reported and must be checked against payer statements and eligibility. No wash sales, carryforwards, corporate actions, return-of-capital adjustments, reinvestment purchases or treaty rules are inferred.')
    return result
