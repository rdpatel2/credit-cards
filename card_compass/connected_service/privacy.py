"""Owner-scoped identity administration and encrypted, consistent exports.

No password reset bypass, credential export, cross-household private read or
unconfirmed provider revocation. Deletion affects active service records only.
"""
from contextlib import closing
from datetime import datetime,timezone
import hashlib
import hmac
import json
from pathlib import Path
import secrets
import sqlite3
import tempfile
import time
from argon2.exceptions import VerificationError,InvalidHashError
from .store import ServiceError,HASHER,password,digest,matched_step,new_mfa,now_iso

SCHEMA='''
CREATE TABLE IF NOT EXISTS identity_changes(id TEXT PRIMARY KEY,user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
 session_digest TEXT NOT NULL REFERENCES sessions(digest) ON DELETE CASCADE,kind TEXT NOT NULL,data TEXT NOT NULL,expires REAL NOT NULL);
CREATE TABLE IF NOT EXISTS privacy_exports(id TEXT PRIMARY KEY,user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
 session_digest TEXT NOT NULL REFERENCES sessions(digest) ON DELETE CASCADE,created TEXT NOT NULL,expires REAL NOT NULL,manifest TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS privacy_chunks(export_id TEXT NOT NULL REFERENCES privacy_exports(id) ON DELETE CASCADE,
 sequence INTEGER NOT NULL,data TEXT NOT NULL,PRIMARY KEY(export_id,sequence));
CREATE TABLE IF NOT EXISTS deletion_receipts(id TEXT PRIMARY KEY,at TEXT NOT NULL);
'''
SECRET_KEYS={'password','passwordhash','newpassword','secret','clientsecret','clientid','apikey','authorization',
             'accesstoken','publictoken','linktoken','token','cursor','mfasecret','mfa','recovery','recoverycodes','itemid'}


def clean(value):
    if isinstance(value,dict):return {key:clean(item) for key,item in value.items() if key.replace('_','').lower() not in SECRET_KEYS}
    if isinstance(value,list):return [clean(item) for item in value]
    return value


def encoded(value):return json.dumps(value,ensure_ascii=False,sort_keys=True,separators=(',',':'),allow_nan=False)
def checksum(value):return hashlib.sha256(encoded(value).encode()).hexdigest()
def table(db,name):return db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",(name,)).fetchone() is not None


