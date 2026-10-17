import time
import pytest
pytest.importorskip('argon2');pytest.importorskip('cryptography')
from cryptography.fernet import Fernet
from card_compass.connected_service.store import ServiceStore,ServiceError,totp
from card_compass.connected_service.connections import Connections
from card_compass.connected_service.privacy import Privacy
from tests.test_connected_store import identity,snapshot
from tests.test_connected_connections import Provider

PASSWORD='Long testing passphrase 2026'


@pytest.fixture
def admin(tmp_path):
    store=ServiceStore(tmp_path,Fernet.generate_key());provider=Provider();connections=Connections(store,provider);privacy=Privacy(store,connections)
    alice=identity(store,'alice');bob=identity(store,'bob')
    session=store.login('alice',PASSWORD,alice['recoveryCodes'][0]);other=store.login('alice',PASSWORD,alice['recoveryCodes'][1]);bob_session=store.login('bob',PASSWORD,bob['recoveryCodes'][0])
    return store,provider,connections,privacy,alice,bob,session,other,bob_session


def test_public_session_handles_cross_identity_rejection_password_and_other_revocation(admin):
    store,provider,connections,privacy,alice,bob,session,other,bob_session=admin
    rows=privacy.sessions(alice['id'],session['token']);assert len(rows)==2 and sum(row['current'] for row in rows)==1
    assert session['token'] not in str(rows) and 'digest' not in str(rows)
    handle=next(row['id'] for row in rows if not row['current'])
    with pytest.raises(ServiceError):privacy.revoke_session(bob['id'],bob_session['token'],handle)
    privacy.change_password(alice['id'],session['token'],PASSWORD,alice['recoveryCodes'][2],'Changed testing passphrase 2026')
    with pytest.raises(ServiceError):store.authenticate(other['token'])
    assert store.authenticate(session['token'])['id']==alice['id']
    with pytest.raises(ServiceError):store.login('alice',PASSWORD,alice['recoveryCodes'][3])
    assert store.login('alice','Changed testing passphrase 2026',alice['recoveryCodes'][3])['user']['id']==alice['id']


def test_recovery_two_phase_cancel_wrong_session_and_old_codes_until_activation(admin):
    store,provider,connections,privacy,alice,bob,session,other,bob_session=admin
    pending=privacy.begin_change(alice['id'],session['token'],PASSWORD,alice['recoveryCodes'][2],'recovery')
    # Lost begin response does not disable the existing factor/recovery set.
    store.login('alice',PASSWORD,alice['recoveryCodes'][3])
    with pytest.raises(ServiceError):privacy.confirm_change(alice['id'],other['token'],pending['id'],pending['activationCode'])
    with pytest.raises(ServiceError):privacy.confirm_change(alice['id'],session['token'],pending['id'],'wrong')
    privacy.cancel_change(alice['id'],session['token'],pending['id'])
    store.login('alice',PASSWORD,alice['recoveryCodes'][4])
    pending=privacy.begin_change(alice['id'],session['token'],PASSWORD,alice['recoveryCodes'][5],'recovery')
    privacy.confirm_change(alice['id'],session['token'],pending['id'],pending['activationCode'])
    with pytest.raises(ServiceError):store.login('alice',PASSWORD,alice['recoveryCodes'][6])
    fresh=store.login('alice',PASSWORD,pending['recoveryCodes'][0]);assert fresh['user']['id']==alice['id']
    with pytest.raises(ServiceError):store.login('alice',PASSWORD,pending['recoveryCodes'][0])
    assert store.authenticate(session['token'])['id']==alice['id']


