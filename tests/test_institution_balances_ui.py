from copy import deepcopy
import threading
from datetime import date
from PySide6.QtWidgets import QApplication,QMessageBox
from PySide6.QtCore import QSize
from card_compass.institution_balances_ui import InstitutionBalanceReview
from card_compass.connected_client import ConnectionError
from tests.test_connected_ui import window,session,wait
from tests.test_institution_balances import snapshot


def review(dialog):
    dialog.reviewed.setChecked(True);dialog.replace.setChecked(True);dialog.duplicates.setChecked(True);dialog.sandbox.setChecked(True)


def test_selected_balance_recheck_save_source_export_and_no_other_account_import(window):
    source=snapshot();source['accounts'].append(dict(source['accounts'][0],account_id='other-private',name='Other provider account'));calls=[]
    main=session(window,lambda method,url,payload,headers:calls.append((method,url)) or deepcopy(source));dialog=InstitutionBalanceReview(main,source)
    before=deepcopy(window.planning_store.accounts());dialog.confirm();assert not calls and 'Review' in dialog.status.text()
    review(dialog);dialog.confirm();wait(dialog.manager)
    row=next(row for row in window.planning_store.accounts() if row['name']=='Provider checking')
    assert row['amount_cents']==12345 and row['source_info']['date_basis']=='user_reviewed_unknown_provider_date' and len(window.planning_store.accounts())==len(before)+1
    assert calls==[('GET','https://service.example/service/v1/connections/connection-test/balances')]
    index=next(i for i,row in enumerate(window.accounts_page.rows) if row['name']=='Provider checking')
    assert window.accounts_page.table.item(index,5).text()=='Institution PRODUCTION · date unknown'
    assert 'Provider timestamp: unavailable' in window.accounts_page.table.item(index,5).toolTip()
    from card_compass.reports import rows_for
    headers,rows=rows_for(window,'balances',date.today().isoformat(),date.today().isoformat())
    assert headers[-1]=='source' and any('Institution balance' in row[-1] for row in rows)
    assert not any(row['name']=='Other provider account' for row in window.planning_store.accounts())
    review(dialog);dialog.confirm();wait(dialog.manager);assert 'Already imported' in dialog.status.text()
    dialog.close();assert not dialog._subscribed and not dialog.context and not dialog.incoming.toPlainText();main.close()


def test_permission_source_and_local_changes_block_save_after_review(window):
    source=snapshot();calls=[];changed=deepcopy(source);changed['accounts'][0]['balances']['current']=777
    main=session(window,lambda *args:deepcopy(changed));dialog=InstitutionBalanceReview(main,source);before=deepcopy(window.planning_store.accounts())
    review(dialog);dialog.confirm();wait(dialog.manager)
    assert 'after review' in dialog.status.text() and window.planning_store.accounts()==before
    dialog.client.transport=lambda *args:(_ for _ in ()).throw(ConnectionError(404,'Connection access withdrawn'))
    review(dialog);dialog.confirm();wait(dialog.manager);assert 'withdrawn' in dialog.status.text() and window.planning_store.accounts()==before
    target=next(row['id'] for row in before if row['name']=='Selected savings');dialog.destination.setCurrentIndex(dialog.destination.findData(target))
    def transport(*args):window.planning_store.record_balance(target,700,date.today().isoformat());return source
    dialog.client.transport=transport;review(dialog);dialog.confirm();wait(dialog.manager)
    assert 'changed since preview' in dialog.status.text() and next(row['amount_cents'] for row in window.planning_store.accounts() if row['id']==target)==70000
    dialog.close();main.close()


def test_sandbox_ack_inflight_close_and_detach_preserve_data(window,monkeypatch):
    source=snapshot(environment='sandbox');entered=threading.Event();release=threading.Event()
    def transport(*args):entered.set();release.wait(3);return source
    main=session(window,transport);dialog=InstitutionBalanceReview(main,source)
    try:
        dialog.reviewed.setChecked(True);dialog.confirm();assert not entered.is_set() and 'SANDBOX' in dialog.status.text()
        dialog.sandbox.setChecked(True);dialog.confirm();assert entered.wait(1);dialog.reject();assert dialog._subscribed and 'Wait' in dialog.status.text()
        release.set();wait(dialog.manager);before=deepcopy(window.planning_store.accounts())
        monkeypatch.setattr(QMessageBox,'question',lambda *args:QMessageBox.StandardButton.Yes);dialog.detach()
        assert window.planning_store.accounts()==before and 'detached' in dialog.status.text()
        dialog.close();assert not dialog._subscribed
    finally:release.set();main.close()


def test_compact_review_and_missing_current_balance_do_not_invent_value(window):
    source=snapshot();main=session(window,lambda *args:source);dialog=InstitutionBalanceReview(main,source);dialog.available_dialog_size=lambda:QSize(768,700);dialog.resize(768,700);dialog.show();dialog.tabs.setCurrentIndex(1)
    for _ in range(3):QApplication.instance().processEvents()
    assert dialog.buttons.geometry().bottom()<=dialog.height() and dialog.changes.viewport().height()>80
    assert dialog.changes.item(3,2).text()=='User-reviewed; provider date unknown'
    if hasattr(dialog,'text_scroll'):assert dialog.text_scroll.horizontalScrollBar().maximum()==0
    dialog.close();source['accounts'][0]['balances']['current']=None;invalid=InstitutionBalanceReview(main,source)
    assert invalid.preview is None and 'Current balance is missing' in invalid.status.text() and not invalid.incoming.toPlainText()
    invalid.close();main.close()


def test_new_debt_requires_local_terms_and_saved_mapping_is_visible(window):
    source=snapshot(500);source['accounts'][0].update(type='credit',subtype='credit card')
    main=session(window,lambda *args:source);dialog=InstitutionBalanceReview(main,source)
    assert dialog.preview is None and 'reviewed APR' in dialog.status.text()
    dialog.apr.setText('19.99');dialog.minimum.setText('25');assert dialog.preview is not None
    review(dialog);dialog.confirm();wait(dialog.manager)
    row=next(row for row in window.planning_store.accounts() if row['name']=='Provider checking')
    assert row['kind']=='credit_card' and row['apr_bps']==1999 and row['minimum_payment_cents']==2500
    assert dialog.destination.currentData()==row['id'] and not dialog.destination.isEnabled()
    dialog.close();assert not dialog.apr.text() and not dialog.minimum.text() and not dialog.local_name.text()
    main.close()
