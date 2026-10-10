"""Regression coverage for independent research tabs and persisted view customization."""
import os
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
from copy import deepcopy
import pytest
from PySide6.QtCore import Qt, QPoint, QTimer
from PySide6.QtWidgets import QApplication, QTableWidget, QTabBar
from PySide6.QtTest import QTest
from card_compass.app import MainWindow
from card_compass.stocks_ui import ResearchPanel, ViewSettingsDialog
from card_compass.investments import InvestmentStore, validate_view_settings, widget


@pytest.fixture
def window(tmp_path):
    app = QApplication.instance() or QApplication([])
    w = MainWindow(tmp_path, auto_refresh=False)
    w.navigate('stocks')
    w.show()
    app.processEvents()
    yield w
    w.close()
    w.deleteLater()
    app.processEvents()


def double_click_row(table, row):
    table.scrollToItem(table.item(row, 0))
    QApplication.instance().processEvents()
    point = table.visualItemRect(table.item(row, 0)).center()
    QTest.mouseClick(table.viewport(), Qt.MouseButton.LeftButton, pos=point)
    QTest.mouseDClick(table.viewport(), Qt.MouseButton.LeftButton, pos=point)
    QApplication.instance().processEvents()


def test_home_double_click_opens_a_closable_research_tab_and_keeps_home(window):
    p = window.stocks_page
    home_state = deepcopy(p.current_page())
    home_canvas = p.dashboard_page.canvas
    table = next(f.component.table for f in home_canvas.frames.values() if f.block['type']=='watchlist')
    ticker = table.item(0, 0).text()
    double_click_row(table, 0)
    assert p.tabs.count() == 5
    panel = p.tabs.currentWidget()
    assert isinstance(panel, ResearchPanel) and panel.ticker == ticker
    assert p.dashboard_page.canvas is home_canvas
    assert p.tabs.tabText(p.tabs.indexOf(p.dashboard_page)) == 'Investment overview'
    assert p.store.state['pages'][0] == home_state
    p.tabs.setCurrentWidget(p.dashboard_page)
    assert not p.stock_view
    assert p.current_page() == home_state
    chart = next(w for w in p.current_page()['widgets'] if w['type'] == 'chart')
    assert p.widget_symbol(chart) == chart['config']['symbol']
    p.open_symbol(ticker)
    assert p.tabs.count() == 5 and p.tabs.currentWidget() is panel
    index = p.tabs.indexOf(panel)
    close_button = p.tabs.tabBar().tabButton(index, QTabBar.ButtonPosition.RightSide)
    if close_button is None:
        close_button = p.tabs.tabBar().tabButton(index, QTabBar.ButtonPosition.LeftSide)
    assert close_button is not None
    QTest.mouseClick(close_button, Qt.MouseButton.LeftButton)
    assert p.tabs.count() == 4 and p.tabs.currentWidget() is p.dashboard_page


def test_watchlist_double_clicks_keep_separate_stock_contexts_after_tab_reordering(window):
    p = window.stocks_page
    p.tabs.setCurrentWidget(p.watchlist_page)
    double_click_row(p.watchlist_table, 0)
    apple = p.tabs.currentWidget()
    assert apple.ticker == 'AAPL'
    p.tabs.setCurrentWidget(p.watchlist_page)
    double_click_row(p.watchlist_table, 1)
    microsoft = p.tabs.currentWidget()
    assert microsoft.ticker == 'MSFT' and apple is not microsoft
    assert p.tabs.count() == 6
    p.tabs.tabBar().moveTab(p.tabs.indexOf(microsoft), 1)
    p.tabs.setCurrentWidget(apple)
    assert p.stock_view and p.selected_symbol == 'AAPL'
    p.tabs.setCurrentWidget(microsoft)
    assert p.stock_view and p.selected_symbol == 'MSFT'
    p.tabs.setCurrentWidget(p.news_page)
    assert p.news_view and not p.stock_view
    p.tabs.setCurrentWidget(p.dashboard_page)
    assert not p.news_view and not p.stock_view and p.current_page()['id'] == 'home'


def test_stock_layouts_and_notes_remain_independent_from_home_and_each_other(window):
    p = window.stocks_page
    home = deepcopy(p.store.state['pages'][0])
    template = deepcopy(p.store.state['stockTemplate'])
    p.open_symbol('AAPL')
    p.research_tabs['AAPL'].customize.click()
    chart = next(w for w in p.current_page()['widgets'] if w['type'] == 'chart')
    p.remove_widget(chart['id'])
    note = next(w for w in p.current_page()['widgets'] if w['type'] == 'notes')
    p.note_editors['stock-AAPL:' + note['id']].setPlainText('Apple thesis')
    p.open_symbol('MSFT')
    assert any(w['id'] == chart['id'] for w in p.current_page()['widgets'])
    p.research_tabs['MSFT'].customize.click()
    p.note_editors['stock-MSFT:' + note['id']].setPlainText('Microsoft thesis')
    p.tabs.setCurrentWidget(p.research_tabs['AAPL'])
    assert not any(w['id'] == chart['id'] for w in p.current_page()['widgets'])
    assert p.note_editors['stock-AAPL:' + note['id']].toPlainText() == 'Apple thesis'
    assert p.store.state['stockTemplate'] == template
    assert p.store.state['pages'][0] == home
    p.close_research_tab(p.tabs.indexOf(p.research_tabs['MSFT']))
    saved = InvestmentStore(window.directory).state
    assert saved['notes']['stock-MSFT:' + note['id']] == 'Microsoft thesis'
    assert saved['notes']['stock-AAPL:' + note['id']] == 'Apple thesis'


