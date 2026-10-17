"""Reviewed, version-bound local copies of selected service snapshots.

No networking, credentials, background uploads or fabricated provider lots.
Planning records/bindings commit in one SQLite transaction; investment records
and their bindings commit in the same atomic workspace save.
"""
from copy import deepcopy
from dataclasses import dataclass
from datetime import datetime,timezone
import hashlib
import json
import re
import sqlite3
import uuid
from .connected_client import service_url
from .planning import ACCOUNT_TYPES,valid_day
from .sharing import validate_snapshot,investment_snapshot

TABLES={'service_copies','service_record_sources'}
SCHEMA='''
CREATE TABLE IF NOT EXISTS service_copies (
 origin TEXT NOT NULL,identity TEXT NOT NULL,remote_id TEXT NOT NULL,
 kind TEXT NOT NULL,local_id INTEGER NOT NULL,remote_version INTEGER NOT NULL,
 payload_hash TEXT NOT NULL,local_signature TEXT NOT NULL,imported_at TEXT NOT NULL,
 PRIMARY KEY(origin,identity,remote_id),UNIQUE(kind,local_id));
CREATE TABLE IF NOT EXISTS service_record_sources (
 kind TEXT NOT NULL,local_id INTEGER NOT NULL,date TEXT NOT NULL,
 amount_cents INTEGER NOT NULL,source TEXT NOT NULL,origin TEXT NOT NULL,
 identity TEXT NOT NULL,remote_id TEXT NOT NULL,remote_version INTEGER NOT NULL,
 imported_at TEXT NOT NULL,PRIMARY KEY(kind,local_id,date));
'''
BINDING_FIELDS={'origin','identity','remote_id','kind','local_id','remote_version','payload_hash','local_signature','imported_at'}


def encoded(value):return json.dumps(value,sort_keys=True,separators=(',',':'),ensure_ascii=False,allow_nan=False)
def fingerprint(value):return hashlib.sha256(encoded(value).encode()).hexdigest()
def now():return datetime.now(timezone.utc).isoformat()


def moment(value):
    if not isinstance(value,str) or len(value)>60:raise ValueError('Use a source timestamp with a time zone.')
    parsed=datetime.fromisoformat(value.replace('Z','+00:00'))
    if parsed.tzinfo is None:raise ValueError('Use a source timestamp with a time zone.')
    return parsed


def context(origin,identity):
    if not isinstance(origin,str) or len(origin)>2048:raise ValueError('Use a reviewed service origin of at most 2048 characters.')
    normalized=service_url(origin,allow_local_http=True)
    if normalized!=origin:raise ValueError('Use the reviewed service origin exactly, without a trailing slash.')
    if not isinstance(identity,str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,80}',identity):raise ValueError('Use the signed-in service identity ID, never a credential.')
    return origin,identity


def record_value(record):
    if not isinstance(record,dict) or set(record)!={'id','creator','householdId','kind','version','updated','payload'}:raise ValueError('Use a documented service snapshot with its identity, version and recorded payload.')
    for key in ('id','creator'):
        if not isinstance(record[key],str) or not re.fullmatch('[A-Za-z0-9_-]{1,80}',record[key]):raise ValueError('The selected snapshot has invalid identity metadata.')
    if record['householdId'] is not None and (not isinstance(record['householdId'],str) or not re.fullmatch('[A-Za-z0-9_-]{1,80}',record['householdId'])):raise ValueError('The snapshot has invalid sharing metadata.')
    if type(record['version']) is not int or not 1<=record['version']<=2**63-1:raise ValueError('Use a positive service snapshot version.')
    moment(record['updated']);validate_snapshot(record['kind'],record['payload'])
    return deepcopy(record)


