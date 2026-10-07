"""Local CSV imports with explicit columns and amount-sign conventions."""
from __future__ import annotations

import csv
import io
import re
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path

from .statements import Statement, Transaction, kind_for, parse_date

FIELDS = {
    'date': ('date', 'transactiondate', 'transdate', 'posteddate', 'postingdate'),
    'description': ('description', 'payee', 'merchant', 'merchantname', 'memo'),
    'amount': ('amount', 'transactionamount'),
    'debit': ('debit', 'debits', 'debitamount', 'charge', 'charges'),
    'credit': ('credit', 'credits', 'creditamount'),
    'posting_date': ('postingdate', 'posteddate', 'postdate'),
    'reference': ('referencenumber', 'reference', 'transactionid', 'ref'),
    'account': ('accountnumber', 'account', 'accountending', 'cardnumber'),
    'category': ('category', 'merchantcategory'),
    'type': ('type', 'transactiontype'),
}


def read_csv_text(path):
    if Path(path).stat().st_size > 30_000_000:
        raise ValueError('Select a CSV smaller than 30 MB.')
    raw = Path(path).read_bytes()
    if b'\0' in raw:
        raise ValueError('Select a text CSV export, not an Excel workbook or binary file.')
    try:
        return raw.decode('utf-8-sig')
    except UnicodeDecodeError:
        return raw.decode('cp1252')


def csv_table(text):
    if not text.strip():
        raise ValueError('This CSV is empty.')
    try:
        dialect = csv.Sniffer().sniff(text[:10000], delimiters=',;\t')
        rows = list(csv.reader(io.StringIO(text), dialect))
    except csv.Error:
        rows = list(csv.reader(io.StringIO(text)))
    rows = [row for row in rows if any(cell.strip() for cell in row)]
    aliases = {name for values in FIELDS.values() for name in values}
    # Some issuer exports prepend account/balance summaries before the header.
    header = next((i for i, row in enumerate(rows[:30]) if
                   sum(re.sub(r'[^a-z0-9]', '', cell.lower()) in aliases for cell in row) >= 2), 0)
    columns = [cell.strip() for cell in rows[header]]
    if len(columns) < 2 or len(set(columns)) != len(columns) or any(not c for c in columns):
        raise ValueError('The CSV needs unique, nonempty column headings.')
    records = rows[header + 1:]
    if len(records) > 50000:
        raise ValueError('Select a CSV with no more than 50,000 transactions.')
    return columns, records


def suggested_columns(columns):
    normalized = {re.sub(r'[^a-z0-9]', '', c.lower()): c for c in columns}
    return {field: next((normalized[a] for a in aliases if a in normalized), None)
            for field, aliases in FIELDS.items()}


def csv_cents(value):
    value = value.strip()
    if not value:
        return 0
    negative = value.startswith('(') and value.endswith(')')
    if not re.fullmatch(r'\(?\s*[+-]?\s*\$?\s*[+-]?\s*(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d{1,2})?\s*\)?', value) or ('(' in value) != (')' in value):
        raise ValueError('Invalid amount. Use US dollar formatting with at most two decimal places.')
    clean = value.strip('()').replace('$', '').replace(',', '').strip()
    try:
        amount = Decimal(clean)
        if not amount.is_finite() or amount * 100 != (amount * 100).to_integral_value():
            raise ValueError('Amounts must have at most two decimal places.')
        return int(amount * 100) * (-1 if negative else 1)
    except InvalidOperation:
        raise ValueError('Invalid amount.') from None


def csv_date(value, closing=None):
    value = value.strip()
    for fmt in ('%Y-%m-%d', '%m/%d/%Y', '%m/%d/%y'):
        try:
            return datetime.strptime(value, fmt).date().isoformat()
        except ValueError:
            pass
    if re.fullmatch(r'\d{1,2}/\d{1,2}', value):
        return parse_date(value, closing)
    raise ValueError('Dates must use MM/DD/YYYY, MM/DD/YY, or YYYY-MM-DD.')


