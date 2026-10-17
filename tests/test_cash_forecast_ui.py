import os
os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
from datetime import date, timedelta
import pytest
from PySide6.QtCore import QDate, QEvent
from PySide6.QtWidgets import QApplication
from card_compass.app import MainWindow
from card_compass.cash_forecast_ui import CashForecastDialog


@pytest.fixture
def window(tmp_path):
    app=QApplication.instance() or QApplication([])
    window=MainWindow(tmp_path,auto_refresh=False)
    window.show();app.processEvents()
    yield window
    window.close();app.sendPostedEvents(None,QEvent.Type.DeferredDelete);app.processEvents()


def test_variable_income_overrides_save_reload_and_invalidation(window):
    dialog=CashForecastDialog(window)
    dialog.opening.setValue(1000)
    dialog.reserve.setValue(500)
    dialog.mode.setCurrentIndex(dialog.mode.findData('range'))
    dialog.low.setValue(300)
    dialog.typical.setValue(600)
    dialog.high.setValue(900)
    dialog.override_low.setValue(200)
    dialog.override_typical.setValue(500)
    dialog.override_high.setValue(800)
    dialog.set_override()
    assert dialog.override_table.rowCount()==1
    dialog.calculate()
    assert dialog.result and dialog.table.rowCount()==12 and dialog.tabs.currentIndex()==3
    assert dialog.result['scenarios'][1]['rows'][0]['closing']==150000
    dialog.save()
    saved=window.cash_forecast_store.load()
    dialog.opening.setValue(1001)
    assert dialog.result is None and dialog.table.rowCount()==0
    dialog.low.setValue(1000);dialog.save()
    assert 'Could not save' in dialog.error.text() and window.cash_forecast_store.load()==saved
    reload=CashForecastDialog(window)
    assert reload.typical.value()==600 and reload.override_table.rowCount()==1
    dialog.close();reload.close()


def test_only_today_usd_cash_is_copyable_and_stale_cash_refuses_forecast(window):
    planning=window.planning_store
    today=date.today().isoformat();older=(date.today()-timedelta(days=1)).isoformat()
    checking=planning.save_account('Checking','checking','USD','Me',1000,today)
    planning.save_account('Old savings','savings','USD','Me',1000,older)
    planning.save_account('Euro savings','savings','EUR','Me',1000,today)
    dialog=CashForecastDialog(window)
    assert dialog.cash_account.count()==2
    dialog.cash_account.setCurrentIndex(dialog.cash_account.findData(checking))
    planning.record_balance(checking,1200,today)
    dialog.use_balance()
    assert dialog.opening.value()==1200 and 'Checking' in dialog.source.text()
    dialog.as_of.setDate(QDate.fromString(older,'yyyy-MM-dd'))
    dialog.calculate()
    assert dialog.result is None and 'Update current cash' in dialog.error.text()
    dialog.close()


def test_corrupt_assumptions_are_preserved_until_reviewed_save(window):
    with window.cash_forecast_store.connection() as db:
        db.execute("INSERT INTO cash_forecast_settings VALUES (1,'{}')")
    dialog=CashForecastDialog(window)
    assert 'original record is preserved' in dialog.error.text()
    with window.cash_forecast_store.connection() as db:
        assert db.execute('SELECT settings FROM cash_forecast_settings').fetchone()[0]=='{}'
    dialog.opening.setValue(500);dialog.save()
    assert window.cash_forecast_store.load()['openingCash']==50000
    dialog.close()


def test_small_screen_scenarios_and_close_controls_remain_visible(window):
    dialog=CashForecastDialog(window)
    dialog.resize(980,700);dialog.show();QApplication.instance().processEvents()
    dialog.calculate();QApplication.instance().processEvents()
    assert dialog.table.viewport().height()>=150
    assert dialog.table.columnWidth(0)>=90
    assert dialog.tabs.width()<=dialog.width()
    assert dialog.history.rowCount()==12
    assert any('Cash runway' in action.text() for action in window.budget_page.plan_tools_menu.actions())
    dialog.close()