def validate_binding(row,investment=False):
    if not isinstance(row,dict) or set(row)!=BINDING_FIELDS:raise ValueError('Invalid service-copy metadata; credentials and extra fields are excluded.')
    context(row['origin'],row['identity'])
    if not isinstance(row['remote_id'],str) or not re.fullmatch('[A-Za-z0-9_-]{1,80}',row['remote_id']):raise ValueError('Invalid copied snapshot identity.')
    if row['kind'] not in ({'investment_account'} if investment else {'manual_account','goal'}):raise ValueError('Invalid service-copy record type.')
    if investment:
        if not isinstance(row['local_id'],str) or not re.fullmatch('[A-Za-z0-9_-]{1,80}',row['local_id']) or row['local_id']=='legacy':raise ValueError('Use a separate named investment account for a service copy.')
    elif type(row['local_id']) is not int or row['local_id']<1:raise ValueError('Invalid local service-copy record.')
    if type(row['remote_version']) is not int or not 1<=row['remote_version']<=2**63-1:raise ValueError('Invalid copied snapshot version.')
    for key in ('payload_hash','local_signature'):
        if not isinstance(row[key],str) or not re.fullmatch('[a-f0-9]{64}',row[key]):raise ValueError('Invalid service-copy checksum.')
    moment(row['imported_at'])


def validate_investment_copies(rows,accounts):
    if not isinstance(rows,list) or len(rows)>99:raise ValueError('Keep at most 99 copied investment accounts.')
    remote=set();local=set()
    for row in rows:
        validate_binding(row,investment=True)
        key=(row['origin'],row['identity'],row['remote_id'])
        if key in remote or row['local_id'] in local or row['local_id'] not in accounts:raise ValueError('Invalid or duplicate copied investment account mapping.')
        remote.add(key);local.add(row['local_id'])


def table_present(db,name):return db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",(name,)).fetchone() is not None


def planning_data(db,kind,ident):
    table='financial_accounts' if kind=='manual_account' else 'savings_goals'
    history='balance_snapshots' if kind=='manual_account' else 'goal_snapshots'
    field='account_id' if kind=='manual_account' else 'goal_id'
    row=db.execute('SELECT * FROM '+table+' WHERE id=?',(ident,)).fetchone()
    if not row:raise ValueError('The local copy was deleted. Refresh the selection before importing again.')
    return dict(record=dict(row),history=[dict(r) for r in db.execute('SELECT * FROM '+history+' WHERE '+field+'=? ORDER BY date',(ident,))])


def source_for(db,kind,ident,day,amount):
    row=db.execute('SELECT * FROM service_record_sources WHERE kind=? AND local_id=? AND date=? AND amount_cents=?',(kind,ident,day,amount)).fetchone() if day and table_present(db,'service_record_sources') else None
    if row:return dict(row)
    if kind=='manual_account':
        from .institution_balances import source_for as institution_source
        return institution_source(db,ident,day,amount)
    return None


def source_label(source):return (('Institution balance · ' if source.get('source_type')=='institution_balance' else 'Service snapshot · ')+source['source'][:100])[:120]


def planning_matches(db,table,name,currency):
    return [dict(row) for row in db.execute('SELECT id,name,currency FROM '+table+' WHERE currency=? ORDER BY id',(currency,)) if row['name'].strip().casefold()==name.strip().casefold()]


def clear_source(db,kind,ident,day=None,delete=False):
    if kind=='manual_account':
        from .institution_balances import clear
        clear(db,ident,day,delete)
    if table_present(db,'service_record_sources'):
        db.execute('DELETE FROM service_record_sources WHERE kind=? AND local_id=?'+(' AND date=?' if day else ''),(kind,ident,day) if day else (kind,ident))
    if delete and table_present(db,'service_copies'):db.execute('DELETE FROM service_copies WHERE kind=? AND local_id=?',(kind,ident))


