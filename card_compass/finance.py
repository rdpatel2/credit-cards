"""Monthly cash flow in integer cents, sharing the existing local ledger database."""
from datetime import date
from decimal import Decimal, InvalidOperation
import re
from .ledger import Ledger


def cents(value):
    try:
        number = Decimal(str(value))
        if not number.is_finite() or number < 0:
            raise ValueError('Enter a finite, non-negative amount.')
        return int((number * 100).quantize(Decimal('1')))
    except (InvalidOperation, OverflowError):
        raise ValueError('Enter a valid dollar amount.') from None


def valid_month(month):
    if not re.fullmatch(r'\d{4}-\d{2}', month):
        raise ValueError('Use YYYY-MM for the month.')
    date.fromisoformat(month + '-01')
    return month


def shift_month(month, offset):
    year, number = map(int, valid_month(month).split('-'))
    year, number = divmod(year * 12 + number - 1 + offset, 12)
    return f'{year:04d}-{number + 1:02d}'


class FinanceStore(Ledger):
    def __init__(self, directory):
        super().__init__(directory)
        with self.connection() as db:
            db.executescript('''
                CREATE TABLE IF NOT EXISTS cash_entries (
                    id INTEGER PRIMARY KEY, name TEXT NOT NULL, kind TEXT NOT NULL,
                    category TEXT NOT NULL, amount_cents INTEGER NOT NULL CHECK(amount_cents>=0),
                    start_month TEXT NOT NULL, end_month TEXT, recurring INTEGER NOT NULL
                );
                CREATE TABLE IF NOT EXISTS cash_postings (
                    entry_id INTEGER NOT NULL REFERENCES cash_entries(id) ON DELETE CASCADE,
                    month TEXT NOT NULL, PRIMARY KEY(entry_id,month)
                );
                CREATE TABLE IF NOT EXISTS budget_targets (
                    month TEXT NOT NULL, category TEXT NOT NULL, amount_cents INTEGER NOT NULL,
                    PRIMARY KEY(month,category)
                );
                CREATE TABLE IF NOT EXISTS recurring_targets (
                    category TEXT NOT NULL, start_month TEXT NOT NULL, amount_cents INTEGER NOT NULL,
                    PRIMARY KEY(category,start_month)
                );
            ''')

    def save_entry(self, name, kind, category, amount, month, recurring=False, ident=None, end_month=None):
        valid_month(month)
        if end_month:
            valid_month(end_month)
            if end_month < month:
                raise ValueError('The end month must follow the start month.')
        if not name.strip() or kind not in ('income', 'expense') or not category.strip():
            raise ValueError('Name, category, and a valid entry type are required.')
        value = cents(amount)
        with self.connection() as db:
            values = (name.strip(), kind, category.strip(), value, month, end_month, int(recurring))
            if ident is None:
                ident = db.execute('INSERT INTO cash_entries(name,kind,category,amount_cents,start_month,end_month,recurring) VALUES (?,?,?,?,?,?,?)', values).lastrowid
            else:
                db.execute('UPDATE cash_entries SET name=?,kind=?,category=?,amount_cents=?,start_month=?,end_month=?,recurring=? WHERE id=?', (*values, ident))
                db.execute('DELETE FROM cash_postings WHERE entry_id=? AND (month<? OR (? IS NOT NULL AND month>?) OR (?=0 AND month<>?))', (ident, month, end_month, end_month, int(recurring), month))
        return ident

    def edit_occurrences(self, ident, effective_month, scope, **values):
        """Split a series atomically, preserving earlier amounts and posting history."""
        valid_month(effective_month)
        if scope not in ('month', 'future', 'all'):
            raise ValueError('Choose which months to change.')
        value = cents(values['amount'])
        name, kind, category = values['name'].strip(), values['kind'], values['category'].strip()
        if not name or kind not in ('income', 'expense') or not category:
            raise ValueError('Enter a name, type, and category.')
        with self.connection() as db:
            old = db.execute('SELECT * FROM cash_entries WHERE id=?', (ident,)).fetchone()
            if old is None:
                raise ValueError('This entry no longer exists.')
            old = dict(old)
            if effective_month < old['start_month'] or (old['end_month'] and effective_month > old['end_month']) or (not old['recurring'] and effective_month != old['start_month']):
                raise ValueError('Choose a month in this entry’s schedule.')
            if scope == 'all' or not old['recurring']:
                db.execute('UPDATE cash_entries SET name=?,kind=?,category=?,amount_cents=? WHERE id=?', (name, kind, category, value, ident))
                return ident
            postings = [r['month'] for r in db.execute('SELECT month FROM cash_postings WHERE entry_id=?', (ident,))]
            # Reuse the existing id for the past segment where possible.
            if effective_month > old['start_month']:
                db.execute('UPDATE cash_entries SET end_month=? WHERE id=?', (shift_month(effective_month, -1), ident))
                db.execute('DELETE FROM cash_postings WHERE entry_id=? AND month>=?', (ident, effective_month))
            else:
                db.execute('DELETE FROM cash_entries WHERE id=?', (ident,))
            end = effective_month if scope == 'month' else old['end_month']
            new_id = db.execute('INSERT INTO cash_entries(name,kind,category,amount_cents,start_month,end_month,recurring) VALUES (?,?,?,?,?,?,?)',
                                (name, kind, category, value, effective_month, end, int(scope == 'future'))).lastrowid
            for month in postings:
                if month >= effective_month and (end is None or month <= end):
                    db.execute('INSERT INTO cash_postings VALUES (?,?)', (new_id, month))
            after = shift_month(effective_month, 1)
            if scope == 'month' and (old['end_month'] is None or after <= old['end_month']):
                after_id = db.execute('INSERT INTO cash_entries(name,kind,category,amount_cents,start_month,end_month,recurring) VALUES (?,?,?,?,?,?,1)',
                                      (old['name'], old['kind'], old['category'], old['amount_cents'], after, old['end_month'])).lastrowid
                for month in postings:
                    if month >= after:
                        db.execute('INSERT INTO cash_postings VALUES (?,?)', (after_id, month))
            return new_id

    def end_entry(self, ident, last_month):
        valid_month(last_month)
        with self.connection() as db:
            row = db.execute('SELECT start_month FROM cash_entries WHERE id=?', (ident,)).fetchone()
            if not row or last_month < row['start_month']:
                raise ValueError('The end month must not precede the start month.')
            db.execute('UPDATE cash_entries SET end_month=? WHERE id=?', (last_month, ident))
            db.execute('DELETE FROM cash_postings WHERE entry_id=? AND month>?', (ident, last_month))

    def delete_entry(self, ident):
        with self.connection() as db:
            db.execute('DELETE FROM cash_entries WHERE id=?', (ident,))

    def entries(self, month):
        valid_month(month)
        with self.connection() as db:
            return [dict(row) for row in db.execute('''SELECT e.*, EXISTS(SELECT 1 FROM cash_postings p WHERE p.entry_id=e.id AND p.month=?) AS posted
                FROM cash_entries e WHERE start_month<=? AND (end_month IS NULL OR end_month>=?)
                AND (recurring=1 OR start_month=?) ORDER BY kind,name,id''', (month, month, month, month))]

    def post(self, ident, month, posted):
        if not any(row['id'] == ident for row in self.entries(month)):
            raise ValueError('This entry does not occur in the selected month.')
        with self.connection() as db:
            if posted:
                db.execute('INSERT OR IGNORE INTO cash_postings VALUES (?,?)', (ident, month))
            else:
                db.execute('DELETE FROM cash_postings WHERE entry_id=? AND month=?', (ident, month))

    def set_target(self, month, category, value):
        valid_month(month)
        if not category.strip():
            raise ValueError('A category is required.')
        with self.connection() as db:
            db.execute('INSERT INTO budget_targets VALUES (?,?,?) ON CONFLICT(month,category) DO UPDATE SET amount_cents=excluded.amount_cents', (month, category, cents(value)))

    def set_recurring_target(self, month, category, value):
        valid_month(month)
        if not category.strip():
            raise ValueError('A category is required.')
        with self.connection() as db:
            db.execute('INSERT OR REPLACE INTO recurring_targets VALUES (?,?,?)', (category, month, cents(value)))
            db.execute('DELETE FROM budget_targets WHERE month=? AND category=?', (month, category))

    def target_values(self, month):
        valid_month(month)
        with self.connection() as db:
            values = {r['category']: (r['amount_cents'], 'Repeats monthly') for r in db.execute('''
                SELECT r.* FROM recurring_targets r WHERE start_month=(
                    SELECT MAX(start_month) FROM recurring_targets WHERE category=r.category AND start_month<=?)''', (month,))}
            values.update({r['category']: (r['amount_cents'], 'This month only') for r in db.execute('SELECT * FROM budget_targets WHERE month=?', (month,))})
        return values

    def copy_targets(self, source, destination):
        valid_month(source)
        valid_month(destination)
        with self.connection() as db:
            db.execute('INSERT OR REPLACE INTO budget_targets SELECT ?,category,amount_cents FROM budget_targets WHERE month=?', (destination, source))

    def summary(self, month, card_plan=None):
        valid_month(month)
        entries = self.entries(month)
        target_values = self.target_values(month)
        targets = {key: value[0] for key, value in target_values.items()}
        categories = {}
        def bucket(category):
            return categories.setdefault(category, {'target': targets.get(category, cents((card_plan or {}).get(category, 0))), 'actual': 0, 'scheduled': 0})
        for category in set(targets) | set(card_plan or {}):
            bucket(category)
        income = received = 0
        for row in entries:
            if row['kind'] == 'income':
                income += row['amount_cents']
                if row['posted']:
                    received += row['amount_cents']
            else:
                b = bucket(row['category'])
                b['scheduled'] += row['amount_cents']
                if row['posted']:
                    b['actual'] += row['amount_cents']
        card_total = 0
        for row in self.transactions():
            if row['date'][:7] == month and row['kind'] in ('purchase', 'refund'):
                # Ledger refunds are already stored with negative amounts.
                bucket(row['category'] or 'uncategorized')['actual'] += row['amount_cents']
                card_total += row['amount_cents']
        for b in categories.values():
            b['planned'] = max(b['target'], b['scheduled'])
        planned = sum(b['planned'] for b in categories.values())
        actual = sum(b['actual'] for b in categories.values())
        return dict(income=income, received=received, planned=planned, actual=actual, target_sources={key: value[1] for key, value in target_values.items()},
                    remaining=income-planned, cash_flow=received-actual,
                    card_total=card_total, categories=categories, entries=entries)
