import os
os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
from copy import deepcopy
import pytest
from PySide6.QtCore import QEventLoop,QTimer
from PySide6.QtGui import QPalette
from PySide6.QtWidgets import QApplication,QDialog,QTextEdit,QFileDialog,QMessageBox
from card_compass.app import MainWindow,CardSettings
from card_compass.investments import widget
from card_compass.theme import color
from tests.test_investments import trade


@pytest.fixture
def window(tmp_path):
    app=QApplication.instance() or QApplication([])
    w=MainWindow(tmp_path,auto_refresh=False)
    yield w
    w.close()
    w.deleteLater()
    app.processEvents()


def test_theme_covers_native_pages_dialogs_card_details_and_persists(window):
    app=QApplication.instance()
    window.theme_toggle.click()
    assert window.profile['theme']=='dark'
    assert window.palette().color(QPalette.ColorRole.Window).name()=='#101923'
    for key in window.page_indexes:
        window.navigate(key)
        assert window.pages.currentWidget().palette().color(QPalette.ColorRole.Text).name()=='#e4edf8'
    card=window.by_id['chase-freedom-unlimited']
    window.show_card(card)
    assert '#d4e2f1' in window.details.document().defaultStyleSheet()
    dialog=CardSettings(card,window.profile,window)
    assert dialog.palette().color(QPalette.ColorRole.Window).name()=='#101923'
    window.coverage_widgets['dining'].setValue(0)
    window.recalculate()
    assert '#492831' in window.coverage_widgets['dining'].styleSheet()
    assert color('#ffffff')=='#192533'
    reopened=MainWindow(window.directory,auto_refresh=False)
    assert reopened.theme_toggle.isChecked()
    reopened.theme_toggle.click()
    assert reopened.profile['theme']=='light'
    assert reopened.palette().color(QPalette.ColorRole.Base).name()=='#ffffff'
    dialog.close();dialog.deleteLater();reopened.close();reopened.deleteLater();app.processEvents()


def test_positions_integrate_with_overview_and_watchlist_removal_retains_history(window):
    investments=window.stocks_page
    window.investment_store.trade('AAPL',trade('buy',2.5,100,fees=5))
    investments.reload();window.refresh_investment_summary()
    assert window.overview_page.investment_metric.value.text()=='$255.00'
    assert investments.metrics[0].value.text()=='$255.00'
    assert investments.watchlist_table.item(0,4).text()=='2.5'
    investments.watchlist_table.selectRow(0);investments.remove_symbol()
    assert window.investment_store.state['trades']['AAPL'][0]['quantity']==2.5
    assert investments.trade_table.rowCount()==1
    assert not hasattr(investments,'process')


def test_trade_selection_uses_unique_id_when_dates_match(window):
    p=window.stocks_page
    p.store.trade('AAPL',trade('first',1,100));p.store.trade('AAPL',trade('second',1,110));p.reload()
    p.trade_table.selectRow(0);assert p.selected_trade()[1]['id']=='first'
    p.data_layer.notify_local();assert p.selected_trade()[1]['id']=='first'
    p.store.trade('AAPL',trade('first',1,105));p.reload()
    assert p.selected_trade()[1]['id']=='first' and p.selected_trade()[1]['price']==105


def test_modular_geometry_notes_and_stock_templates_survive_restarts(window):
    from card_compass.dashboard.layout_engine import ENGINE
    investments=window.stocks_page;ident=investments.add_block('notes')
    editor=investments.note_editors['home:'+ident];editor.setPlainText('Save this thesis before switching pages.')
    canvas=investments.active_canvas();key=canvas.size_key
    geometry=ENGINE.move(canvas.current_layout,ident,{'x':6,'y':80},False)
    investments.layout_changed(investments.dashboard_page,key,geometry)
    assert investments.store.state['notes']['home:'+ident]=='Save this thesis before switching pages.'
    assert next(g for g in canvas.current_layout if g['i']==ident)['x']==6
    investments.open_symbol('MSFT');stock_note=next(w for w in investments.current_page()['widgets'] if w['type']=='notes')
    note_key='stock-MSFT:'+stock_note['id'];investments.note_editors[note_key].setPlainText('Stock-specific thesis')
    investments.flush_notes();investments.customize_stock(True);investments.remove_widget(stock_note['id']);investments.layout_store.flush()
    assert any(w['id']==stock_note['id'] for w in investments.store.state['stockTemplate']['widgets'])
    from card_compass.investments import InvestmentStore
    assert InvestmentStore(window.directory).state['notes'][note_key]=='Stock-specific thesis'


def test_native_demo_refresh_provenance_and_sector_notes(window):
    from PySide6.QtTest import QTest
    p=window.stocks_page;p.mode.setCurrentIndex(p.mode.findData('demo'))
    for _ in range(100):
        QTest.qWait(30)
        if p.quote('AAPL') and not p.data_layer.pending and not p.data_layer.loading:break
    assert p.quote('AAPL')['demo'] and p.metrics[2].value.text()=='—'
    p.tabs.setCurrentWidget(p.news_page);p.sectors.setCurrentIndex(p.sectors.findData('technology'))
    assert p.news_table.rowCount()==1 and 'Nvidia' in p.news_table.item(0,0).text()
    note=next(w for w in p.current_page()['widgets'] if w['type']=='notes');key=p.current_page()['id']+':'+note['id']
    p.note_editors[key].setPlainText('Technology notes');p.sectors.setCurrentIndex(p.sectors.findData('energy'))
    assert p.store.state['notes'][key]=='Technology notes' and p.news_table.rowCount()==1


def test_native_import_export_preserves_folio_note_keys_and_invalid_import_is_atomic(window,tmp_path,monkeypatch):
    p=window.stocks_page
    state=deepcopy(p.store.state);note=next(w for w in state['stockTemplate']['widgets'] if w['type']=='notes')
    state['notes']['stock-AAPL:'+note['id']]='Imported Folio thesis'
    from card_compass.catalog import write_json,read_json
    path=tmp_path/'folio.json';write_json(path,state)
    monkeypatch.setattr(QFileDialog,'getOpenFileName',lambda *a,**k:(str(path),''))
    monkeypatch.setattr(QMessageBox,'question',lambda *a,**k:QMessageBox.StandardButton.Yes)
    p.import_workspace();p.open_symbol('AAPL')
    assert p.note_editors['stock-AAPL:'+note['id']].toPlainText()=='Imported Folio thesis'
    out=tmp_path/'export.json'
    monkeypatch.setattr(QFileDialog,'getSaveFileName',lambda *a,**k:(str(out),''))
    p.export_workspace();assert read_json(out)==p.store.state
    before=p.store.path.read_bytes();path.write_text('{bad')
    p.import_workspace();assert p.store.path.read_bytes()==before
    assert p.status.text()