def validate_tables(db):
    present={name for name in TABLES if table_present(db,name)}
    if not present:return
    if present!=TABLES:raise ValueError('The backup is missing service-copy source/mapping tables.')
    factory=db.row_factory;db.row_factory=sqlite3.Row
    try:
        rows=[dict(row) for row in db.execute('SELECT * FROM service_copies')]
        if len(rows)>500:raise ValueError('The backup exceeds the service-copy mapping limit.')
        seen_remote=set();seen_local=set()
        for row in rows:
            validate_binding(row);planning_data(db,row['kind'],row['local_id'])
            key=row['origin'],row['identity'],row['remote_id'];local=row['kind'],row['local_id']
            if key in seen_remote or local in seen_local:raise ValueError('Duplicate service-copy mappings in backup.')
            seen_remote.add(key);seen_local.add(local)
        sources=[dict(row) for row in db.execute('SELECT * FROM service_record_sources')]
        if len(sources)>10000:raise ValueError('The backup exceeds the source-history limit.')
        seen=set()
        for row in sources:
            if set(row)!={'kind','local_id','date','amount_cents','source','origin','identity','remote_id','remote_version','imported_at'}:raise ValueError('Invalid service-copy source schema.')
            validate_binding({key:row[key] for key in BINDING_FIELDS if key in row}|dict(payload_hash='0'*64,local_signature='0'*64))
            valid_day(row['date'],future=False)
            if not isinstance(row['source'],str) or not 1<=len(row['source'].strip())<=120:raise ValueError('Invalid recorded source description.')
            data=planning_data(db,row['kind'],row['local_id']);field='amount_cents' if row['kind']=='manual_account' else 'saved_cents'
            if type(row['amount_cents']) is not int or not 0<=row['amount_cents']<=100000000000000 or not any(item['date']==row['date'] and item[field]==row['amount_cents'] for item in data['history']):raise ValueError('Recorded provenance does not match its dated financial record.')
            key=row['kind'],row['local_id'],row['date']
            if key in seen:raise ValueError('Duplicate recorded source history.')
            seen.add(key)
    except (sqlite3.Error,KeyError,TypeError) as exc:raise ValueError('The service-copy backup schema cannot be read.') from exc
    finally:db.row_factory=factory


@dataclass(frozen=True)
class CopyPreview:
    _json:str
    @property
    def value(self):return json.loads(self._json)


def compare_remote(record,binding,signature):
    if not binding:return False,False
    if record['version']<binding['remote_version']:raise ValueError('This snapshot is older than the version already copied. Refresh the service instead of rolling back local records.')
    same=record['version']==binding['remote_version']
    if same and fingerprint(record['payload'])!=binding['payload_hash']:raise ValueError('The service changed a payload without changing its version. Stop and review service integrity.')
    changed=signature!=binding['local_signature']
    return same and not changed,changed


def binding_value(origin,identity,record,ident,signature):
    return dict(origin=origin,identity=identity,remote_id=record['id'],kind=record['kind'],local_id=ident,remote_version=record['version'],payload_hash=fingerprint(record['payload']),local_signature=signature,imported_at=now())


