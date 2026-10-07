"""Transactional local purchase history, merchant memory, and duplicate checks."""
from __future__ import annotations

from collections import Counter, defaultdict
from contextlib import contextmanager
from dataclasses import asdict
from datetime import date
import hashlib
import json
from pathlib import Path
import sqlite3

from .statements import Statement, merchant_key

SCHEMA = '''
CREATE TABLE IF NOT EXISTS accounts (
    account_key TEXT PRIMARY KEY, issuer TEXT NOT NULL, ending TEXT NOT NULL, card_id TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS statements (
    id INTEGER PRIMARY KEY, source_hash TEXT NOT NULL, filename TEXT NOT NULL,
    account_key TEXT NOT NULL REFERENCES accounts(account_key), closing_date TEXT NOT NULL,
    UNIQUE(source_hash, account_key, closing_date)
);
CREATE TABLE IF NOT EXISTS rules (
    scope TEXT NOT NULL, merchant_key TEXT NOT NULL, display_name TEXT NOT NULL, category TEXT NOT NULL,
    PRIMARY KEY(scope, merchant_key)
);
CREATE TABLE IF NOT EXISTS aliases (
    scope TEXT NOT NULL, alias TEXT NOT NULL, merchant_key TEXT NOT NULL,
    PRIMARY KEY(scope, alias),
    FOREIGN KEY(scope, merchant_key) REFERENCES rules(scope, merchant_key)
);
CREATE TABLE IF NOT EXISTS transactions (
    id INTEGER PRIMARY KEY, statement_id INTEGER NOT NULL REFERENCES statements(id),
    account_key TEXT NOT NULL REFERENCES accounts(account_key), fingerprint TEXT NOT NULL,
    occurrence INTEGER NOT NULL, date TEXT NOT NULL, posting_date TEXT NOT NULL,
    description TEXT NOT NULL, merchant_key TEXT NOT NULL, amount_cents INTEGER NOT NULL,
    kind TEXT NOT NULL, reference TEXT NOT NULL, issuer_category TEXT NOT NULL, page INTEGER NOT NULL,
    category TEXT, manual_override INTEGER NOT NULL DEFAULT 0,
    one_off INTEGER NOT NULL DEFAULT 0 CHECK(one_off IN (0,1)),
    UNIQUE(account_key, fingerprint, occurrence)
);
'''


def fingerprint(transaction):
    fields = [transaction.date, transaction.posting_date, ' '.join(transaction.description.upper().split()),
              transaction.amount_cents, transaction.kind, transaction.reference]
    return hashlib.sha256(json.dumps(fields).encode()).hexdigest()


def account_key(statement, card_id):
    return f'{statement.issuer}:{statement.account_ending or card_id}'


