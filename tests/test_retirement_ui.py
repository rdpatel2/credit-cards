import os
os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
import csv
from datetime import date,timedelta
import pytest
from PySide6.QtCore import QEvent,QDate
from PySide6.QtWidgets import QApplication,QFileDialog,QDialogButtonBox
from card_compass.app import MainWindow
from card_compass.retirement_ui import RetirementDialog


@pytest.fixture
def window(tmp_path):
    app=QApplication.instance() or QApplication([])
    window=MainWindow(tmp_path,auto_refresh=False);window.show();app.processEvents()
    yield window
    window.close();app.sendPostedEvents(None,QEvent.Type.DeferredDelete);app.processEvents()


def test_scenarios_saving_export_and_input_changes_do_not_leave_stale_results(window,tmp_path,monkeypatch):
    dialog=RetirementDialog(window)
    dialog.ages['currentAge'].setValue(64);dialog.ages['retireAge'].setValue(65);dialog.ages['endAge'].setValue(68)
    dialog.amounts['openingCents'].setValue(1000);dialog.amounts['annualContributionCents'].setValue(100)
    dialog.amounts['annualSpendingCents'].setValue(600);dialog.rates['inflationBps'].setValue(0)
    for field in ('lowReturnBps','typicalReturnBps','highReturnBps'):dialog.rates[field].setValue(0)
    dialog.calculate()
    assert dialog.result and dialog.table.rowCount()==4 and '66' in dialog.summary.text()
    dialog.save();assert dialog.store.load()['openingCents']==100000
    path=tmp_path/'scenarios.csv';monkeypatch.setattr(QFileDialog,'getSaveFileName',lambda *args:(str(path),''))
    dialog.export()
    rows=list(csv.DictReader(path.read_text(encoding='utf-8-sig').splitlines()))
    assert len(rows)==12 and rows[0]['dataset']=='retirement_scenarios'
    assert rows[0]['closing_nominal_usd']=='1100' and rows[0]['fee_percent']=='0'
    assert rows[0]['retirement_age']=='65' and rows[0]['annual_spending_today_usd']=='600'
    dialog.amounts['annualSpendingCents'].setValue(700)
    assert dialog.result is None and dialog.table.rowCount()==0
    dialog.export();assert 'Calculate current' in dialog.error.text()
    saved=dialog.store.load();dialog.ages['endAge'].setValue(60);dialog.save()
    assert dialog.store.load()==saved and 'Could not save' in dialog.error.text()
    dialog.close()


def test_balance_copy_uses_only_reviewed_eligible_usd_snapshot_and_stale_date_refuses(window):
    today=date.today().isoformat();old=(date.today()-timedelta(days=1)).isoformat()
    ident=window.planning_store.save_account('Retirement','ira','USD','Me',1000,today)
    window.planning_store.save_account('Old','ira','USD','Me',2000,old)
    window.planning_store.save_account('House','real_estate','USD','Me',3000,today)
    dialog=RetirementDialog(window)
    assert dialog.account.count()==2
    dialog.account.setCurrentIndex(dialog.account.findData(ident));dialog.use_balance()
    assert dialog.amounts['openingCents'].value()==1000 and 'Retirement' in dialog.source.text()
    dialog.as_of.setDate(QDate.fromString(old,'yyyy-MM-dd'));dialog.calculate()
    assert dialog.result is None and 'update the as-of' in dialog.error.text()
    dialog.close()


def test_compact_demo_scenarios_and_close_button_are_visible(window):
    window.is_demo=True
    dialog=RetirementDialog(window);dialog.resize(980,700);dialog.show();dialog.calculate()
    for _ in range(3):QApplication.instance().processEvents()
    assert dialog.table.viewport().height()>=150 and hasattr(dialog,'demo_banner')
    if hasattr(dialog,'text_scroll'):assert dialog.text_scroll.horizontalScrollBar().maximum()==0
    box=dialog.findChild(QDialogButtonBox)
    assert box.mapTo(dialog,box.rect().bottomRight()).y()<=dialog.height()
    dialog.close()