def test_authenticator_replacement_confirms_new_code_and_expired_setup_keeps_old_factor(admin):
    store,provider,connections,privacy,alice,bob,session,other,bob_session=admin
    pending=privacy.begin_change(alice['id'],session['token'],PASSWORD,alice['recoveryCodes'][2],'mfa')
    with store.connection() as db:db.execute('UPDATE identity_changes SET expires=?',(time.time()-1,))
    with pytest.raises(ServiceError):privacy.confirm_change(alice['id'],session['token'],pending['id'],totp(pending['mfaSecret'],int(time.time()//30)))
    pending=privacy.begin_change(alice['id'],session['token'],PASSWORD,alice['recoveryCodes'][3],'mfa')
    code=totp(pending['mfaSecret'],int(time.time()//30));privacy.confirm_change(alice['id'],session['token'],pending['id'],code)
    with pytest.raises(ServiceError):store.login('alice',PASSWORD,code)
    assert store.login('alice',PASSWORD,pending['recoveryCodes'][0])['user']['id']==alice['id']
    raw=store.path.read_bytes();assert pending['mfaSecret'].encode() not in raw and pending['recoveryCodes'][0].encode() not in raw


def test_export_consistent_owner_scope_credentials_excluded_claim_and_expiry(admin):
    store,provider,connections,privacy,alice,bob,session,other,bob_session=admin
    store.save_resource(alice['id'],'manual_account',snapshot('Alice private'))
    store.save_resource(bob['id'],'manual_account',snapshot('Bob private'))
    provider.ready=True;item=connections.complete(alice['id'],connections.begin(alice['id'],['transactions','investments'])['id'])['itemId'];connections.refresh(alice['id'],item)
    manifest=privacy.prepare_export(alice['id'],session['token'],PASSWORD,alice['recoveryCodes'][2]);records=[]
    store.save_resource(alice['id'],'manual_account',snapshot('Later private addition'))
    for sequence in range(manifest['chunkCount']):records.extend(privacy.export_chunk(alice['id'],session['token'],manifest['id'],sequence)['records'])
    assert 'Alice private' in str(records) and 'Bob private' not in str(records) and 'Later private addition' not in str(records)
    assert 'Private purchase' in str(records) and 'secret-access-fixture' not in str(records) and 'new-cursor' not in str(records)
    assert 'password_hash' not in str(records) and alice['mfaSecret'] not in str(records) and session['token'] not in str(records)
    with pytest.raises(ServiceError):privacy.export_chunk(alice['id'],other['token'],manifest['id'],0)
    with pytest.raises(ServiceError):privacy.cancel_export(bob['id'],bob_session['token'],manifest['id'])
    privacy.claim_export(alice['id'],other['token'],manifest['id'],PASSWORD,alice['recoveryCodes'][3])
    with pytest.raises(ServiceError):privacy.export_chunk(alice['id'],session['token'],manifest['id'],0)
    assert privacy.export_chunk(alice['id'],other['token'],manifest['id'],0)['records']
    with store.connection() as db:db.execute('UPDATE privacy_exports SET expires=?',(time.time()-1,))
    with pytest.raises(ServiceError):privacy.export_chunk(alice['id'],other['token'],manifest['id'],0)
    with store.connection() as db:privacy.cleanup(db);assert not db.execute('SELECT 1 FROM privacy_chunks').fetchone()


def test_deletion_transfer_guard_review_changes_and_other_private_records_survive(admin):
    store,provider,connections,privacy,alice,bob,session,other,bob_session=admin
    home=store.create_household(alice['id'],'Shared home');invite=store.invite(alice['id'],home['id'],'bob','editor');store.accept_invite(bob['id'],invite['invite'])
    alice_record=store.save_resource(alice['id'],'manual_account',snapshot('Alice shared'),home['id']);bob_record=store.save_resource(bob['id'],'manual_account',snapshot('Bob private'))
    plan=privacy.deletion_plan(alice['id'],session['token']);assert plan['blockers']
    with pytest.raises(ServiceError):privacy.delete_identity(alice['id'],session['token'],PASSWORD,alice['recoveryCodes'][2],plan['planHash'],'alice')
    store.set_role(alice['id'],home['id'],bob['id'],'owner');plan=privacy.deletion_plan(alice['id'],session['token'])
    store.save_resource(alice['id'],'manual_account',snapshot('Alice later'))
    with pytest.raises(ServiceError,match='scope changed'):privacy.delete_identity(alice['id'],session['token'],PASSWORD,alice['recoveryCodes'][2],plan['planHash'],'alice')
    plan=privacy.deletion_plan(alice['id'],session['token']);result=privacy.delete_identity(alice['id'],session['token'],PASSWORD,alice['recoveryCodes'][2],plan['planHash'],'alice')
    assert result['identityDeleted'] and not result['localCopiesDeleted'] and not result['providerRevocationPerformed']
    with pytest.raises(ServiceError):store.authenticate(session['token'])
    assert store.resource(bob['id'],bob_record['id'])['payload']['name']=='Bob private'
    with pytest.raises(ServiceError):store.resource(bob['id'],alice_record['id'])
    assert store.households(bob['id'])[0]['role']=='owner'
    with store.connection() as db:assert db.execute('SELECT COUNT(*) FROM deletion_receipts').fetchone()[0]==1 and not db.execute('SELECT 1 FROM service_audit WHERE user_id=?',(alice['id'],)).fetchone()


def test_sole_home_requires_explicit_close_and_pending_consents_block_until_external_resolution(admin):
    store,provider,connections,privacy,alice,bob,session,other,bob_session=admin
    home=store.create_household(alice['id'],'Sole home');invite=store.invite(alice['id'],home['id'],'new_member','read_only')
    store.begin_enrollment(invite['invite'],'new_member',PASSWORD)
    link=connections.begin(alice['id'],['transactions']);connections.cancel(alice['id'],link['id'])
    plan=privacy.deletion_plan(alice['id'],session['token']);assert plan['blockers']
    connections.resolve_uncertain('alice',link['id'],'Synthetic external revocation review')
    plan=privacy.deletion_plan(alice['id'],session['token']);assert not plan['blockers']
    with pytest.raises(ServiceError,match='sole-member'):privacy.delete_identity(alice['id'],session['token'],PASSWORD,alice['recoveryCodes'][2],plan['planHash'],'alice')
    privacy.delete_identity(alice['id'],session['token'],PASSWORD,alice['recoveryCodes'][2],plan['planHash'],'alice',True)
    with store.connection() as db:assert not db.execute('SELECT 1 FROM enrollments').fetchone() and not db.execute('SELECT 1 FROM households').fetchone()