class PlanningCopies:
    def __init__(self,store):
        self.store=store
        with store.connection() as db:db.executescript(SCHEMA)
    def binding(self,db,origin,identity,remote):
        row=db.execute('SELECT * FROM service_copies WHERE origin=? AND identity=? AND remote_id=?',(origin,identity,remote)).fetchone()
        return dict(row) if row else None
    def preview(self,origin,identity,record,target=None):
        context(origin,identity);record=record_value(record)
        if record['kind']=='investment_account':raise ValueError('Use investment-account reconciliation for investment snapshots.')
        with self.store.connection() as db:
            binding=self.binding(db,origin,identity,record['id'])
            if binding:
                if binding['kind']!=record['kind'] or target not in (None,binding['local_id']):raise ValueError('The service-copy mapping changed type or target; detach it before choosing a different record.')
                target=binding['local_id']
            if target is not None and (type(target) is not int or target<1):raise ValueError('Choose a valid local record to reconcile.')
            local=planning_data(db,record['kind'],target) if target is not None else None
            if target is not None:
                if record['kind']=='manual_account' and table_present(db,'institution_balance_bindings') and db.execute('SELECT 1 FROM institution_balance_bindings WHERE local_id=?',(target,)).fetchone():raise ValueError('This destination is bound to an institution account; detach it before choosing a shared source.')
                occupied=db.execute('SELECT * FROM service_copies WHERE kind=? AND local_id=?',(record['kind'],target)).fetchone()
                if occupied and (occupied['origin'],occupied['identity'],occupied['remote_id'])!=(origin,identity,record['id']):raise ValueError('This local record is already bound to a different service snapshot/identity.')
            self.compatible(record,local)
            signature=fingerprint(local) if local else None
            unchanged,conflict=compare_remote(record,binding,signature)
            table='financial_accounts' if record['kind']=='manual_account' else 'savings_goals'
            matches=planning_matches(db,table,record['payload']['name'],record['payload']['currency']) if target is None else []
        return CopyPreview(encoded(dict(origin=origin,identity=identity,record=record,target=target,binding=binding,local=local,signature=signature,unchanged=unchanged,conflict=conflict,possibleDuplicates=matches)))
    @staticmethod
    def compatible(record,local):
        if not local:return
        row=local['record'];payload=record['payload']
        if row['currency']!=payload['currency']:raise ValueError('Currencies differ. Create a separate local copy; earlier amounts cannot be reinterpreted through sync.')
        if record['kind']=='manual_account' and ACCOUNT_TYPES[row['kind']][1]!=ACCOUNT_TYPES[payload['accountType']][1]:raise ValueError('Asset/debt classification differs. Create a separate local copy to preserve historical meaning.')
    def commit(self,preview,reviewed=False,replace_local=False,duplicates_reviewed=False):
        try:return self._commit(preview,reviewed,replace_local,duplicates_reviewed)
        except sqlite3.Error:raise ValueError('Local financial records could not be saved. Nothing was committed; check storage and rebuild the review.') from None
    def _commit(self,preview,reviewed,replace_local,duplicates_reviewed):
        value=preview.value;record=value['record'];record_value(record);context(value['origin'],value['identity'])
        if not reviewed:raise ValueError('Review the exact source, amounts, dates and destination before copying.')
        if value['target'] is not None and not value['unchanged'] and not replace_local:raise ValueError('Explicitly review updating this existing local record.')
        if value['possibleDuplicates'] and not duplicates_reviewed:raise ValueError('Review same-name local records before creating another copy that could double-count assets/goals.')
        with self.store.connection() as db:
            db.execute('BEGIN IMMEDIATE')
            if self.binding(db,value['origin'],value['identity'],record['id'])!=value['binding']:raise ValueError('The local service mapping changed since preview. Build a fresh preview.')
            if record['kind']=='manual_account' and value['target'] and table_present(db,'institution_balance_bindings') and db.execute('SELECT 1 FROM institution_balance_bindings WHERE local_id=?',(value['target'],)).fetchone():raise ValueError('Institution mapping changed since preview; no shared snapshot was copied.')
            local=planning_data(db,record['kind'],value['target']) if value['target'] is not None else None
            if (fingerprint(local) if local else None)!=value['signature']:raise ValueError('Local records changed since preview. Nothing was overwritten; build a fresh preview.')
            if value['target'] is None:
                table='financial_accounts' if record['kind']=='manual_account' else 'savings_goals'
                matches=planning_matches(db,table,record['payload']['name'],record['payload']['currency'])
                if matches!=value['possibleDuplicates']:raise ValueError('Same-name local records changed after preview. Review possible double counting before copying.')
            if value['unchanged']:return dict(status='unchanged',kind=record['kind'],localId=value['target'])
            if value['target'] is None and db.execute('SELECT COUNT(*) FROM service_copies').fetchone()[0]>=500:raise ValueError('Service-copy mapping capacity reached; detach an unused mapping first.')
            payload=record['payload'];ident=value['target'];kind=record['kind']
            if kind=='manual_account':
                fields=payload['name'].strip(),payload['accountType'],payload['currency'],payload['owner'].strip(),payload['aprBps'],payload['minimumPaymentCents']
                if ident is None:ident=db.execute('INSERT INTO financial_accounts(name,kind,currency,owner,apr_bps,minimum_payment_cents) VALUES (?,?,?,?,?,?)',fields).lastrowid
                else:db.execute('UPDATE financial_accounts SET name=?,kind=?,currency=?,owner=?,apr_bps=?,minimum_payment_cents=? WHERE id=?',(*fields,ident))
                db.execute('INSERT INTO balance_snapshots VALUES (?,?,?) ON CONFLICT(account_id,date) DO UPDATE SET amount_cents=excluded.amount_cents',(ident,payload['asOf'],payload['amountCents']))
            else:
                fields=payload['name'].strip(),payload['goalType'],payload['currency'],payload['targetCents'],payload['targetDate']
                if ident is None:ident=db.execute('INSERT INTO savings_goals(name,kind,currency,target_cents,target_date) VALUES (?,?,?,?,?)',fields).lastrowid
                else:db.execute('UPDATE savings_goals SET name=?,kind=?,currency=?,target_cents=?,target_date=? WHERE id=?',(*fields,ident))
                db.execute('INSERT INTO goal_snapshots VALUES (?,?,?) ON CONFLICT(goal_id,date) DO UPDATE SET saved_cents=excluded.saved_cents',(ident,payload['asOf'],payload['amountCents']))
            binding=binding_value(value['origin'],value['identity'],record,ident,fingerprint(planning_data(db,kind,ident)))
            db.execute('INSERT INTO service_copies VALUES (?,?,?,?,?,?,?,?,?) ON CONFLICT(origin,identity,remote_id) DO UPDATE SET remote_version=excluded.remote_version,payload_hash=excluded.payload_hash,local_signature=excluded.local_signature,imported_at=excluded.imported_at',tuple(binding[key] for key in ('origin','identity','remote_id','kind','local_id','remote_version','payload_hash','local_signature','imported_at')))
            clear_source(db,kind,ident,payload['asOf'])
            if db.execute('SELECT COUNT(*) FROM service_record_sources').fetchone()[0]>=10000:raise ValueError('Source-history capacity reached; nothing was committed.')
            db.execute('INSERT INTO service_record_sources VALUES (?,?,?,?,?,?,?,?,?,?) ON CONFLICT(kind,local_id,date) DO UPDATE SET amount_cents=excluded.amount_cents,source=excluded.source,origin=excluded.origin,identity=excluded.identity,remote_id=excluded.remote_id,remote_version=excluded.remote_version,imported_at=excluded.imported_at',(kind,ident,payload['asOf'],payload['amountCents'],payload['source'],binding['origin'],binding['identity'],binding['remote_id'],binding['remote_version'],binding['imported_at']))
            self.store.record(db,'service_snapshot_copied',ident,dict(kind=kind,remote_id=record['id'],version=record['version'],as_of=payload['asOf']))
        return dict(status='copied',kind=kind,localId=ident)
    def detach(self,origin,identity,remote):
        context(origin,identity)
        with self.store.connection() as db:
            row=self.binding(db,origin,identity,remote)
            if not row:raise ValueError('This selected snapshot has no local mapping for the current service identity.')
            db.execute('DELETE FROM service_copies WHERE origin=? AND identity=? AND remote_id=?',(origin,identity,remote));self.store.record(db,'service_copy_detached',row['local_id'],dict(kind=row['kind'],remote_id=remote))
        # Historical provenance and financial values remain; no server write.


