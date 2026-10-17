import os
os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
import time
import threading
from copy import deepcopy
from datetime import date
import pytest
from PySide6.QtCore import QEvent
from PySide6.QtWidgets import QApplication,QMessageBox,QTextEdit
from card_compass.app import MainWindow
from card_compass.connected_client import ConnectedClient,ConnectionError
from card_compass.connected_ui import ConnectedServicesDialog,ShareReviewDialog,SharedValueEditor,EnrollmentDialog
from card_compass.shared_sync_ui import SharedCopyReview
from tests.test_shared_sync import account_payload,investment_payload,record
def snapshot():
    return dict(version=1,name='Reviewed account',currency='USD',accountType='savings',owner='Reviewed owner',asOf='2026-10-10',amountCents=50000,aprBps=0,minimumPaymentCents=0,source='Synthetic manual fixture')


@pytest.fixture
def window(tmp_path):
    app=QApplication.instance() or QApplication([])
    window=MainWindow(tmp_path,auto_refresh=False)
    window.planning_store.save_account('Selected savings','savings','USD','Me',100,date.today().isoformat())
    window.planning_store.save_account('Other private account','checking','USD','Me',200,date.today().isoformat())
    yield window
    if hasattr(window,'connected_manager'):wait(window.connected_manager)
    window.close();app.sendPostedEvents(None,QEvent.Type.DeferredDelete);app.processEvents()


def wait(manager):
    deadline=time.monotonic()+5
    while manager.busy and time.monotonic()<deadline:QApplication.instance().processEvents();time.sleep(.01)
    QApplication.instance().processEvents();assert not manager.busy


def session(window,transport):
    dialog=ConnectedServicesDialog(window)
    client=ConnectedClient('https://service.example',transport=transport)
    client.accept_session(dict(token='Synthetic-session-secret-token',expires=time.time()+43200,user=dict(id='alice',username='alice')))
    dialog.manager.client=client
    return dialog


def test_publication_is_exact_reviewed_selection_and_private_scope(window,monkeypatch):
    calls=[]
    def transport(method,url,payload,headers):calls.append((method,url,payload));return dict(id='fixture',version=1)
    dialog=session(window,transport)
    before=deepcopy(window.planning_store.accounts())
    dialog.local_record.setCurrentIndex(dialog.local_record.findData('manual:1'))
    monkeypatch.setattr(ShareReviewDialog,'exec',lambda editor:editor.DialogCode.Rejected)
    dialog.publish();assert not calls
    def review(editor):
        assert 'Other private account' not in '\n'.join(field.toPlainText() for field in editor.findChildren(QTextEdit))
        editor.reviewed.setChecked(True);return editor.DialogCode.Accepted
    monkeypatch.setattr(ShareReviewDialog,'exec',review)
    dialog.publish();wait(dialog.manager)
    payload=calls[0][2]
    assert payload['householdId'] is None and payload['payload']['name']=='Selected savings'
    assert payload['payload']['amountCents']==10000 and 'Other private account' not in str(payload)
    assert window.planning_store.accounts()==before
    dialog.close()


def test_failed_async_service_request_preserves_local_records_and_scope_change_discards_stale_response(window):
    def transport(*args):raise ConnectionError(503,'Controlled service failure')
    dialog=session(window,transport);before=deepcopy(window.planning_store.accounts())
    dialog.refresh();wait(dialog.manager)
    assert 'Controlled service failure' in dialog.status.text() and before==window.planning_store.accounts()
    dialog.household.blockSignals(True);dialog.household.addItem('New scope','new');dialog.household.setCurrentIndex(1);dialog.household.blockSignals(False)
    dialog.result('scope',dict(scope='old',snapshots=[],members=[]),'')
    assert 'Scope changed' in dialog.status.text() and dialog.shared.rowCount()==0
    dialog.close()


