import time
from pathlib import Path
import pytest
from PySide6.QtCore import QObject, Signal, QByteArray, QBuffer, QIODevice, QTimer
from PySide6.QtGui import QImage, QColor
from PySide6.QtNetwork import QNetworkRequest, QNetworkReply
from PySide6.QtWidgets import QApplication
from PySide6.QtTest import QTest
from card_compass.artwork import ArtworkCache, artwork_url, decode_artwork, MAX_BYTES
from card_compass.catalog import write_json


@pytest.fixture(scope='session')
def app():
    return QApplication.instance() or QApplication([])


def image_bytes(color='#246842', width=120, height=75, format='PNG'):
    image = QImage(width, height, QImage.Format.Format_ARGB32)
    image.fill(QColor(color))
    image.setPixelColor(0, 0, QColor(0, 0, 0, 0))
    buffer = QBuffer()
    buffer.open(QIODevice.OpenModeFlag.WriteOnly)
    assert image.save(buffer, format)
    return bytes(buffer.data())


class Reply(QObject):
    readyRead = Signal()
    downloadProgress = Signal(int, int)
    finished = Signal()

    def __init__(self, raw, status=200):
        super().__init__()
        self.raw = raw
        self.status = status
        self.error_code = QNetworkReply.NetworkError.NoError
        self.done = False

    def readAll(self):
        raw, self.raw = self.raw, b''
        return QByteArray(raw)

    def attribute(self, key):
        return self.status

    def error(self):
        return self.error_code

    def rawHeader(self, header):
        return QByteArray(b'"version-one"' if header == 'ETag' else b'Mon, 05 Oct 2026 12:00:00 GMT')

    def deliver(self):
        if self.done:
            return
        self.downloadProgress.emit(len(self.raw), len(self.raw))
        if self.done:
            return
        self.readyRead.emit()
        if not self.done:
            self.done = True
            self.finished.emit()

    def abort(self):
        if not self.done:
            self.done = True
            self.error_code = QNetworkReply.NetworkError.OperationCanceledError
            self.finished.emit()


class Manager:
    def __init__(self, raw, status=200, automatic=True):
        self.raw, self.status, self.automatic = raw, status, automatic
        self.requests, self.replies = [], []

    def get(self, request):
        self.requests.append(request)
        reply = Reply(self.raw, self.status)
        self.replies.append(reply)
        if self.automatic:
            QTimer.singleShot(0, reply.deliver)
        return reply


def wait_until(app, condition):
    deadline = time.monotonic() + 2
    while not condition() and time.monotonic() < deadline:
        app.processEvents()
        QTest.qWait(5)
    assert condition()


def sample(url='https://example.com/card.png'):
    return {'id': 'sample', 'imageUrl': url}


def test_catalog_relative_reference_and_invalid_urls():
    assert artwork_url(sample('data/assets/cards/sample.png')) == 'https://raw.githubusercontent.com/fuermosi777/rewards/main/data/assets/cards/sample.png'
    for url in ('file:///tmp/card.png', 'http://example.com/a.png', 'data/assets/cards/../../secret', 'https://user:pass@example.com/card.png', '//example.com/card.png'):
        assert artwork_url(sample(url)) is None
    assert artwork_url({}) is None


def test_download_deduplication_disk_cache_and_offline_restart(app, tmp_path):
    manager = Manager(image_bytes())
    cache = ArtworkCache(tmp_path, manager=manager)
    ready = []
    cache.imageReady.connect(ready.append)
    assert cache.pixmap(sample()) is None
    assert cache.pixmap(sample()) is None
    wait_until(app, lambda: bool(ready))
    assert len(manager.requests) == 1
    assert cache.pixmap(sample()).toImage().pixelColor(1, 1) == QColor('#246842')
    assert cache.pixmap(sample()).toImage().pixelColor(0, 0).alpha() == 0
    cache.close()
    offline = ArtworkCache(tmp_path, network_enabled=False)
    assert offline.pixmap(sample()) is not None
    assert not offline.active and not offline.queue
    offline.close()


def test_conditional_check_and_changed_reference(app, tmp_path):
    first = ArtworkCache(tmp_path, manager=Manager(image_bytes()))
    first.pixmap(sample())
    wait_until(app, lambda: not first.queue and not first.active)
    first.close()
    manager = Manager(b'', status=304)
    cache = ArtworkCache(tmp_path, manager=manager)
    assert cache.pixmap(sample()) is not None
    wait_until(app, lambda: len(manager.requests) == 1 and not cache.active)
    request = manager.requests[0]
    assert bytes(request.rawHeader('If-None-Match')) == b'"version-one"'
    assert request.hasRawHeader('If-Modified-Since')
    assert cache.pixmap(sample()) is not None
    assert cache.pixmap(sample('https://example.com/new.png')) is None
    cache.close()


