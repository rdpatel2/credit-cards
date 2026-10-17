import pytest
pytest.importorskip('cryptography');pytest.importorskip('argon2')
from cryptography.fernet import Fernet
from datetime import datetime,timezone,timedelta
from card_compass.connected_service.store import ServiceStore,ServiceError
from card_compass.connected_service.connections import Connections
from card_compass.plaid_provider import ProviderError
from tests.test_connected_store import identity


class Provider:
    environment='sandbox'
    def __init__(self):self.ready=False;self.fail=False;self.removed=[];self.amount=100;self.exchanges=0
    def create_link(self,*args):return dict(link_token='secret-link',hosted_link_url='https://secure.plaid.com/hl/fixture',expiration=(datetime.now(timezone.utc)+timedelta(minutes=30)).isoformat())
    def link_results(self,*args):return ['public-fixture'] if self.ready else []
    def exchange(self,*args):self.exchanges+=1;return dict(access_token='secret-access-fixture',item_id='private-item-fixture')
    def accounts(self,*args,**kwargs):return dict(accounts=[dict(account_id='account-fixture',name='Private checking',balances={'current':self.amount,'iso_currency_code':'USD'})])
    def holdings(self,*args):return dict(accounts=[],securities=[],holdings=[])
    def sync_transactions(self,access,cursor):
        if self.fail:raise ProviderError('ITEM_LOGIN_REQUIRED')
        return dict(added=[dict(transaction_id='tx1',name='Private purchase',amount=10)],modified=[],removed=[],cursor='new-cursor',status='HISTORICAL_UPDATE_COMPLETE',requestIds=['fixture'])
    def remove(self,access):
        if self.fail:raise ProviderError('NETWORK_UNAVAILABLE')
        self.removed.append(access);return {}


def test_owner_bound_consent_encrypted_token_atomic_refresh_and_confirmed_revocation(tmp_path):
    store=ServiceStore(tmp_path,Fernet.generate_key());owner=identity(store,'owner');other=identity(store,'other')
    provider=Provider();connections=Connections(store,provider)
    link=connections.begin(owner['id'],['transactions','investments'])
    with pytest.raises(ServiceError):connections.complete(other['id'],link['id'])
    assert connections.complete(owner['id'],link['id'])['status']=='waiting'
    provider.ready=True;done=connections.complete(owner['id'],link['id']);ident=done['itemId']
    assert connections.complete(owner['id'],link['id'])['itemId']==ident
    connections.refresh(owner['id'],ident)
    snapshot=connections.snapshot(owner['id'],ident)
    assert snapshot['accounts'][0]['balances']['current']==100 and snapshot['transactions'][0]['name']=='Private purchase'
    assert 'secret-access' not in str(snapshot) and 'new-cursor' not in str(snapshot)
    raw=store.path.read_bytes()
    assert b'secret-access-fixture' not in raw and b'Private purchase' not in raw
    provider.amount=200;provider.fail=True
    with pytest.raises(ProviderError):connections.refresh(owner['id'],ident)
    saved=connections.snapshot(owner['id'],ident)
    assert saved['status']=='attention' and saved['accounts'][0]['balances']['current']==100 and saved['lastSuccess']==snapshot['lastSuccess']
    with pytest.raises(ProviderError):connections.disconnect(owner['id'],ident)
    assert len(connections.items(owner['id']))==1
    provider.fail=False;connections.disconnect(owner['id'],ident)
    assert not connections.items(owner['id']) and provider.removed==['secret-access-fixture']
    with pytest.raises(ServiceError):connections.snapshot(other['id'],ident)


def test_duplicate_same_item_does_not_revoke_original_and_saved_snapshots_work_without_provider(tmp_path):
    store=ServiceStore(tmp_path,Fernet.generate_key());owner=identity(store,'owner');provider=Provider();provider.ready=True
    connections=Connections(store,provider)
    first=connections.complete(owner['id'],connections.begin(owner['id'],['transactions'])['id'])
    second=connections.complete(owner['id'],connections.begin(owner['id'],['investments'])['id'])
    assert first['itemId']==second['itemId'] and not provider.removed and len(connections.items(owner['id']))==1
    connections.refresh(owner['id'],first['itemId'])
    cached=Connections(store,None).snapshot(owner['id'],first['itemId'])
    assert not cached['providerConfigured'] and cached['accounts']
    with pytest.raises(ServiceError):Connections(store,None).refresh(owner['id'],first['itemId'])


def test_cancel_is_owner_bound_and_never_claims_an_existing_item_was_revoked(tmp_path):
    store=ServiceStore(tmp_path,Fernet.generate_key());owner=identity(store,'owner');other=identity(store,'other');provider=Provider()
    connections=Connections(store,provider);link=connections.begin(owner['id'],['transactions'])
    with pytest.raises(ServiceError):connections.cancel(other['id'],link['id'])
    connections.cancel(owner['id'],link['id']);provider.ready=True
    with pytest.raises(ServiceError):connections.complete(owner['id'],link['id'])
    assert not provider.removed and not connections.items(owner['id'])


def test_provider_item_identity_is_scoped_to_environment(tmp_path):
    store=ServiceStore(tmp_path,Fernet.generate_key());owner=identity(store,'owner');provider=Provider();provider.ready=True
    connections=Connections(store,provider)
    sandbox=connections.complete(owner['id'],connections.begin(owner['id'],['transactions'])['id'])
    provider.environment='production'
    production=connections.complete(owner['id'],connections.begin(owner['id'],['transactions'])['id'])
    assert sandbox['itemId']!=production['itemId']
    assert {r['environment'] for r in connections.items(owner['id'])}=={'sandbox','production'}
    with pytest.raises(ServiceError):connections.refresh(owner['id'],sandbox['itemId'])
    assert not provider.removed


