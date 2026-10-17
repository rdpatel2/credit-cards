"""Manual balance sheets, savings allocations and transparent debt scenarios."""
from datetime import date, datetime, timezone
from decimal import Decimal, ROUND_HALF_UP, localcontext, InvalidOperation
import json
import re
from .finance import cents
from .ledger import Ledger

ACCOUNT_TYPES = {
    'checking': ('Checking', 'asset'), 'savings': ('Savings', 'asset'),
    'brokerage': ('Taxable brokerage', 'asset'), 'ira': ('Traditional IRA', 'asset'),
    'roth': ('Roth IRA', 'asset'), '401k': ('401(k)', 'asset'), 'hsa': ('HSA', 'asset'),
    'real_estate': ('Real estate', 'asset'), 'vehicle': ('Vehicle', 'asset'),
    'other_asset': ('Other asset', 'asset'), 'credit_card': ('Credit-card debt', 'liability'),
    'loan': ('Loan', 'liability'), 'mortgage': ('Mortgage', 'liability'),
    'other_debt': ('Other debt', 'liability'),
}
CURRENCIES = ('USD', 'CAD', 'EUR', 'GBP', 'AUD', 'CHF')


def valid_day(value, future=True):
    if not isinstance(value, str) or not re.fullmatch(r'\d{4}-\d{2}-\d{2}', value):
        raise ValueError('Use YYYY-MM-DD for the date.')
    parsed = date.fromisoformat(value)
    if not future and parsed > date.today():
        raise ValueError('A recorded balance cannot be dated in the future.')
    return value


def money_value(value):
    result = cents(value)
    if result > 100_000_000_000_000:
        raise ValueError('Amount exceeds the supported limit.')
    return result


def currency_value(value):
    if value not in CURRENCIES:
        raise ValueError('Choose a supported currency. Currency totals are never combined without exchange rates.')
    return value