def parse_csv_text(text, mapping, convention, issuer='csv', closing_date=None, account_ending=''):
    if account_ending and not re.fullmatch(r'\d{4,5}', account_ending):
        raise ValueError('Account ending must contain only the last 4 or 5 digits.')
    columns, records = csv_table(text)
    if convention not in ('positive', 'negative', 'debit_credit'):
        raise ValueError('Choose whether purchases are positive, negative, or in debit/credit columns.')
    required = ['date', 'description'] + (['debit', 'credit'] if convention == 'debit_credit' else ['amount'])
    if any(mapping.get(field) not in columns for field in required):
        raise ValueError('Select the date, description, and required amount columns.')
    indexes = {field: columns.index(column) for field, column in mapping.items() if column in columns}
    transactions = []
    for number, row in enumerate(records, 1):
        def cell(field):
            return row[indexes[field]].strip() if field in indexes and indexes[field] < len(row) else ''
        try:
            if len(row) != len(columns):
                raise ValueError('The number of values does not match the header.')
            if not cell('description'):
                raise ValueError('A description is missing.')
            if convention == 'debit_credit':
                if not cell('debit') and not cell('credit'):
                    raise ValueError('An amount is missing.')
                debit, credit = csv_cents(cell('debit')), csv_cents(cell('credit'))
                if debit < 0 or credit < 0 or (debit and credit):
                    raise ValueError('Debit/credit rows must have one nonnegative amount.')
                amount = debit - credit
            else:
                if not cell('amount'):
                    raise ValueError('An amount is missing.')
                amount = csv_cents(cell('amount')) * (-1 if convention == 'negative' else 1)
            when = csv_date(cell('date'), closing_date)
            posted = csv_date(cell('posting_date'), closing_date) if cell('posting_date') else when
            raw_kind = cell('type').lower()
            explicit = {'payment': 'payment', 'interest': 'interest', 'fee': 'fee',
                        'transfer': 'transfer', 'refund': 'refund', 'return': 'refund'}.get(raw_kind)
            kind = explicit or kind_for(cell('description'), amount, 'purchase')
            if kind == 'refund' and amount > 0:
                raise ValueError('Refund is positive under this convention. Check the amount signs.')
            ending = re.sub(r'\D', '', cell('account'))[-(5 if issuer == 'amex' else 4):] or account_ending
            transactions.append(Transaction(when, posted, cell('description'), amount, kind,
                                            cell('reference'), ending, cell('category')))
        except ValueError as exc:
            raise ValueError(f'CSV data row {number}: {exc}') from None
    if not transactions:
        raise ValueError('This CSV contains no transactions.')
    endings = {t.account_ending for t in transactions if t.account_ending}
    if len(endings) > 1:
        raise ValueError('This CSV includes multiple accounts. Export one card account at a time.')
    close = closing_date or max(t.posting_date for t in transactions)
    date.fromisoformat(close)
    warnings = ['Check the amount convention against a known purchase and payment before saving.',
                'CSV exports do not provide a statement purchase total to reconcile.',
                'Duplicate checks need matching dates, descriptions, amounts, references, and account identity. PDF and CSV exports may differ; review overlapping imports.']
    if not closing_date:
        warnings.append('Closing date inferred from the latest posting date. Set the actual statement closing date before saving.')
    if not endings:
        warnings.append('No account ending supplied. This import uses the selected wallet card as its account identity.')
    if any(t.posting_date > close for t in transactions):
        warnings.append('Some transactions post after the selected closing date. Check the dates.')
    if (date.fromisoformat(max(t.date for t in transactions)) - date.fromisoformat(min(t.date for t in transactions))).days > 45:
        warnings.append('This export spans multiple billing periods. Spending averages treat this file as one statement period; export one billing period per file before applying imported spending.')
    return Statement(issuer, next(iter(endings), ''), close, transactions,
                     warnings=warnings, date_confirmed=bool(closing_date))