def investment_binding(state,origin,identity,remote):
    return next((deepcopy(row) for row in state.get('serviceCopies',[]) if (row['origin'],row['identity'],row['remote_id'])==(origin,identity,remote)),None)


def investment_signature(state,ident):
    account=next((row for row in state['investmentAccounts'] if row['id']==ident),None)
    if not account:raise ValueError('The selected local investment account no longer exists.')
    def rows(values):return [row for row in values if row.get('accountId','legacy')==ident]
    return fingerprint(dict(account=account,trades={ticker:rows(values) for ticker,values in state['trades'].items() if rows(values)},performance={key:rows(values) for key,values in state['performanceRecords'].items()},dividends=rows(state.get('dividendEvents',[]))))


def investment_preview(store,origin,identity,record,target=None):
    if store.error:raise ValueError('Resolve the unreadable investment workspace before copying service records. The original file is preserved.')
    context(origin,identity);record=record_value(record)
    if record['kind']!='investment_account':raise ValueError('Choose an investment account snapshot.')
    binding=investment_binding(store.state,origin,identity,record['id'])
    if binding:
        if target not in (None,binding['local_id']):raise ValueError('Detach the existing copy before choosing a different investment account.')
        target=binding['local_id']
    if target=='legacy':raise ValueError('Use a separate named account; the legacy unassigned ledger cannot be replaced by sync.')
    local=investment_snapshot(store.state,target) if target is not None else None
    signature=investment_signature(store.state,target) if local else None
    if target is not None and any(row['local_id']==target and (row['origin'],row['identity'],row['remote_id'])!=(origin,identity,record['id']) for row in store.state.get('serviceCopies',[])):raise ValueError('This investment account is already bound to a different service snapshot/identity.')
    if local:
        before=next(row for row in local['workspace']['investmentAccounts'] if row['id']=='shared');after=next(row for row in record['payload']['workspace']['investmentAccounts'] if row['id']=='shared')
        if before['currency']!=after['currency'] or before['kind']!=after['kind']:raise ValueError('Review a separate copy when currency/account tax treatment differs; existing history cannot be silently reclassified.')
    unchanged,conflict=compare_remote(record,binding,signature)
    duplicates=[dict(id=row['id'],name=row['name'],currency=row['currency']) for row in store.state['investmentAccounts'] if row['name'].strip().casefold()==record['payload']['name'].strip().casefold()] if target is None else []
    disk=hashlib.sha256(store.path.read_bytes()).hexdigest() if store.path.exists() else None
    return CopyPreview(encoded(dict(origin=origin,identity=identity,record=record,target=target,binding=binding,local=local,signature=signature,unchanged=unchanged,conflict=conflict,possibleDuplicates=duplicates,disk=disk)))


