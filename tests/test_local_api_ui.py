import os
os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
import pytest
from PySide6.QtCore import QEvent,Qt
from PySide6.QtWidgets import QApplication,QMessageBox,QLineEdit
from card_compass.app import MainWindow
from card_compass.local_api_ui import LocalAPIDialog,APITokenDialog


@pytest.fixture
def window(tmp_path):
    app=QApplication.instance() or QApplication([])
    window=MainWindow(tmp_path,auto_refresh=False)
    yield window
    window.close();app.sendPostedEvents(None,QEvent.Type.DeferredDelete);app.processEvents()


def test_opt_in_scopes_secret_display_clear_and_stop_revoke(window,monkeypatch):
    dialog=LocalAPIDialog(window);assert not window.local_api.running
    monkeypatch.setattr(QMessageBox,'question',lambda *args:QMessageBox.StandardButton.No)
    dialog.start();assert not window.local_api.running
    monkeypatch.setattr(QMessageBox,'question',lambda *args:QMessageBox.StandardButton.Yes)
    dialog.start();assert window.local_api.running and '127.0.0.1' in dialog.address.text()
    token=APITokenDialog(window);token.name.setText('Reviewed client');token.issue();assert not token.secret
    token.scopes.item(0).setCheckState(Qt.CheckState.Checked);token.issue()
    secret=token.secret;assert secret and len(window.local_api.token_info()[0]['scopes'])==1
    token.token.setEchoMode(QLineEdit.EchoMode.Normal)
    token.close();assert token.secret=='' and not token.token.text()
    token.token.setReadOnly(False);token.token.undo();assert not token.token.text()
    dialog.reload();assert dialog.tokens.rowCount()==1
    dialog.tokens.selectRow(0);dialog.revoke();assert not window.local_api.tokens
    dialog.stop();assert not window.local_api.running
    dialog.close()


def test_api_exits_with_desktop_and_demo_cannot_enable(window,monkeypatch):
    window.is_demo=True
    dialog=LocalAPIDialog(window);dialog.start()
    assert not window.local_api.running and 'disabled' in dialog.status.text()
    dialog.resize(1000,700);dialog.show()
    for _ in range(3):QApplication.instance().processEvents()
    assert hasattr(dialog,'demo_banner') and dialog.tokens.viewport().height()>120
    if hasattr(dialog,'text_scroll'):assert dialog.text_scroll.horizontalScrollBar().maximum()==0
    dialog.close();window.is_demo=False;window.local_api.start();window.close()
    assert not window.local_api.running
