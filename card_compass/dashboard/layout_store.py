"""Optimistic dashboard state, bounded full history and debounced durable saves."""
from copy import deepcopy
from PySide6.QtCore import QObject, Signal, QTimer

HISTORY_KEYS = ('pages', 'stockTemplate', 'stockOverrides', 'newsPages', 'templates')


def snapshot(state):
    return {key: deepcopy(state.get(key, {})) for key in HISTORY_KEYS}


class LayoutStore(QObject):
    changed = Signal()
    saveState = Signal(str)
    historyChanged = Signal()
    def __init__(self, backend, validator, parent=None):
        super().__init__(parent)
        self.backend, self.validator = backend, validator
        self.past, self.future = [], []
        self.status = 'Saved'
        self.dirty = False
        self.timer = QTimer(self)
        self.timer.setSingleShot(True)
        self.timer.setInterval(500)
        self.timer.timeout.connect(self.flush)

    def replace(self, candidate, label='Change dashboard', history=True):
        candidate = self.validator(candidate)
        old = snapshot(self.backend.state)
        new = snapshot(candidate)
        if history and old != new:
            self.past.append((label, old))
            self.past = self.past[-100:]
            self.future.clear()
        self.backend.state = candidate
        self.dirty = True
        self.status = 'Saving…'
        self.saveState.emit(self.status)
        self.timer.start()
        self.changed.emit()
        self.historyChanged.emit()

    def mutate(self, label, mutation):
        candidate = deepcopy(self.backend.state)
        mutation(candidate)
        self.replace(candidate, label)

    def undo(self):
        if not self.past:
            return
        label, old = self.past.pop()
        self.future.append((label, snapshot(self.backend.state)))
        candidate = deepcopy(self.backend.state)
        candidate.update(old)
        self.replace(candidate, history=False)

    def redo(self):
        if not self.future:
            return
        label, new = self.future.pop()
        self.past.append((label, snapshot(self.backend.state)))
        candidate = deepcopy(self.backend.state)
        candidate.update(new)
        self.replace(candidate, history=False)

    def flush(self):
        self.timer.stop()
        if not self.dirty:
            return True
        try:
            self.backend.save(self.backend.state)
        except (OSError, ValueError) as exc:
            self.status = 'Failed – retry'
            self.last_error = str(exc)
            self.saveState.emit(self.status)
            return False
        self.dirty = False
        self.status = 'Saved'
        self.saveState.emit(self.status)
        return True