class PlanningStore(Ledger):
    def __init__(self, directory):
        super().__init__(directory)
        with self.connection() as db:
            db.executescript('''
                CREATE TABLE IF NOT EXISTS financial_accounts (
                    id INTEGER PRIMARY KEY, name TEXT NOT NULL, kind TEXT NOT NULL,
                    currency TEXT NOT NULL, owner TEXT NOT NULL,
                    apr_bps INTEGER NOT NULL DEFAULT 0 CHECK(apr_bps>=0 AND apr_bps<=10000),
                    minimum_payment_cents INTEGER NOT NULL DEFAULT 0 CHECK(minimum_payment_cents>=0)
                );
                CREATE TABLE IF NOT EXISTS balance_snapshots (
                    account_id INTEGER NOT NULL REFERENCES financial_accounts(id) ON DELETE CASCADE,
                    date TEXT NOT NULL, amount_cents INTEGER NOT NULL CHECK(amount_cents>=0),
                    PRIMARY KEY(account_id,date)
                );
                CREATE TABLE IF NOT EXISTS savings_goals (
                    id INTEGER PRIMARY KEY, name TEXT NOT NULL, kind TEXT NOT NULL,
                    currency TEXT NOT NULL, target_cents INTEGER NOT NULL CHECK(target_cents>0),
                    target_date TEXT NOT NULL, account_id INTEGER REFERENCES financial_accounts(id) ON DELETE SET NULL,
                    category TEXT NOT NULL DEFAULT ''
                );
                CREATE TABLE IF NOT EXISTS goal_snapshots (
                    goal_id INTEGER NOT NULL REFERENCES savings_goals(id) ON DELETE CASCADE,
                    date TEXT NOT NULL, saved_cents INTEGER NOT NULL CHECK(saved_cents>=0),
                    PRIMARY KEY(goal_id,date)
                );
                CREATE TABLE IF NOT EXISTS planning_events (
                    id INTEGER PRIMARY KEY, created TEXT NOT NULL, event TEXT NOT NULL,
                    entity_id INTEGER NOT NULL, details TEXT NOT NULL
                );
            ''')

    @staticmethod
    def record(db, event, ident, details):
        db.execute('INSERT INTO planning_events(created,event,entity_id,details) VALUES (?,?,?,?)',
                   (datetime.now(timezone.utc).isoformat(), event, ident, json.dumps(details)))

    def save_account(self, name, kind, currency, owner, balance, as_of, apr=0, minimum_payment=0, ident=None):
        if not name.strip() or len(name.strip()) > 120 or not owner.strip() or len(owner.strip()) > 120:
            raise ValueError('Enter an account name and owner (up to 120 characters).')
        if kind not in ACCOUNT_TYPES:
            raise ValueError('Choose an account type.')
        currency_value(currency)
        valid_day(as_of, future=False)
        value, minimum = money_value(balance), money_value(minimum_payment)
        try:
            rate = Decimal(str(apr))
        except InvalidOperation:
            raise ValueError('Enter a valid APR.') from None
        if not rate.is_finite() or not 0 <= rate <= 100:
            raise ValueError('APR must be between 0% and 100%.')
        rate_bps = int((rate * 100).quantize(Decimal('1'), rounding=ROUND_HALF_UP))
        if ACCOUNT_TYPES[kind][1] == 'asset' and (rate_bps or minimum):
            raise ValueError('Debt APR and minimum payments belong to liability accounts.')
        with self.connection() as db:
            fields = (name.strip(), kind, currency, owner.strip(), rate_bps, minimum)
            if ident is None:
                ident = db.execute('INSERT INTO financial_accounts(name,kind,currency,owner,apr_bps,minimum_payment_cents) VALUES (?,?,?,?,?,?)', fields).lastrowid
            else:
                old = db.execute('SELECT * FROM financial_accounts WHERE id=?', (ident,)).fetchone()
                if old is None:
                    raise ValueError('This account no longer exists.')
                if old['currency'] != currency or ACCOUNT_TYPES[old['kind']][1] != ACCOUNT_TYPES[kind][1]:
                    raise ValueError('Create a separate account to change its currency or asset/liability classification; historical balances must keep their meaning.')
                db.execute('UPDATE financial_accounts SET name=?,kind=?,currency=?,owner=?,apr_bps=?,minimum_payment_cents=? WHERE id=?', (*fields, ident))
            db.execute('INSERT INTO balance_snapshots VALUES (?,?,?) ON CONFLICT(account_id,date) DO UPDATE SET amount_cents=excluded.amount_cents', (ident, as_of, value))
            from .shared_sync import clear_source
            clear_source(db,'manual_account',ident,as_of)
            self.record(db, 'account_saved', ident, dict(name=name.strip(), kind=kind, currency=currency, as_of=as_of, amount_cents=value))
        return ident

    def record_balance(self, ident, balance, as_of):
        valid_day(as_of, future=False)
        value = money_value(balance)
        with self.connection() as db:
            if db.execute('SELECT id FROM financial_accounts WHERE id=?', (ident,)).fetchone() is None:
                raise ValueError('This account no longer exists.')
            db.execute('INSERT INTO balance_snapshots VALUES (?,?,?) ON CONFLICT(account_id,date) DO UPDATE SET amount_cents=excluded.amount_cents', (ident, as_of, value))
            from .shared_sync import clear_source
            clear_source(db,'manual_account',ident,as_of)
            self.record(db, 'balance_recorded', ident, dict(as_of=as_of, amount_cents=value))

    def accounts(self, as_of=None):
        as_of = valid_day(as_of or date.today().isoformat())
        with self.connection() as db:
            rows = [dict(row) for row in db.execute('''
                SELECT a.*,b.date AS as_of,b.amount_cents FROM financial_accounts a
                LEFT JOIN balance_snapshots b ON b.account_id=a.id AND b.date=(
                    SELECT MAX(date) FROM balance_snapshots WHERE account_id=a.id AND date<=?
                ) ORDER BY a.name,a.id
            ''', (as_of,))]
            self.sources(db,'manual_account',rows)
        for row in rows:
            row['side'] = ACCOUNT_TYPES[row['kind']][1]
            row['stale'] = row['as_of'] is None or (date.fromisoformat(as_of) - date.fromisoformat(row['as_of'])).days >= 30
        return rows

    def net_worth(self, as_of=None):
        totals = {}
        for row in self.accounts(as_of):
            summary = totals.setdefault(row['currency'], dict(assets=0, liabilities=0, net_worth=0, priced=0, missing=0, stale=0))
            if row['amount_cents'] is None:
                summary['missing'] += 1
                continue
            summary['assets' if row['side'] == 'asset' else 'liabilities'] += row['amount_cents']
            summary['priced'] += 1
            summary['stale'] += int(row['stale'])
            summary['net_worth'] = summary['assets'] - summary['liabilities']
        return totals

    def history(self, currency='USD'):
        currency_value(currency)
        with self.connection() as db:
            dates = [row[0] for row in db.execute('SELECT DISTINCT date FROM balance_snapshots JOIN financial_accounts a ON a.id=account_id WHERE currency=? ORDER BY date', (currency,))]
        return [dict(date=day, **self.net_worth(day).get(currency, dict(assets=0, liabilities=0, net_worth=0, priced=0, missing=0, stale=0))) for day in dates]

    def delete_account(self, ident):
        with self.connection() as db:
            from .shared_sync import clear_source
            clear_source(db,'manual_account',ident,delete=True)
            db.execute('DELETE FROM financial_accounts WHERE id=?', (ident,))
            self.record(db, 'account_deleted', ident, {})

    def save_goal(self, name, kind, currency, target, target_date, saved=0, as_of=None, account_id=None, category='', ident=None):
        if not name.strip() or len(name.strip()) > 120 or kind not in ('emergency', 'savings'):
            raise ValueError('Enter a goal name and choose emergency fund or savings.')
        currency_value(currency)
        valid_day(target_date)
        as_of = valid_day(as_of or date.today().isoformat(), future=False)
        target_cents, saved_cents = money_value(target), money_value(saved)
        if target_cents <= 0:
            raise ValueError('A goal target must be greater than zero.')
        if len(category.strip()) > 120:
            raise ValueError('Category must be at most 120 characters.')
        with self.connection() as db:
            if account_id is not None:
                account = db.execute('SELECT * FROM financial_accounts WHERE id=?', (account_id,)).fetchone()
                if account is None or account['currency'] != currency or ACCOUNT_TYPES[account['kind']][1] != 'asset':
                    raise ValueError('Link an asset account in the same currency as this goal.')
            fields = (name.strip(), kind, currency, target_cents, target_date, account_id, category.strip())
            if ident is None:
                ident = db.execute('INSERT INTO savings_goals(name,kind,currency,target_cents,target_date,account_id,category) VALUES (?,?,?,?,?,?,?)', fields).lastrowid
            else:
                old = db.execute('SELECT currency FROM savings_goals WHERE id=?', (ident,)).fetchone()
                if old is None:
                    raise ValueError('This goal no longer exists.')
                if old['currency'] != currency:
                    raise ValueError('Create a new goal to change its currency without reinterpreting earlier savings.')
                db.execute('UPDATE savings_goals SET name=?,kind=?,currency=?,target_cents=?,target_date=?,account_id=?,category=? WHERE id=?', (*fields, ident))
            db.execute('INSERT INTO goal_snapshots VALUES (?,?,?) ON CONFLICT(goal_id,date) DO UPDATE SET saved_cents=excluded.saved_cents', (ident, as_of, saved_cents))
            from .shared_sync import clear_source
            clear_source(db,'goal',ident,as_of)
            self.record(db, 'goal_saved', ident, dict(name=name.strip(), target_cents=target_cents, saved_cents=saved_cents, as_of=as_of))
        return ident

    def goals(self, as_of=None):
        as_of = valid_day(as_of or date.today().isoformat())
        with self.connection() as db:
            rows = [dict(row) for row in db.execute('''
                SELECT g.*,s.date AS as_of,COALESCE(s.saved_cents,0) AS saved_cents FROM savings_goals g
                LEFT JOIN goal_snapshots s ON s.goal_id=g.id AND s.date=(
                    SELECT MAX(date) FROM goal_snapshots WHERE goal_id=g.id AND date<=?
                ) ORDER BY g.target_date,g.id
            ''', (as_of,))]
            self.sources(db,'goal',rows)
        accounts = {row['id']: row for row in self.accounts(as_of)}
        allocations = {}
        for row in rows:
            if row['account_id']:
                allocations[row['account_id']] = allocations.get(row['account_id'], 0) + row['saved_cents']
        for row in rows:
            row['remaining_cents'] = max(0, row['target_cents'] - row['saved_cents'])
            row['progress'] = row['saved_cents'] / row['target_cents']
            days = (date.fromisoformat(row['target_date']) - date.fromisoformat(as_of)).days
            row['monthly_needed_cents'] = round(row['remaining_cents'] / max(1, days / 30.4375))
            row['overdue'] = days < 0 and row['remaining_cents'] > 0
            linked = accounts.get(row['account_id'])
            row['account_name'] = linked['name'] if linked else ''
            row['warning'] = ''
            if linked and (linked['amount_cents'] is None or allocations[row['account_id']] > linked['amount_cents']):
                row['warning'] = 'Goal allocations exceed the recorded linked balance, or its balance is unknown. Update the balance or allocations.'
            elif linked and linked['stale']:
                row['warning'] = 'Linked balance is at least 30 days old. Update it before relying on progress.'
        return rows

    def delete_goal(self, ident):
        with self.connection() as db:
            from .shared_sync import clear_source
            clear_source(db,'goal',ident,delete=True)
            db.execute('DELETE FROM savings_goals WHERE id=?', (ident,))
            self.record(db, 'goal_deleted', ident, {})

    @staticmethod
    def sources(db,kind,rows):
        from .shared_sync import source_for,source_label
        for row in rows:
            source=source_for(db,kind,row['id'],row['as_of'],row['amount_cents'] if kind=='manual_account' else row['saved_cents'])
            row['source_info']=source
            row['source']=source_label(source) if source else ('Recorded manual balance' if kind=='manual_account' else 'Recorded manual goal allocation')


