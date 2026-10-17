"""Release native windows between tests so theme changes stay isolated."""
import pytest
from PySide6.QtCore import QEvent
from PySide6.QtWidgets import QApplication, QMainWindow


@pytest.fixture(autouse=True)
def cleanup_windows():
    yield
    app = QApplication.instance()
    if app is None:
        return
    # Signal closures can retain closed windows and their widget trees. Qt
    # restyles those trees too when a later test changes the application theme.
    for window in app.topLevelWidgets():
        if isinstance(window, QMainWindow) and window.close():
            window.deleteLater()
    app.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    app.processEvents()
