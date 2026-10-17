import pytest
pytest.importorskip('cryptography');pytest.importorskip('argon2')
from cryptography.fernet import Fernet
from card_compass.connected_service.__main__ import main
from card_compass.connected_service.lease import DirectoryLease


def test_setup_rejects_desktop_profile_public_http_wildcard_proxy_and_missing_key(tmp_path,monkeypatch,capsys):
    monkeypatch.delenv('FINANCE_SERVICE_KEY',raising=False)
    monkeypatch.setattr('sys.argv',['service','--data-dir',str(tmp_path)])
    with pytest.raises(SystemExit):main()
    assert 'FINANCE_SERVICE_KEY' in capsys.readouterr().err
    monkeypatch.setenv('FINANCE_SERVICE_KEY',Fernet.generate_key().decode())
    for extra in (['--host','0.0.0.0'],['--trusted-proxy','*'],['--tls-cert','cert.pem']):
        monkeypatch.setattr('sys.argv',['service','--data-dir',str(tmp_path),*extra])
        with pytest.raises(SystemExit):main()
    (tmp_path/'profile.json').write_text('{}')
    monkeypatch.setattr('sys.argv',['service','--data-dir',str(tmp_path)])
    with pytest.raises(SystemExit):main()
    assert 'separate service directory' in capsys.readouterr().err


def test_process_lease_prevents_second_cli_and_releases_after_exit(tmp_path,monkeypatch,capsys):
    monkeypatch.setenv('FINANCE_SERVICE_KEY',Fernet.generate_key().decode())
    monkeypatch.setattr('sys.argv',['service','--data-dir',str(tmp_path),'--local-http'])
    with DirectoryLease(tmp_path):
        with pytest.raises(SystemExit):main()
        assert 'Another service/operator process' in capsys.readouterr().err
        assert not (tmp_path/'service.sqlite3').exists()
    with DirectoryLease(tmp_path):assert (tmp_path/'service.lock').exists()


def test_operator_resolution_requires_explicit_external_attestation(tmp_path,monkeypatch,capsys):
    monkeypatch.setenv('FINANCE_SERVICE_KEY',Fernet.generate_key().decode())
    monkeypatch.setattr('sys.argv',['service','--data-dir',str(tmp_path),'--resolve-consent','fixture'])
    with pytest.raises(SystemExit):main()
    assert '--confirm-external-revocation' in capsys.readouterr().err
    assert not (tmp_path/'service.sqlite3').exists()