class Ledger:
    def __init__(self, directory):
        directory = Path(directory)
        directory.mkdir(parents=True, exist_ok=True)
        self.path = directory / 'transactions.sqlite3'
        with self.connection() as db:
            db.executescript(SCHEMA)
            columns = {row['name'] for row in db.execute('PRAGMA table_info(transactions)')}
            if 'one_off' not in columns:
                db.execute('ALTER TABLE transactions ADD COLUMN one_off INTEGER NOT NULL DEFAULT 0 CHECK(one_off IN (0,1))')

    @contextmanager
    def connection(self):
        db = sqlite3.connect(self.path)
        db.row_factory = sqlite3.Row
        db.execute('PRAGMA foreign_keys = ON')
        try:
            with db:
                yield db
        finally:
            db.close()

    @staticmethod
    def resolve(db, key, issuer, card_id):
        for scope in (f'card:{card_id}', f'issuer:{issuer}', 'global'):
            row = db.execute('SELECT r.* FROM rules r JOIN aliases a ON a.scope=r.scope AND a.merchant_key=r.merchant_key WHERE a.scope=? AND a.alias=?', (scope, key)).fetchone()
            if not row:
                row = db.execute('SELECT * FROM rules WHERE scope=? AND merchant_key=?', (scope, key)).fetchone()
            if row:
                return dict(row)
        return None

    def remembered_card(self, statement):
        if not statement.account_ending:
            return None
        with self.connection() as db:
            row = db.execute('SELECT card_id FROM accounts WHERE account_key=?', (account_key(statement, ''),)).fetchone()
            return row['card_id'] if row else None

    def preview(self, statement, card_id):
        counts, rows = Counter(), []
        account = account_key(statement, card_id)
        with self.connection() as db:
            binding = db.execute('SELECT card_id FROM accounts WHERE account_key=?', (account,)).fetchone()
            if binding and binding['card_id'] != card_id:
                raise ValueError('This statement account is linked to another card. Select the previously linked card.')
            for transaction in statement.transactions:
                fp = fingerprint(transaction)
                counts[fp] += 1
                occurrence = counts[fp]
                duplicate = db.execute('SELECT id FROM transactions WHERE account_key=? AND fingerprint=? AND occurrence=?', (account, fp, occurrence)).fetchone() is not None
                key = merchant_key(transaction.description)
                rule = self.resolve(db, key, statement.issuer, card_id)
                rows.append({**asdict(transaction), 'fingerprint': fp, 'occurrence': occurrence,
                             'merchant_key': key, 'category': rule['category'] if rule else None,
                             'merchant_name': rule['display_name'] if rule else key.title(),
                             'duplicate': duplicate, 'include': not duplicate,
                             'manual_override': False, 'one_off': False})
        return rows

    @staticmethod
    def save_rule(db, alias, display_name, category, scope):
        if not category or not display_name.strip() or not alias:
            raise ValueError('Merchant name and category are required.')
        key = merchant_key(display_name)
        db.execute('INSERT INTO rules VALUES (?,?,?,?) ON CONFLICT(scope,merchant_key) DO UPDATE SET display_name=excluded.display_name,category=excluded.category', (scope, key, display_name.strip(), category))
        for raw in {key, alias}:
            db.execute('INSERT INTO aliases VALUES (?,?,?) ON CONFLICT(scope,alias) DO UPDATE SET merchant_key=excluded.merchant_key', (scope, raw, key))
        return key

    def commit(self, statement, card_id, rows, mappings):
        """Commit the reviewed statement and its new merchant rules atomically."""
        if not statement.date_confirmed:
            raise ValueError('Confirm the actual statement closing date before importing.')
        date.fromisoformat(statement.closing_date)
        if not any(row['include'] for row in rows):
            return 0
        account = account_key(statement, card_id)
        count = 0
        with self.connection() as db:
            binding = db.execute('SELECT card_id FROM accounts WHERE account_key=?', (account,)).fetchone()
            if binding and binding['card_id'] != card_id:
                raise ValueError('This account is already linked to another card.')
            db.execute('INSERT OR IGNORE INTO accounts VALUES (?,?,?,?)', (account, statement.issuer, statement.account_ending, card_id))
            for key, choice in mappings.items():
                self.save_rule(db, key, choice['name'], choice['category'], choice.get('scope', 'global'))
            db.execute('INSERT OR IGNORE INTO statements(source_hash,filename,account_key,closing_date) VALUES (?,?,?,?)', (statement.source_hash, statement.filename, account, statement.closing_date))
            ident = db.execute('SELECT id FROM statements WHERE source_hash=? AND account_key=? AND closing_date=?', (statement.source_hash, account, statement.closing_date)).fetchone()['id']
            for row in rows:
                if not row['include']:
                    continue
                category = row.get('category')
                if not row.get('manual_override'):
                    rule = self.resolve(db, row['merchant_key'], statement.issuer, card_id)
                    category = rule['category'] if rule else category
                if row['kind'] in ('purchase', 'refund') and not category:
                    raise ValueError(f"Choose a category for {row['merchant_name']}.")
                occurrence = row['occurrence']
                if row.get('duplicate'):
                    # An explicitly re-included duplicate represents another purchase.
                    occurrence = db.execute('SELECT COALESCE(MAX(occurrence),0)+1 FROM transactions WHERE account_key=? AND fingerprint=?', (account, row['fingerprint'])).fetchone()[0]
                cursor = db.execute('''INSERT OR IGNORE INTO transactions(
                    statement_id,account_key,fingerprint,occurrence,date,posting_date,description,merchant_key,
                    amount_cents,kind,reference,issuer_category,page,category,manual_override,one_off)
                    VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)''',
                    (ident, account, row['fingerprint'], occurrence, row['date'], row['posting_date'], row['description'],
                     row['merchant_key'], row['amount_cents'], row['kind'], row['reference'], row['issuer_category'], row['page'], category, int(row.get('manual_override', False)), int(bool(row.get('one_off', False)))))
                count += cursor.rowcount
        return count

    def transactions(self):
        with self.connection() as db:
            return [dict(row) for row in db.execute('''SELECT t.*,a.card_id,a.issuer,s.filename,s.closing_date
                FROM transactions t JOIN accounts a USING(account_key) JOIN statements s ON s.id=t.statement_id
                ORDER BY date DESC,t.id DESC''')]

    def rules(self):
        with self.connection() as db:
            return [dict(row) for row in db.execute('SELECT * FROM rules ORDER BY display_name,scope')]

    def breakdown_rows(self):
        """Resolve saved merchant aliases without changing historical categories."""
        rows = self.transactions()
        with self.connection() as db:
            rules = {(r['scope'], r['merchant_key']): dict(r) for r in db.execute('SELECT * FROM rules')}
            aliases = {(r['scope'], r['alias']): r['merchant_key'] for r in db.execute('SELECT * FROM aliases')}
        for row in rows:
            row['merchant_group'] = row['merchant_key']
            row['merchant_name'] = row['merchant_key'].title()
            for scope in (f"card:{row['card_id']}", f"issuer:{row['issuer']}", 'global'):
                key = aliases.get((scope, row['merchant_key']), row['merchant_key'])
                rule = rules.get((scope, key))
                if rule:
                    row['merchant_group'], row['merchant_name'] = key, rule['display_name']
                    break
        return rows

    def accounts(self):
        with self.connection() as db:
            return [dict(row) for row in db.execute('SELECT a.*,COUNT(s.id) AS statement_count FROM accounts a LEFT JOIN statements s USING(account_key) GROUP BY a.account_key ORDER BY a.issuer,a.ending')]

    def reassign_account(self, key, card_id):
        with self.connection() as db:
            db.execute('UPDATE accounts SET card_id=? WHERE account_key=?', (card_id, key))

    def imports(self):
        with self.connection() as db:
            return [dict(row) for row in db.execute('''SELECT s.*,COUNT(t.id) AS transaction_count
                FROM statements s LEFT JOIN transactions t ON t.statement_id=s.id GROUP BY s.id ORDER BY s.id DESC''')]

    def remove_import(self, ident):
        with self.connection() as db:
            db.execute('DELETE FROM transactions WHERE statement_id=?', (ident,))
            db.execute('DELETE FROM statements WHERE id=?', (ident,))

    def update_rule(self, scope, key, category, apply_history=False):
        with self.connection() as db:
            db.execute('UPDATE rules SET category=? WHERE scope=? AND merchant_key=?', (category, scope, key))
            if apply_history:
                for row in db.execute('SELECT t.*,a.issuer,a.card_id FROM transactions t JOIN accounts a USING(account_key)').fetchall():
                    if row['manual_override'] or row['kind'] not in ('purchase', 'refund'):
                        continue
                    rule = self.resolve(db, row['merchant_key'], row['issuer'], row['card_id'])
                    if rule:
                        db.execute('UPDATE transactions SET category=? WHERE id=?', (rule['category'], row['id']))

    def override_transaction(self, ident, category):
        with self.connection() as db:
            db.execute('UPDATE transactions SET category=?,manual_override=1 WHERE id=? AND kind IN (\'purchase\',\'refund\')', (category, ident))

    def set_one_off(self, ident, one_off):
        with self.connection() as db:
            db.execute("UPDATE transactions SET one_off=? WHERE id=? AND kind IN ('purchase','refund')", (int(bool(one_off)), ident))

    def monthly_spending(self):
        """Average each card's spending over its imported statement closing months."""
        amounts, months = defaultdict(lambda: defaultdict(int)), defaultdict(set)
        with self.connection() as db:
            # Include zero-spend statements in the denominator.
            for row in db.execute('SELECT a.card_id,s.closing_date FROM statements s JOIN accounts a USING(account_key)'):
                months[row['card_id']].add(row['closing_date'][:7])
            for row in db.execute('SELECT a.card_id,t.category,t.amount_cents FROM transactions t JOIN accounts a USING(account_key) WHERE t.kind IN (\'purchase\',\'refund\') AND t.one_off=0'):
                if row['category']:
                    amounts[row['card_id']][row['category']] += row['amount_cents']
        totals = defaultdict(float)
        for card_id, categories in amounts.items():
            for category, amount in categories.items():
                totals[category] += amount / 100 / len(months[card_id])
        return {k: round(max(0, value), 2) for k, value in totals.items()}, {k: len(v) for k, v in months.items()}
