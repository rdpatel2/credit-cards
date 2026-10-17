import pytest
pytest.importorskip('fastapi');pytest.importorskip('httpx');pytest.importorskip('argon2');pytest.importorskip('cryptography')
from tests.test_connected_api import connected,login
from tests.test_connected_store import identity,snapshot


def test_http_security_proof_does_not_revoke_valid_session_until_activation(connected):
    store,client=connected;alice=identity(store,'alice');headers=login(client,alice)
    failed=client.post('/service/v1/auth/password',headers=headers,json=dict(password='Wrong passphrase',code='123456',newPassword='New testing passphrase 2026'))
    assert failed.status_code==403 and client.get('/service/v1/me',headers=headers).status_code==200
    for _ in range(15):assert client.get('/service/v1/auth/sessions',headers=headers).status_code==200
    begin=client.post('/service/v1/auth/security/recovery/begin',headers=headers,json=dict(password='Long testing passphrase 2026',code=alice['recoveryCodes'][1]))
    assert begin.status_code==200;pending=begin.json()
    assert client.post(f"/service/v1/auth/security/{pending['id']}/confirm",headers=headers,json=dict(code='0'*20)).status_code==403
    assert client.get('/service/v1/me',headers=headers).status_code==200
    activated=client.post(f"/service/v1/auth/security/{pending['id']}/confirm",headers=headers,json=dict(code=pending['activationCode']))
    assert activated.status_code==200 and activated.json()['otherSessionsRevoked']
    assert client.post('/service/v1/auth/login',json=dict(username='alice',password='Long testing passphrase 2026',code=alice['recoveryCodes'][2])).status_code==401
    assert client.post('/service/v1/auth/login',json=dict(username='alice',password='Long testing passphrase 2026',code=pending['recoveryCodes'][0])).status_code==200


def test_http_export_revoke_invitation_and_reviewed_deletion(connected):
    store,client=connected;alice=identity(store,'alice');bob=identity(store,'bob');headers=login(client,alice);other=login(client,bob)
    store.save_resource(alice['id'],'manual_account',snapshot('Alice selected source'));store.save_resource(bob['id'],'manual_account',snapshot('Bob private source'))
    home=store.create_household(alice['id'],'Alice household');invite=store.invite(alice['id'],home['id'],'new_member','read_only')
    staged=store.begin_enrollment(invite['invite'],'new_member','Long testing passphrase 2026')
    rows=client.get(f"/service/v1/households/{home['id']}/invitations",headers=headers).json()['invitations']
    assert client.delete(f"/service/v1/households/{home['id']}/invitations/{rows[0]['digest']}",headers=other).status_code==403
    assert client.delete(f"/service/v1/households/{home['id']}/invitations/{rows[0]['digest']}",headers=headers).status_code==200
    with store.connection() as db:assert not db.execute('SELECT 1 FROM enrollments').fetchone()
    prepared=client.post('/service/v1/privacy/exports',headers=headers,json=dict(password='Long testing passphrase 2026',code=alice['recoveryCodes'][1])).json()
    chunk=client.get(f"/service/v1/privacy/exports/{prepared['id']}/chunks/0",headers=headers)
    assert chunk.status_code==200 and 'Alice selected source' in chunk.text and 'Bob private source' not in chunk.text
    assert alice['mfaSecret'] not in chunk.text and alice['recoveryCodes'][2] not in chunk.text
    assert client.get(f"/service/v1/privacy/exports/{prepared['id']}/chunks/0",headers=other).status_code==404
    plan=client.get('/service/v1/privacy/deletion-plan',headers=headers).json()
    store.save_resource(alice['id'],'manual_account',snapshot('After review'))
    payload=dict(password='Long testing passphrase 2026',code=alice['recoveryCodes'][2],planHash=plan['planHash'],confirmation='alice',closeSoleHouseholds=True)
    assert client.post('/service/v1/privacy/delete-identity',headers=headers,json=payload).status_code==409
    payload['planHash']=client.get('/service/v1/privacy/deletion-plan',headers=headers).json()['planHash']
    removed=client.post('/service/v1/privacy/delete-identity',headers=headers,json=payload)
    assert removed.status_code==200 and removed.json()['identityDeleted'] and not removed.json()['providerRevocationPerformed']
    assert client.get('/service/v1/me',headers=headers).status_code==401
    assert client.get('/service/v1/me',headers=other).status_code==200
    assert len(store.resources(bob['id']))==1


def test_export_capacity_rejection_does_not_consume_fresh_recovery_proof(connected):
    store,client=connected;alice=identity(store,'alice');headers=login(client,alice)
    prepared=client.post('/service/v1/privacy/exports',headers=headers,json=dict(password='Long testing passphrase 2026',code=alice['recoveryCodes'][1]))
    assert prepared.status_code==200
    assert client.post('/service/v1/privacy/exports',headers=headers,json=dict(password='Long testing passphrase 2026',code=alice['recoveryCodes'][2])).status_code==409
    assert client.post('/service/v1/auth/login',json=dict(username='alice',password='Long testing passphrase 2026',code=alice['recoveryCodes'][2])).status_code==200
