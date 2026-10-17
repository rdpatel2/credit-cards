"""Reviewed source-bound institution balances, never inferred trade history."""
import json
from dataclasses import dataclass
from datetime import date,datetime,timezone
from decimal import Decimal,InvalidOperation,ROUND_HALF_UP
import re
import sqlite3
from .planning import ACCOUNT_TYPES,CURRENCIES,valid_day
from .shared_sync import context,encoded,fingerprint,moment,now,table_present,planning_data,planning_matches

TABLES={'institution_balance_bindings','institution_balance_sources'}
SCHEMA='''
CREATE TABLE IF NOT EXISTS institution_balance_bindings (
 origin TEXT NOT NULL,identity TEXT NOT NULL,environment TEXT NOT NULL,connection_id TEXT NOT NULL,provider_account_id TEXT NOT NULL,
 local_id INTEGER NOT NULL UNIQUE REFERENCES financial_accounts(id) ON DELETE CASCADE,
 retrieved_at TEXT NOT NULL,provider_signature TEXT NOT NULL,local_signature TEXT NOT NULL,imported_at TEXT NOT NULL,
 PRIMARY KEY(origin,identity,environment,connection_id,provider_account_id));
CREATE TABLE IF NOT EXISTS institution_balance_sources (
 local_id INTEGER NOT NULL REFERENCES financial_accounts(id) ON DELETE CASCADE,date TEXT NOT NULL,amount_cents INTEGER NOT NULL,
 source TEXT NOT NULL,origin TEXT NOT NULL,identity TEXT NOT NULL,environment TEXT NOT NULL,connection_id TEXT NOT NULL,provider_account_id TEXT NOT NULL,
 retrieved_at TEXT NOT NULL,balance_updated_at TEXT NOT NULL,date_basis TEXT NOT NULL,balance_mode TEXT NOT NULL,imported_at TEXT NOT NULL,
 PRIMARY KEY(local_id,date));
'''
BINDING_KEYS=('origin','identity','environment','connection_id','provider_account_id','local_id','retrieved_at','provider_signature','local_signature','imported_at')
SOURCE_KEYS=('local_id','date','amount_cents','source','origin','identity','environment','connection_id','provider_account_id','retrieved_at','balance_updated_at','date_basis','balance_mode','imported_at')
ACCOUNT_KEYS={'provider_account_id','name','mask','type','subtype','currency','amount_cents','balance_updated_at','side','suggested_kind'}


def ident(value):
    if not isinstance(value,str) or not re.fullmatch('[A-Za-z0-9_-]{1,200}',value):raise ValueError('The provider returned an unsupported account/connection identity.')
    return value


def name(value,what):
    if not isinstance(value,str) or not 1<=len(value.strip())<=120:raise ValueError('Enter '+what+' (up to 120 characters).')
    return value.strip()


def balance(value):
    if isinstance(value,bool) or value is None:raise ValueError('Current balance is missing; available cash or credit limits cannot substitute for total value/debt.')
    try:number=Decimal(str(value))
    except (InvalidOperation,TypeError):raise ValueError('Current balance is invalid.') from None
    if not number.is_finite() or not 0<=number<=1000000000000:raise ValueError('Negative or unsupported current balances need a separate manual review; their sign/classification cannot be silently reversed.')
    return int((number*100).quantize(Decimal('1'),rounding=ROUND_HALF_UP))


def account_value(row):
    if not isinstance(row,dict) or not isinstance(row.get('balances'),dict):raise ValueError('Provider account balance data is missing.')
    values=row['balances'];currency=values.get('iso_currency_code')
    if currency not in CURRENCIES or values.get('unofficial_currency_code'):raise ValueError('Unsupported/unknown balance currency. No currency conversion is assumed.')
    category=row.get('type');subtype=row.get('subtype') or ''
    if category not in ('depository','credit','loan','investment'):raise ValueError('Review the unknown provider account classification manually; no asset/debt type is inferred.')
    side='liability' if category in ('credit','loan') else 'asset'
    suggested={'credit':'credit_card','loan':'mortgage' if subtype=='mortgage' else 'loan','depository':'savings' if subtype=='savings' else 'checking','investment':{'ira':'ira','roth':'roth','401k':'401k','hsa':'hsa','brokerage':'brokerage'}.get(subtype,'other_asset')}[category]
    updated=values.get('last_updated_datetime') or ''
    if updated and moment(updated)>datetime.now(timezone.utc):raise ValueError('Provider balance timestamp is in the future; review the source before importing.')
    return dict(provider_account_id=ident(row.get('account_id')),name=name(row.get('name'),'an account name'),mask=str(row.get('mask') or '')[:20],type=category,subtype=str(subtype)[:100],currency=currency,amount_cents=balance(values.get('current')),balance_updated_at=updated,side=side,suggested_kind=suggested)


