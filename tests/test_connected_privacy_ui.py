from copy import deepcopy
import threading
import time
from PySide6.QtWidgets import QApplication,QMessageBox,QFileDialog
from card_compass.connected_privacy_ui import ServicePrivacyDialog,ProofDialog
from card_compass.connected_client import ConnectionError
from tests.test_connected_ui import window,session,wait
from tests.test_privacy_export import manifest


def proof(dialog):
    dialog.payload=dict(password='Synthetic test passphrase',code='123456')
    if dialog.changing_password:dialog.payload['newPassword']='New synthetic passphrase'
    return dialog.DialogCode.Accepted


def test_staged_security_requires_private_storage_and_close_clears_undo(window,monkeypatch):
    calls=[];codes=[f'{i:020x}' for i in range(10)]
    def transport(method,url,payload,headers):
        calls.append((method,url,payload))
        if url.endswith('/recovery/begin'):return dict(id='a'*32,kind='recovery',expires=time.time()+600,mfaSecret=None,recoveryCodes=codes,activationCode='b'*20)
        if url.endswith('/confirm'):return dict(changed=True,otherSessionsRevoked=True,currentSessionRetained=True)
        raise AssertionError(url)
    main=session(window,transport);dialog=ServicePrivacyDialog(main);monkeypatch.setattr(ProofDialog,'exec',proof)
    dialog.begin_security('recovery');wait(dialog.manager)
    assert dialog.pending and dialog.recovery.toPlainText().splitlines()==codes and not dialog.recovery.isVisible()
    assert dialog.seed.text()=='' and dialog.activation.text()=='b'*20
    dialog.confirm_code.setText('b'*20);dialog.confirm_security();assert len(calls)==1 and 'Store' in dialog.status.text()
    dialog.saved.setChecked(True);dialog.confirm_security();wait(dialog.manager)
    assert len(calls)==2 and calls[1][2]==dict(code='b'*20) and not dialog.pending and dialog.client.signed_in
    assert not dialog.recovery.toPlainText() and not dialog.confirm_code.text() and not dialog.activation.text()
    dialog.activation.setReadOnly(False);dialog.activation.undo();assert not dialog.activation.text()
    dialog.close();assert not dialog._subscribed and not dialog.context;main.close()


def test_deleted_scope_requires_review_exact_identity_and_preserves_local_accounts(window,monkeypatch):
    calls=[];before=deepcopy(window.planning_store.accounts())
    plan=dict(planHash='a'*64,username='alice',createdSnapshots=1,sessions=1,pendingExports=0,pendingSecurityChanges=0,institutionConnections=0,unresolvedConsents=[],blockers=[],households=[dict(id='home',name='My home',role='owner',members=1,closeIfSole=True)],limitations='Local copies and operator backups remain separate.')
    def transport(method,url,payload,headers):
        calls.append((method,url,payload))
        if url.endswith('/deletion-plan'):return deepcopy(plan)
        if url.endswith('/delete-identity'):return dict(identityDeleted=True,receiptId='b'*32)
        raise AssertionError(url)
    main=session(window,transport);dialog=ServicePrivacyDialog(main);monkeypatch.setattr(ProofDialog,'exec',proof)
    dialog.delete_identity();assert not calls
    dialog.deletion_plan();wait(dialog.manager)
    assert 'My home' in dialog.deletion_details.toPlainText() and 'Local copies' in dialog.deletion_details.toPlainText()
    dialog.confirmation.setText('alice');dialog.delete_identity();assert len(calls)==1 and 'Explicitly' in dialog.status.text()
    dialog.close_households.setChecked(True);dialog.delete_identity();wait(dialog.manager)
    assert calls[-1][2]['planHash']=='a'*64 and calls[-1][2]['closeSoleHouseholds'] is True
    assert not dialog.client.signed_in and 'Receipt' in dialog.status.text() and window.planning_store.accounts()==before
    assert not dialog.confirmation.text() and not dialog.deletion_details.toPlainText();dialog.close();main.close()