def investment_commit(store,preview,reviewed=False,replace_local=False,duplicates_reviewed=False):
    from .investments import validate_workspace
    if store.error:raise ValueError('Resolve the unreadable investment workspace before copying; no records were changed.')
    value=preview.value;record=record_value(value['record']);context(value['origin'],value['identity'])
    if not reviewed:raise ValueError('Review the exact investment history and destination before copying.')
    if value['target'] is not None and not value['unchanged'] and not replace_local:raise ValueError('Review full replacement of the selected account history; omitted trades, values, flows and dividends will be removed.')
    if value['possibleDuplicates'] and not duplicates_reviewed:raise ValueError('Review same-name accounts before creating another investment copy.')
    if investment_binding(store.state,value['origin'],value['identity'],record['id'])!=value['binding']:raise ValueError('The local service mapping changed. Build a fresh preview.')
    local=investment_snapshot(store.state,value['target']) if value['target'] is not None else None
    disk=hashlib.sha256(store.path.read_bytes()).hexdigest() if store.path.exists() else None
    if (investment_signature(store.state,value['target']) if local else None)!=value['signature'] or disk!=value['disk']:raise ValueError('The local investment workspace changed since preview. Nothing was overwritten; review a fresh preview.')
    if value['target'] is None and [dict(id=row['id'],name=row['name'],currency=row['currency']) for row in store.state['investmentAccounts'] if row['name'].strip().casefold()==record['payload']['name'].strip().casefold()]!=value['possibleDuplicates']:raise ValueError('Same-name investment accounts changed since preview; review possible double counting.')
    if value['unchanged']:return dict(status='unchanged',kind='investment_account',localId=value['target'])
    candidate=deepcopy(store.state);ident=value['target'] or uuid.uuid4().hex
    remote=record['payload']['workspace'];account=deepcopy(next(row for row in remote['investmentAccounts'] if row['id']=='shared'));account['id']=ident
    candidate['investmentAccounts']=[row for row in candidate['investmentAccounts'] if row['id']!=ident]+[account]
    namespace=uuid.uuid5(uuid.NAMESPACE_URL,'Finance Compass local copy:'+ident)
    def remap(row,category):
        result=deepcopy(row);result['accountId']=ident
        if 'id' in result:result['id']=uuid.uuid5(namespace,category+':'+result['id']).hex
        return result
    trades={ticker:[row for row in rows if row.get('accountId','legacy')!=ident] for ticker,rows in candidate['trades'].items()}
    for ticker,rows in remote['trades'].items():trades.setdefault(ticker,[]).extend(remap(row,'trade:'+ticker) for row in rows)
    candidate['trades']={ticker:rows for ticker,rows in trades.items() if rows}
    for key in ('valuations','flows'):
        candidate['performanceRecords'][key]=[row for row in candidate['performanceRecords'][key] if row['accountId']!=ident]+[remap(row,key) for row in remote['performanceRecords'][key]]
    candidate['dividendEvents']=[row for row in candidate.get('dividendEvents',[]) if row['accountId']!=ident]+[remap(row,'dividend') for row in remote['dividendEvents']]
    binding=binding_value(value['origin'],value['identity'],record,ident,investment_signature(candidate,ident))
    candidate['serviceCopies']=[row for row in candidate.get('serviceCopies',[]) if (row['origin'],row['identity'],row['remote_id'])!=(value['origin'],value['identity'],record['id'])]+[binding]
    validate_workspace(candidate);store.save(candidate)
    return dict(status='copied',kind='investment_account',localId=ident)


def detach_investment(store,origin,identity,remote):
    context(origin,identity)
    if not investment_binding(store.state,origin,identity,remote):raise ValueError('No investment mapping exists for this service identity/snapshot.')
    candidate=deepcopy(store.state);candidate['serviceCopies']=[row for row in candidate['serviceCopies'] if (row['origin'],row['identity'],row['remote_id'])!=(origin,identity,remote)];store.save(candidate)
