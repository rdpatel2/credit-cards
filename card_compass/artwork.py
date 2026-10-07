"""Asynchronous, demand-driven card images with a validated persistent cache."""
from collections import deque, OrderedDict
import hashlib
from pathlib import Path
from urllib.parse import urljoin, urlsplit

from PySide6.QtCore import QObject, Signal, QTimer, QUrl, QByteArray, QBuffer, QIODevice
from PySide6.QtGui import QImageReader, QPixmap
from PySide6.QtNetwork import QNetworkAccessManager, QNetworkRequest, QNetworkReply

from .catalog import read_json, write_json

ASSET_ROOT = "https://raw.githubusercontent.com/fuermosi777/rewards/main/"
MAX_BYTES = 2_000_000
MAX_PIXELS = 4_000_000


def artwork_url(card):
    reference = card.get('imageUrl')
    if not isinstance(reference, str) or not reference:
        return None
    if reference.startswith('data/assets/cards/') and '..' not in reference.split('/'):
        reference = urljoin(ASSET_ROOT, reference)
    parsed = urlsplit(reference)
    if parsed.scheme != 'https' or not parsed.hostname or parsed.username or parsed.password:
        return None
    return reference


def decode_artwork(raw):
    if not raw or len(raw) > MAX_BYTES:
        return None
    buffer = QBuffer()
    buffer.setData(QByteArray(raw))
    buffer.open(QIODevice.OpenModeFlag.ReadOnly)
    reader = QImageReader(buffer)
    if bytes(reader.format()).lower() not in (b'png', b'jpeg', b'webp'):
        return None
    size = reader.size()
    if size.width() <= 0 or size.height() <= 0 or size.width() * size.height() > MAX_PIXELS:
        return None
    image = reader.read()
    if image.isNull():
        return None
    # Keep decoded memory bounded even for larger source assets.
    if image.width() > 1000 or image.height() > 1000:
        from PySide6.QtCore import Qt
        image = image.scaled(1000, 1000, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation)
    return QPixmap.fromImage(image)


class ArtworkCache(QObject):
    imageReady = Signal(str)

    def __init__(self, directory: Path, parent=None, network_enabled=True, manager=None):
        super().__init__(parent)
        self.directory = Path(directory) / 'artwork'
        self.network_enabled = network_enabled
        self.manager = manager if manager is not None else QNetworkAccessManager(self)
        self.memory = OrderedDict()
        self.loaded = set()
        self.attempted = set()
        self.queue = deque()
        self.active = {}
        self.closed = False

    def paths(self, url):
        key = hashlib.sha256(url.encode()).hexdigest()
        return self.directory / (key + '.image'), self.directory / (key + '.json')

    def pixmap(self, card):
        url = artwork_url(card)
        if not url:
            return None
        if url not in self.loaded:
            self.loaded.add(url)
            path, _ = self.paths(url)
            try:
                if path.stat().st_size <= MAX_BYTES:
                    pixmap = decode_artwork(path.read_bytes())
                    if pixmap is not None:
                        self._remember(url, pixmap)
            except OSError:
                pass
        if self.network_enabled and not self.closed and url not in self.attempted:
            self.attempted.add(url)
            self.queue.append(url)
            # A paint call must never start nested UI work or block on a request.
            QTimer.singleShot(0, self._pump)
        if url in self.memory:
            self.memory.move_to_end(url)
        return self.memory.get(url)

    def _remember(self, url, pixmap):
        self.memory[url] = pixmap
        self.memory.move_to_end(url)
        while len(self.memory) > 64:
            evicted, _ = self.memory.popitem(last=False)
            self.loaded.discard(evicted)

    def _pump(self):
        while not self.closed and self.queue and len(self.active) < 4:
            url = self.queue.popleft()
            _, metadata_path = self.paths(url)
            metadata = read_json(metadata_path, {})
            if not isinstance(metadata, dict):
                metadata = {}
            request = QNetworkRequest(QUrl(url))
            request.setTransferTimeout(10000)
            request.setAttribute(QNetworkRequest.Attribute.RedirectPolicyAttribute,
                                 QNetworkRequest.RedirectPolicy.NoLessSafeRedirectPolicy)
            request.setRawHeader(b'User-Agent', b'CardCompass/0.1')
            if url in self.memory:
                for key, header in [('etag', b'If-None-Match'), ('last_modified', b'If-Modified-Since')]:
                    value = metadata.get(key)
                    if isinstance(value, str) and value:
                        request.setRawHeader(header, value.encode())
            reply = self.manager.get(request)
            self.active[reply] = (url, bytearray())
            reply.readyRead.connect(lambda r=reply: self._read(r))
            reply.downloadProgress.connect(lambda received, total, r=reply: self._limit(r, received, total))
            reply.finished.connect(lambda r=reply: self._finished(r))

    def _limit(self, reply, received, total):
        if received > MAX_BYTES or total > MAX_BYTES:
            reply.abort()

    def _read(self, reply):
        entry = self.active.get(reply)
        if entry is None:
            return
        raw = entry[1]
        raw.extend(bytes(reply.readAll()))
        if len(raw) > MAX_BYTES:
            reply.abort()

    def _finished(self, reply):
        if reply not in self.active:
            reply.deleteLater()
            return
        self._read(reply)
        if reply not in self.active:
            return
        url, raw = self.active.pop(reply)
        status = reply.attribute(QNetworkRequest.Attribute.HttpStatusCodeAttribute)
        if not self.closed and reply.error() == QNetworkReply.NetworkError.NoError and status == 200:
            pixmap = decode_artwork(raw)
            if pixmap is not None:
                self._remember(url, pixmap)
                path, metadata_path = self.paths(url)
                try:
                    self.directory.mkdir(parents=True, exist_ok=True)
                    temporary = path.with_suffix('.tmp')
                    temporary.write_bytes(raw)
                    temporary.replace(path)
                    write_json(metadata_path, {'url': url,
                        'etag': bytes(reply.rawHeader('ETag')).decode('utf-8', errors='replace'),
                        'last_modified': bytes(reply.rawHeader('Last-Modified')).decode('utf-8', errors='replace')})
                except OSError:
                    # Artwork remains usable in this session if disk caching fails.
                    pass
                self.imageReady.emit(url)
        reply.deleteLater()
        self._pump()

    def refresh(self):
        # Existing images remain visible; only displayed cards are rechecked.
        self.attempted = set(self.active[r][0] for r in self.active) | set(self.queue)

    def close(self):
        self.closed = True
        self.queue.clear()
        for reply in list(self.active):
            reply.abort()