def test_compact_service_tables_demo_gate_and_close_clear_password(window):
    window.is_demo=True
    dialog=ConnectedServicesDialog(window);dialog.resize(1080,700);dialog.show()
    for _ in range(3):QApplication.instance().processEvents()
    dialog.tabs.setCurrentIndex(3)
    for _ in range(3):QApplication.instance().processEvents()
    assert dialog.institutions.viewport().height()>120 and hasattr(dialog,'demo_banner')
    if hasattr(dialog,'text_scroll'):assert dialog.text_scroll.horizontalScrollBar().maximum()==0
    dialog.url.setText('https://service.example');dialog.password.setText('Private input');dialog.code.setText('123456');dialog.sign_in()
    assert not dialog.manager.busy and 'disabled' in dialog.status.text()
    dialog.password.setText('Must clear');dialog.close();assert not dialog.password.text() and not dialog._subscribed


def test_shared_edit_preserves_scope_fields_and_optimistic_version(window,monkeypatch):
    record=dict(id='fixture',kind='manual_account',creator='alice',householdId='household',version=7,updated='2026-10-10',payload=snapshot())
    editor=SharedValueEditor(window,record)
    editor.amount.setText('1.001');editor.reviewed.setChecked(True);editor.confirm();assert editor.payload is None
    editor.amount.setText('600');editor.confirm()
    assert editor.payload['amountCents']==60000 and editor.payload['currency']=='USD' and record['payload']['amountCents']==50000
    editor.close();calls=[]
    dialog=session(window,lambda method,url,payload,headers:calls.append(payload) or dict(ok=True))
    dialog.snapshots=[record];dialog.shared.setRowCount(1);dialog.shared.selectRow(0)
    def accept(form):form.reviewed.setChecked(True);form.amount.setText('600');form.confirm();return form.DialogCode.Accepted
    monkeypatch.setattr(SharedValueEditor,'exec',accept)
    dialog.edit_shared();wait(dialog.manager)
    assert calls[0]['expectedVersion']==7 and calls[0]['householdId']=='household' and calls[0]['payload']['amountCents']==60000
    dialog.close()


def test_expired_session_clears_old_identity_data_and_abandoned_consent_can_resume(window):
    dialog=session(window,lambda *args:dict(ok=True))
    dialog.household.blockSignals(True);dialog.household.addItem('Private old household','old');dialog.household.blockSignals(False)
    dialog.snapshots=[dict(payload=dict(name='Old private source'))]
    dialog.shared_details.setPlainText('Old private source');dialog.client.forget()
    dialog.result('scope',None,'Session expired or revoked.')
    assert dialog.household.count()==1 and not dialog.snapshots and not dialog.shared_details.toPlainText()
    client=dialog.client;client.accept_session(dict(token='Synthetic-session-secret-token',expires=time.time()+43200,user=dict(id='alice',username='alice')))
    pending=dict(id='pending',products=['transactions'],url='https://secure.plaid.com/hl/fixture',expires=time.time()+600)
    dialog.result('refresh',dict(households=[],connections=[],pending=[pending],capabilities=dict(provider='sandbox')),'')
    dialog.pending_sessions.setCurrentIndex(1)
    assert dialog.pending_link=='pending' and dialog.pending_sessions.currentData(257)['url']==pending['url']
    dialog.close()


