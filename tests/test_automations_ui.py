import os
os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
from datetime import datetime,timezone,timedelta
import json
import time
import pytest
from PySide6.QtCore import QEvent
from PySide6.QtWidgets import QApplication,QMessageBox
from card_compass.app import MainWindow
from card_compass.automations_ui import AutomationEditor,AutomationsDialog
from card_compass.backup import create_backup,read_backup,restore_backup
from tests.test_automations import job


@pytest.fixture
def window(tmp_path):
    app=QApplication.instance() or QApplication([])
    window=MainWindow(tmp_path/'profile',auto_refresh=False)
    window.automation_engine.timer.stop()
    yield window
    deadline=time.monotonic()+5
    while window.automation_engine.busy and time.monotonic()<deadline:app.processEvents();time.sleep(.01)
    window.close();app.sendPostedEvents(None,QEvent.Type.DeferredDelete);app.processEvents()


def wait(engine):
    deadline=time.monotonic()+5
    while engine.worker is not None and time.monotonic()<deadline:QApplication.instance().processEvents();time.sleep(.01)
    assert engine.worker is None


def test_editor_requires_real_destination_and_jobs_start_paused(window,tmp_path):
    editor=AutomationEditor(window);editor.name.setText('Local report');editor.confirm()
    assert editor.job is None and 'folder' in editor.error.text()
    editor.folder.setText(str(tmp_path));editor.confirm()
    assert editor.job and not editor.job['enabled']
    editor.close()
    rule=job(datetime.now(timezone.utc),tmp_path,enabled=False)
    window.automation_engine.store.save(rule)
    dialog=AutomationsDialog(window);assert dialog.jobs.rowCount()==1 and dialog.jobs.item(0,2).text()=='Paused'
    dialog.jobs.selectRow(0);dialog.pause();assert not window.automation_engine.store.jobs()[0]['enabled']
    dialog.close()


def test_async_report_run_history_permission_and_demo_protection(window,tmp_path,monkeypatch):
    rule=job(datetime.now(timezone.utc),tmp_path,enabled=False)
    engine=window.automation_engine;engine.store.save(rule)
    dialog=AutomationsDialog(window);dialog.jobs.selectRow(0)
    monkeypatch.setattr(QMessageBox,'question',lambda *args:QMessageBox.StandardButton.Yes)
    dialog.run_now();wait(engine)
    assert engine.store.history()[0]['status']=='completed' and dialog.runs.rowCount()==1
    dialog.runs.selectRow(0);assert 'schemaVersion' in dialog.detail.toPlainText()
    window.is_demo=True
    dialog.jobs.selectRow(0);dialog.run_now();assert 'disabled' in dialog.status.text()
    assert len(engine.store.history())==1
    dialog.close()


def test_restored_jobs_pause_even_with_same_profile_and_malformed_runs_rejected(window,tmp_path):
    rule=job(datetime.now(timezone.utc),tmp_path)
    store=window.automation_engine.store;store.save(rule)
    path=tmp_path/'data.zip';create_backup(window.directory,path);read_backup(path)
    restore_backup(window.directory,path)
    assert not store.jobs()[0]['enabled']
    with store.connection() as db:
        db.execute('INSERT INTO automation_runs VALUES (?,?,?,?,?,?,?,?,?)',('bad',rule['id'],'bad','bad','','running','','','{}'))
    invalid=tmp_path/'invalid.zip';create_backup(window.directory,invalid)
    with pytest.raises(ValueError):read_backup(invalid)


def test_compact_automation_editor_and_history_fit(window,tmp_path):
    window.is_demo=True
    editor=AutomationEditor(window);editor.resize(850,700);editor.show()
    for _ in range(3):QApplication.instance().processEvents()
    assert hasattr(editor,'demo_banner')
    if hasattr(editor,'text_scroll'):assert editor.text_scroll.horizontalScrollBar().maximum()==0
    editor.close()
    dialog=AutomationsDialog(window);dialog.resize(1020,700);dialog.show()
    for _ in range(3):QApplication.instance().processEvents()
    assert dialog.jobs.viewport().height()>150
    if hasattr(dialog,'text_scroll'):assert dialog.text_scroll.horizontalScrollBar().maximum()==0
    dialog.close()