def test_resize_handle_changes_geometry_without_overlap_and_persists(window):
    from card_compass.dashboard.layout_engine import intersects
    from PySide6.QtCore import QPointF
    p=window.stocks_page;p.edit_layout.setChecked(True);canvas=p.active_canvas()
    before=deepcopy(canvas.current_layout);geometry=before[0];frame=canvas.frames[geometry['i']]
    start=canvas.mapFromScene(canvas.items[geometry['i']].pos()+QPointF(frame.width()-8,frame.height()-8))
    end=start+QPoint(round(canvas.column_width),10)
    QTest.mousePress(canvas.viewport(),Qt.MouseButton.LeftButton,pos=start);QTest.mouseMove(canvas.viewport(),end)
    QTest.mouseRelease(canvas.viewport(),Qt.MouseButton.LeftButton,pos=end);p.layout_store.flush()
    after=canvas.current_layout;resized=next(g for g in after if g['i']==geometry['i'])
    assert resized['w']==geometry['w']+1 and resized['h']==geometry['h']+1
    assert not any(intersects(a,b) for i,a in enumerate(after) for b in after[i+1:])
    assert InvestmentStore(window.directory).state['pages'][0]['layouts'][canvas.size_key]==after


def test_view_settings_customize_tiles_columns_density_and_saved_column_order(window):
    p = window.stocks_page
    def configure():
        dialog = QApplication.activeModalWidget()
        assert isinstance(dialog, ViewSettingsDialog)
        dialog.metrics[1].setChecked(False)
        dialog.columns['watchlist'][1].setChecked(False)
        dialog.columns['trades'][5].setChecked(False)
        dialog.height.setValue(40)
        dialog.accept()
    QTimer.singleShot(0, configure)
    p.configure_views()
    assert p.metrics[1].isHidden()
    assert p.watchlist_table.isColumnHidden(1)
    assert p.trade_table.isColumnHidden(5)
    assert p.trade_table.verticalHeader().defaultSectionSize() == 40
    p.watchlist_table.horizontalHeader().moveSection(0, 2)
    p.flush_notes()
    order = [p.watchlist_table.horizontalHeader().logicalIndex(i) for i in range(7)]
    assert p.store.state['viewSettings']['order']['watchlist'] == order
    reopened = MainWindow(window.directory, auto_refresh=False)
    assert reopened.stocks_page.metrics[1].isHidden()
    assert reopened.stocks_page.watchlist_table.isColumnHidden(1)
    assert [reopened.stocks_page.watchlist_table.horizontalHeader().logicalIndex(i) for i in range(7)] == order
    reopened.close()
    reopened.deleteLater()


def test_credit_cards_uses_normal_navigation_and_indented_children(window):
    window.cards_toggle.click()
    QApplication.instance().processEvents()
    assert window.cards_toggle.objectName() == window.navigation['stocks'].objectName() == 'nav'
    assert window.cards_toggle.isChecked() and window.active_page == 'explore'
    parent_x = window.cards_toggle.mapTo(window.centralWidget(), QPoint(0, 0)).x()
    for key in ('explore', 'matches', 'wallet', 'spending', 'imports', 'breakdown', 'coverage'):
        child = window.navigation[key]
        assert child.mapTo(window.centralWidget(), QPoint(0, 0)).x() > parent_x
        assert child.property('navLevel') == 'child'
        child.click()
        assert window.active_page == key and window.cards_toggle.isChecked() and child.isChecked()
    window.navigation['stocks'].click()
    assert not window.cards_toggle.isChecked()
    assert all(not window.navigation[key].isChecked() for key in ('explore', 'wallet', 'coverage'))


def test_invalid_view_settings_are_rejected_before_saving(window):
    state = deepcopy(window.investment_store.state)
    for invalid in ({'columns': {'watchlist': []}}, {'metrics': [True]}, {'rowHeight': 0}, {'order': {'watchlist': [0, 1]}}):
        state['viewSettings'] = invalid
        with pytest.raises(ValueError):
            window.investment_store.save(state)
    assert 'viewSettings' not in window.investment_store.state


def test_market_requests_cover_home_and_each_open_stock(window):
    p=window.stocks_page;home_chart=next(w for w in p.current_page()['widgets'] if w['type']=='chart')
    p.open_symbol('NVDA');p.open_symbol('AMD');research_chart=next(w for w in p.current_page()['widgets'] if w['type']=='chart')
    requests=p.requests()
    assert ('quote',home_chart['config']['symbol']+'|'+home_chart['config']['range']) in requests
    assert ('quote','NVDA|'+research_chart['config']['range']) in requests
    assert ('quote','AMD|'+research_chart['config']['range']) in requests
    assert len(requests)==len(set(requests))


def test_market_refresh_keeps_research_note_text_and_cursor(window):
    p = window.stocks_page
    p.open_symbol('AAPL')
    note=next(w for w in p.current_page()['widgets'] if w['type']=='notes')
    key='stock-AAPL:'+note['id'];editor=p.note_editors[key]
    p.active_canvas().ensureVisible(p.active_canvas().items[note['id']])
    editor.setPlainText('Research notes in progress')
    editor.setFocus()
    cursor = editor.textCursor()
    cursor.setPosition(8)
    editor.setTextCursor(cursor)
    QApplication.instance().processEvents()
    p.market_finished()
    replacement = p.note_editors[key]
    assert replacement.toPlainText() == 'Research notes in progress'
    assert replacement.textCursor().position() == 8
    assert replacement.hasFocus()