def test_invited_enrollment_is_reviewed_confirmed_and_secrets_cleared(window,tmp_path):
    dialog=ConnectedServicesDialog(window);calls=[]
    recovery=[f'{i:020x}' for i in range(10)]
    def transport(method,url,payload,headers):
        calls.append((url,payload,headers))
        if url.endswith('/auth/enroll'):return dict(challenge='Synthetic-enrollment-challenge',mfaSecret='A'*32,expires=time.time()+600,username='bob')
        if url.endswith('/auth/enroll/confirm'):return dict(token='Synthetic-enrolled-session-token',expires=time.time()+43200,user=dict(id='bob',username='bob'),recoveryCodes=recovery)
        raise AssertionError('Unexpected endpoint')
    client=ConnectedClient('https://service.example',transport=transport)
    setup=EnrollmentDialog(dialog,dialog.manager,client,'bob');setup.show()
    setup.invitation.setText('Synthetic-private-invitation');setup.password.setText('Long testing passphrase');setup.confirm_password.setText('Long testing passphrase')
    setup.begin();assert not calls and 'Review' in setup.status.text()
    setup.reviewed.setChecked(True);setup.begin();wait(dialog.manager)
    assert setup.challenge and setup.seed.text()=='A'*32 and not client.signed_in
    assert 'Authorization' not in calls[0][2] and not setup.password.text() and not setup.invitation.text()
    setup.reveal_seed.setChecked(True)
    setup.code.setText('123456');setup.confirm();wait(dialog.manager)
    assert client.signed_in and setup.completed and not setup.seed.text() and not setup.code.text()
    setup.seed.setReadOnly(False);setup.reveal_seed.setChecked(True);setup.seed.undo();assert not setup.seed.text()
    assert setup.recovery.toPlainText().splitlines()==recovery and not setup.recovery.isVisible()
    setup.reject();assert setup._subscribed and 'Store' in setup.status.text()
    setup.saved_recovery.setChecked(True);setup.reject()
    assert not setup._subscribed and not setup.recovery.toPlainText() and not setup.challenge
    for file in tmp_path.rglob('*'):
        if file.is_file():assert b'Synthetic-private-invitation' not in file.read_bytes() and b'Synthetic-enrolled-session-token' not in file.read_bytes()
    dialog.close()


def test_cancel_pending_enrollment_preserves_invitation_and_failed_confirmation_can_retry(window):
    dialog=ConnectedServicesDialog(window);calls=[]
    def transport(method,url,payload,headers):
        calls.append(url)
        if url.endswith('/auth/enroll'):return dict(challenge='Synthetic-enrollment-challenge',mfaSecret='A'*32,expires=time.time()+600,username='bob')
        if url.endswith('/auth/enroll/confirm'):raise ConnectionError(401,'Confirm the current authenticator code.')
        if url.endswith('/auth/enroll/cancel'):return dict(canceled=True)
        raise AssertionError('Unexpected endpoint')
    setup=EnrollmentDialog(dialog,dialog.manager,ConnectedClient('https://service.example',transport=transport),'bob')
    setup.invitation.setText('Synthetic-private-invitation');setup.password.setText('Long testing passphrase');setup.confirm_password.setText('Long testing passphrase');setup.reviewed.setChecked(True)
    setup.begin();wait(dialog.manager);setup.code.setText('123456');setup.confirm();wait(dialog.manager)
    assert setup.challenge and setup.seed.text() and 'current authenticator' in setup.status.text()
    setup.reject();wait(dialog.manager)
    assert calls[-1].endswith('/auth/enroll/cancel') and not setup.challenge and not setup.seed.text() and not setup._subscribed
    dialog.close()


def test_uncertain_and_retained_exchange_states_are_visible_and_not_reopened(window,monkeypatch):
    dialog=session(window,lambda *args:dict(ok=True));opened=[]
    monkeypatch.setattr('card_compass.connected_ui.QDesktopServices.openUrl',lambda url:opened.append(url))
    pending=dict(id='uncertain',status='uncertain',environment='sandbox',products=['transactions'],url=None,expires=time.time()-10)
    dialog.result('refresh',dict(households=[],connections=[],pending=[pending],capabilities=dict(provider='sandbox')),'')
    dialog.pending_sessions.setCurrentIndex(1)
    assert 'uncertain' in dialog.pending_sessions.currentText()
    dialog.reopen_link();assert not opened and 'unexpired pending' in dialog.status.text()
    pending.update(status='exchanged',expires=time.time()+600)
    dialog.result('refresh',dict(households=[],connections=[],pending=[pending],capabilities=dict(provider='sandbox')),'');dialog.pending_sessions.setCurrentIndex(1)
    dialog.reopen_link();assert not opened and 'without reopening' in dialog.status.text()
    dialog.close()


