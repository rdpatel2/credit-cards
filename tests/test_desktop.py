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
    assert not window.details_dialog.isVisible()
    window.open_catalog.click()
    assert window.current_card["id"] == "chase-freedom-unlimited"
    assert "Published signup offers" in window.offer_details.toPlainText()
    assert "Offers reviewed by source" in window.offer_details.toPlainText()
    window.add_wallet_card()
    window.wallet.setCurrentRow(0)
    window.remove_wallet_card()
    assert window.wallet.count() == 0


def test_venture_compound_offer_and_limited_time_label(window):
    from card_compass.offers import VENTURE_ID
    card = window.by_id[VENTURE_ID]
    window.show_card(card)
    text = window.offer_details.toPlainText()
    assert '75,000 miles + $300 Capital One Travel Stays credit' in text
    assert 'Limited-time offer' in text
    assert 'hotel' in text and 'vacation rentals' in text
    assert 'Expires one year after account opening' in text
    assert 'excluded from estimated value and rankings' in text
    assert 'Last successful check:' in text
    card['issuerOfferCheck']['status'] = 'unconfirmed'
    window.show_card(card)
    assert 'Current issuer offer unconfirmed' in window.offer_details.toPlainText()
    assert 'unconfirmed' in window.detail_browsers['Sources'].toPlainText()


def test_offer_additional_perks_are_visible(window):
    card = window.by_id['cardless-bilt-blue']
    window.show_card(card)
    assert 'Paid as Bilt Cash on approval' in window.offer_details.toPlainText()


def test_dashboard_is_startup_page_and_tiles_open_active_cards(window):
    from card_compass.discovery import card_groups
    assert window.active_page == 'overview'
    window.navigate('explore')
    assert not window.dashboard_scroll.isHidden()
    assert window.catalog_table.isHidden()
    assert window.catalog_table.rowCount() == 0
    assert window.catalog_table.columnCount() == 2
    assert [window.catalog_table.horizontalHeaderItem(i).text() for i in range(2)] == ['CARD', 'ANNUAL FEE']
    for key, tile in window.category_tiles.items():
        tile.click()
        assert not window.catalog_table.isHidden()
        assert window.dashboard_scroll.isHidden()
        assert window.explored
        assert all(c['status'] in ('active', 'custom') and key in card_groups(c) for c in window.explored)
        assert window.category_counts[key].text().split()[0] == str(len(window.explored))
    window.back_to_dashboard.click()
    assert not window.dashboard_scroll.isHidden()
    assert window.catalog_table.isHidden()


def test_search_after_tile_spans_all_categories(window):
    window.category_tiles['cashback'].click()
    window.search.setText('Venture Rewards')
    assert 'capital-one-venture-rewards' in {c['id'] for c in window.explored}
    assert window.discovery_group is None
    window.search.clear()
    assert not window.dashboard_scroll.isHidden()


def test_discontinued_cards_never_appear_in_discovery_but_wallet_is_preserved(window):
    from card_compass.discovery import discoverable
    old = next(c for c in window.cards if c['status'] == 'discontinued')
    window.search.setText(old['id'])
    assert window.catalog_table.rowCount() == 0
    window.view_all_cards.click()
    assert len(window.explored) == sum(discoverable(c) for c in window.cards)
    assert all(c['status'] != 'discontinued' for c in window.explored)
    window.profile['wallet'].append(old['id'])
    window.reload_cards()
    assert window.wallet.count() == 1
    assert window.wallet.item(0).text() == old['name']
    assert old['id'] not in {c['id'] for c in window.explored}


def test_category_counts_and_results_update_after_refresh(window):
    window.category_tiles['miles'].click()
    before = len(window.explored)
    ident = window.explored[0]['id']
    next(c for c in window.catalog['cards'] if c['id'] == ident)['status'] = 'discontinued'
    window.reload_cards()
    assert len(window.explored) == before - 1
    assert window.category_counts['miles'].text().split()[0] == str(before - 1)