def debt_scenario(accounts, extra=0, strategy='avalanche', max_months=600):
    """Fixed-rate monthly scenario, with rounded interest and a constant total payment."""
    if strategy not in ('avalanche', 'snowball'):
        raise ValueError('Choose avalanche or snowball.')
    if not isinstance(max_months, int) or not 1 <= max_months <= 600:
        raise ValueError('Choose a horizon between 1 and 600 months.')
    debts = [dict(row) for row in accounts if row.get('side') == 'liability' and row.get('amount_cents')]
    if len({row['currency'] for row in debts}) > 1:
        raise ValueError('Compare debt scenarios separately by currency; exchange rates are not assumed.')
    if any(row['amount_cents'] < 0 or row['minimum_payment_cents'] < 0 or not 0 <= row['apr_bps'] <= 10000 for row in debts):
        raise ValueError('Debt balances, minimums and APR must be valid.')
    budget = sum(row['minimum_payment_cents'] for row in debts) + money_value(extra)
    balances = {row['id']: row['amount_cents'] for row in debts}
    history, interest_total, paid_total = [], 0, 0
    months = 0
    if debts and not budget:
        return dict(paid_off=False, months=0, interest_cents=0, paid_cents=0, remaining_cents=sum(balances.values()), budget_cents=0, history=[], reason='Enter minimum payments or an extra monthly payment.')
    for month in range(1, max_months + 1):
        if not sum(balances.values()):
            break
        months = month
        available = budget
        month_interest = 0
        for row in debts:
            with localcontext() as context:
                context.prec = 80
                interest = int((Decimal(balances[row['id']]) * row['apr_bps'] / 120000).quantize(Decimal('1'), rounding=ROUND_HALF_UP))
            balances[row['id']] += interest
            month_interest += interest
            payment = min(balances[row['id']], row['minimum_payment_cents'], available)
            balances[row['id']] -= payment
            available -= payment
        ordered = sorted(debts, key=lambda row: (-row['apr_bps'], balances[row['id']], row['id']) if strategy == 'avalanche' else (balances[row['id']], -row['apr_bps'], row['id']))
        for row in ordered:
            payment = min(balances[row['id']], available)
            balances[row['id']] -= payment
            available -= payment
        interest_total += month_interest
        paid_total += budget - available
        history.append(dict(month=month, remaining_cents=sum(balances.values()), interest_cents=month_interest, payment_cents=budget-available))
    remaining = sum(balances.values())
    return dict(paid_off=remaining == 0, months=months, interest_cents=interest_total, paid_cents=paid_total,
                remaining_cents=remaining, budget_cents=budget, history=history,
                reason='' if remaining == 0 else 'Not repaid within this horizon. Payments may be too small; inspect the remaining balance.')


