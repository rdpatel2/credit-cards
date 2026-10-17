import os
os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
from copy import deepcopy
import pytest
from PySide6.QtWidgets import QApplication,QMessageBox
from PySide6.QtCore import QEvent,QDate
from card_compass.app import MainWindow
from card_compass.performance import save_record
from card_compass.performance_ui import PerformanceDialog,PerformanceRecordDialog
from card_compass.reports import rows_for


@pytest.fixture
def window(tmp_path):
    app=QApplication.instance() or QApplication([])
    window=MainWindow(tmp_path,auto_refresh=False)
    state=save_record(window.investment_store.state,'legacy','2025-01-01',100,'Broker statement')
    state=save_record(state,'legacy','2026-01-01',120,'Broker statement')
    window.investment_store.save(state)
    yield window
    window.close();window.deleteLater();app.sendPostedEvents(None,QEvent.Type.DeferredDelete);app.processEvents()


def test_returns_need_coverage_confirmation_and_period_changes_clear_results(window):
    dialog=PerformanceDialog(window.stocks_page)
    dialog.use_range();dialog.benchmark.clear()
    dialog.calculate_returns()
    assert 'Confirm recorded flow' in dialog.results.toPlainText()
    dialog.confirmation.setChecked(True);dialog.calculate_returns()
    assert 'TWR (cumulative, end-day flows): +20.00%' in dialog.results.toPlainText()
    assert 'MWR (annualized XIRR, actual days / 365): +20.00%' in dialog.results.toPlainText()
    assert dialog.intervals.rowCount()==1
    dialog.start.setDate(QDate(2025,1,2))
    assert not dialog.confirmation.isChecked() and not dialog.results.toPlainText()
    dialog.reject()


def test_record_ui_delete_export_and_workspace_roundtrip(window,monkeypatch):
    dialog=PerformanceDialog(window.stocks_page)
    def accept(editor):
        editor.day.setDate(QDate(2025,6,1));editor.amount.setValue(50)
        return editor.DialogCode.Accepted
    monkeypatch.setattr(PerformanceRecordDialog,'exec',accept)
    dialog.edit_record(True)
    assert dialog.flows.rowCount()==1 and dialog.flow_rows[0]['amount']=='50'
    headers,rows=rows_for(window,'funding_flows','2025-01-01','2025-12-31')
    assert rows[0][headers.index('kind')]=='contribution'
    from card_compass.investments import InvestmentStore
    assert len(InvestmentStore(window.directory).state['performanceRecords']['flows'])==1
    monkeypatch.setattr(QMessageBox,'question',lambda *args:QMessageBox.StandardButton.Yes)
    dialog.tabs.setCurrentWidget(dialog.flows);dialog.flows.selectRow(0);dialog.delete_selected()
    assert not window.investment_store.state['performanceRecords']['flows']
    dialog.reject()


def test_benchmark_selection_persists_and_subscriptions_cleanup(window):
    dialog=PerformanceDialog(window.stocks_page)
    dialog.benchmark.setText('QQQ');dialog.range.setCurrentIndex(dialog.range.findData('1y'))
    dialog.load_benchmark()
    assert window.investment_store.state['performanceBenchmark']=={'symbol':'QQQ','range':'1y'}
    assert dialog.subscription in window.stocks_page.data_layer.subscriptions
    assert 'Not loaded' in dialog.status.text()
    dialog.reject()
    assert dialog.subscription not in window.stocks_page.data_layer.subscriptions


def test_combined_account_scope_and_small_screen_dialog(window):
    dialog=PerformanceDialog(window.stocks_page)
    dialog.account.setCurrentIndex(dialog.account.findData(None));dialog.use_range();dialog.benchmark.clear()
    dialog.confirmation.setChecked(True);dialog.calculate_returns()
    assert 'Included accounts: Unassigned historical trades' in dialog.results.toPlainText()
    dialog.resize(768,752);dialog.show();QApplication.instance().processEvents()
    if hasattr(dialog,'text_scroll'):
        assert dialog.text_scroll.horizontalScrollBar().maximum()==0
    assert dialog.confirmation.minimumSizeHint().width()<700
    dialog.reject()


def test_performance_import_ui_maps_accounts_and_reviews_corrections(window,tmp_path,monkeypatch):
    from PySide6.QtWidgets import QFileDialog
    from card_compass.reports import write_csv
    from card_compass.performance_import_ui import PerformanceImportDialog
    path=tmp_path/'values.csv'
    write_csv(path,['account_id','date','amount_usd','source'],[['source','2025-01-01',200,'Updated broker statement']])
    monkeypatch.setattr(QFileDialog,'getOpenFileName',lambda *args:(str(path),''))
    dialog=PerformanceImportDialog(window.stocks_page);dialog.choose_file();dialog.build()
    assert 'Map every' in dialog.status.text() and not dialog.confirm.isEnabled()
    combo=dialog.mapping['source'];combo.setCurrentIndex(combo.findData('legacy'))
    dialog.build();dialog.save()
    assert 'corrections' in dialog.status.text() and not dialog.ack.isHidden()
    assert '100' in dialog.table.item(0,4).text()
    dialog.ack.setChecked(True);dialog.save()
    assert window.investment_store.state['performanceRecords']['valuations'][0]['amount']=='200'
    dialog.close()