def test_enrollment_cannot_close_inflight_and_failed_cancel_has_explicit_local_recovery(window,monkeypatch):
    dialog=ConnectedServicesDialog(window);entered=threading.Event();release=threading.Event()
    def transport(method,url,payload,headers):
        if url.endswith('/auth/enroll'):
            entered.set();release.wait(3)
            return dict(challenge='Synthetic-enrollment-challenge',mfaSecret='A'*32,expires=time.time()+600,username='bob')
        raise ConnectionError(503,'Controlled service unavailable')
    setup=EnrollmentDialog(dialog,dialog.manager,ConnectedClient('https://service.example',transport=transport),'bob')
    setup.invitation.setText('Synthetic-private-invitation');setup.password.setText('Long testing passphrase');setup.confirm_password.setText('Long testing passphrase');setup.reviewed.setChecked(True)
    try:
        setup.begin();assert entered.wait(1)
        setup.reject();assert setup._subscribed and 'Wait' in setup.status.text()
        release.set();wait(dialog.manager);setup.reject();wait(dialog.manager)
        assert setup._subscribed and setup.seed.text() and 'unavailable' in setup.status.text()
        monkeypatch.setattr(QMessageBox,'question',lambda *args:QMessageBox.StandardButton.Yes)
        setup.forget_local();assert not setup._subscribed and not setup.seed.text() and not setup.challenge
    finally:release.set();dialog.close()


def test_compact_enrollment_footer_is_reachable_without_horizontal_overflow(window):
    window.is_demo=True;dialog=ConnectedServicesDialog(window)
    setup=EnrollmentDialog(dialog,dialog.manager,ConnectedClient('https://service.example'),'synthetic-invited-user')
    setup.resize(790,700);setup.show()
    for _ in range(3):QApplication.instance().processEvents()
    assert hasattr(setup,'demo_banner') and setup.buttons.isVisible()
    assert setup.buttons.geometry().bottom()<=setup.height()
    if hasattr(setup,'text_scroll'):assert setup.text_scroll.horizontalScrollBar().maximum()==0
    setup.close();dialog.close()


def copy_selection(dialog,remote):
    dialog.result('scope',dict(scope=None,snapshots=[remote],members=[]),'');dialog.shared.selectRow(0)


def accept_copy(review):
    review.reviewed.setChecked(True);review.replace.setChecked(True);review.duplicates.setChecked(True);review.confirm()
    return review.DialogCode.Accepted


def test_reviewed_copy_uses_read_only_service_calls_and_records_local_provenance(window,monkeypatch):
    remote=record(account_payload());calls=[]
    def transport(method,url,payload,headers):calls.append((method,url));return deepcopy(remote)
    dialog=session(window,transport);copy_selection(dialog,remote)
    assert 'My created records' in dialog.household.currentText() and dialog.shared.item(0,4).text()=='Household members'
    before=deepcopy(window.planning_store.accounts())
    monkeypatch.setattr(SharedCopyReview,'exec',lambda review:review.DialogCode.Rejected)
    dialog.download_snapshot();wait(dialog.manager)
    assert window.planning_store.accounts()==before and len(calls)==1
    monkeypatch.setattr(SharedCopyReview,'exec',accept_copy)
    dialog.download_snapshot();wait(dialog.manager)
    row=next(row for row in window.planning_store.accounts() if row['name']==remote['payload']['name'])
    assert row['source_info']['remote_version']==1 and row['amount_cents']==12345 and row['as_of']=='2026-01-01'
    imported_id=row['id']
    assert all(method=='GET' for method,url in calls) and len(calls)==3
    assert 'local copy saved' in dialog.status.text()
    dialog.download_snapshot();wait(dialog.manager)
    assert len(window.planning_store.accounts())==len(before)+1 and 'Already copied' in dialog.status.text()
    window.refresh_planning()
    index=next(i for i,row in enumerate(window.accounts_page.rows) if row['id']==imported_id)
    assert window.accounts_page.table.item(index,5).text().startswith('Service v1')
    dialog.close()