def test_cached_card_artwork_renders_in_details_and_list(window, app):
    from tests.test_artwork import image_bytes
    from card_compass.artwork import artwork_url
    card = window.by_id['capital-one-venture-rewards']
    path, _ = window.artwork.paths(artwork_url(card))
    path.parent.mkdir(parents=True)
    path.write_bytes(image_bytes('#b83e2d'))
    window.search.setText('Venture Rewards')
    window.show()
    window.show_card(card)
    app.processEvents()
    art = window.detail_art.grab().toImage()
    assert art.pixelColor(art.width() // 2, art.height() // 2).name() == '#b83e2d'
    table_image = window.catalog_table.viewport().grab().toImage()
    assert table_image.pixelColor(50, 40).name() == '#b83e2d'
    assert 'Card artwork' in window.detail_browsers['Sources'].toPlainText()
    assert window.artwork is window.hero_art.artwork
    assert window.artwork is window.wallet.itemDelegate().artwork
    assert window.artwork is window.recommendations.itemDelegateForColumn(0).artwork


def test_monthly_spend_header_sorts_numerically_and_preserves_editors(window, app):
    window.navigate('spending')
    window.show()
    window.spend_widgets['dining'].setValue(100)
    window.spend_widgets['groceries'].setValue(900)
    window.spend_widgets['airfare'].setValue(20)
    window.recalculate()
    header = window.spending.horizontalHeader()
    from PySide6.QtCore import QPoint
    position = QPoint(header.sectionViewportPosition(1) + 30, header.height() // 2)
    ordered = [window.spending.cellWidget(row, 1).value() for row in range(window.spending.rowCount())]
    assert ordered == sorted(ordered, reverse=True)
    assert window.spending.item(0, 0).data(Qt.ItemDataRole.UserRole) == 'groceries'
    window.spend_widgets['airfare'].setValue(1200)
    window.recalculate()
    assert window.spending.item(0, 0).data(Qt.ItemDataRole.UserRole) == 'airfare'
    for row in range(window.spending.rowCount()):
        category = window.spending.item(row, 0).data(Qt.ItemDataRole.UserRole)
        assert window.spending.cellWidget(row, 1) is window.spend_widgets[category]
        assert window.spending.cellWidget(row, 2).isAncestorOf(window.coverage_widgets[category])
        assert window.spending.cellWidget(row, 2).isAncestorOf(window.coverage_modes[category])
    QTest.mouseClick(header.viewport(), Qt.MouseButton.LeftButton, pos=position)
    ordered = [window.spending.cellWidget(row, 1).value() for row in range(window.spending.rowCount())]
    assert ordered == sorted(ordered)
    saved = read_json(window.directory / 'profile.json')
    assert saved['spend']['airfare'] == 1200 and saved['spend']['groceries'] == 900


def test_spending_coverage_colors_match_boundaries_and_auto_rates(window):
    for value, band in [(0, 'low'), (1, 'medium'), (1.99, 'medium'), (2, 'high'), (3, 'high'), (5, 'high')]:
        window.coverage_widgets['dining'].setValue(value)
        window.recalculate()
        assert window.coverage_widgets['dining'].property('coverageBand') == band
    window.profile['wallet'] = ['chase-freedom-unlimited']
    window.reload_cards()
    window.spend_widgets['everything_else'].setValue(200)
    window.coverage_widgets['everything_else'].setValue(-1)
    window.recalculate()
    widget = window.coverage_widgets['everything_else']
    assert widget.property('coverageBand') == 'medium'
    assert '1.50%' in widget.text()
    window.manage_one_off.click()
    assert window.active_page == 'imports' and window.imports_page.tabs.currentIndex() == 0


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
    options.setCurrentIndex(1)
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
    window.navigate('explore')
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
    assert not window.details_dialog.isVisible()
    window.open_catalog.click()
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


@pytest.mark.parametrize('selected,spend_category', [('dining', 'dining'), ('online_shopping', 'online_retail')])
def test_bofa_choice_saves_and_updates_coverage(window, app, selected, spend_category):
    from PySide6.QtWidgets import QDialogButtonBox
    from card_compass.ui import SearchableCategoryBox
    ident = 'bank-of-america-customized-cash-rewards'
    card = window.by_id[ident]
    window.show_card(card)
    window.add_wallet_card()
    window.spend_widgets[spend_category].setValue(200)
    window.spend_widgets['groceries'].setValue(100)
    benefit = card['benefits'][0]['id']
    def choose():
        active = QApplication.activeModalWidget()
        picker, _ = active.choices[benefit]
        assert isinstance(picker, SearchableCategoryBox)
        picker.lineEdit().selectAll()
        query = 'online' if selected == 'online_shopping' else 'dining'
        QTest.keyClicks(picker.lineEdit(), query)
        completer = picker.completer()
        completer.setCompletionPrefix(query)
        completer.complete()
        index = completer.completionModel().index(0, 0)
        QTest.mouseClick(completer.popup().viewport(), Qt.MouseButton.LeftButton,
                         pos=completer.popup().visualRect(index).center())
        QTest.mouseClick(active.findChild(QDialogButtonBox).button(QDialogButtonBox.StandardButton.Save), Qt.MouseButton.LeftButton)
    QTimer.singleShot(0, choose)
    window.edit_card_settings()
    saved = read_json(window.directory / 'profile.json')
    assert saved['card_settings'][ident]['selections'][benefit] == [selected]
    from card_compass.engine import label
    row = next(i for i in range(window.coverage.rowCount()) if window.coverage.item(i, 0).text() == label(spend_category))
    assert window.coverage.item(row, 2).text() == '3.00%'
    reopened = MainWindow(window.directory, auto_refresh=False)
    settings = CardSettings(reopened.by_id[ident], reopened.profile, reopened)
    assert settings.choices[benefit][0].currentData() == selected
    row = next(i for i in range(reopened.coverage.rowCount()) if reopened.coverage.item(i, 0).text() == label(spend_category))
    assert reopened.coverage.item(row, 2).text() == '3.00%'
    settings.close()
    reopened.close()


def test_single_reward_category_cannot_save_without_selection(window):
    from PySide6.QtWidgets import QDialog
    card = window.by_id['bank-of-america-customized-cash-rewards']
    dialog = CardSettings(card, window.profile, window)
    assert dialog.active.isHidden()
    dialog.validate()
    assert dialog.result() != QDialog.DialogCode.Accepted
    assert 'Choose your current rewards category' in dialog.error.text()
    options, _ = next(iter(dialog.choices.values()))
    options.setCurrentIndex(options.findData('dining'))
    dialog.validate()
    assert dialog.result() == QDialog.DialogCode.Accepted
    dialog.close()
