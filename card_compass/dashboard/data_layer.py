"""Shared, deduplicated public data and local investment state subscriptions."""
from copy import deepcopy
import time
from PySide6.QtCore import QObject, Signal, QTimer
from ..market_data import MarketWorker
from ..catalog import read_json, write_json


class DataLayer(QObject):
    changed = Signal(str)
    message = Signal(str)
    def __init__(self, backend, directory, network_enabled=False, parent=None):
        super().__init__(parent)
        self.backend, self.directory = backend, directory
        self.network_enabled = network_enabled
        self.cache = read_json(directory / 'market-cache.json', {}) or {}
        self.cache = {k: dict(v, stale=True) for k, v in self.cache.items() if isinstance(v, dict)}
        self.failures, self.pending, self.loading, self.requested, self.loaded_at = {}, set(), set(), set(), {}
        self.subscriptions,self.permanent={},set()
        self.worker = None
        self.mode=self.state.get('dataMode','auto');self.generation=0
        self.stopping = False
        self.queue_timer = QTimer(self)
        self.queue_timer.setSingleShot(True)
        self.queue_timer.timeout.connect(self.start_requests)
        self.refresh_timer = QTimer(self)
        self.refresh_timer.setInterval(60000)
        self.refresh_timer.timeout.connect(self.refresh_due)
        self.refresh_timer.start()
        self.local_timer = QTimer(self)
        self.local_timer.setSingleShot(True)
        self.local_timer.setInterval(500)
        self.local_timer.timeout.connect(self.flush_local)
        self.local_dirty=False
        self.persist,self.flush_persist=None,None
        self.actions = {}

    @property
    def state(self):
        return self.backend.state

    def data(self, kind, key):
        value = self.cache.get(kind + ':' + key)
        if value and bool(value.get('demo')) == (self.state.get('dataMode') == 'demo'):
            return value
        return None

    def status(self, kind, key):
        request = (kind, key)
        value = self.data(kind, key)
        if request in self.loading or request in self.pending:
            return 'loading'
        if kind + ':' + key in self.failures:
            return 'stale' if value else 'error'
        return 'stale' if value and value.get('stale') else 'ready' if value else 'empty'

    def subscribe(self, requirements, owner=None):
        self.ensure_mode()
        if owner is None:self.permanent.update(requirements)
        else:self.subscriptions[owner]=set(requirements)
        self.update_requested()
        missing = {request for request in requirements if self.status(*request) in ('empty', 'stale') and request not in self.loading and ':'.join(request) not in self.failures}
        if self.network_enabled or self.state.get('dataMode') == 'demo':
            self.pending.update(missing)
            if missing:
                self.queue_timer.start(0)

    def update_requested(self):
        self.requested=self.permanent.union(*self.subscriptions.values())
        self.pending.intersection_update(self.requested)

    def unsubscribe(self,owner):
        self.subscriptions.pop(owner,None);self.update_requested()

    def refresh_due(self):
        if self.stopping or not (self.network_enabled or self.state.get('dataMode')=='demo'):return
        intervals={'quote':300,'news':300,'modules':3600,'earnings':3600}
        now=time.monotonic()
        due={request for request in self.requested if now-self.loaded_at.get(request,0)>=intervals.get(request[0],300)}
        if due:self.refresh(due)

    def refresh(self, requirements=None):
        self.ensure_mode()
        requirements = set(self.requested if requirements is None else requirements)
        if not self.network_enabled and self.state.get('dataMode') != 'demo':
            self.message.emit('Offline session · reconnect, then Refresh to load public data.')
            for kind, key in requirements:
                self.failures[kind + ':' + key] = 'Offline session: reconnect and refresh.'
                self.changed.emit(kind + ':' + key)
            return
        self.pending.update(requirements - self.loading)
        for kind, key in requirements:
            self.failures.pop(kind + ':' + key, None)
            self.changed.emit(kind + ':' + key)
        self.queue_timer.start(0)

    def start_requests(self):
        if self.stopping or not self.pending or (self.worker and self.worker.isRunning()):
            return
        tasks = sorted(self.pending)
        self.pending.clear()
        self.loading.update(tasks)
        if self.worker:self.worker.deleteLater()
        self.worker = MarketWorker(tasks, self.state.get('dataMode', 'auto'), self)
        generation=self.generation
        self.worker.result.connect(lambda kind,key,value:self.result(kind,key,value) if generation==self.generation else None)
        self.worker.failed.connect(lambda kind,key,message:self.failed(kind,key,message) if generation==self.generation else None)
        self.worker.finished.connect(self.finished)
        for kind, key in tasks:
            self.loaded_at[(kind,key)]=time.monotonic()
            self.changed.emit(kind + ':' + key)
        self.worker.start()

    def ensure_mode(self):
        mode=self.state.get('dataMode','auto')
        if mode==self.mode:return
        self.mode=mode;self.generation+=1
        self.loading.clear();self.pending.clear();self.loaded_at.clear();self.failures.clear()
        if self.worker and self.worker.isRunning():self.worker.requestInterruption()
        if self.network_enabled or mode=='demo':self.pending.update(self.requested)

    def result(self, kind, key, value):
        self.cache[kind + ':' + key] = value
        self.loaded_at[(kind, key)] = time.monotonic()
        self.loading.discard((kind, key))
        self.failures.pop(kind + ':' + key, None)
        self.changed.emit(kind + ':' + key)

    def failed(self, kind, key, message):
        self.failures[kind + ':' + key] = message
        self.loading.discard((kind, key))
        if kind + ':' + key in self.cache:
            self.cache[kind + ':' + key]['stale'] = True
        self.changed.emit(kind + ':' + key)

    def finished(self):
        if self.stopping:
            return
        try:
            write_json(self.directory / 'market-cache.json', self.cache)
        except OSError as exc:
            self.message.emit('Market cache could not be saved: ' + str(exc))
        else:
            self.message.emit('Market refresh finished. Unavailable data has a retry action.')
        if self.pending:
            self.queue_timer.start(0)

    def put_note(self, key, value):
        self.state['notes'][key] = value[:20000]
        self.schedule_local()
        self.changed.emit('note:'+key)

    def schedule_local(self):
        self.local_dirty=True
        if self.persist:self.persist()
        else:self.local_timer.start()

    def saved(self,status):
        if status=='Saved':self.local_dirty=False

    def flush_local(self):
        self.local_timer.stop()
        if not self.local_dirty:return
        if self.flush_persist:self.flush_persist();return
        try:
            self.backend.save(self.state)
        except (OSError, ValueError) as exc:
            self.message.emit('Notes could not be saved: ' + str(exc))
        else:self.local_dirty=False

    def notify_local(self):
        self.changed.emit('workspace')

    def action(self, name, *args):
        callback = self.actions.get(name)
        if callback:
            return callback(*args)

    def shutdown(self):
        self.stopping = True
        self.queue_timer.stop();self.refresh_timer.stop()
        self.flush_local()
        if self.worker and self.worker.isRunning():
            self.worker.requestInterruption();self.worker.wait()