def test_changed_service_version_or_permission_stops_copy_after_review(window,monkeypatch):
    remote=record(account_payload());calls=[]
    def transport(method,url,payload,headers):
        calls.append(url)
        return deepcopy(remote) if len(calls)==1 else record(dict(account_payload(),amountCents=999),version=2)
    dialog=session(window,transport);copy_selection(dialog,remote);before=deepcopy(window.planning_store.accounts())
    monkeypatch.setattr(SharedCopyReview,'exec',accept_copy);dialog.download_snapshot();wait(dialog.manager)
    assert 'changed after review' in dialog.status.text() and window.planning_store.accounts()==before
    calls.clear()
    def forbidden(method,url,payload,headers):
        calls.append(url)
        if len(calls)==1:return deepcopy(remote)
        raise ConnectionError(404,'Snapshot is no longer accessible')
    dialog.client.transport=forbidden;dialog.download_snapshot();wait(dialog.manager)
    assert 'no longer accessible' in dialog.status.text() and not dialog._copy_review and window.planning_store.accounts()==before
    dialog.close()


def test_reviewed_investment_copy_keeps_private_notes_and_failed_save_preserves_state(window,monkeypatch):
    remote=record(investment_payload(),kind='investment_account');dialog=session(window,lambda *args:deepcopy(remote));copy_selection(dialog,remote)
    window.investment_store.state['notes']['PRIVATE']='Keep local research';window.stocks_page.layout_store.schedule_save()
    monkeypatch.setattr(SharedCopyReview,'exec',accept_copy);dialog.download_snapshot();wait(dialog.manager)
    ident=window.investment_store.state['serviceCopies'][0]['local_id']
    assert window.investment_store.state['notes']['PRIVATE']=='Keep local research' and window.investment_store.state['trades']['TEST'][0]['accountId']==ident
    from card_compass.broker_import_ui import InvestmentAccountsDialog
    account_view=InvestmentAccountsDialog(window.stocks_page);account_view.accounts.setCurrentIndex(account_view.accounts.findData(ident))
    assert 'service snapshot v1' in account_view.source.text() and 'matches this copy' in account_view.source.text();account_view.close()
    remote=record(investment_payload(),kind='investment_account',version=2)
    copy_selection(dialog,remote);before=deepcopy(window.investment_store.state)
    monkeypatch.setattr(window.investment_store,'save',lambda *args:(_ for _ in ()).throw(OSError('Controlled disk failure')))
    dialog.download_snapshot();wait(dialog.manager)
    assert 'Controlled disk failure' in dialog.status.text() and window.investment_store.state==before
    dialog.close()


def test_local_edit_during_verification_blocks_existing_record_replacement(window,monkeypatch):
    remote=record(account_payload());dialog=session(window,lambda *args:deepcopy(remote));copy_selection(dialog,remote)
    monkeypatch.setattr(SharedCopyReview,'exec',accept_copy);dialog.download_snapshot();wait(dialog.manager)
    ident=next(row['id'] for row in window.planning_store.accounts() if row['name']==remote['payload']['name'])
    remote=record(dict(account_payload(),amountCents=15000),version=2);copy_selection(dialog,remote);calls=[]
    def transport(method,url,payload,headers):
        calls.append(url)
        if len(calls)==2:window.planning_store.record_balance(ident,700,'2026-01-01')
        return deepcopy(remote)
    dialog.client.transport=transport;dialog.download_snapshot();wait(dialog.manager)
    assert 'changed since preview' in dialog.status.text()
    assert next(row['amount_cents'] for row in window.planning_store.accounts() if row['id']==ident)==70000
    dialog.close()


def test_compact_copy_review_exposes_exact_data_and_requires_existing_destination_review(window):
    window.is_demo=True;remote=record(account_payload());review=SharedCopyReview(window,'https://service.example','alice','alice',remote)
    review.resize(960,700);review.show()
    for _ in range(3):QApplication.instance().processEvents()
    assert hasattr(review,'demo_banner') and review.changes.viewport().height()>100
    if hasattr(review,'text_scroll'):assert review.text_scroll.horizontalScrollBar().maximum()==0
    review.destination.setCurrentIndex(review.destination.findData(1));review.reviewed.setChecked(True);review.confirm()
    assert review.confirmed is None and 'Confirm' in review.status.text()
    review.replace.setChecked(True);review.confirm();assert review.confirmed and review.confirmed[0].value['target']==1
    review.close()
