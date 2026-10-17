import os
os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
from copy import deepcopy
from datetime import date
from decimal import Decimal,ROUND_HALF_UP
import csv
import pytest
from PySide6.QtCore import QEvent,QDate,Qt
from PySide6.QtWidgets import QApplication,QFileDialog,QMessageBox
from card_compass.app import MainWindow
from card_compass.performance import save_record
from card_compass.risk_analysis import month_end
from card_compass.risk_analysis_ui import RiskAnalysisDialog,FactorImportDialog
from card_compass.finance import shift_month


def seed(state):
    start=shift_month(date.today().strftime('%Y-%m'),-13)
    balance=Decimal(1000)
    state=save_record(state,'legacy',month_end(start),balance,'Synthetic monthly fixture')
    for i in range(1,13):
        balance=(balance*(Decimal('1.01') if i%2 else Decimal('.995'))).quantize(Decimal('.01'),rounding=ROUND_HALF_UP)
        state=save_record(state,'legacy',month_end(shift_month(start,i)),balance,'Synthetic monthly fixture')
    return state


@pytest.fixture
def window(tmp_path):
    app=QApplication.instance() or QApplication([])
    window=MainWindow(tmp_path,auto_refresh=False)
    window.investment_store.save(seed(window.investment_store.state));window.stocks_page.reload()
    window.show();app.processEvents()
    yield window
    window.close();app.sendPostedEvents(None,QEvent.Type.DeferredDelete);app.processEvents()


def test_monthly_risk_requires_review_and_clears_old_results_on_scope_change(window,tmp_path,monkeypatch):
    dialog=RiskAnalysisDialog(window.stocks_page)
    dialog.calculate();assert dialog.result is None and 'confirm' in dialog.error.text()
    dialog.complete.setChecked(True);dialog.calculate()
    assert dialog.result and dialog.table.rowCount()==12
    assert dialog.result['sharpe'] is None and 'Not supplied' in dialog.summary.toPlainText()
    dialog.use_rf.setChecked(True);dialog.rf.setValue(4);dialog.calculate()
    assert dialog.result['sharpe'] is not None and 'user-entered' in dialog.details.toPlainText()
    destination=tmp_path/'risk.csv';monkeypatch.setattr(QFileDialog,'getSaveFileName',lambda *args:(str(destination),''))
    dialog.export()
    rows=list(csv.DictReader(destination.read_text(encoding='utf-8-sig').splitlines()))
    assert len(rows)==12 and rows[0]['annual_rf_assumption_percent']=='4.0'
    dialog.start.setDate(dialog.start.date().addMonths(1))
    assert not dialog.complete.isChecked() and dialog.result is None
    dialog.close()


def test_factor_preview_cancel_invalidates_and_saved_dataset_survives_restart(window,tmp_path,monkeypatch):
    path=tmp_path/'factors.csv'
    path.write_text('period,market_excess_percent,rf_percent,smb_percent,hml_percent\n2025-01,2,.1,1,-1\n')
    dialog=FactorImportDialog(window.stocks_page,path)
    dialog.source.setText('Synthetic fixture');dialog.preview()
    assert dialog.dataset and dialog.table.item(0,1).text()=='2.00'
    dialog.source.setText('Changed release');assert dialog.dataset is None and dialog.table.rowCount()==0
    path.write_text('invalid\n');dialog.preview();assert dialog.dataset is None
    dialog.close()
    path.write_text('period,market_excess_percent,rf_percent,smb_percent,hml_percent\n2025-01,2,.1,1,-1\n')
    risk=RiskAnalysisDialog(window.stocks_page)
    monkeypatch.setattr(QFileDialog,'getOpenFileName',lambda *args:(str(path),''))
    def accept(editor):editor.source.setText('Synthetic fixture');editor.preview();return editor.DialogCode.Accepted
    monkeypatch.setattr(FactorImportDialog,'exec',accept)
    risk.import_factors()
    assert len(window.investment_store.state['riskFactorSets'])==1
    from card_compass.investments import InvestmentStore
    assert len(InvestmentStore(window.directory).state['riskFactorSets'])==1
    monkeypatch.setattr(QMessageBox,'question',lambda *args:QMessageBox.StandardButton.No)
    risk.remove_factors();assert len(window.investment_store.state['riskFactorSets'])==1
    monkeypatch.setattr(QMessageBox,'question',lambda *args:QMessageBox.StandardButton.Yes)
    risk.remove_factors();assert not window.investment_store.state['riskFactorSets']
    risk.close()


def test_compact_risk_results_leave_table_and_close_controls_visible(window):
    window.is_demo=True
    dialog=RiskAnalysisDialog(window.stocks_page);dialog.resize(980,700);dialog.complete.setChecked(True);dialog.calculate();dialog.show()
    for _ in range(3):QApplication.instance().processEvents()
    assert dialog.table.viewport().height()>=120 and hasattr(dialog,'demo_banner')
    if hasattr(dialog,'text_scroll'):assert dialog.text_scroll.horizontalScrollBar().maximum()==0
    dialog.close()


def test_correlations_require_each_selected_account_history(window):
    state=deepcopy(window.investment_store.state)
    state['investmentAccounts'].append(dict(id='second',name='Second recorded account',kind='brokerage',currency='USD',owner=''))
    records=deepcopy(state['performanceRecords']['valuations'])
    for record in records:
        state=save_record(state,'second',record['date'],record['amount'],'Synthetic second account')
    window.investment_store.save(state)
    dialog=RiskAnalysisDialog(window.stocks_page)
    for i in range(2):dialog.correlation_accounts.item(i).setCheckState(Qt.CheckState.Checked)
    dialog.complete.setChecked(True);dialog.compare_accounts()
    assert dialog.matrix.rowCount()==2 and dialog.matrix.item(0,2).text()=='1.000'
    state=deepcopy(window.investment_store.state)
    state['performanceRecords']['valuations']=[r for r in state['performanceRecords']['valuations'] if not (r['accountId']=='second' and r['date']==records[-1]['date'])]
    window.investment_store.save(state);dialog.compare_accounts()
    assert dialog.matrix.rowCount()==0 and 'Could not compare' in dialog.error.text()
    dialog.close()


def test_factor_save_failure_preserves_workspace_and_preview(window,tmp_path,monkeypatch):
    path=tmp_path/'factors.csv'
    path.write_text('period,market_excess_percent,rf_percent,smb_percent,hml_percent\n2025-01,2,.1,1,-1\n')
    risk=RiskAnalysisDialog(window.stocks_page)
    monkeypatch.setattr(QFileDialog,'getOpenFileName',lambda *args:(str(path),''))
    def accept(editor):editor.source.setText('Synthetic fixture');editor.preview();return editor.DialogCode.Accepted
    monkeypatch.setattr(FactorImportDialog,'exec',accept)
    original=deepcopy(window.investment_store.state)
    def fail(candidate):raise OSError('Controlled disk failure')
    monkeypatch.setattr(window.investment_store,'save',fail)
    risk.import_factors()
    assert window.investment_store.state==original and risk.factors.count()==1
    assert 'Controlled disk failure' in risk.error.text()
    risk.close()
