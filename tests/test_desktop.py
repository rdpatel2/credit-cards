"""Offscreen native-widget workflow tests, using only the bundled snapshot."""
import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtWidgets import QApplication
from PySide6.QtCore import QEventLoop, QTimer, Qt
from PySide6.QtTest import QTest
from card_compass.app import MainWindow, STYLE, CardSettings
from card_compass.catalog import read_json


@pytest.fixture(scope="session")
def app():
    instance = QApplication.instance() or QApplication([])
    instance.setStyleSheet(STYLE)
    return instance


@pytest.fixture
def window(app, tmp_path):
    w = MainWindow(tmp_path, auto_refresh=False)
    yield w
    w.close()


def test_wallet_spend_coverage_and_persistence(window, app):
    window.spend_widgets["dining"].setValue(400)
    window.spend_widgets["groceries"].setValue(600)
    window.recalculate()
    initial_count = window.recommendations.rowCount()
    c = window.by_id["chase-freedom-unlimited"]
    window.show_card(c)
    window.add_wallet_card()
    assert window.wallet.count() == 1
    assert window.recommendations.rowCount() == initial_count - 1
    assert "Already in wallet" == window.add_wallet.text()
    window.coverage_widgets["groceries"].setValue(0)
    window.recalculate()
    rows = {window.coverage.item(i, 0).text(): i for i in range(window.coverage.rowCount())}
    assert window.coverage.item(rows["Groceries (US supermarkets)"], 3).text() == "Gap"
    saved = read_json(window.directory / "profile.json")
    assert saved["wallet"] == [c["id"]]
    assert saved["spend"]["groceries"] == 600
    assert saved["coverage"]["groceries"] == 0
    reopened = MainWindow(window.directory, auto_refresh=False)
    assert reopened.wallet.count() == 1
    assert reopened.spend_widgets["dining"].value() == 400
    reopened.close()


def test_search_details_and_remove(window):
    window.search.setText("freedom unlimited")
    assert window.catalog_table.rowCount() == 1
    window.catalog_table.selectRow(0)
    assert window.current_card["id"] == "chase-freedom-unlimited"
    assert "Published signup offers" in window.offer_details.toPlainText()
    assert "Offers reviewed by source" in window.offer_details.toPlainText()
    window.add_wallet_card()
    window.wallet.setCurrentRow(0)
    window.remove_wallet_card()
    assert window.wallet.count() == 0


def test_refresh_failure_is_async_and_preserves_wallet(window, app):
    c = window.by_id["chase-freedom-unlimited"]
    window.show_card(c)
    window.add_wallet_card()
    def fail(url):
        raise OSError("Test offline")
    window.store.fetcher = fail
    window.refresh()
    loop = QEventLoop()
    window.worker.finished.connect(loop.quit)
    QTimer.singleShot(5000, loop.quit)
    loop.exec()
    app.processEvents()
    assert not window.worker.isRunning()
    assert window.wallet.count() == 1
    assert "Refresh failed" in window.freshness.text()
    assert window.refresh_button.isEnabled()


def test_card_settings_choice_saved_shape(window):
    c = window.by_id["bank-of-america-customized-cash-rewards"]
    dialog = CardSettings(c, window.profile, window)
    assert dialog.choices
    options, count = next(iter(dialog.choices.values()))
    from PySide6.QtCore import Qt
    options.item(0).setCheckState(Qt.CheckState.Checked)
    settings = dialog.settings()
    assert any(len(v) == 1 for v in settings["selections"].values())
    dialog.close()


def test_successful_background_refresh_reloads_cards_and_keeps_profile(window, app):
    from tests.test_catalog import fake_fetch
    window.spend_widgets["dining"].setValue(100)
    window.store.fetcher = fake_fetch
    window.refresh()
    loop = QEventLoop()
    window.worker.finished.connect(loop.quit)
    QTimer.singleShot(5000, loop.quit)
    loop.exec()
    app.processEvents()
    assert not window.worker.isRunning()
    assert "sample" in window.by_id
    assert window.spend_widgets["dining"].value() == 100
    assert (window.directory / "catalog.json").exists()
    assert window.refresh_button.isEnabled()


def test_sidebar_navigation_and_spending_updates(window, app):
    window.show()
    assert not window.setup.isHidden()
    assert window.hero.isHidden()
    assert window.recommendations.isHidden()
    QTest.mouseClick(window.navigation['spending'], Qt.MouseButton.LeftButton)
    assert window.active_page == 'spending'
    window.spend_widgets['dining'].setValue(400)
    QTest.qWait(350)
    assert read_json(window.directory / 'profile.json')['spend']['dining'] == 400
    QTest.mouseClick(window.navigation['matches'], Qt.MouseButton.LeftButton)
    assert not window.hero.isHidden()
    assert not window.recommendations.isHidden()
    assert window.setup.isHidden()
    assert window.monthly_metric.value.text() == '$400'
    QTest.mouseClick(window.hero_button, Qt.MouseButton.LeftButton)
    assert window.current_card['id'] == window.matches[0]['card']['id']
    assert window.details_dialog.isVisible()


def test_closed_details_stay_closed_and_reopen_from_keyboard(window, app):
    window.show()
    window.navigate('explore')
    window.search.setText('freedom unlimited')
    window.catalog_table.selectRow(0)
    assert window.details_dialog.isVisible()
    window.detail_tabs.setCurrentIndex(1)
    window.details_dialog.close()
    window.recalculate()
    assert not window.details_dialog.isVisible()
    window.catalog_table.setFocus()
    QTest.keyClick(window.catalog_table, Qt.Key.Key_Return)
    assert window.details_dialog.isVisible()
    # Refreshing the same card preserves the tab the user is reading.
    assert window.detail_tabs.currentIndex() == 1
    QTest.keyClick(window.details_dialog, Qt.Key.Key_Escape)
    assert not window.details_dialog.isVisible()


def test_catalog_issuer_filter_and_no_results(window):
    window.issuer_filter.setCurrentIndex(window.issuer_filter.findData('chase'))
    assert window.explored and all(c['issuerId'] == 'chase' for c in window.explored)
    window.search.setText('no-such-card-123')
    assert window.catalog_table.rowCount() == 0
    assert not window.search_empty.isHidden()
    window.search.clear()
    assert window.catalog_table.rowCount() > 0
    assert window.search_empty.isHidden()


def test_compact_window_keeps_preferences_usable(window, app):
    window.resize(1040, 720)
    window.show()
    app.processEvents()
    assert window.width() == 1040
    for page in window.page_indexes:
        window.navigate(page)
        app.processEvents()
        assert window.pages.currentIndex() == window.page_indexes[page]
        assert window.navigation[page].isChecked()
    for control in (window.points, window.max_fee, window.target):
        assert control.lineEdit().height() >= control.fontMetrics().height()


def test_full_bank_name_finds_customized_cash_and_dated_sources(window):
    window.search.setText('Bank of America customized cash rewards card')
    assert 'bank-of-america-customized-cash-rewards' in {c['id'] for c in window.explored}
    window.show_card(window.by_id['bank-of-america-customized-cash-rewards'])
    sources = window.detail_browsers['Sources'].toPlainText()
    assert 'Issuer terms checked 2026-10-07' in sources
    assert 'Issuer domain' in sources
    assert '6%' in sources
    assert 'excluded' in sources