def test_checkpointed_exchange_resumes_persistence_without_reusing_public_token(tmp_path,monkeypatch):
    store=ServiceStore(tmp_path,Fernet.generate_key());owner=identity(store,'owner');provider=Provider();provider.ready=True
    connections=Connections(store,provider);link=connections.begin(owner['id'],['transactions'])
    original=connections.persist_exchange
    def fail(*args):raise OSError('Controlled persistence failure')
    monkeypatch.setattr(connections,'persist_exchange',fail)
    with pytest.raises(ServiceError) as error:connections.complete(owner['id'],link['id'])
    assert error.value.status==503 and provider.exchanges==1
    pending=connections.pending(owner['id'])[0]
    assert pending['status']=='exchanged' and 'secret-access-fixture' not in str(pending)
    monkeypatch.setattr(connections,'persist_exchange',original)
    recovered=Connections(store,provider).complete(owner['id'],link['id'])
    assert recovered['status']=='completed' and provider.exchanges==1 and not provider.removed


def test_lost_exchange_checkpoint_is_uncertain_and_never_silently_retried(tmp_path,monkeypatch):
    store=ServiceStore(tmp_path,Fernet.generate_key());owner=identity(store,'owner');provider=Provider();provider.ready=True
    connections=Connections(store,provider);link=connections.begin(owner['id'],['transactions']);checkpoint=connections.checkpoint
    def fail(user,ident,data,status,expected=None):
        if status=='exchanged':raise OSError('Controlled post-exchange power loss')
        return checkpoint(user,ident,data,status,expected)
    monkeypatch.setattr(connections,'checkpoint',fail)
    with pytest.raises(ServiceError):connections.complete(owner['id'],link['id'])
    assert provider.exchanges==1
    # Simulate a restart after the claim grace period, not a live in-flight call.
    with store.connection() as db:
        row=db.execute('SELECT data FROM provider_links WHERE id=?',(link['id'],)).fetchone();data=store.decrypt(row[0])
        data['exchangeStarted']=(datetime.now(timezone.utc)-timedelta(minutes=2)).isoformat()
        db.execute('UPDATE provider_links SET data=? WHERE id=?',(store.encrypt(data),link['id']))
    with pytest.raises(ServiceError) as error:Connections(store,provider).complete(owner['id'],link['id'])
    assert 'not durably confirmed' in str(error.value) and provider.exchanges==1
    assert connections.pending(owner['id'])[0]['status']=='uncertain'
    with pytest.raises(ServiceError):connections.cancel(owner['id'],link['id'])


def test_uncertain_resolution_requires_owner_binding_external_review_and_preserves_audit(tmp_path):
    store=ServiceStore(tmp_path,Fernet.generate_key());owner=identity(store,'owner');provider=Provider();provider.ready=True
    connections=Connections(store,provider);link=connections.begin(owner['id'],['transactions'])
    with store.connection() as db:
        row=db.execute('SELECT data FROM provider_links WHERE id=?',(link['id'],)).fetchone();data=store.decrypt(row['data']);data['exchangeStarted']=(datetime.now(timezone.utc)-timedelta(minutes=2)).isoformat()
    connections.checkpoint(owner['id'],link['id'],data,'uncertain')
    with pytest.raises(ServiceError):connections.resolve_uncertain('other',link['id'],'Synthetic case ref')
    result=Connections(store,None).resolve_uncertain('owner',link['id'],'Synthetic case ref')
    assert result==dict(status='resolved_external',providerConfirmed=False,externalAttestation=True)
    assert not provider.removed and provider.exchanges==0 and not connections.pending(owner['id'])
    with store.connection() as db:
        row=db.execute('SELECT * FROM provider_links WHERE id=?',(link['id'],)).fetchone()
        assert row['status']=='resolved_external' and 'linkToken' not in store.decrypt(row['data'])
        assert db.execute("SELECT COUNT(*) FROM service_audit WHERE action='institution_external_revocation_attested'").fetchone()[0]==1
    retained=connections.begin(owner['id'],['transactions']);connections.complete(owner['id'],retained['id'])
    with pytest.raises(ServiceError):connections.resolve_uncertain('owner',retained['id'],'Synthetic case ref')


def test_expired_and_canceled_consents_survive_new_link_and_deleted_identity_has_no_outbound_link(tmp_path):
    store=ServiceStore(tmp_path,Fernet.generate_key());owner=identity(store,'owner');provider=Provider();connections=Connections(store,provider)
    expired=connections.begin(owner['id'],['transactions'])
    with store.connection() as db:db.execute('UPDATE provider_links SET expires=? WHERE id=?',(0,expired['id']))
    canceled=connections.begin(owner['id'],['transactions']);connections.cancel(owner['id'],canceled['id'])
    rows=connections.pending(owner['id'])
    assert {row['status'] for row in rows}=={'expired_pending','canceled'} and all(row['url'] is None for row in rows)
    canceled_row=next(row for row in rows if row['status']=='canceled');assert canceled_row['environment']=='sandbox' and canceled_row['products']==['transactions']
    with store.connection() as db:assert db.execute('SELECT COUNT(*) FROM provider_links').fetchone()[0]==2
    connections.resolve_uncertain('owner',expired['id'],'Synthetic expired-consent review');connections.resolve_uncertain('owner',canceled['id'],'Synthetic canceled-consent review')
    assert not connections.pending(owner['id']) and not provider.removed
    with store.connection() as db:db.execute('DELETE FROM users WHERE id=?',(owner['id'],))
    provider.create_link=lambda *args:pytest.fail('No outbound consent for a deleted identity')
    with pytest.raises(ServiceError,match='no longer exists'):connections.begin(owner['id'],['transactions'])
