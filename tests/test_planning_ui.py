import os
os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
from copy import deepcopy
from pathlib import Path
import pytest
from PySide6.QtCore import QDate, Qt, QEvent
from PySide6.QtWidgets import QApplication, QMessageBox, QFileDialog
from card_compass.app import MainWindow
from card_compass.planning_ui import AccountDialog, GoalDialog
from card_compass.catalog import read_json


@pytest.fixture
def window(tmp_path):
    app=QApplication.instance() or QApplication([])
    window=MainWindow(tmp_path,auto_refresh=False)
    window.resize(1280,900)
    window.show()
    app.processEvents()
    yield window
    window.close()
    app.sendPostedEvents(None,QEvent.Type.DeferredDelete)
    app.processEvents()


def test_accounts_goals_history_and_debt_are_real_routes(window):
    window.navigate('accounts')
    assert window.pages.currentWidget() is window.accounts_page
    savings=window.planning_store.save_account('Savings','savings','USD','Me',1000,'2025-01-01')
    window.planning_store.record_balance(savings,1500,'2025-02-01')
    window.planning_store.save_account('Loan','loan','USD','Me',100,'2025-01-01',apr=0,minimum_payment=10)
    window.planning_store.save_goal('Buffer','emergency','USD',2000,'2027-01-01',saved=500,account_id=savings)
    window.refresh_planning()
    assert window.accounts_page.table.rowCount()==2
    assert window.accounts_page.history_table.rowCount()==2
    assert 'Repaid' in window.accounts_page.debt_table.item(0,5).text()
    assert window.accounts_page.debt_history.rowCount()==10
    window.navigate('goals')
    assert window.goals_page.table.item(0,3).text()=='25%'
    assert window.goals_page.table.item(0,6).text()=='Savings'


def test_dialogs_expose_currency_owner_and_allocation_details(window):
    account=AccountDialog(window)
    account.kind.setCurrentIndex(account.kind.findData('loan'))
    account.currency.setCurrentIndex(account.currency.findData('EUR'))
    account.name.setText('My loan')
    account.owner_name.setText('Partner')
    account.balance.setValue(1200)
    account.apr.setValue(9)
    account.minimum.setValue(40)
    account.as_of.setDate(QDate(2025,1,1))
    ident=window.planning_store.save_account(**account.values())
    assert window.planning_store.accounts()[0]['owner']=='Partner'
    assert account.balance.prefix()=='EUR '
    goal=GoalDialog(window)
    assert goal.account.count()==1  # debt is not eligible
    account.close()
    goal.close()


def test_isolated_demo_mutations_reset_and_cleanup_leave_real_data_untouched(window):
    before_accounts=deepcopy(window.planning_store.accounts())
    before_trades=deepcopy(window.investment_store.state['trades'])
    demo=window.start_demo()
    directory=demo.directory
    assert demo.is_demo and demo.artwork.network_enabled is False
    assert directory!=window.directory
    assert len(demo.planning_store.accounts())==3
    from card_compass.stocks_ui import TradeDialog
    dialog=TradeDialog(demo.stocks_page,'SAMPLE');dialog.show()
    QApplication.instance().processEvents()
    assert dialog.windowTitle().startswith('DEMO') and hasattr(dialog,'demo_banner')
    dialog.close()
    savings=next(row for row in demo.planning_store.accounts() if row['kind']=='savings')
    demo.planning_store.record_balance(savings['id'],1,'2025-01-01')
    demo.investment_store.state['notes']['example']='Synthetic edit'
    demo.reset_demo()
    app=QApplication.instance()
    app.sendPostedEvents(None,QEvent.Type.DeferredDelete)
    app.processEvents()
    fresh=window.demo_windows[-1]
    assert fresh.is_demo and fresh.directory!=directory
    assert not fresh.investment_store.state['notes']
    assert not directory.exists()
    assert window.planning_store.accounts()==before_accounts
    assert window.investment_store.state['trades']==before_trades
    fresh.close()
    app.sendPostedEvents(None,QEvent.Type.DeferredDelete)
    app.processEvents()
    assert not window.demo_windows


def test_persona_and_checklist_persist_with_actual_records(window):
    page=window.onboarding_page
    page.persona.setCurrentIndex(page.persona.findData('saver'))
    window.planning_store.save_account('Savings','savings','USD','Me',1000,'2025-01-01')
    window.refresh_planning()
    assert page.completion()['accounts']
    assert not page.completion()['goals']
    assert read_json(window.directory/'profile.json')['onboarding_persona']=='saver'
    before=len(window.investment_store.state['pages'])
    page.create_template()
    assert len(window.investment_store.state['pages'])==before+1
    assert 'home' in [row['id'] for row in window.investment_store.state['pages']]
    window.navigate('start')
    QApplication.instance().processEvents()
    assert page.table.columnWidth(3)>=100


def test_budget_coverage_and_csv_route_show_real_sources(window,tmp_path,monkeypatch):
    before=window.finance_store.coverage('2025-01',['missing-card'])
    assert before['without_closing']==['missing-card'] and before['imported_transactions']==0
    window.finance_store.save_entry('Salary','income','salary',2500,'2025-01',True)
    details=[]
    monkeypatch.setattr(QMessageBox,'information',lambda *args:details.append(args[2]))
    window.set_finance_month(QDate(2025,1,1))
    window.overview_page.show_coverage()
    assert 'does not mean no money was spent' in details[-1]
    window.navigate('reports')
    report=window.reports_page
    report.dataset.setCurrentIndex(report.dataset.findData('cash_flow'))
    report.start.setDate(QDate(2025,1,1))
    report.end.setDate(QDate(2025,1,31))
    path=tmp_path/'report.csv'
    monkeypatch.setattr(QFileDialog,'getSaveFileName',lambda *args:(str(path),''))
    report.export()
    assert path.is_file() and 'Exported 1 rows' in report.status.text()
    assert 'Salary' in path.read_text(encoding='utf-8-sig')
