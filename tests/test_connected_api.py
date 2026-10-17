import pytest
pytest.importorskip('fastapi');pytest.importorskip('httpx');pytest.importorskip('argon2');pytest.importorskip('cryptography')
from cryptography.fernet import Fernet
from fastapi.testclient import TestClient
from card_compass.connected_service.store import ServiceStore,totp
from card_compass.connected_service.api import create_app
from tests.test_connected_store import identity,snapshot
import time


@pytest.fixture
def connected(tmp_path):
    store=ServiceStore(tmp_path,Fernet.generate_key())
    app=create_app(store,allowed_hosts=['service.test'])
    with TestClient(app,base_url='https://service.test') as client:yield store,client


def login(client,user):
    result=client.post('/service/v1/auth/login',json=dict(username=user['username'],password='Long testing passphrase 2026',code=user['recoveryCodes'][0]))
    assert result.status_code==200
    return {'Authorization':'Bearer '+result.json()['token']}


def test_http_auth_tls_body_limits_validation_redaction_and_revocation(connected):
    store,client=connected;user=identity(store,'alice');headers=login(client,user)
    assert client.get('/service/v1/me').status_code==401
    response=client.get('/service/v1/me',headers=headers)
    assert response.status_code==200 and response.headers['cache-control']=='no-store'
    assert client.get('/service/v1/me',headers=dict(headers,Origin='https://untrusted.example')).status_code==403
    assert client.get('http://service.test/service/v1/me',headers=headers).status_code==403
    invalid=client.post('/service/v1/auth/login',json=dict(username='alice',password='SECRET',code=1,extra='PRIVATE'))
    assert invalid.status_code==422 and 'SECRET' not in invalid.text and 'PRIVATE' not in invalid.text
    assert client.post('/service/v1/auth/login',content=b'x'*600001).status_code==413
    assert client.post('/service/v1/auth/logout',headers=headers,json=dict(allSessions=True)).status_code==200
    assert client.get('/service/v1/me',headers=headers).status_code==401


def test_real_http_household_permissions_conflicts_and_invited_mfa_enrollment(connected):
    store,client=connected;alice=identity(store,'alice');headers=login(client,alice)
    home=client.post('/service/v1/households',headers=headers,json=dict(name='Home')).json()
    invitation=client.post(f"/service/v1/households/{home['id']}/invitations",headers=headers,json=dict(username='bob',role='read_only')).json()
    begin=client.post('/service/v1/auth/enroll',json=dict(invitation=invitation['invite'],username='bob',password='Long testing passphrase 2026'))
    assert begin.status_code==200
    pending=begin.json();code=totp(pending['mfaSecret'],int(time.time()//30))
    accepted=client.post('/service/v1/auth/enroll/confirm',json=dict(challenge=pending['challenge'],code=code))
    assert accepted.status_code==200 and len(accepted.json()['recoveryCodes'])==10
    bob={'Authorization':'Bearer '+accepted.json()['token']}
    shared=client.post('/service/v1/snapshots',headers=headers,json=dict(kind='manual_account',payload=snapshot(),householdId=home['id']))
    assert shared.status_code==200
    record=shared.json()
    assert client.get('/service/v1/snapshots',headers=bob,params=dict(household=home['id'])).json()['snapshots'][0]['id']==record['id']
    update=dict(kind='manual_account',payload=dict(snapshot(),amountCents=60000),householdId=home['id'],expectedVersion=1)
    assert client.put('/service/v1/snapshots/'+record['id'],headers=bob,json=update).status_code==403
    assert client.put('/service/v1/snapshots/'+record['id'],headers=headers,json=update).status_code==200
    assert client.put('/service/v1/snapshots/'+record['id'],headers=headers,json=update).status_code==409
    private=client.post('/service/v1/snapshots',headers=headers,json=dict(kind='manual_account',payload=snapshot('Private account'))).json()
    assert client.get('/service/v1/snapshots/'+private['id'],headers=bob).status_code==404
    assert client.post('/service/v1/auth/enroll/confirm',json=dict(challenge=pending['challenge'],code=code)).status_code==403


def test_pending_enrollment_cancel_releases_invitation_without_deleting_identity(connected):
    store,client=connected;alice=identity(store,'alice');headers=login(client,alice)
    home=store.create_household(alice['id'],'Home')
    invitation=store.invite(alice['id'],home['id'],'bob','read_only')['invite']
    payload=dict(invitation=invitation,username='bob',password='Long testing passphrase 2026')
    first=client.post('/service/v1/auth/enroll',json=payload).json()
    assert client.post('/service/v1/auth/enroll',json=payload).status_code==409
    assert client.post('/service/v1/auth/enroll/cancel',json=dict(challenge='Unrelated-synthetic-challenge')).status_code==404
    assert client.post('/service/v1/auth/enroll/cancel',json=dict(challenge=first['challenge'])).json()==dict(canceled=True)
    second=client.post('/service/v1/auth/enroll',json=payload).json()
    assert first['challenge']!=second['challenge']
    code=totp(second['mfaSecret'],int(time.time()//30))
    result=client.post('/service/v1/auth/enroll/confirm',json=dict(challenge=second['challenge'],code=code)).json()
    assert client.post('/service/v1/auth/enroll/cancel',json=dict(challenge=second['challenge'])).status_code==404
    assert store.authenticate(result['token'])['username']=='bob'
