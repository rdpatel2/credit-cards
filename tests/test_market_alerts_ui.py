import os
os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
from copy import deepcopy
import pytest
from PySide6.QtWidgets import QApplication,QMessageBox
from PySide6.QtCore import QEvent
from card_compass.app import MainWindow
from card_compass.market_alerts_ui import MarketAlertsDialog,AlertRuleDialog
from card_compass.market_alerts import initial_alerts,rule_values
from card_compass.investments import InvestmentStore
from tests.test_market_alerts import quote


@pytest.fixture
def window(tmp_path):
    app=QApplication.instance() or QApplication([])
    window=MainWindow(tmp_path,auto_refresh=False)
    window.stocks_page.data_layer.cache['quote:AAPL|1d']=quote()
    yield window
    window.close();window.deleteLater();app.sendPostedEvents(None,QEvent.Type.DeferredDelete);app.processEvents()


def test_rule_creation_activity_read_clear_and_restart_persistence(window,monkeypatch):
    owner=window.stocks_page
    dialog=MarketAlertsDialog(owner)
    def accept(editor):
        editor.name.setText('My level');editor.ticker.setText('AAPL');editor.threshold.setValue(100)
        return editor.DialogCode.Accepted
    monkeypatch.setattr(AlertRuleDialog,'exec',accept)
    dialog.edit_rule(True)
    assert len(owner.alert_engine.state()['events'])==1 and owner.alerts_button.text()=='Alerts (1)'
    assert dialog.events.item(0,2).text().startswith('AAPL meets your')
    restored=InvestmentStore(window.directory).state['marketAlerts']
    assert restored['rules'][0]['runtime']['active']
    owner.alert_engine.check()
    assert len(owner.alert_engine.state()['events'])==1
    dialog.events.selectRow(0);dialog.mark_selected()
    assert owner.alerts_button.text()=='Alerts (0)'
    monkeypatch.setattr(QMessageBox,'question',lambda *args:QMessageBox.StandardButton.Yes)
    dialog.clear_read()
    assert not owner.alert_engine.state()['events'] and len(owner.alert_engine.state()['rules'])==1
    dialog.close()


def test_disabled_rules_stop_subscriptions_and_demo_never_fires_personal_price_alerts(window):
    owner=window.stocks_page
    alerts=initial_alerts();alerts['rules']=[rule_values('Ticker not watched elsewhere','price',symbol='TEST',direction='above',threshold='10')]
    owner.alert_engine.persist(alerts);owner.alert_engine.check()
    assert ('quote','TEST|1d') in owner.data_layer.subscriptions[owner.alert_engine.subscription]
    alerts=deepcopy(owner.alert_engine.state());alerts['rules'][0]['enabled']=False
    owner.alert_engine.persist(alerts);owner.alert_engine.check()
    assert not owner.data_layer.subscriptions[owner.alert_engine.subscription]
    alerts['rules'][0]['enabled']=True
    owner.data_layer.cache['quote:TEST|1d']=dict(quote(),demo=True)
    owner.alert_engine.persist(alerts);owner.alert_engine.check()
    assert not owner.alert_engine.state()['events']


def test_rule_edit_preserves_live_latches_and_invalid_quiet_hours_do_not_save(window,monkeypatch):
    owner=window.stocks_page
    alerts=initial_alerts();alerts['rules']=[rule_values('Old name','price',symbol='AAPL',direction='above',threshold='100')]
    owner.alert_engine.persist(alerts);owner.alert_engine.check()
    dialog=MarketAlertsDialog(owner);dialog.rules.selectRow(0)
    def accept(editor):editor.name.setText('Renamed');return editor.DialogCode.Accepted
    monkeypatch.setattr(AlertRuleDialog,'exec',accept)
    dialog.edit_rule()
    assert owner.alert_engine.state()['rules'][0]['name']=='Renamed'
    assert len(owner.alert_engine.state()['events'])==1
    original=deepcopy(owner.alert_engine.state())
    dialog.quiet.setChecked(True);dialog.start.setValue(8);dialog.end.setValue(8);dialog.save_quiet()
    assert 'different' in dialog.status.text() and owner.alert_engine.state()==original
    dialog.close()


def test_failed_save_does_not_claim_delivery_or_mutate_personal_rules(window,monkeypatch):
    owner=window.stocks_page
    alerts=initial_alerts();alerts['rules']=[rule_values('Level','price',symbol='AAPL',direction='above',threshold='100')]
    owner.alert_engine.persist(alerts)
    before=deepcopy(owner.alert_engine.state())
    def fail(*args):raise OSError('Read-only profile')
    monkeypatch.setattr(owner.store,'save',fail)
    owner.alert_engine.check()
    assert owner.alert_engine.state()==before and owner.alert_engine.error=='Read-only profile'
    assert owner.alerts_button.text()=='Alerts (0)'
