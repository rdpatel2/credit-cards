import os
os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
from copy import deepcopy
from pathlib import Path
from tempfile import TemporaryDirectory
import pytest
from PySide6.QtWidgets import QApplication
from PySide6.QtTest import QTest
from card_compass.app import MainWindow
from card_compass.investments import initial_workspace, validate_workspace
from card_compass.dashboard.widget_registry import REGISTRY
from card_compass.dashboard.layout_engine import intersects


def test_legacy_migration_keeps_ids_notes_trades_and_unknown_configs():
    raw=initial_workspace();raw['version']=1
    dashboards=[*raw['pages'],raw['stockTemplate']]
    for p in dashboards:
        p['layout']=[dict(i=g['i'],x=g['x'],y=g['y']//3,w=g['w'],h=9) for g in p.pop('layouts')['lg']]
        p['settings']['compact']=False
        for w in p['widgets']:
            w.update(w.pop('config'))
    raw['pages'][0]['widgets'][0]['type']='external-missing'
    raw['pages'][0]['widgets'][0]['customExternalConfig']='keep me'
    raw['notes']['home:'+raw['pages'][0]['widgets'][0]['id']]='Existing note'
    before=deepcopy(raw)
    migrated=validate_workspace(raw)
    assert raw==before and migrated['version']==2
    assert migrated['notes']==raw['notes'] and migrated['trades']==raw['trades']
    assert migrated['pages'][0]['widgets'][0]['config']['customExternalConfig']=='keep me'
    assert migrated['pages'][0]['layouts']['lg'][0]['h']==29
    assert set(migrated['pages'][0]['layouts'])=={'lg','md','sm','xs'}


def test_every_registered_widget_builds_without_overflow_and_has_refresh_states(tmp_path):
    app=QApplication.instance() or QApplication([])
    owner=MainWindow(tmp_path,auto_refresh=False);owner.resize(1200,820);owner.show();owner.navigate('stocks')
    p=owner.stocks_page
    for kind in REGISTRY.definitions:
        if kind=='unknown':continue
        ident=p.add_block(kind)
        QTest.qWait(5)
        frame=p.active_canvas().frames[ident]
        assert frame.component is not None, (kind,frame.footer.text())
        assert frame.body.geometry().bottom()<frame.footer.parentWidget().geometry().top()
        assert frame.footer.parentWidget().geometry().bottom()<frame.height()
    owner.close();owner.deleteLater();app.processEvents()


def test_unknown_widget_placeholder_preserves_other_widgets(tmp_path):
    app=QApplication.instance() or QApplication([])
    owner=MainWindow(tmp_path,auto_refresh=False);p=owner.stocks_page
    state=deepcopy(p.store.state);state['pages'][0]['widgets'][0]['type']='custom-component-not-installed';p.commit(state)
    canvas=p.dashboard_page.canvas
    missing=state['pages'][0]['widgets'][0]['id']
    assert 'unavailable' in canvas.frames[missing].component.message_label.text()
    assert len(canvas.frames)==5
    owner.close();owner.deleteLater();app.processEvents()
