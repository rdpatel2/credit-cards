"""Authenticated owner-bound Plaid consent, encrypted tokens and refresh data."""
from datetime import datetime,timezone
import json
import threading
import time
import secrets
import sqlite3
from ..plaid_provider import ProviderError
from .store import ServiceError,now_iso

SCHEMA='''
CREATE TABLE IF NOT EXISTS provider_links(id TEXT PRIMARY KEY,user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
 data TEXT NOT NULL,expires REAL NOT NULL,status TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS provider_items(id TEXT PRIMARY KEY,user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
 secret TEXT NOT NULL,products TEXT NOT NULL,environment TEXT NOT NULL,data TEXT NOT NULL,
 last_success TEXT NOT NULL,last_attempt TEXT NOT NULL,status TEXT NOT NULL,error_code TEXT NOT NULL);
'''


class Connections:
    def __init__(self,store,provider,redirect_uri=None):
        self.store=store;self.provider=provider;self.redirect_uri=redirect_uri;self.lock=threading.RLock()
        with store.connection() as db:db.executescript(SCHEMA)

    def ready(self):
        if self.provider is None:raise ServiceError(503,'The operator has not configured Plaid credentials/product access. Local records remain available.')

    def begin(self,user,products):
        if not isinstance(products,list) or not products or len(products)!=len(set(products)) or set(products)-{'transactions','investments'}:raise ServiceError(400,'Choose distinct Transactions and/or Investments read-only products.')
        self.ready()
        with self.lock:
            with self.store.connection() as db:
                if not db.execute('SELECT 1 FROM users WHERE id=?',(user,)).fetchone():raise ServiceError(401,'The service identity no longer exists; sign in again.')
                # Browser expiry is not external consent revocation. Retain
                # the owner-bound evidence until completion/operator review.
                if db.execute("SELECT COUNT(*) FROM provider_links WHERE user_id=? AND status IN ('pending','canceled','exchanging','exchanged','uncertain')",(user,)).fetchone()[0]>=10:raise ServiceError(409,'Complete or externally review an existing Link session first; expired/canceled browser sessions are not proof of bank revocation.')
            result=self.provider.create_link(user,products,self.redirect_uri)
            ident=secrets.token_hex(16)
            expires=datetime.fromisoformat(result['expiration'].replace('Z','+00:00')).timestamp()
            if expires<=time.time():raise ProviderError('EXPIRED_LINK_RESULT')
            with self.store.connection() as db:
                db.execute('INSERT INTO provider_links VALUES (?,?,?,?,?)',(ident,user,self.store.encrypt(dict(linkToken=result['link_token'],products=products,url=result['hosted_link_url'],environment=self.provider.environment)),expires,'pending'))
                self.store.audit(db,user,'institution_consent_started',ident)
            return dict(id=ident,url=result['hosted_link_url'],expires=expires,products=products,environment=self.provider.environment)

    def complete(self,user,ident):
        try:return self.complete_exchange(user,ident)
        except (sqlite3.Error,OSError):raise ServiceError(503,'Connection persistence could not be confirmed. Review the retained consent session after storage recovery; do not repeat the one-use token exchange or assume revocation.') from None

    def complete_exchange(self,user,ident):
        self.ready()
        with self.lock:
            with self.store.connection() as db:
                row=db.execute('SELECT * FROM provider_links WHERE id=? AND user_id=?',(ident,user)).fetchone()
                if not row:raise ServiceError(404,'Connection session not found.')
                if row['status']=='completed':return dict(status='completed',itemId=self.store.decrypt(row['data'])['itemId'])
                data=self.store.decrypt(row['data'])
            if row['status'] in ('exchanging','uncertain'):
                if row['status']=='exchanging' and datetime.now(timezone.utc).timestamp()-datetime.fromisoformat(data['exchangeStarted']).timestamp()<90:raise ServiceError(409,'A token exchange was claimed recently. Wait for its result and refresh this session; another exchange will not be started.')
                self.checkpoint(user,ident,data,'uncertain')
                raise ServiceError(409,'The earlier token exchange was not durably confirmed. It will not be retried. Ask the service operator to review Plaid/bank consent and resolve this session; no revocation or successful connection is assumed.')
            if row['status'] not in ('pending','exchanged') or (row['status']=='pending' and row['expires']<=time.time()):raise ServiceError(410,'Connection session expired/canceled; start a new consent flow.')
            if data.get('environment')!=self.provider.environment:raise ServiceError(409,'Restore this consent session’s provider environment before completing it.')
            if row['status']=='pending':
                tokens=self.provider.link_results(data['linkToken'])
                if not tokens:return dict(status='waiting',message='Complete the Plaid browser session, then check again. No connection is assumed from opening the browser.')
                if len(tokens)!=1:raise ProviderError('UNEXPECTED_MULTI_ITEM_RESULT')
                data.update(preparedItemId=secrets.token_hex(16),exchangeStarted=now_iso())
                # Commit intent before invoking the one-use remote exchange.
                # A crash after this commit is an explicit uncertain outcome.
                self.checkpoint(user,ident,data,'exchanging',expected='pending')
                try:exchanged=self.provider.exchange(tokens[0])
                except ProviderError as exc:
                    data['exchangeError']=exc.code;self.checkpoint(user,ident,data,'uncertain');raise
                data['exchanged']=exchanged
                # If this write fails or the process dies, the preceding phase
                # remains uncertain; never exchange that public token again.
                self.checkpoint(user,ident,data,'exchanged')
            exchanged=data['exchanged'];item_id=self.persist_exchange(user,ident,data)
            return dict(status='completed',itemId=item_id,environment=self.provider.environment)

    def checkpoint(self,user,ident,data,status,expected=None):
        with self.store.connection() as db:
            query='UPDATE provider_links SET status=?,data=? WHERE id=? AND user_id=?';values=[status,self.store.encrypt(data),ident,user]
            if expected:query+=' AND status=?';values.append(expected)
            if db.execute(query,values).rowcount!=1:raise ServiceError(409,'Another request changed this consent session; reload before completing it.')
            self.store.audit(db,user,'institution_exchange_'+status,ident)

    def persist_exchange(self,user,ident,data):
        exchanged=data['exchanged'];item_id=data['preparedItemId'];existing_item=False
        with self.store.connection() as db:
            db.execute('BEGIN IMMEDIATE')
            for existing in db.execute('SELECT id,secret,products FROM provider_items WHERE user_id=? AND environment=?',(user,self.provider.environment)):
                if self.store.decrypt(existing['secret'])['itemId']==exchanged['item_id']:
                    existing_item=True;item_id=existing['id'];products=sorted(set(json.loads(existing['products']))|set(data['products']))
                    db.execute('UPDATE provider_items SET secret=?,products=? WHERE id=?',(self.store.encrypt(dict(accessToken=exchanged['access_token'],itemId=exchanged['item_id'])),json.dumps(products),item_id));break
            if not existing_item:
                if db.execute('SELECT COUNT(*) FROM provider_items WHERE user_id=?',(user,)).fetchone()[0]>=30:raise ServiceError(409,'Exchange is safely retained. Disconnect an unused Item, then complete this session again; the public token will not be exchanged again.')
                db.execute('INSERT INTO provider_items VALUES (?,?,?,?,?,?,?,?,?,?)',(item_id,user,self.store.encrypt(dict(accessToken=exchanged['access_token'],itemId=exchanged['item_id'])),json.dumps(data['products']),self.provider.environment,self.store.encrypt(dict(accounts=[],holdings=None,transactions={},cursor=None,transactionsStatus='NOT_REQUESTED')),'','','connected',''))
            db.execute('UPDATE provider_links SET status=?,data=? WHERE id=?',('completed',self.store.encrypt(dict(itemId=item_id)),ident));self.store.audit(db,user,'institution_connected',item_id)
        return item_id

    def cancel(self,user,ident):
        with self.lock,self.store.connection() as db:
            row=db.execute('SELECT status,data FROM provider_links WHERE id=? AND user_id=?',(ident,user)).fetchone()
            if not row:raise ServiceError(404,'Connection session not found.')
            if row[0]=='completed':raise ServiceError(409,'This session is already connected; disconnect its Item instead.')
            if row[0] not in ('pending','canceled'):raise ServiceError(409,'An exchange is retained or uncertain. Complete the retained exchange or ask the operator to resolve consent; cancel will not erase recovery evidence.')
            data=self.store.decrypt(row['data']);retained=dict(environment=data.get('environment'),products=data.get('products',[]))
            db.execute('UPDATE provider_links SET status=?,data=? WHERE id=?',('canceled',self.store.encrypt(retained),ident));self.store.audit(db,user,'institution_consent_canceled',ident)

    def items(self,user):
        with self.store.connection() as db:return [dict(id=r['id'],products=json.loads(r['products']),environment=r['environment'],lastSuccess=r['last_success'],lastAttempt=r['last_attempt'],status=r['status'],errorCode=r['error_code']) for r in db.execute('SELECT * FROM provider_items WHERE user_id=?',(user,))]

    def pending(self,user):
        with self.store.connection() as db:
            result=[]
            for row in db.execute("SELECT * FROM provider_links WHERE user_id=? AND status IN ('pending','exchanging','exchanged','uncertain','canceled')",(user,)):
                data=self.store.decrypt(row['data']);result.append(dict(id=row['id'],status='expired_pending' if row['status']=='pending' and row['expires']<=time.time() else row['status'],environment=data.get('environment'),products=data.get('products',[]),url=data.get('url') if row['status']=='pending' and row['expires']>time.time() else None,expires=row['expires']))
            return result

    def resolve_uncertain(self,name,ident,evidence):
        """Offline unexchanged/uncertain consent attestation, never API proof.

        The CLI holds an exclusive process lease and requires explicit
        external-consent review. A retained successful exchange must instead
        be completed/disconnected, never discarded by this operation.
        """
        from .store import username
        name=username(name)
        if not isinstance(evidence,str) or not 1<=len(evidence.strip())<=200:raise ServiceError(400,'Supply a private external revocation case/reference (up to 200 characters), never a credential.')
        with self.store.connection() as db:
            db.execute('BEGIN IMMEDIATE')
            row=db.execute('SELECT l.* FROM provider_links l JOIN users u ON u.id=l.user_id WHERE l.id=? AND u.username=?',(ident,name)).fetchone()
            if not row:raise ServiceError(404,'The consent session was not found for that identity.')
            data=self.store.decrypt(row['data'])
            if row['status'] not in ('pending','canceled','exchanging','uncertain'):raise ServiceError(409,'Only unexchanged/uncertain consent can be resolved by external attestation. Complete a retained exchange, then disconnect its Item.')
            if row['status']=='exchanging' and datetime.now(timezone.utc).timestamp()-datetime.fromisoformat(data['exchangeStarted']).timestamp()<90:raise ServiceError(409,'Wait for the recent exchange claim to age before operator resolution.')
            retained=dict(environment=data.get('environment'),products=data.get('products',[]),exchangeStarted=data.get('exchangeStarted'),resolution='external_revocation_attested',resolvedAt=now_iso(),evidenceReference=evidence.strip())
            db.execute('UPDATE provider_links SET status=?,data=? WHERE id=?',('resolved_external',self.store.encrypt(retained),ident));self.store.audit(db,row['user_id'],'institution_external_revocation_attested',ident)
        return dict(status='resolved_external',providerConfirmed=False,externalAttestation=True)

    def owned(self,user,ident,require_environment=True):
        with self.store.connection() as db:row=db.execute('SELECT * FROM provider_items WHERE id=? AND user_id=?',(ident,user)).fetchone()
        if not row:raise ServiceError(404,'Institution connection not found.')
        if require_environment and (not self.provider or row['environment']!=self.provider.environment):raise ServiceError(409,'Restore the configured provider environment before refreshing this connection.')
        return row

    def refresh(self,user,ident,fresh_balances=False):
        self.ready()
        with self.lock:
            row=self.owned(user,ident);secret=self.store.decrypt(row['secret']);data=self.store.decrypt(row['data']);products=json.loads(row['products'])
            with self.store.connection() as db:db.execute('UPDATE provider_items SET last_attempt=? WHERE id=?',(now_iso(),ident))
            try:
                accounts=self.provider.accounts(secret['accessToken'],fresh=fresh_balances)
                if not isinstance(accounts.get('accounts'),list) or len(accounts['accounts'])>100:raise ProviderError('INVALID_ACCOUNT_RESPONSE')
                data['accounts']=accounts['accounts'];data['itemStatus']=accounts.get('item',{});data['accountRequestId']=accounts.get('request_id','')
                if 'investments' in products:data['holdings']=self.provider.holdings(secret['accessToken'])
                if 'transactions' in products:
                    sync=self.provider.sync_transactions(secret['accessToken'],data['cursor']);transactions=data['transactions']
                    for changed in sync['added']+sync['modified']:transactions[changed['transaction_id']]=changed
                    for removed in sync['removed']:transactions.pop(removed['transaction_id'],None)
                    if len(transactions)>50000:raise ProviderError('TRANSACTION_CAPACITY')
                    data['cursor']=sync['cursor'];data['transactionsStatus']=sync['status'];data['transactionRequestIds']=sync['requestIds']
                encoded=json.dumps(data,allow_nan=False)
                if len(encoded.encode())>20_000_000:raise ProviderError('CONNECTION_DATA_CAPACITY')
                succeeded=now_iso();data['retrievedAt']=succeeded;data['balanceRequestMode']='requested_current' if fresh_balances else 'provider_recorded'
                with self.store.connection() as db:
                    db.execute('UPDATE provider_items SET data=?,last_success=?,status=?,error_code=? WHERE id=?',(self.store.encrypt(data),succeeded,'ready','',ident));self.store.audit(db,user,'institution_refreshed',ident)
            except ProviderError as exc:
                with self.store.connection() as db:db.execute('UPDATE provider_items SET status=?,error_code=? WHERE id=?',('attention',exc.code,ident))
                raise
            return dict(status='ready',lastSuccess=succeeded,transactionsStatus=data['transactionsStatus'])

    def snapshot(self,user,ident):
        row=self.owned(user,ident,require_environment=False);data=self.store.decrypt(row['data'])
        if not row['last_success']:raise ServiceError(409,'Refresh this connection before reviewing its data.')
        # Credential/cursor and internal Item metadata are never sent back to
        # the desktop. Data remains a provider snapshot, not historical lots.
        return dict(id=ident,environment=row['environment'],providerConfigured=self.provider is not None,lastSuccess=row['last_success'],lastAttempt=row['last_attempt'],status=row['status'],errorCode=row['error_code'],accounts=data['accounts'],holdings=data['holdings'],transactions=list(data['transactions'].values()),transactionsStatus=data['transactionsStatus'],balanceRequestMode=data.get('balanceRequestMode'),coverage='Saved provider-retrieved snapshot, not a live read. Review account currencies and source dates; holdings do not establish complete historical tax lots. No automatic desktop reconciliation.')

    def disconnect(self,user,ident):
        self.ready()
        with self.lock:
            row=self.owned(user,ident)
            # Never claim revocation until the provider confirms Item removal.
            self.provider.remove(self.store.decrypt(row['secret'])['accessToken'])
            with self.store.connection() as db:
                db.execute('DELETE FROM provider_items WHERE id=? AND user_id=?',(ident,user));self.store.audit(db,user,'institution_disconnected',ident)
        return dict(revoked=True,localDataDeleted=True)

    def balances(self,user,ident):
        snapshot=self.snapshot(user,ident)
        return {key:snapshot[key] for key in ('id','environment','providerConfigured','lastSuccess','lastAttempt','status','errorCode','accounts','balanceRequestMode')}