def snapshot_value(snapshot,account_id):
    if not isinstance(snapshot,dict) or snapshot.get('environment') not in ('sandbox','production') or snapshot.get('status')!='ready':raise ValueError('Refresh and review a healthy saved connection first; attention/error data cannot overwrite local balances.')
    retrieved=moment(snapshot.get('lastSuccess'))
    if retrieved>datetime.now(timezone.utc):raise ValueError('Provider retrieval time is in the future; review service integrity.')
    accounts=snapshot.get('accounts')
    if not isinstance(accounts,list) or len(accounts)>100:raise ValueError('Provider account coverage is unsupported.')
    keys=[ident(row.get('account_id')) for row in accounts if isinstance(row,dict)]
    if len(keys)!=len(accounts) or len(keys)!=len(set(keys)):raise ValueError('Provider account identities are missing/duplicated; no balance was selected.')
    row=next((row for row in accounts if row['account_id']==account_id),None)
    if row is None:raise ValueError('The selected provider account disappeared; no local record was changed.')
    account=account_value(row)
    if account['balance_updated_at'] and moment(account['balance_updated_at'])>retrieved:raise ValueError('Balance timestamp follows retrieval time; review the source.')
    mode=snapshot.get('balanceRequestMode')
    if mode not in ('provider_recorded','requested_current'):raise ValueError('The balance request/source mode is missing; refresh the connection.')
    projected=dict(connection_id=ident(snapshot.get('id')),environment=snapshot['environment'],retrieved_at=snapshot['lastSuccess'],balance_mode=mode,account=account)
    # Bind the exact selected row, not unrelated provider accounts/history.
    projected['provider_signature']=fingerprint(dict(metadata=projected,selected=row))
    return projected


def clear(db,ident,day=None,delete=False):
    if table_present(db,'institution_balance_sources'):db.execute('DELETE FROM institution_balance_sources WHERE local_id=?'+(' AND date=?' if day else ''),(ident,day) if day else (ident,))
    if delete and table_present(db,'institution_balance_bindings'):db.execute('DELETE FROM institution_balance_bindings WHERE local_id=?',(ident,))


def source_for(db,ident,day,amount):
    if not day or not table_present(db,'institution_balance_sources'):return None
    row=db.execute('SELECT * FROM institution_balance_sources WHERE local_id=? AND date=? AND amount_cents=?',(ident,day,amount)).fetchone()
    return dict(row,source_type='institution_balance') if row else None


@dataclass(frozen=True)
class BalancePreview:
    _json:str
    @property
    def value(self):return json.loads(self._json)