def validate_planning_tables(db):
    """Reject malformed new-schema backups; older backups can omit this entire family."""
    import sqlite3
    family = {'financial_accounts','balance_snapshots','savings_goals','goal_snapshots','planning_events'}
    present = {row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")} & family
    if not present:
        return
    if present != family:
        raise ValueError('The backup is missing account or goal tables.')
    factory = db.row_factory
    db.row_factory = sqlite3.Row
    try:
        for row in db.execute('SELECT * FROM financial_accounts'):
            if row['kind'] not in ACCOUNT_TYPES or row['currency'] not in CURRENCIES or not isinstance(row['name'],str) or not row['name'].strip() or not isinstance(row['owner'],str) or not row['owner'].strip():
                raise ValueError('The backup contains an invalid financial account.')
            if type(row['apr_bps']) is not int or not 0<=row['apr_bps']<=10000 or type(row['minimum_payment_cents']) is not int or not 0<=row['minimum_payment_cents']<=100_000_000_000_000:
                raise ValueError('The backup contains invalid debt assumptions.')
        for table,field in [('balance_snapshots','amount_cents'),('goal_snapshots','saved_cents')]:
            for row in db.execute('SELECT * FROM '+table):
                valid_day(row['date'],future=False)
                if type(row[field]) is not int or not 0<=row[field]<=100_000_000_000_000:
                    raise ValueError('The backup contains an invalid balance or goal allocation.')
        for row in db.execute('SELECT * FROM savings_goals'):
            valid_day(row['target_date'])
            if row['kind'] not in ('emergency','savings') or row['currency'] not in CURRENCIES or type(row['target_cents']) is not int or not 0<row['target_cents']<=100_000_000_000_000:
                raise ValueError('The backup contains an invalid savings goal.')
            if row['account_id'] is not None:
                linked = db.execute('SELECT kind,currency FROM financial_accounts WHERE id=?',(row['account_id'],)).fetchone()
                if linked is None or linked['currency']!=row['currency'] or ACCOUNT_TYPES[linked['kind']][1]!='asset':
                    raise ValueError('The backup contains an invalid savings-account link.')
    except (sqlite3.Error,KeyError,TypeError) as exc:
        raise ValueError('The account/goal backup schema cannot be read.') from exc
    finally:
        db.row_factory = factory