def test_export_download_failure_and_cleanup_failure_have_distinct_outcomes(window,tmp_path,monkeypatch):
    calls=[];prepared=manifest()
    def transport(method,url,payload,headers):
        calls.append((method,url))
        if url.endswith('/exports') and method=='GET':return dict(exports=[dict(prepared,currentSession=True)])
        if '/chunks/' in url:
            sequence=int(url.rsplit('/',1)[1]);return dict(id='a'*32,sequence=sequence,records=[dict(dataset='identity' if sequence==0 else 'snapshots',value=dict(name='Synthetic selected record'))])
        if method=='DELETE':raise ConnectionError(503,'Controlled cleanup unavailable')
        raise AssertionError(url)
    main=session(window,transport);dialog=ServicePrivacyDialog(main);dialog.refresh_exports();wait(dialog.manager)
    path=tmp_path/'outside-profile.zip'
    # The window fixture uses tmp_path as its profile, so use a separate sibling.
    path=tmp_path.parent/(tmp_path.name+'-reviewed.zip')
    monkeypatch.setattr(QFileDialog,'getSaveFileName',lambda *args:(str(path),'ZIP'))
    try:
        dialog.download();wait(dialog.manager)
        assert path.exists() and 'archive saved' in dialog.status.text() and 'cleanup was not confirmed' in dialog.status.text()
        assert next(row['amount_cents'] for row in window.planning_store.accounts() if row['name']=='Selected savings')==10000
    finally:path.unlink(missing_ok=True);dialog.close();main.close()


def test_revoke_current_session_expiry_and_inflight_close(window,monkeypatch):
    entered=threading.Event();release=threading.Event()
    def transport(method,url,payload,headers):
        if method=='GET':entered.set();release.wait(3);return dict(sessions=[dict(id='a'*32,label='Synthetic desktop',created='',expires=time.time()+600,current=True)])
        return dict(revoked=True,current=True)
    main=session(window,transport);dialog=ServicePrivacyDialog(main)
    try:
        dialog.refresh_sessions();assert entered.wait(1);dialog.reject();assert dialog._subscribed and 'Wait' in dialog.status.text()
        release.set();wait(dialog.manager);dialog.session_table.selectRow(0)
        monkeypatch.setattr(QMessageBox,'question',lambda *args:QMessageBox.StandardButton.Yes)
        dialog.revoke_session();wait(dialog.manager)
        assert not dialog.client.signed_in and dialog.session_table.rowCount()==0 and not main.snapshots
        dialog.close();assert not dialog._subscribed
    finally:release.set();main.close()


def test_compact_privacy_controls_are_reachable_and_proof_clears_input(window):
    main=session(window,lambda *args:dict(ok=True));dialog=ServicePrivacyDialog(main);dialog.resize(970,700);dialog.show()
    dialog.tabs.setCurrentIndex(1)
    for _ in range(3):QApplication.instance().processEvents()
    assert dialog.buttons.geometry().bottom()<=dialog.height()
    if hasattr(dialog,'text_scroll'):assert dialog.text_scroll.horizontalScrollBar().maximum()==0
    proof_dialog=ProofDialog(dialog,'Synthetic proof',True);proof_dialog.password.setText('Private passphrase');proof_dialog.code.setText('123456');proof_dialog.new_password.setText('New private passphrase');proof_dialog.confirm_password.setText('New private passphrase');proof_dialog.confirm()
    assert proof_dialog.payload and not proof_dialog.password.text() and not proof_dialog.code.text()
    proof_dialog.password.undo();assert not proof_dialog.password.text();proof_dialog.payload=None
    dialog.close();main.close()


def test_locally_expired_service_session_clears_private_fields_before_request(window):
    calls=[];main=session(window,lambda *args:calls.append(args) or dict(ok=True));dialog=ServicePrivacyDialog(main)
    dialog.seed.setText('A'*32);dialog.recovery.setPlainText('Private recovery material');dialog.deletion_details.setPlainText('Private deletion review')
    dialog.client.expires=time.time()-1;dialog.refresh_sessions()
    assert not calls and not dialog.client.token and not dialog.seed.text() and not dialog.recovery.toPlainText()
    assert not dialog.deletion_details.toPlainText() and 'session changed' in dialog.status.text()
    dialog.close();main.close()