class InstitutionBalances:
    def __init__(self,store):
        self.store=store
        with store.connection() as db:db.executescript(SCHEMA)

    @staticmethod
    def key(origin,identity,selected):return (origin,identity,selected['environment'],selected['connection_id'],selected['account']['provider_account_id'])

    def binding(self,db,key):
        row=db.execute('SELECT * FROM institution_balance_bindings WHERE origin=? AND identity=? AND environment=? AND connection_id=? AND provider_account_id=?',key).fetchone()
        return dict(row) if row else None

    def preview(self,origin,identity,snapshot,account_id,as_of,local_name,owner,kind,target=None,apr=None,minimum_payment=None):
        context(origin,identity);selected=snapshot_value(snapshot,account_id);account=selected['account'];valid_day(as_of,future=False)
        local_name=name(local_name,'a local account name');owner=name(owner,'an owner')
        if kind not in ACCOUNT_TYPES or ACCOUNT_TYPES[kind][1]!=account['side']:raise ValueError('Choose a local account type with the provider’s asset/debt classification.')
        if account['balance_updated_at']:
            if as_of!=moment(account['balance_updated_at']).astimezone().date().isoformat():raise ValueError('Keep the institution-reported balance date; download time does not replace it.')
            basis='provider_timestamp'
        else:
            if date.fromisoformat(as_of)>moment(selected['retrieved_at']).astimezone().date():raise ValueError('The reviewed balance date cannot follow the saved provider retrieval date.')
            basis='user_reviewed_unknown_provider_date'
        key=self.key(origin,identity,selected)
        with self.store.connection() as db:
            binding=self.binding(db,key)
            if binding:
                if target not in (None,binding['local_id']):raise ValueError('This provider account has a saved local mapping; detach it before choosing a different destination.')
                target=binding['local_id']
                if moment(selected['retrieved_at'])<moment(binding['retrieved_at']):raise ValueError('This saved provider snapshot is older than the imported source; refresh instead of rolling back.')
                if selected['retrieved_at']==binding['retrieved_at'] and selected['provider_signature']!=binding['provider_signature']:raise ValueError('Provider values changed without a new successful retrieval; review service integrity.')
            if target is not None and (type(target) is not int or target<1):raise ValueError('Select a valid local account destination.')
            local=planning_data(db,'manual_account',target) if target else None
            if local:
                if local['record']['currency']!=account['currency'] or ACCOUNT_TYPES[local['record']['kind']][1]!=account['side']:raise ValueError('Destination currency or asset/debt side differs; existing balances cannot be reinterpreted.')
                occupied=db.execute('SELECT * FROM institution_balance_bindings WHERE local_id=?',(target,)).fetchone()
                if occupied and tuple(occupied[k] for k in ('origin','identity','environment','connection_id','provider_account_id'))!=key:raise ValueError('The destination is bound to another institution account/service identity.')
                if table_present(db,'service_copies') and db.execute("SELECT 1 FROM service_copies WHERE kind='manual_account' AND local_id=?",(target,)).fetchone():raise ValueError('This account is bound to a shared snapshot; detach that mapping before choosing an institution source.')
            signature=fingerprint(local) if local else None
            duplicates=planning_matches(db,'financial_accounts',local_name,account['currency']) if target is None else []
            source=source_for(db,target,as_of,account['amount_cents']) if target else None
        debt_terms=None
        if target is None and account['side']=='liability':
            if apr is None or minimum_payment is None or str(apr).strip()=='' or str(minimum_payment).strip()=='':raise ValueError('A new debt needs user-reviewed APR and minimum payment; provider balances do not supply those terms. Enter them or select an existing reviewed debt account.')
            try:rate=Decimal(str(apr))
            except InvalidOperation:raise ValueError('Enter a finite APR from 0% to 100%.') from None
            if not rate.is_finite() or not 0<=rate<=100:raise ValueError('Enter a finite APR from 0% to 100%.')
            debt_terms=dict(apr_bps=int((rate*100).quantize(Decimal('1'),rounding=ROUND_HALF_UP)),minimum_payment_cents=balance(minimum_payment))
        change=dict(name=local_name,owner=owner,kind=kind,as_of=as_of,date_basis=basis,debt_terms=debt_terms)
        unchanged=bool(binding and source and source['retrieved_at']==selected['retrieved_at'] and source['date_basis']==basis and binding['provider_signature']==selected['provider_signature'] and binding['local_signature']==signature and local['record']['name']==local_name and local['record']['owner']==owner and local['record']['kind']==kind)
        return BalancePreview(encoded(dict(origin=origin,identity=identity,selected=selected,change=change,target=target,binding=binding,local=local,signature=signature,possibleDuplicates=duplicates,unchanged=unchanged)))

    def commit(self,preview,verified_snapshot,reviewed=False,replace_local=False,duplicates_reviewed=False,sandbox_reviewed=False):
        try:return self._commit(preview,verified_snapshot,reviewed,replace_local,duplicates_reviewed,sandbox_reviewed)
        except sqlite3.Error:raise ValueError('Local institution balance could not be saved. Nothing was committed; check storage and rebuild the review.') from None

    def _commit(self,preview,verified_snapshot,reviewed,replace_local,duplicates_reviewed,sandbox_reviewed):
        value=preview.value;selected=value['selected'];account=selected['account'];context(value['origin'],value['identity'])
        if not reviewed:raise ValueError('Review the exact current balance, source dates and local destination first.')
        if selected!=snapshot_value(verified_snapshot,account['provider_account_id']):raise ValueError('Provider source/access changed after review. No local balance was saved; fetch and review again.')
        if selected['environment']=='sandbox' and not sandbox_reviewed:raise ValueError('Explicitly acknowledge that sandbox balances are synthetic and will affect the selected local balance sheet.')
        if value['target'] is not None and not value['unchanged'] and not replace_local:raise ValueError('Explicitly review updating this existing local balance, including local edits.')
        if value['possibleDuplicates'] and not duplicates_reviewed:raise ValueError('Review same-name accounts before creating another asset/debt that could double count net worth.')
        key=self.key(value['origin'],value['identity'],selected);change=value['change']
        with self.store.connection() as db:
            db.execute('BEGIN IMMEDIATE')
            if self.binding(db,key)!=value['binding']:raise ValueError('Institution mapping changed since preview; rebuild the review.')
            occupied=db.execute('SELECT * FROM institution_balance_bindings WHERE local_id=?',(value['target'],)).fetchone() if value['target'] else None
            if occupied and tuple(occupied[k] for k in ('origin','identity','environment','connection_id','provider_account_id'))!=key:raise ValueError('Destination institution mapping changed after review; no balance was saved.')
            local=planning_data(db,'manual_account',value['target']) if value['target'] else None
            if (fingerprint(local) if local else None)!=value['signature']:raise ValueError('Local account/history changed since preview. Nothing was overwritten; rebuild the review.')
            if value['target'] is None and planning_matches(db,'financial_accounts',change['name'],account['currency'])!=value['possibleDuplicates']:raise ValueError('Possible duplicate accounts changed since preview; review before importing.')
            if value['target'] and table_present(db,'service_copies') and db.execute("SELECT 1 FROM service_copies WHERE kind='manual_account' AND local_id=?",(value['target'],)).fetchone():raise ValueError('Shared-source mapping changed since preview; no institution balance was saved.')
            if value['unchanged']:return dict(status='unchanged',localId=value['target'])
            if not value['binding'] and db.execute('SELECT COUNT(*) FROM institution_balance_bindings').fetchone()[0]>=500:raise ValueError('Institution mapping capacity reached; detach an unused mapping.')
            ident=value['target']
            if ident is None:
                terms=change['debt_terms'] or dict(apr_bps=0,minimum_payment_cents=0)
                ident=db.execute('INSERT INTO financial_accounts(name,kind,currency,owner,apr_bps,minimum_payment_cents) VALUES (?,?,?,?,?,?)',(change['name'],change['kind'],account['currency'],change['owner'],terms['apr_bps'],terms['minimum_payment_cents'])).lastrowid
            else:db.execute('UPDATE financial_accounts SET name=?,kind=?,owner=? WHERE id=?',(change['name'],change['kind'],change['owner'],ident))
            db.execute('INSERT INTO balance_snapshots VALUES (?,?,?) ON CONFLICT(account_id,date) DO UPDATE SET amount_cents=excluded.amount_cents',(ident,change['as_of'],account['amount_cents']))
            from .shared_sync import clear_source
            clear_source(db,'manual_account',ident,change['as_of'])
            imported=now();source='Plaid '+selected['environment']+' current balance; '+('provider timestamp' if change['date_basis']=='provider_timestamp' else 'user-reviewed date, provider timestamp unavailable')
            if db.execute('SELECT COUNT(*) FROM institution_balance_sources').fetchone()[0]>=10000:raise ValueError('Institution source-history capacity reached; nothing was committed.')
            binding=dict(zip(BINDING_KEYS,(*key,ident,selected['retrieved_at'],selected['provider_signature'],fingerprint(planning_data(db,'manual_account',ident)),imported)))
            db.execute('INSERT INTO institution_balance_bindings VALUES (?,?,?,?,?,?,?,?,?,?) ON CONFLICT(origin,identity,environment,connection_id,provider_account_id) DO UPDATE SET retrieved_at=excluded.retrieved_at,provider_signature=excluded.provider_signature,local_signature=excluded.local_signature,imported_at=excluded.imported_at',tuple(binding[k] for k in BINDING_KEYS))
            db.execute('INSERT INTO institution_balance_sources VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)',(ident,change['as_of'],account['amount_cents'],source,value['origin'],value['identity'],selected['environment'],selected['connection_id'],account['provider_account_id'],selected['retrieved_at'],account['balance_updated_at'],change['date_basis'],selected['balance_mode'],imported))
            self.store.record(db,'institution_balance_imported',ident,dict(connection_id=selected['connection_id'],provider_account_id=account['provider_account_id'],environment=selected['environment'],as_of=change['as_of'],retrieved_at=selected['retrieved_at'],date_basis=change['date_basis']))
        return dict(status='imported',localId=ident)

    def detach(self,origin,identity,environment,connection_id,account_id):
        context(origin,identity);key=(origin,identity,environment,connection_id,account_id)
        with self.store.connection() as db:
            binding=self.binding(db,key)
            if not binding:raise ValueError('No local mapping for this provider account and service identity.')
            db.execute('DELETE FROM institution_balance_bindings WHERE origin=? AND identity=? AND environment=? AND connection_id=? AND provider_account_id=?',key)
            self.store.record(db,'institution_balance_detached',binding['local_id'],dict(connection_id=connection_id,provider_account_id=account_id))