class Privacy:
    def __init__(self,store,connections):
        self.store=store;self.connections=connections
        with store.connection() as db:db.executescript(SCHEMA);self.cleanup(db)

    @staticmethod
    def active(db,user,token):
        row=db.execute('SELECT u.* FROM users u JOIN sessions s ON s.user_id=u.id WHERE u.id=? AND s.digest=? AND s.expires>?',(user,digest(token),time.time())).fetchone()
        if not row:raise ServiceError(401,'Session expired or revoked; sign in again.')
        return row

    def proof(self,db,user,token,secret,code):
        row=self.active(db,user,token)
        if not isinstance(secret,str) or not 1<=len(secret)<=256:raise ServiceError(403,'Invalid passphrase or second factor.')
        try:valid=HASHER.verify(row['password_hash'],secret)
        except (VerificationError,InvalidHashError):valid=False
        if not valid:raise ServiceError(403,'Invalid passphrase or second factor.')
        step=matched_step(self.store.decrypt(row['mfa']),code,row['last_step']);recovery=json.loads(row['recovery'])
        key=digest(code) if isinstance(code,str) and len(code)==20 else ''
        if step is None and key not in recovery:raise ServiceError(403,'Invalid passphrase or second factor. Use a fresh authenticator code or unused recovery code.')
        if step is None:
            recovery.remove(key);db.execute('UPDATE users SET recovery=? WHERE id=?',(json.dumps(recovery),user))
        else:db.execute('UPDATE users SET last_step=? WHERE id=?',(step,user))
        return row

    @staticmethod
    def cleanup(db):
        db.execute('DELETE FROM sessions WHERE expires<=?',(time.time(),))
        db.execute('DELETE FROM identity_changes WHERE expires<=?',(time.time(),))
        db.execute('DELETE FROM privacy_exports WHERE expires<=?',(time.time(),))

    def sessions(self,user,token):
        with self.store.connection() as db:
            self.active(db,user,token)
            return [dict(id=row['id'],created=row['created'],label=row['label'],expires=row['expires'],current=hmac.compare_digest(row['digest'],digest(token))) for row in db.execute('SELECT s.digest,s.expires,d.* FROM sessions s JOIN session_details d ON d.digest=s.digest WHERE s.user_id=? AND s.expires>? ORDER BY s.expires DESC',(user,time.time()))]

    def revoke_session(self,user,token,ident):
        with self.store.connection() as db:
            db.execute('BEGIN IMMEDIATE');self.active(db,user,token)
            row=db.execute('SELECT s.digest FROM sessions s JOIN session_details d ON d.digest=s.digest WHERE d.id=? AND s.user_id=?',(ident,user)).fetchone()
            if not row:raise ServiceError(404,'This session was not found for your identity.')
            current=hmac.compare_digest(row['digest'],digest(token));db.execute('DELETE FROM sessions WHERE digest=?',(row['digest'],));self.store.audit(db,user,'session_revoked',ident)
        return dict(revoked=True,current=current)

    def change_password(self,user,token,secret,code,new_password):
        new_password=password(new_password)
        with self.store.connection() as db:
            db.execute('BEGIN IMMEDIATE');self.proof(db,user,token,secret,code)
            db.execute('UPDATE users SET password_hash=? WHERE id=?',(HASHER.hash(new_password),user))
            db.execute('DELETE FROM sessions WHERE user_id=? AND digest<>?',(user,digest(token)))
            db.execute('DELETE FROM identity_changes WHERE user_id=?',(user,));self.store.audit(db,user,'passphrase_changed',user)
        return dict(changed=True,otherSessionsRevoked=True,currentSessionRetained=True)

    def begin_change(self,user,token,secret,code,kind):
        if kind not in ('mfa','recovery'):raise ServiceError(400,'Choose authenticator replacement or recovery-code rotation.')
        codes=[secrets.token_hex(10) for _ in range(10)];activation=secrets.token_hex(10);mfa=new_mfa() if kind=='mfa' else None
        ident=secrets.token_hex(16);expires=time.time()+600
        with self.store.connection() as db:
            db.execute('BEGIN IMMEDIATE');self.cleanup(db)
            if db.execute('SELECT 1 FROM identity_changes WHERE user_id=?',(user,)).fetchone():raise ServiceError(409,'A security setup is already pending. Cancel it or wait ten minutes before starting another.')
            self.proof(db,user,token,secret,code)
            db.execute('INSERT INTO identity_changes VALUES (?,?,?,?,?,?)',(ident,user,digest(token),kind,self.store.encrypt(dict(recovery=[digest(item) for item in codes],activation=digest(activation),mfa=mfa)),expires))
            self.store.audit(db,user,'security_setup_started',ident)
        return dict(id=ident,kind=kind,mfaSecret=mfa,recoveryCodes=codes,activationCode=activation if kind=='recovery' else None,expires=expires)

    def changes(self,user,token):
        with self.store.connection() as db:
            self.active(db,user,token)
            return [dict(id=row['id'],kind=row['kind'],expires=row['expires'],currentSession=row['session_digest']==digest(token)) for row in db.execute('SELECT * FROM identity_changes WHERE user_id=? AND expires>?',(user,time.time()))]

    def cancel_change(self,user,token,ident):
        with self.store.connection() as db:
            db.execute('BEGIN IMMEDIATE');self.active(db,user,token)
            if db.execute('DELETE FROM identity_changes WHERE id=? AND user_id=?',(ident,user)).rowcount!=1:raise ServiceError(404,'This pending security setup was not found.')
            self.store.audit(db,user,'security_setup_canceled',ident)
        return dict(canceled=True,existingFactorsRetained=True)

    def confirm_change(self,user,token,ident,code):
        with self.store.connection() as db:
            db.execute('BEGIN IMMEDIATE');self.active(db,user,token)
            row=db.execute('SELECT * FROM identity_changes WHERE id=? AND user_id=? AND session_digest=? AND expires>?',(ident,user,digest(token),time.time())).fetchone()
            if not row:raise ServiceError(403,'Setup expired, canceled or belongs to another session. Existing factors are unchanged.')
            data=self.store.decrypt(row['data'])
            if row['kind']=='mfa':
                step=matched_step(data['mfa'],code,-1)
                if step is None:raise ServiceError(403,'Confirm the current code from your new authenticator.')
                db.execute('UPDATE users SET mfa=?,last_step=?,recovery=? WHERE id=?',(self.store.encrypt(data['mfa']),step,json.dumps(data['recovery']),user))
            else:
                if not isinstance(code,str) or not hmac.compare_digest(digest(code),data['activation']):raise ServiceError(403,'Confirm the separate activation code shown with your new recovery codes.')
                db.execute('UPDATE users SET recovery=? WHERE id=?',(json.dumps(data['recovery']),user))
            db.execute('DELETE FROM sessions WHERE user_id=? AND digest<>?',(user,digest(token)));db.execute('DELETE FROM identity_changes WHERE user_id=?',(user,));self.store.audit(db,user,'authenticator_replaced' if row['kind']=='mfa' else 'recovery_codes_rotated',ident)
        return dict(changed=True,otherSessionsRevoked=True,currentSessionRetained=True)

    def export_records(self,db,user,token):
        row=self.active(db,user,token)
        homes=[dict(id=r['id'],name=self.store.decrypt(r['name']),role=r['role']) for r in db.execute('SELECT h.*,m.role FROM households h JOIN members m ON m.household_id=h.id WHERE m.user_id=?',(user,))]
        sessions=[dict(id=r['id'],created=r['created'],label=r['label'],expires=r['expires'],current=r['digest']==digest(token)) for r in db.execute('SELECT s.digest,s.expires,d.* FROM sessions s JOIN session_details d ON d.digest=s.digest WHERE s.user_id=? AND s.expires>?',(user,time.time()))]
        yield 'identity',dict(id=user,username=row['username'],households=homes,sessions=sessions)
        for r in db.execute('SELECT id,creator,household_id,kind,version,payload,updated FROM resources WHERE creator=? ORDER BY id',(user,)):
            yield 'snapshots',dict(id=r['id'],creator=user,householdId=r['household_id'],kind=r['kind'],version=r['version'],updated=r['updated'],payload=clean(self.store.decrypt(r['payload'])))
        for r in db.execute('SELECT at,action,entity_id FROM service_audit WHERE user_id=? ORDER BY id',(user,)):yield 'activity',dict(r)
        for r in db.execute('SELECT household_id,role,expires FROM invites WHERE username=? ORDER BY expires',(row['username'],)):yield 'invitations',dict(r)
        for r in db.execute('SELECT id,products,environment,last_success,last_attempt,status,error_code,data FROM provider_items WHERE user_id=? ORDER BY id',(user,)):
            data=self.store.decrypt(r['data']);metadata=dict(connectionId=r['id'],products=json.loads(r['products']),environment=r['environment'],lastSuccess=r['last_success'],lastAttempt=r['last_attempt'],status=r['status'],errorCode=r['error_code'],transactionsStatus=data.get('transactionsStatus'),balanceRequestMode=data.get('balanceRequestMode'),coverage='Saved provider snapshot, not a live read or complete historical tax lots.')
            yield 'connections',metadata
            for item in data.get('accounts',[]):yield 'accounts',dict(connectionId=r['id'],record=clean(item))
            holdings=data.get('holdings')
            if holdings is not None:
                for key in ('accounts','securities','holdings'):
                    for item in holdings.get(key,[]):yield 'investment_'+key,dict(connectionId=r['id'],record=clean(item))
            for item in data.get('transactions',{}).values():yield 'transactions',dict(connectionId=r['id'],record=clean(item))
        for r in db.execute('SELECT id,status,expires,data FROM provider_links WHERE user_id=? ORDER BY id',(user,)):
            data=self.store.decrypt(r['data']);yield 'consent_sessions',dict(id=r['id'],status=r['status'],expires=r['expires'],environment=data.get('environment'),products=data.get('products',[]),exchangeStarted=data.get('exchangeStarted'),resolution=data.get('resolution'),resolvedAt=data.get('resolvedAt'))

    def prepare_export(self,user,token,secret,code):
        # Proof commits before taking a consistent SQLite backup. The backup
        # contains encrypted vault records, stays in a private temp directory,
        # and is deleted after bounded encrypted chunks are committed.
        with self.store.connection() as db:
            db.execute('BEGIN IMMEDIATE');self.cleanup(db);self.active(db,user,token)
            if db.execute('SELECT COUNT(*) FROM privacy_exports WHERE user_id=?',(user,)).fetchone()[0]>=1:raise ServiceError(409,'Finish/cancel your existing export before preparing another.')
            if db.execute('SELECT COUNT(*) FROM privacy_exports').fetchone()[0]>=3:raise ServiceError(503,'Export capacity is busy; retry after a pending export expires.')
            self.proof(db,user,token,secret,code)
        with tempfile.TemporaryDirectory(prefix='finance-compass-private-export-') as temp:
            path=Path(temp)/'source.sqlite3'
            with self.store.connection() as live,closing(sqlite3.connect(path)) as snapshot:live.backup(snapshot)
            path.chmod(0o600)
            with closing(sqlite3.connect(path)) as source,self.store.connection() as db:
                source.row_factory=sqlite3.Row;db.execute('BEGIN IMMEDIATE');self.cleanup(db);self.active(db,user,token)
                if db.execute('SELECT COUNT(*) FROM privacy_exports WHERE user_id=?',(user,)).fetchone()[0]>=1:raise ServiceError(409,'Finish/cancel your existing export before preparing another.')
                if db.execute('SELECT COUNT(*) FROM privacy_exports').fetchone()[0]>=3:raise ServiceError(503,'Export capacity is busy; retry after a pending export expires.')
                ident=secrets.token_hex(16);created=now_iso();expires=time.time()+900;counts={};chunks=[];sequence=0;total=0
                db.execute('INSERT INTO privacy_exports VALUES (?,?,?,?,?,?)',(ident,user,digest(token),created,expires,self.store.encrypt({})))
                def flush():
                    nonlocal sequence,chunks
                    if chunks:db.execute('INSERT INTO privacy_chunks VALUES (?,?,?)',(ident,sequence,self.store.encrypt(dict(records=chunks))));sequence+=1;chunks=[]
                size=0
                for dataset,value in self.export_records(source,user,token):
                    record=dict(dataset=dataset,value=value);amount=len(encoded(record).encode())
                    if amount>19_000_000:raise ServiceError(413,'A saved record exceeds the supported export response size; contact the operator for reviewed recovery.')
                    total+=amount
                    if total>1_000_000_000:raise ServiceError(413,'The export exceeds 1 GB. No partial export is assumed; contact the operator for reviewed archival recovery.')
                    if chunks and size+amount>1_000_000:flush();size=0
                    chunks.append(record);size+=amount;counts[dataset]=counts.get(dataset,0)+1
                flush();manifest=dict(schemaVersion=1,id=ident,created=created,expires=expires,chunkCount=sequence,counts=counts,excluded='Credentials, verifiers, MFA/recovery material, internal cursors and other identities’ private payloads.',scope='Your identity, memberships, created snapshots, own activity and saved provider records. Consistent recorded snapshot; not a live banking read or a service-restoration backup.')
                db.execute('UPDATE privacy_exports SET manifest=? WHERE id=?',(self.store.encrypt(manifest),ident));self.store.audit(db,user,'privacy_export_prepared',ident)
        return manifest

    def exports(self,user,token):
        with self.store.connection() as db:
            self.active(db,user,token)
            return [dict(self.store.decrypt(row['manifest']),currentSession=row['session_digest']==digest(token)) for row in db.execute('SELECT * FROM privacy_exports WHERE user_id=? AND expires>?',(user,time.time()))]

    def export_chunk(self,user,token,ident,sequence):
        if type(sequence) is not int or sequence<0:raise ServiceError(400,'Choose a nonnegative export chunk sequence.')
        with self.store.connection() as db:
            self.active(db,user,token)
            row=db.execute('SELECT c.data FROM privacy_chunks c JOIN privacy_exports e ON e.id=c.export_id WHERE e.id=? AND e.user_id=? AND e.session_digest=? AND e.expires>? AND c.sequence=?',(ident,user,digest(token),time.time(),sequence)).fetchone()
            if not row:raise ServiceError(404,'Export chunk expired, missing or belongs to another identity/session.')
            return dict(id=ident,sequence=sequence,**self.store.decrypt(row['data']))

    def claim_export(self,user,token,ident,secret,code):
        with self.store.connection() as db:
            db.execute('BEGIN IMMEDIATE');self.proof(db,user,token,secret,code)
            if db.execute('UPDATE privacy_exports SET session_digest=? WHERE id=? AND user_id=? AND expires>?',(digest(token),ident,user,time.time())).rowcount!=1:raise ServiceError(404,'This export expired or was not found for your identity.')
            self.store.audit(db,user,'privacy_export_claimed',ident)
        return dict(claimed=True)

    def cancel_export(self,user,token,ident):
        with self.store.connection() as db:
            db.execute('BEGIN IMMEDIATE');self.active(db,user,token)
            if db.execute('DELETE FROM privacy_exports WHERE id=? AND user_id=?',(ident,user)).rowcount!=1:raise ServiceError(404,'The export was not found for your identity.')
            self.store.audit(db,user,'privacy_export_removed',ident)
        return dict(removed=True)

    def deletion_data(self,db,user,token):
        row=self.active(db,user,token);homes=[];blockers=[];memberships=[]
        for item in db.execute('SELECT h.*,m.role FROM households h JOIN members m ON m.household_id=h.id WHERE m.user_id=? ORDER BY h.id',(user,)):
            members=[tuple(r) for r in db.execute('SELECT user_id,role FROM members WHERE household_id=? ORDER BY user_id',(item['id'],))];owners=sum(role=='owner' for ident,role in members)
            sole=len(members)==1;last=item['role']=='owner' and owners==1
            homes.append(dict(id=item['id'],name=self.store.decrypt(item['name']),role=item['role'],members=len(members),closeIfSole=sole,lastOwner=last))
            memberships.append(dict(id=item['id'],members=members))
            if last and not sole:blockers.append('Transfer household ownership before deletion: '+self.store.decrypt(item['name']))
        links=[dict(r) for r in db.execute('SELECT id,status,data,expires FROM provider_links WHERE user_id=? ORDER BY id',(user,))]
        active_links=[dict(id=r['id'],status=r['status']) for r in links if r['status'] not in ('completed','resolved_external')]
        items=[dict(r) for r in db.execute('SELECT id,environment,status,last_success,last_attempt FROM provider_items WHERE user_id=? ORDER BY id',(user,))]
        if items:blockers.append('Disconnect every institution through confirmed provider removal before deleting the identity.')
        if active_links:blockers.append('Resolve pending/canceled/uncertain browser consents with the operator before deletion; canceling Link alone is not bank-side revocation.')
        resources=[dict(r) for r in db.execute('SELECT id,version,updated,household_id FROM resources WHERE creator=? ORDER BY id',(user,))]
        invitations=[dict(r) for r in db.execute('SELECT * FROM invites WHERE username=? ORDER BY digest',(row['username'],))]
        sessions=[tuple(r) for r in db.execute('SELECT digest,expires FROM sessions WHERE user_id=? ORDER BY digest',(user,))]
        exports=[tuple(r) for r in db.execute('SELECT id,created,expires FROM privacy_exports WHERE user_id=? ORDER BY id',(user,))]
        changes=[tuple(r) for r in db.execute('SELECT id,kind,expires FROM identity_changes WHERE user_id=? ORDER BY id',(user,))]
        signature=dict(identity=user,username=row['username'],memberships=memberships,resources=resources,invitations=invitations,sessions=sessions,items=items,links=links,exports=exports,changes=changes)
        return dict(planHash=checksum(signature),username=row['username'],createdSnapshots=len(resources),sessions=len(sessions),pendingExports=len(exports),pendingSecurityChanges=len(changes),households=homes,institutionConnections=len(items),unresolvedConsents=active_links,blockers=blockers,limitations='Deletes active service records only. Local/downloaded copies, external provider/bank consent and operator backups/retention are separate; no secure-erasure claim.')

    def deletion_plan(self,user,token):
        with self.store.connection() as db:
            db.execute('BEGIN');return self.deletion_data(db,user,token)

    def delete_identity(self,user,token,secret,code,plan_hash,confirmation,close_sole=False):
        with self.connections.lock,self.store.connection() as db:
            db.execute('PRAGMA secure_delete=ON');db.execute('BEGIN IMMEDIATE');plan=self.deletion_data(db,user,token)
            if confirmation!=plan['username']:raise ServiceError(400,'Type your exact service username to confirm identity deletion.')
            if not isinstance(plan_hash,str) or not hmac.compare_digest(plan_hash,plan['planHash']):raise ServiceError(409,'The deletion scope changed; reload and review the current plan.')
            if plan['blockers']:raise ServiceError(409,'Resolve the displayed institution/consent/ownership blockers first. Nothing was deleted.')
            sole=[home for home in plan['households'] if home['closeIfSole']]
            if sole and not close_sole:raise ServiceError(409,'Explicitly review closing your sole-member households before deletion.')
            self.proof(db,user,token,secret,code)
            for home in sole:
                # Preserve any other creator's legacy publication as private,
                # rather than allowing the household FK cascade to erase it.
                db.execute('UPDATE resources SET household_id=NULL,version=version+1,updated=? WHERE household_id=? AND creator<>?',(now_iso(),home['id'],user))
                db.execute('DELETE FROM enrollments WHERE invite_digest IN (SELECT digest FROM invites WHERE household_id=?)',(home['id'],));db.execute('DELETE FROM households WHERE id=?',(home['id'],))
            db.execute('DELETE FROM enrollments WHERE username=?',(plan['username'],));db.execute('DELETE FROM invites WHERE username=?',(plan['username'],))
            db.execute('DELETE FROM service_audit WHERE user_id=?',(user,));db.execute('DELETE FROM users WHERE id=?',(user,))
            receipt=secrets.token_hex(16);db.execute('INSERT INTO deletion_receipts VALUES (?,?)',(receipt,now_iso()))
        return dict(identityDeleted=True,receiptId=receipt,closedHouseholds=[home['id'] for home in sole],activeServiceRecordsRemoved=True,localCopiesDeleted=False,providerRevocationPerformed=False,backupErasureClaimed=False)