def test_changed_content_replaces_cache_after_explicit_refresh(app, tmp_path):
    manager = Manager(image_bytes())
    cache = ArtworkCache(tmp_path, manager=manager)
    cache.pixmap(sample())
    wait_until(app, lambda: bool(cache.memory))
    manager.raw = image_bytes('#bb4422')
    cache.refresh()
    cache.pixmap(sample())
    wait_until(app, lambda: len(manager.requests) == 2 and not cache.active)
    assert cache.pixmap(sample()).toImage().pixelColor(1, 1) == QColor('#bb4422')
    cache.close()


@pytest.mark.parametrize('raw,status', [(b'<html>blocked</html>', 200), (b'', 404), (b'x' * (MAX_BYTES + 1), 200)])
def test_failed_or_invalid_download_keeps_previous_artwork(app, tmp_path, raw, status):
    cache = ArtworkCache(tmp_path, manager=Manager(raw, status))
    path, meta = cache.paths(artwork_url(sample()))
    path.parent.mkdir(parents=True)
    path.write_bytes(image_bytes())
    before = path.read_bytes()
    assert cache.pixmap(sample()) is not None
    wait_until(app, lambda: cache.manager.requests and not cache.active)
    assert path.read_bytes() == before
    assert cache.pixmap(sample()) is not None
    assert len(cache.manager.requests) == 1
    cache.close()


def test_corrupt_disk_image_is_repaired_and_bad_metadata_ignored(app, tmp_path):
    cache = ArtworkCache(tmp_path, manager=Manager(image_bytes()))
    path, metadata = cache.paths(artwork_url(sample()))
    path.parent.mkdir(parents=True)
    path.write_bytes(b'broken')
    write_json(metadata, ['bad metadata'])
    assert cache.pixmap(sample()) is None
    wait_until(app, lambda: bool(cache.memory))
    assert decode_artwork(path.read_bytes()) is not None
    cache.close()


def test_concurrency_is_bounded_and_close_cancels_queue(app, tmp_path):
    manager = Manager(image_bytes(), automatic=False)
    cache = ArtworkCache(tmp_path, manager=manager)
    for i in range(10):
        cache.pixmap(sample(f'https://example.com/{i}.png'))
    wait_until(app, lambda: len(cache.active) == 4)
    assert len(manager.requests) == 4
    manager.replies[0].deliver()
    assert len(manager.requests) == 5 and len(cache.active) == 4
    cache.close()
    assert not cache.active and not cache.queue
    cache.pixmap(sample('https://example.com/after-close.png'))
    assert len(manager.requests) == 5


def test_decode_rejects_oversized_dimensions_and_supports_webp(app):
    assert decode_artwork(image_bytes(width=4001, height=1001)) is None
    assert decode_artwork(image_bytes(format='WEBP')) is not None


@pytest.mark.parametrize('dpr', [1, 1.25, 1.5, 2, 3])
def test_thumbnail_resampling_preserves_fine_detail_at_display_scale(app, dpr):
    from PySide6.QtGui import QPixmap, QPainter
    from PySide6.QtCore import QRectF
    from card_compass.ui import paint_artwork
    # Thin white strokes cover one third of this source. Undersampling can
    # erase them completely when the image is reduced to a list thumbnail.
    source = QImage(480, 300, QImage.Format.Format_RGB32)
    painter = QPainter(source)
    painter.fillRect(source.rect(), QColor('black'))
    painter.setPen(QColor('white'))
    for x in range(2, 480, 3):
        painter.drawLine(x, 0, x, 299)
    painter.end()
    output = QImage(round(160 * dpr), round(100 * dpr), QImage.Format.Format_RGB32)
    output.setDevicePixelRatio(dpr)
    output.fill(QColor('red'))
    painter = QPainter(output)
    paint_artwork(painter, QRectF(0, 0, 160, 100), QPixmap.fromImage(source))
    painter.end()
    average = sum(output.pixelColor(x, output.height() // 2).red() for x in range(output.width())) / output.width()
    assert average == pytest.approx(85, abs=2)


def test_thumbnail_repaint_uses_updated_source_and_preserves_transparency(app):
    from PySide6.QtGui import QPixmap, QPainter
    from PySide6.QtCore import QRectF
    from card_compass.ui import paint_artwork
    colors = []
    for color in ('#cc3322', '#2266bb'):
        source = QImage(400, 250, QImage.Format.Format_ARGB32)
        source.fill(QColor(color))
        painter = QPainter(source)
        painter.setCompositionMode(QPainter.CompositionMode.CompositionMode_Source)
        painter.fillRect(0, 0, 40, 40, QColor(0, 0, 0, 0))
        painter.end()
        output = QImage(128, 80, QImage.Format.Format_ARGB32)
        output.fill(QColor(0, 0, 0, 0))
        painter = QPainter(output)
        paint_artwork(painter, QRectF(0, 0, 128, 80), QPixmap.fromImage(source))
        painter.end()
        assert output.pixelColor(1, 1).alpha() == 0
        colors.append(output.pixelColor(64, 40).name())
    assert colors == ['#cc3322', '#2266bb']