def validate_tables(db):
    present={table for table in TABLES if table_present(db,table)}
    if not present:return
    if present!=TABLES:raise ValueError('Institution balance backup is missing mapping/source tables.')
    factory=db.row_factory;db.row_factory=sqlite3.Row
    try:
        bindings=[dict(row) for row in db.execute('SELECT * FROM institution_balance_bindings')];sources=[dict(row) for row in db.execute('SELECT * FROM institution_balance_sources')]
        if len(bindings)>500 or len(sources)>10000:raise ValueError('Institution backup exceeds mapping/source limits.')
        seen_remote=set();seen_local=set();seen_sources=set()
        for row in bindings+sources:
            expected=set(BINDING_KEYS) if 'provider_signature' in row else set(SOURCE_KEYS)
            if set(row)!=expected:raise ValueError('Invalid institution provenance schema; extra fields are excluded.')
            context(row['origin'],row['identity']);ident(row['connection_id']);ident(row['provider_account_id'])
            if row['environment'] not in ('sandbox','production') or type(row['local_id']) is not int or row['local_id']<1:raise ValueError('Invalid institution account mapping.')
            moment(row['retrieved_at']);moment(row['imported_at']);local=planning_data(db,'manual_account',row['local_id'])
            if 'provider_signature' in row:
                key=tuple(row[k] for k in ('origin','identity','environment','connection_id','provider_account_id'))
                if key in seen_remote or row['local_id'] in seen_local:raise ValueError('Duplicate institution mappings in backup.')
                seen_remote.add(key);seen_local.add(row['local_id'])
                if any(not isinstance(row[k],str) or not re.fullmatch('[a-f0-9]{64}',row[k]) for k in ('provider_signature','local_signature')):raise ValueError('Invalid institution source checksum.')
                if table_present(db,'service_copies') and db.execute("SELECT 1 FROM service_copies WHERE kind='manual_account' AND local_id=?",(row['local_id'],)).fetchone():raise ValueError('Conflicting shared/institution mappings in backup.')
            else:
                key=row['local_id'],row['date']
                if key in seen_sources:raise ValueError('Duplicate institution balance provenance in backup.')
                seen_sources.add(key)
                if table_present(db,'service_record_sources') and db.execute("SELECT 1 FROM service_record_sources WHERE kind='manual_account' AND local_id=? AND date=?",key).fetchone():raise ValueError('Conflicting shared/institution provenance in backup.')
                valid_day(row['date'],future=False)
                if type(row['amount_cents']) is not int or not 0<=row['amount_cents']<=100000000000000 or not any(item['date']==row['date'] and item['amount_cents']==row['amount_cents'] for item in local['history']):raise ValueError('Institution source does not match its dated balance.')
                if not isinstance(row['source'],str) or not 1<=len(row['source'])<=160 or row['balance_mode'] not in ('provider_recorded','requested_current') or row['date_basis'] not in ('provider_timestamp','user_reviewed_unknown_provider_date'):raise ValueError('Invalid institution date/source description.')
                if row['balance_updated_at']:
                    updated=moment(row['balance_updated_at'])
                    if row['date_basis']!='provider_timestamp' or updated>moment(row['retrieved_at']) or updated.astimezone().date().isoformat()!=row['date']:raise ValueError('Institution reported date does not match provenance.')
                elif row['date_basis']!='user_reviewed_unknown_provider_date' or date.fromisoformat(row['date'])>moment(row['retrieved_at']).astimezone().date():raise ValueError('Missing institution timestamp needs an explicit reviewed date.')
    except (sqlite3.Error,KeyError,TypeError) as exc:raise ValueError('Institution balance backup schema cannot be read.') from exc
    finally:db.row_factory=factory
