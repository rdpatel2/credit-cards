"""Offscreen native-widget workflow tests, using only the bundled snapshot."""
import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtWidgets import QApplication
from PySide6.QtCore import QEventLoop, QTimer
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
    assert "Published signup offers" in window.details.toPlainText()
    assert "Offers reviewed by source" in window.details.toPlainText()
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
