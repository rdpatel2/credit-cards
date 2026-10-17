import time
import pytest
from card_compass.connected_client import ConnectedClient,ConnectionError,service_url


def test_https_default_loopback_opt_in_no_credentials_or_redirect_targets():
    assert service_url('https://service.example/')=='https://service.example'
    assert service_url('http://127.0.0.1:8765',True)=='http://127.0.0.1:8765'
    for url in ('http://service.example','https://user:secret@service.example','https://service.example/other','https://service.example?token=secret','https://service.example:bad'):
        with pytest.raises(ValueError):service_url(url,True)


def test_session_credentials_not_sent_before_login_or_after_logout():
    calls=[]
    def transport(method,url,payload,headers):
        calls.append((method,url,payload,headers))
        if url.endswith('/auth/login'):return dict(token='Synthetic-session-secret-token',expires=time.time()+43200,user=dict(id='fixture',username='alice'))
        return dict(ok=True)
    client=ConnectedClient('https://service.example',transport=transport)
    with pytest.raises(ConnectionError):client.call('GET','/service/v1/me')
    client.login('alice','A reviewed long passphrase','123456')
    assert 'Authorization' not in calls[0][3]
    client.call('GET','/service/v1/me')
    assert calls[-1][3]['Authorization']=='Bearer Synthetic-session-secret-token'
    client.logout();assert not client.signed_in and not client.token
    with pytest.raises(ValueError):client.call('GET','https://untrusted.example/service/v1/me')


def test_invalid_enrollment_material_never_installs_session():
    client=ConnectedClient('https://service.example',transport=lambda *args:dict(challenge='Synthetic-challenge-token',mfaSecret='INVALID',expires=time.time()+600,username='bob'))
    with pytest.raises(ConnectionError):client.begin_enrollment('Synthetic-invitation-token','bob','Long testing passphrase')
    client.transport=lambda *args:dict(token='Synthetic-session-secret-token',expires=time.time()+43200,user=dict(id='bob',username='bob'),recoveryCodes=['duplicate']*10)
    with pytest.raises(ConnectionError):client.finish_enrollment('Synthetic-challenge-token','123456')
    assert not client.signed_in
    client.transport=lambda *args:dict(token='Synthetic-session-secret-token',expires=time.time()+43200,user=dict(id='eve',username='eve'),recoveryCodes=[f'{i:020x}' for i in range(10)])
    with pytest.raises(ConnectionError):client.finish_enrollment('Synthetic-challenge-token','123456',expected_name='bob')
    assert not client.signed_in
