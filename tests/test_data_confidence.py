import os
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

import time
from copy import deepcopy
import pytest
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication, QFileDialog, QMessageBox
from card_compass.app import MainWindow
from card_compass.catalog import read_json
from card_compass.dashboard.data_health import DataHealthDialog
from card_compass.dashboard.data_layer import DataLayer
from card_compass.investments import InvestmentStore
from card_compass.market_data import demo


@pytest.fixture
def app():
    return QApplication.instance() or QApplication([])


def test_refresh_attempt_keeps_last_success_and_cached_values(app, tmp_path, monkeypatch):
    layer = DataLayer(InvestmentStore(tmp_path), tmp_path, True)
    request = ('quote', 'AAPL|1d')
    value = dict(demo(*request), demo=False, source='Example public feed', asOf='2026-10-09T20:00:00+00:00')
    layer.result(*request, value)
    layer.subscribe({request}, 'position')
    successful_at = layer.loaded_at[request]
    retrieved_at = layer.health()[0]['retrieved_at']

    class Worker:
        def __init__(self, *args):
            self.result = SignalStub()
            self.failed = SignalStub()
            self.finished = SignalStub()
        def start(self):
            pass
        def isRunning(self):
            return False

    class SignalStub:
        def connect(self, callback):
            pass

    monkeypatch.setattr('card_compass.dashboard.data_layer.MarketWorker', Worker)
    layer.refresh({request})
    layer.queue_timer.stop()
    layer.start_requests()
    assert request in layer.attempted_at
    assert layer.loaded_at[request] == successful_at
    layer.failed(*request, 'Rate limited')
    row = layer.health()[0]
    assert row['retrieved_at'] == retrieved_at and row['error'] == 'Rate limited'
    assert row['status'] == 'stale' and row['mode'] == 'Cached'
    assert layer.data(*request)['price'] == value['price']
    assert value.get('retrievedAt') is None  # caller-owned provider result is untouched
    layer.shutdown()


def test_expired_quote_is_excluded_from_market_total_and_notifies_offline_ui(app, tmp_path):
    window = MainWindow(tmp_path, auto_refresh=False)
    page = window.stocks_page
    page.store.state['trades']['AAPL'] = [dict(id='buy', side='buy', date='2026-01-01', quantity=2, price=100, fees=0)]
    request = ('quote', 'AAPL|1d')
    page.data_layer.result(*request, dict(demo(*request), demo=False, source='Example', price=150))
    page.render_metrics()
    assert page.metrics[2].value.text() == '$300.00'
    page.data_layer.loaded_at[request] = time.monotonic() - 301
    page.data_layer.refresh_due()
    assert page.metrics[2].value.text() == '—'
    assert 'Cached quote' in page.unpriced[0]
    assert page.data_layer.cache['quote:AAPL|1d'].get('stale') is None
    assert next(row for row in page.data_layer.health() if (row['kind'], row['key']) == request)['mode'] == 'Cached'
    window.close()


def test_health_panel_retry_targets_only_selected_feed_and_preserves_last_success(app, tmp_path, monkeypatch):
    window = MainWindow(tmp_path, auto_refresh=False)
    page = window.stocks_page
    layer = page.data_layer
    request = ('quote', 'AAPL|1d')
    layer.result(*request, dict(demo(*request), demo=False, source='Example'))
    layer.failed(*request, 'Provider unavailable')
    before = deepcopy(layer.cache)
    dialog = DataHealthDialog(page)
    row = next(i for i in range(dialog.table.rowCount()) if tuple(dialog.table.item(i, 0).data(Qt.ItemDataRole.UserRole)) == request)
    dialog.table.selectRow(row)
    calls = []
    monkeypatch.setattr(layer, 'refresh', lambda requirements: calls.append(requirements))
    dialog.retry_selected.click()
    assert calls == [{request}]
    assert 'Provider unavailable' in dialog.table.item(row, 4).text()
    assert dialog.table.item(row, 3).toolTip() == before['quote:AAPL|1d']['retrievedAt']
    layer.unsubscribe('portfolio-summary')
    assert layer.cache == before
    dialog.close()
    window.close()


def test_demo_badge_remains_visible_and_metric_has_plain_language_help(app, tmp_path):
    window = MainWindow(tmp_path, auto_refresh=False)
    page = window.stocks_page
    page.store.state['dataMode'] = 'demo'
    page.data_layer.ensure_mode()
    page.data_layer.queue_timer.stop()
    for request in page.data_layer.requested:
        page.data_layer.result(*request, demo(*request))
    frame = next(frame for frame in page.active_canvas().frames.values() if frame.block['type'] == 'chart')
    assert frame.confidence.text() == 'DEMO'
    assert 'As of' in frame.footer_text and 'illustrative only' in frame.footer.toolTip()
    assert 'not their current market value' in page.metrics[0].caption.toolTip()
    page.open_symbol('AAPL')
    metrics = next(frame.component for frame in page.active_canvas().frames.values() if frame.block['type'] == 'metrics')
    for request in metrics.keys:
        page.data_layer.result(*request, demo(*request))
    assert 'earnings per share' in metrics.table.item(0, 0).toolTip().lower()
    page.data_layer.queue_timer.stop()
    window.close()


def test_health_panel_tracks_added_and_removed_feed_subscriptions(app, tmp_path):
    window = MainWindow(tmp_path, auto_refresh=False)
    layer = window.stocks_page.data_layer
    dialog = DataHealthDialog(window.stocks_page)
    original_rows = dialog.table.rowCount()
    request = ('earnings', '2026-10-12')
    layer.subscribe({request}, 'new-widget')
    assert dialog.table.rowCount() == original_rows + 1
    row = next(i for i in range(dialog.table.rowCount()) if tuple(dialog.table.item(i, 0).data(Qt.ItemDataRole.UserRole)) == request)
    dialog.table.selectRow(row)
    layer.unsubscribe('new-widget')
    assert dialog.table.rowCount() == original_rows
    assert not dialog.retry_selected.isEnabled()
    dialog.close()
    window.close()


def test_backup_status_persists_only_after_success_and_reports_missing_file(app, tmp_path, monkeypatch):
    window = MainWindow(tmp_path, auto_refresh=False)
    assert 'No backup recorded' in window.backup_status.text()
    destination = tmp_path / 'portable.zip'
    monkeypatch.setattr(QFileDialog, 'getSaveFileName', lambda *args: (str(destination), ''))
    notices = []
    monkeypatch.setattr(QMessageBox, 'information', lambda *args: notices.append(args[2]))
    monkeypatch.setattr(QMessageBox, 'warning', lambda *args: notices.append(args[2]))
    window.backup_data()
    record = deepcopy(window.profile['last_backup'])
    assert destination.is_file() and 'Last backup:' in window.backup_status.text()
    assert read_json(tmp_path / 'profile.json')['last_backup'] == record
    assert 'Later changes need a new backup' in notices[-1]
    monkeypatch.setattr('card_compass.backup.create_backup', lambda *args: (_ for _ in ()).throw(OSError('disk full')))
    window.backup_data()
    assert window.profile['last_backup'] == record and notices[-1] == 'disk full'
    window.close()
    destination.unlink()
    reopened = MainWindow(tmp_path, auto_refresh=False)
    assert 'File not found' in reopened.backup_status.text()
    reopened.close()
