import pytest
pytest.importorskip('argon2');pytest.importorskip('cryptography')
from cryptography.fernet import Fernet,InvalidToken
import time
from card_compass.connected_service.store import ServiceStore,ServiceError,totp,matched_step


@pytest.fixture
def store(tmp_path):return ServiceStore(tmp_path,Fernet.generate_key())


def identity(store,name):return store.create_user(name,'Long testing passphrase 2026')


def snapshot(name='Reviewed account'):
    return dict(version=1,name=name,currency='USD',accountType='savings',owner='Reviewed owner',asOf='2026-10-10',amountCents=50000,aprBps=0,minimumPaymentCents=0,source='Synthetic manual fixture')


def test_password_mfa_replay_recovery_expiry_logout_and_key_identity(store):
    user=identity(store,'alice');step=int(time.time()//30);code=totp(user['mfaSecret'],step)
    assert matched_step(user['mfaSecret'],code,-1)==step
    session=store.login('ALICE','Long testing passphrase 2026',code)
    assert store.authenticate(session['token'])['id']==user['id']
    with pytest.raises(ServiceError):store.login('alice','Long testing passphrase 2026',code)
    recovery=user['recoveryCodes'][0]
    store.login('alice','Long testing passphrase 2026',recovery)
    with pytest.raises(ServiceError):store.login('alice','Long testing passphrase 2026',recovery)
    with pytest.raises(ServiceError):store.login('missing','wrong password',code)
    store.logout(user['id'],session['token'],all_sessions=True)
    with pytest.raises(ServiceError):store.authenticate(session['token'])
    with pytest.raises(InvalidToken):ServiceStore(store.directory,Fernet.generate_key())
    raw=store.path.read_bytes()
    assert user['mfaSecret'].encode() not in raw and b'Long testing passphrase 2026' not in raw and session['token'].encode() not in raw


def test_invitation_binding_one_use_role_enforcement_private_accounts_and_cross_households(store):
    alice=identity(store,'alice');bob=identity(store,'bob');eve=identity(store,'eve')
    household=store.create_household(alice['id'],'Private household name')
    invitation=store.invite(alice['id'],household['id'],'bob','read_only')
    with pytest.raises(ServiceError):store.accept_invite(eve['id'],invitation['invite'])
    store.accept_invite(bob['id'],invitation['invite'])
    with pytest.raises(ServiceError):store.accept_invite(bob['id'],invitation['invite'])
    shared=store.save_resource(alice['id'],'manual_account',snapshot(),household['id'])
    private=store.save_resource(alice['id'],'manual_account',snapshot('Private account'))
    private=store.save_resource(alice['id'],'manual_account',dict(snapshot('Private account'),amountCents=52000),None,private['id'],1)
    assert private['version']==2
    assert store.resource(bob['id'],shared['id'])['payload']['amountCents']==50000
    assert len(store.resources(bob['id'],household['id']))==1
    with pytest.raises(ServiceError) as failure:store.resource(bob['id'],private['id'])
    assert failure.value.status==404
    with pytest.raises(ServiceError):store.resource(eve['id'],shared['id'])
    with pytest.raises(ServiceError):store.save_resource(bob['id'],'manual_account',snapshot(),household['id'],shared['id'],1)
    with pytest.raises(ServiceError):store.invite(bob['id'],household['id'],'eve','owner')
    assert b'Private account' not in store.path.read_bytes() and b'Private household name' not in store.path.read_bytes()


def test_optimistic_versions_editor_scope_last_owner_and_member_removal(store):
    alice=identity(store,'alice');bob=identity(store,'bob')
    household=store.create_household(alice['id'],'Shared home')
    invite=store.invite(alice['id'],household['id'],'bob','editor');store.accept_invite(bob['id'],invite['invite'])
    resource=store.save_resource(alice['id'],'manual_account',snapshot(),household['id'])
    edited=store.save_resource(bob['id'],'manual_account',dict(snapshot(),amountCents=51000),household['id'],resource['id'],1)
    assert edited['version']==2
    with pytest.raises(ServiceError) as failure:store.save_resource(alice['id'],'manual_account',snapshot(),household['id'],resource['id'],1)
    assert failure.value.status==409
    with pytest.raises(ServiceError):store.save_resource(bob['id'],'manual_account',snapshot(),None,resource['id'],2)
    with pytest.raises(ServiceError):store.set_role(alice['id'],household['id'],alice['id'],None)
    publication=store.save_resource(bob['id'],'manual_account',snapshot('Bob snapshot'),household['id'])
    store.set_role(alice['id'],household['id'],bob['id'],None)
    with pytest.raises(ServiceError):store.resource(bob['id'],resource['id'])
    assert store.resource(bob['id'],publication['id'])['householdId'] is None
    with pytest.raises(ServiceError):store.resource(alice['id'],publication['id'])


def test_read_only_creator_can_withdraw_or_delete_own_publication(store):
    alice=identity(store,'alice');bob=identity(store,'bob');household=store.create_household(alice['id'],'Home')
    invitation=store.invite(alice['id'],household['id'],'bob','editor');store.accept_invite(bob['id'],invitation['invite'])
    publication=store.save_resource(bob['id'],'manual_account',snapshot(),household['id'])
    second=store.save_resource(bob['id'],'manual_account',snapshot(),household['id'])
    store.set_role(alice['id'],household['id'],bob['id'],'read_only')
    withdrawn=store.save_resource(bob['id'],'manual_account',snapshot(),None,publication['id'],1)
    assert withdrawn['householdId'] is None
    with pytest.raises(ServiceError):store.resource(alice['id'],publication['id'])
    store.delete_resource(bob['id'],second['id'],1)
