"""Actual loopback HTTP through Uvicorn and the desktop urllib client."""
import pytest
pytest.importorskip('uvicorn');pytest.importorskip('cryptography');pytest.importorskip('argon2');pytest.importorskip('fastapi')
from cryptography.fernet import Fernet
import threading
import time
import uvicorn
from card_compass.connected_service.store import ServiceStore,totp
from card_compass.connected_service.api import create_app
from card_compass.connected_client import ConnectedClient,ConnectionError
from tests.test_connected_store import identity,snapshot


def test_two_desktop_clients_real_http_private_shared_read_only_and_conflict(tmp_path,monkeypatch):
    store=ServiceStore(tmp_path,Fernet.generate_key());alice=identity(store,'alice')
    app=create_app(store,allowed_hosts=['127.0.0.1'],allow_local_http=True)
    server=uvicorn.Server(uvicorn.Config(app,host='127.0.0.1',port=0,access_log=False,log_level='error',lifespan='off'))
    thread=threading.Thread(target=server.run,daemon=True);thread.start()
    deadline=time.monotonic()+5
    while not server.started and time.monotonic()<deadline:time.sleep(.01)
    assert server.started
    port=server.servers[0].sockets[0].getsockname()[1]
    first=ConnectedClient(f'http://127.0.0.1:{port}',allow_local_http=True)
    second=ConnectedClient(f'http://127.0.0.1:{port}',allow_local_http=True)
    try:
        first.login('alice','Long testing passphrase 2026',alice['recoveryCodes'][0])
        home=first.call('POST','/service/v1/households',dict(name='Verified household'))
        invite=first.call('POST',f"/service/v1/households/{home['id']}/invitations",dict(username='bob',role='read_only'))
        pending=second.begin_enrollment(invite['invite'],'bob','Long testing passphrase 2026')
        second.cancel_enrollment(pending['challenge'])
        pending=second.begin_enrollment(invite['invite'],'bob','Long testing passphrase 2026')
        enrolled=second.finish_enrollment(pending['challenge'],totp(pending['mfaSecret'],int(time.time()//30)),expected_name='bob')
        assert second.identity['username']=='bob' and len(enrolled['recoveryCodes'])==10
        shared=first.call('POST','/service/v1/snapshots',dict(kind='manual_account',payload=snapshot(),householdId=home['id']))
        private=first.call('POST','/service/v1/snapshots',dict(kind='manual_account',payload=snapshot('Private saved account')))
        rows=second.call('GET','/service/v1/snapshots',query=dict(household=home['id']))['snapshots']
        assert len(rows)==1 and rows[0]['id']==shared['id']
        # Exercise the actual native async fetch/review/re-fetch/save path,
        # using the read-only household member and real urllib networking.
        import os
        os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
        from PySide6.QtWidgets import QApplication
        from card_compass.app import MainWindow
        from card_compass.connected_ui import ConnectedServicesDialog
        from card_compass.shared_sync_ui import SharedCopyReview
        qt=QApplication.instance() or QApplication([])
        window=MainWindow(tmp_path/'bob-desktop',auto_refresh=False)
        dialog=ConnectedServicesDialog(window);dialog.manager.client=second
        def reviewed(form):
            assert form.origin==second.url and form.identity==second.identity['id']
            form.reviewed.setChecked(True);form.confirm();return form.DialogCode.Accepted
        monkeypatch.setattr(SharedCopyReview,'exec',reviewed)
        try:
            dialog.household.blockSignals(True);dialog.household.addItem('Verified household',home['id']);dialog.household.setCurrentIndex(1);dialog.household.blockSignals(False)
            dialog.refresh_scope()
            def wait_native():
                deadline=time.monotonic()+5
                while dialog.manager.busy and time.monotonic()<deadline:qt.processEvents();time.sleep(.01)
                qt.processEvents();assert not dialog.manager.busy
            wait_native();dialog.shared.selectRow(0);dialog.download_snapshot();wait_native()
            copied=window.planning_store.accounts()
            assert len(copied)==1 and copied[0]['amount_cents']==50000 and copied[0]['source_info']['origin']==second.url
            assert copied[0]['source_info']['identity']==second.identity['id']
            assert second.token.encode() not in (tmp_path/'bob-desktop'/'transactions.sqlite3').read_bytes()
            assert 'local copy saved' in dialog.status.text()
        finally:
            dialog.close()
            # Detach the test client so desktop shutdown doesn't forget its
            # session before the server-revocation assertions below.
            window.connected_manager.client=None;window.close();qt.processEvents()
        with pytest.raises(ConnectionError) as error:second.call('GET','/service/v1/snapshots/'+private['id'])
        assert error.value.status==404
        with pytest.raises(ConnectionError) as error:second.call('PUT','/service/v1/snapshots/'+shared['id'],dict(kind='manual_account',payload=snapshot(),householdId=home['id'],expectedVersion=1))
        assert error.value.status==403
        first.call('PUT','/service/v1/snapshots/'+shared['id'],dict(kind='manual_account',payload=dict(snapshot(),amountCents=60000),householdId=home['id'],expectedVersion=1))
        with pytest.raises(ConnectionError) as error:first.call('PUT','/service/v1/snapshots/'+shared['id'],dict(kind='manual_account',payload=snapshot(),householdId=home['id'],expectedVersion=1))
        assert error.value.status==409
        first.logout();second.logout();assert not first.token and not second.token
        second.login('bob','Long testing passphrase 2026',enrolled['recoveryCodes'][0])
        assert second.call('GET','/service/v1/me')['username']=='bob'
        second.logout()
    finally:
        server.should_exit=True;thread.join(timeout=5)
        assert not thread.is_alive()


def test_real_native_privacy_export_deletion_and_local_profile_preservation(tmp_path,monkeypatch):
    store=ServiceStore(tmp_path/'service',Fernet.generate_key());alice=identity(store,'alice')
    store.save_resource(alice['id'],'manual_account',snapshot('Service selected account'))
    app=create_app(store,allowed_hosts=['127.0.0.1'],allow_local_http=True)
    server=uvicorn.Server(uvicorn.Config(app,host='127.0.0.1',port=0,access_log=False,log_level='error',lifespan='off'))
    thread=threading.Thread(target=server.run,daemon=True);thread.start()
    deadline=time.monotonic()+5
    while not server.started and time.monotonic()<deadline:time.sleep(.01)
    assert server.started
    client=ConnectedClient(f"http://127.0.0.1:{server.servers[0].sockets[0].getsockname()[1]}",allow_local_http=True)
    window=None;main=None;privacy=None
    try:
        client.login('alice','Long testing passphrase 2026',alice['recoveryCodes'][0])
        with pytest.raises(ConnectionError) as error:client.call('POST','/service/v1/auth/security/recovery/begin',dict(password='Wrong passphrase',code='0'*20))
        assert error.value.status==403 and client.signed_in
        import os
        os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
        from PySide6.QtWidgets import QApplication,QFileDialog
        from card_compass.app import MainWindow
        from card_compass.connected_ui import ConnectedServicesDialog
        from card_compass.connected_privacy_ui import ServicePrivacyDialog,ProofDialog
        qt=QApplication.instance() or QApplication([])
        window=MainWindow(tmp_path/'desktop',auto_refresh=False)
        window.planning_store.save_account('Local private savings','savings','USD','Me',321,'2026-10-11')
        before=window.planning_store.accounts();main=ConnectedServicesDialog(window);main.manager.client=client;privacy=ServicePrivacyDialog(main)
        proof_codes=iter(alice['recoveryCodes'][1:])
        def proof(dialog):dialog.payload=dict(password='Long testing passphrase 2026',code=next(proof_codes));return dialog.DialogCode.Accepted
        monkeypatch.setattr(ProofDialog,'exec',proof)
        def wait_native():
            deadline=time.monotonic()+10
            while main.manager.busy and time.monotonic()<deadline:qt.processEvents();time.sleep(.01)
            qt.processEvents();assert not main.manager.busy
        privacy.prepare_export();wait_native();assert privacy.exports and privacy.export_table.rowCount()==1
        path=tmp_path/'reviewed-export.zip';monkeypatch.setattr(QFileDialog,'getSaveFileName',lambda *args:(str(path),'ZIP'))
        privacy.download();wait_native();assert path.exists() and 'archive saved' in privacy.status.text() and 'export removed' in privacy.status.text()
        import zipfile
        with zipfile.ZipFile(path) as archive:assert 'Service selected account' in archive.read('snapshots.jsonl').decode()
        privacy.deletion_plan();wait_native();assert privacy.plan['createdSnapshots']==1
        privacy.confirmation.setText('alice');privacy.delete_identity();wait_native()
        assert not client.signed_in and 'identity deleted' in privacy.status.text()
        assert before==window.planning_store.accounts()
        with store.connection() as db:assert not db.execute('SELECT 1 FROM users WHERE id=?',(alice['id'],)).fetchone()
        assert b'Long testing passphrase 2026' not in (tmp_path/'desktop'/'transactions.sqlite3').read_bytes()
    finally:
        if privacy:privacy.close()
        if main:main.close()
        if window:window.close()
        server.should_exit=True;thread.join(timeout=5);assert not thread.is_alive()


def test_real_service_native_balance_fetch_review_recheck_and_backup(tmp_path,monkeypatch):
    from tests.test_connected_connections import Provider
    class BalanceProvider(Provider):
        def accounts(self,*args,**kwargs):return dict(accounts=[dict(account_id='provider-account',name='Synthetic bank checking',type='depository',subtype='checking',mask='1234',balances=dict(current=123.45,available=100,iso_currency_code='USD'))])
    store=ServiceStore(tmp_path/'service',Fernet.generate_key());alice=identity(store,'alice');provider=BalanceProvider();provider.ready=True
    app=create_app(store,allowed_hosts=['127.0.0.1'],allow_local_http=True,provider=provider)
    server=uvicorn.Server(uvicorn.Config(app,host='127.0.0.1',port=0,access_log=False,log_level='error',lifespan='off'))
    thread=threading.Thread(target=server.run,daemon=True);thread.start();deadline=time.monotonic()+5
    while not server.started and time.monotonic()<deadline:time.sleep(.01)
    assert server.started
    client=ConnectedClient(f"http://127.0.0.1:{server.servers[0].sockets[0].getsockname()[1]}",allow_local_http=True)
    root=None;dialog=None
    try:
        client.login('alice','Long testing passphrase 2026',alice['recoveryCodes'][0])
        consent=client.call('POST','/service/v1/connections/link',dict(products=['transactions']))
        item=client.call('POST',f"/service/v1/connections/link/{consent['id']}/complete")['itemId']
        client.call('POST',f'/service/v1/connections/{item}/refresh',dict(freshBalances=False))
        import os
        os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
        from PySide6.QtWidgets import QApplication,QTableWidgetItem
        from card_compass.app import MainWindow
        from card_compass.connected_ui import ConnectedServicesDialog
        from card_compass.institution_balances_ui import InstitutionBalanceReview
        from card_compass.backup import create_backup,read_backup
        qt=QApplication.instance() or QApplication([]);root=MainWindow(tmp_path/'desktop',auto_refresh=False);dialog=ConnectedServicesDialog(root);dialog.manager.client=client
        dialog.connections=[dict(id=item)];dialog.institutions.setRowCount(1);dialog.institutions.setItem(0,0,QTableWidgetItem(item));dialog.institutions.selectRow(0)
        def wait_native():
            deadline=time.monotonic()+10
            while dialog.manager.busy and time.monotonic()<deadline:qt.processEvents();time.sleep(.01)
            qt.processEvents();assert not dialog.manager.busy
        def reviewed(form):
            wait_native();assert form.snapshot['id']==item and form.preview.value['selected']['account']['amount_cents']==12345
            form.reviewed.setChecked(True);form.sandbox.setChecked(True);form.confirm();wait_native()
            assert form.saved_result['status']=='imported';form.reject();return form.DialogCode.Rejected
        monkeypatch.setattr(InstitutionBalanceReview,'exec',reviewed)
        dialog.import_balance();wait_native()
        row=root.planning_store.accounts()[0];assert row['amount_cents']==12345 and row['source_info']['connection_id']==item
        assert row['source_info']['environment']=='sandbox' and 'reviewed' in row['source_info']['date_basis']
        assert not root.investment_store.state['trades'] and not root.finance_store.transactions()
        root.save_profile();path=tmp_path/'reviewed-backup.zip';create_backup(root.directory,path);assert 'transactions.sqlite3' in read_backup(path)[1]
        assert b'secret-access-fixture' not in (root.directory/'transactions.sqlite3').read_bytes()
    finally:
        if dialog:dialog.close()
        if root:root.close()
        server.should_exit=True;thread.join(timeout=5);assert not thread.is_alive()
