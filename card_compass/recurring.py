"""Conservative monthly-charge suggestions and explicit local review decisions."""
import calendar
from collections import defaultdict
from datetime import date, datetime, timedelta, timezone
import hashlib
import json
from statistics import median_low
from .finance import shift_month
from .ledger import Ledger
from .planning import valid_day


def next_month_day(day):
    month = shift_month(day[:7], 1)
    year, number = map(int, month.split('-'))
    return date(year, number, min(int(day[8:]), calendar.monthrange(year, number)[1])).isoformat()


def detect(rows, today=None):
    """Suggestions, not confirmed subscriptions. Same account, 3+ adjacent months."""
    today = today or date.today()
    groups = defaultdict(list)
    refunds = defaultdict(list)
    for row in rows:
        try:
            day = date.fromisoformat(row['date'])
        except (KeyError, TypeError, ValueError):
            continue
        if not today-timedelta(days=366) <= day <= today or row.get('one_off'):
            continue
        group = row.get('merchant_group') or row.get('merchant_key')
        account = row.get('account_key')
        if not group or not account:
            continue
        if row.get('kind') == 'purchase' and type(row.get('amount_cents')) is int and row['amount_cents'] > 0:
            groups[account, group].append(row)
        elif row.get('kind') == 'refund':
            refunds[account, group].append(row)
    candidates = []
    for (account, merchant), charges in groups.items():
        charges.sort(key=lambda r: (r['date'], r['id']))
        # Use the most recent contiguous run. Multiple charges in a month remain ambiguous.
        months = defaultdict(list)
        for row in charges:
            months[row['date'][:7]].append(row)
        last = max(months)
        if len(months[last]) != 1 or (today-date.fromisoformat(charges[-1]['date'])).days > 70:
            continue
        run = []
        month = last
        while month in months and len(months[month]) == 1:
            run.append(months[month][0])
            month = shift_month(month, -1)
        run.reverse()
        if len(run) < 3:
            continue
        # Monthly dates can shift by up to five days, including short-month end dates.
        anchor_day = max(int(r['date'][8:]) for r in run)
        if any(abs(int(r['date'][8:])-min(anchor_day, calendar.monthrange(int(r['date'][:4]), int(r['date'][5:7]))[1])) > 5 for r in run):
            continue
        amounts = [r['amount_cents'] for r in run]
        typical = median_low(amounts)
        if max(amounts)-min(amounts) > max(100, typical//10):
            continue
        key = hashlib.sha256(json.dumps([account, merchant]).encode()).hexdigest()
        refund_rows = sorted(refunds[account, merchant], key=lambda r: (r['date'], r['id']))
        evidence = [dict(id=r['id'], date=r['date'], amount_cents=r['amount_cents'], category=r.get('category') or '') for r in run]
        refund_evidence = [dict(id=r['id'], date=r['date'], amount_cents=r['amount_cents']) for r in refund_rows]
        digest = hashlib.sha256(json.dumps([evidence, refund_evidence], sort_keys=True).encode()).hexdigest()
        latest = run[-1]
        prior = median_low(amounts[:-1])
        warnings = []
        if refund_rows:
            warnings.append('Matching refunds exist; amounts below are gross purchases. Confirm whether this is still recurring.')
        if amounts[-1] != prior:
            warnings.append('Latest charge differs from prior typical amount by USD ' + f'{(amounts[-1]-prior)/100:+,.2f}' + '. This may be a price or usage change.')
        due = next_month_day(latest['date'])
        if due < today.isoformat():
            warnings.append('Estimated next date has passed; missing imports, cancellation or a changed schedule may explain it.')
        candidates.append(dict(id=key, account_key=account, merchant_group=merchant,
            name=(latest.get('merchant_name') or merchant.title())[:120], category=latest.get('category') or '',
            cadence='monthly', amount_cents=amounts[-1], typical_cents=typical,
            first_date=run[0]['date'], last_date=latest['date'], estimated_next=due,
            history=evidence, refunds=refund_evidence, fingerprint=digest, warnings=warnings,
            source='Imported purchase dates; saved merchant aliases; heuristic monthly pattern'))
    return sorted(candidates, key=lambda r: (r['estimated_next'], r['name'], r['account_key']))


def validate_review(record):
    if not isinstance(record, dict) or record.get('version') != 1 or record.get('status') not in ('confirmed', 'dismissed'):
        raise ValueError('Invalid recurring-charge review.')
    candidate = record.get('candidate')
    if not isinstance(candidate, dict):
        raise ValueError('Recurring review is missing its evidence.')
    for key in ('id', 'fingerprint'):
        value = candidate.get(key)
        if not isinstance(value, str) or len(value) != 64 or any(c not in '0123456789abcdef' for c in value):
            raise ValueError('Invalid recurring evidence identity.')
    for key, limit in (('account_key', 250), ('merchant_group', 500), ('name', 120), ('source', 250)):
        if not isinstance(candidate.get(key), str) or not 1 <= len(candidate[key]) <= limit:
            raise ValueError('Invalid recurring-charge metadata.')
    if candidate.get('cadence') != 'monthly' or not isinstance(candidate.get('category'), str) or len(candidate['category']) > 120:
        raise ValueError('Invalid recurring cadence/category.')
    for key in ('amount_cents', 'typical_cents'):
        if type(candidate.get(key)) is not int or not 0 < candidate[key] <= 100_000_000_000_000:
            raise ValueError('Invalid recurring-charge amount.')
    for key in ('first_date', 'last_date', 'estimated_next'):
        valid_day(candidate.get(key))
    if candidate['first_date'] > candidate['last_date']:
        raise ValueError('Recurring evidence dates are out of order.')
    for key, minimum in (('history', 3), ('refunds', 0)):
        rows = candidate.get(key)
        if not isinstance(rows, list) or not minimum <= len(rows) <= 1000:
            raise ValueError('Invalid recurring source history.')
        for row in rows:
            if not isinstance(row, dict) or type(row.get('id')) is not int or row['id'] < 1 or type(row.get('amount_cents')) is not int or not abs(row['amount_cents']) <= 100_000_000_000_000:
                raise ValueError('Invalid recurring source transaction.')
            valid_day(row.get('date'))
    warnings = candidate.get('warnings')
    if not isinstance(warnings, list) or len(warnings) > 10 or any(not isinstance(v, str) or len(v) > 1000 for v in warnings):
        raise ValueError('Invalid recurring evidence warnings.')
    try:
        parsed = datetime.fromisoformat(record.get('reviewed_at', ''))
        if parsed.tzinfo is None: raise ValueError()
    except (ValueError, TypeError) as exc:
        raise ValueError('Invalid recurring review timestamp.') from exc
    return record


class RecurringStore(Ledger):
    def __init__(self, directory):
        super().__init__(directory)
        with self.connection() as db:
            db.execute('CREATE TABLE IF NOT EXISTS recurring_reviews (id TEXT PRIMARY KEY, record TEXT NOT NULL)')

    @staticmethod
    def source_rows(db):
        rows = [dict(row) for row in db.execute('SELECT t.*,a.card_id,a.issuer FROM transactions t JOIN accounts a USING(account_key)')]
        for row in rows:
            rule = Ledger.resolve(db, row['merchant_key'], row['issuer'], row['card_id'])
            row['merchant_group'] = rule['merchant_key'] if rule else row['merchant_key']
            row['merchant_name'] = rule['display_name'] if rule else row['merchant_key'].title()
        return rows

    def scan(self):
        with self.connection() as db:
            return detect(self.source_rows(db))

    def reviews(self):
        with self.connection() as db:
            return [validate_review(json.loads(row['record'])) for row in db.execute('SELECT record FROM recurring_reviews ORDER BY id')]

    def review(self, candidate, status):
        with self.connection() as db:
            db.execute('BEGIN IMMEDIATE')
            current = next((r for r in detect(self.source_rows(db)) if r['id'] == candidate['id']), None)
            if current is None or current['fingerprint'] != candidate['fingerprint']:
                raise ValueError('Imported evidence changed. Refresh and review the current suggestion before saving.')
            record = validate_review(dict(version=1, status=status, reviewed_at=datetime.now(timezone.utc).isoformat(), candidate=current))
            exists = db.execute('SELECT 1 FROM recurring_reviews WHERE id=?', (current['id'],)).fetchone()
            if not exists and db.execute('SELECT COUNT(*) FROM recurring_reviews').fetchone()[0] >= 1000:
                raise ValueError('The recurring review limit is reached. Remove old reviews before adding more.')
            db.execute('INSERT OR REPLACE INTO recurring_reviews VALUES (?,?)', (current['id'], json.dumps(record)))
        return record

    def remove_review(self, ident):
        with self.connection() as db:
            db.execute('DELETE FROM recurring_reviews WHERE id=?', (ident,))


def validate_table(db):
    tables = {row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    if 'recurring_reviews' in tables:
        rows = list(db.execute('SELECT id,record FROM recurring_reviews'))
        if len(rows) > 1000: raise ValueError('Too many recurring reviews.')
        for ident, value in rows:
            record = validate_review(json.loads(value))
            if record['candidate']['id'] != ident: raise ValueError('Recurring review identity mismatch.')
