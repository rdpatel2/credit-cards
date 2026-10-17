import os
os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
from copy import deepcopy
from pathlib import Path
from tempfile import TemporaryDirectory
import pytest
from PySide6.QtCore import Qt, QPoint, QPointF, QRect, QRectF
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
        import inspect
        assert list(inspect.signature(REGISTRY.get(kind).Component).parameters)==['config','size','isEditing','onConfigChange']
        assert frame.component is not None, (kind,frame.footer.text())
        assert frame.body.geometry().bottom()<frame.footer.parentWidget().geometry().top()
        assert frame.footer.parentWidget().geometry().bottom()<frame.height()
    owner.close();owner.deleteLater();app.processEvents()


def test_every_widget_resizes_in_view_mode_and_saves_with_undo_redo(tmp_path):
    app=QApplication.instance() or QApplication([])
    owner=MainWindow(tmp_path,auto_refresh=False);owner.resize(1280,1000);owner.show();owner.navigate('stocks')
    p=owner.stocks_page
    assert not p.edit_layout.isChecked()
    p.set_dashboard_settings({'reducedMotion':True})
    canvas=p.active_canvas()
    for kind in REGISTRY.definitions:
        ident=p.add_block(kind)
        frame=canvas.frames[ident]
        original=deepcopy(canvas.current_layout)
        geometry=next(g for g in original if g['i']==ident)
        handle=frame.handles['corner']
        assert handle.enabled and frame.body.isEnabled() and not frame.header.enabled,kind
        if not frame.refresh_button.isHidden():
            refresh_rect=QRect(frame.refresh_button.mapTo(frame,QPoint()),frame.refresh_button.size())
            assert not any(handle.geometry().intersects(refresh_rect) for handle in frame.handles.values()),kind
        scene_start=canvas.items[ident].pos()+QPointF(handle.geometry().center())
        canvas.ensureVisible(QRectF(scene_start-QPointF(10,10),QPointF(scene_start+QPointF(20,40))),5,5)
        start=canvas.mapFromScene(scene_start)
        delta=QPoint(-round(canvas.column_width),20)
        QTest.mousePress(canvas.viewport(),Qt.MouseButton.LeftButton,pos=start)
        QTest.mouseMove(canvas.viewport(),start+delta)
        assert canvas.interaction and canvas.interaction['active'],kind
        QTest.mouseRelease(canvas.viewport(),Qt.MouseButton.LeftButton,pos=start+delta)
        resized=next(g for g in canvas.current_layout if g['i']==ident)
        assert resized['w']==geometry['w']-1 and resized['h']==geometry['h']+2,kind
        assert frame.body.isEnabled() and not canvas.editing
        assert frame.body.geometry().bottom()<frame.footer.parentWidget().geometry().top(),kind
        after=deepcopy(canvas.current_layout)
        p.layout_store.undo()
        assert canvas.current_layout==original,kind
        p.layout_store.redo()
        assert canvas.current_layout==after,kind
    assert p.layout_store.flush()
    key=canvas.size_key;expected=deepcopy(canvas.current_layout)
    from card_compass.investments import InvestmentStore
    saved=InvestmentStore(tmp_path).state
    assert next(page for page in saved['pages'] if page['id']==p.current_page()['id'])['layouts'][key]==expected
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


def test_runtime_widget_failure_has_retry_without_rebuilding_neighbors(tmp_path):
    app=QApplication.instance() or QApplication([])
    owner=MainWindow(tmp_path,auto_refresh=False);owner.show();owner.navigate('stocks');p=owner.stocks_page
    canvas=p.active_canvas();ident=p.current_page()['widgets'][0]['id'];frames=dict(canvas.frames);frame=frames[ident]
    def crash():raise RuntimeError('Example data renderer failure')
    frame.component.render=crash;frame.component.safe_render()
    assert frame.component is None and 'error' in frame.footer_text
    assert canvas.frames==frames and all(f.component for i,f in frames.items() if i!=ident)
    p.widget_command(p.dashboard_page,ident,'refresh')
    assert frame.component is not None and canvas.frames==frames
    owner.close();owner.deleteLater();app.processEvents()


def test_invalid_cached_chart_is_isolated_before_painting(tmp_path):
    app=QApplication.instance() or QApplication([])
    owner=MainWindow(tmp_path,auto_refresh=False);p=owner.stocks_page
    ident=next(w['id'] for w in p.current_page()['widgets'] if w['type']=='chart')
    p.data_layer.result('quote','AAPL|1mo',dict(price=100,currency='USD',points=[[1,float('nan')]]))
    frame=p.active_canvas().frames[ident]
    assert frame.component is None and 'error' in frame.footer_text
    assert all(f.component for i,f in p.active_canvas().frames.items() if i!=ident)
    owner.close();owner.deleteLater();app.processEvents()
