import os
os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
from copy import deepcopy
import pytest
from PySide6.QtCore import Qt, QPoint, QPointF, QMimeData, QTimer
from PySide6.QtGui import QDragEnterEvent, QDragMoveEvent, QDropEvent
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication
from card_compass.app import MainWindow
from card_compass.dashboard.gallery import WIDGET_MIME
from card_compass.dashboard.tools import STARTERS, starter
from card_compass.dashboard.layout_engine import intersects


@pytest.fixture
def page(tmp_path):
    app=QApplication.instance() or QApplication([])
    window=MainWindow(tmp_path,auto_refresh=False);window.resize(1280,900);window.show();window.navigate('stocks')
    QTest.qWait(20)
    yield window.stocks_page
    window.close();window.deleteLater();app.processEvents()


def test_gallery_search_preview_add_and_undo(page):
    before=deepcopy(page.current_page())
    page.add_widget();gallery=page.gallery
    gallery.search.setText('earnings')
    item=gallery.list.currentItem()
    assert item.data(Qt.ItemDataRole.UserRole)=='earnings'
    assert gallery.preview.findChildren(type(page.active_canvas().frames[before['widgets'][0]['id']]))
    gallery.add_button.click()
    assert len(page.current_page()['widgets'])==len(before['widgets'])+1
    page.layout_store.undo();assert page.current_page()==before
    page.layout_store.redo();assert page.current_page()['widgets'][-1]['type']=='earnings'
    gallery.search.setText('no such widget');assert not gallery.add_button.isEnabled()


def test_native_gallery_drop_inserts_at_snapped_position(page):
    page.set_dashboard_settings(dict(compact=False));canvas=page.active_canvas()
    mime=QMimeData();mime.setData(WIDGET_MIME,b'notes')
    point=canvas.mapFromScene(QPointF(8+6*canvas.column_width,900))
    enter=QDragEnterEvent(point,Qt.DropAction.CopyAction,mime,Qt.MouseButton.LeftButton,Qt.KeyboardModifier.NoModifier)
    QApplication.sendEvent(canvas.viewport(),enter);assert enter.isAccepted()
    move=QDragMoveEvent(point,Qt.DropAction.CopyAction,mime,Qt.MouseButton.LeftButton,Qt.KeyboardModifier.NoModifier)
    QApplication.sendEvent(canvas.viewport(),move);assert canvas.placeholder.isVisible()
    drop=QDropEvent(QPointF(point),Qt.DropAction.CopyAction,mime,Qt.MouseButton.LeftButton,Qt.KeyboardModifier.NoModifier)
    QApplication.sendEvent(canvas.viewport(),drop);assert drop.isAccepted()
    block=page.current_page()['widgets'][-1]
    g=next(g for g in page.current_page()['layouts'][canvas.size_key] if g['i']==block['id'])
    assert g['x']==6 and g['y']==89
    assert not canvas.placeholder.isVisible()


def test_native_gallery_beside_drop_splits_and_is_undoable(page):
    canvas=page.active_canvas();before=deepcopy(page.current_page());target=canvas.current_layout[0]
    rect=canvas.rect_for(target);point=canvas.mapFromScene(QPointF(rect.left()+rect.width()*.9,rect.top()+70))
    mime=QMimeData();mime.setData(WIDGET_MIME,b'notes')
    QApplication.sendEvent(canvas.viewport(),QDragEnterEvent(point,Qt.DropAction.CopyAction,mime,Qt.MouseButton.LeftButton,Qt.KeyboardModifier.NoModifier))
    QApplication.sendEvent(canvas.viewport(),QDragMoveEvent(point,Qt.DropAction.CopyAction,mime,Qt.MouseButton.LeftButton,Qt.KeyboardModifier.NoModifier))
    assert canvas.indicator.isVisible()
    QApplication.sendEvent(canvas.viewport(),QDropEvent(QPointF(point),Qt.DropAction.CopyAction,mime,Qt.MouseButton.LeftButton,Qt.KeyboardModifier.NoModifier))
    layout=canvas.current_layout;added=next(g for g in layout if g['i']==page.current_page()['widgets'][-1]['id']);neighbor=next(g for g in layout if g['i']==target['i'])
    assert added['x']==neighbor['x']+neighbor['w'] and added['y']==neighbor['y']
    page.layout_store.undo();assert page.current_page()==before


def test_duplicate_remove_move_copy_dashboard_and_undo(page):
    before=deepcopy(page.store.state['pages'])
    block=page.current_page()['widgets'][0]
    new_id=page.duplicate_widget(block['id']);assert new_id!=block['id']
    page.layout_store.undo();assert page.store.state['pages']==before
    page.layout_store.redo();assert len(page.current_page()['widgets'])==6
    page.remove_widget(new_id);page.layout_store.undo()
    snapshot=deepcopy(page.store.state['pages'])
    page.move_widget_to(new_id,target_id='positions')
    assert not any(w['id']==new_id for w in page.current_page()['widgets'])
    assert any(w['id']==new_id for w in page.store.state['pages'][1]['widgets'])
    page.layout_store.undo();assert page.store.state['pages']==snapshot
    page.move_widget_to(new_id,copy=True,target_id='positions')
    assert any(w['id']==new_id for w in page.current_page()['widgets'])
    assert len(page.store.state['pages'][1]['widgets'])==2
    page.layout_store.undo();assert page.store.state['pages']==snapshot
    dashboard_id=page.duplicate_dashboard()
    assert page.current_page()['id']==dashboard_id
    page.reorder_dashboard(-1)
    assert page.store.state['pages'][-2]['id']==dashboard_id
    page.delete_dashboard();assert not any(d['id']==dashboard_id for d in page.store.state['pages'])
    page.layout_store.undo();assert any(d['id']==dashboard_id for d in page.store.state['pages'])


@pytest.mark.parametrize('name',list(STARTERS))
def test_template_import_settings_reset_are_undoable(page,name):
    before=deepcopy(page.current_page())
    template=starter(name);page.apply_template_data(template)
    assert [w['type'] for w in page.current_page()['widgets']]==STARTERS[name][1]
    for layout in page.current_page()['layouts'].values():
        assert not any(intersects(a,b) for i,a in enumerate(layout) for b in layout[i+1:])
    page.layout_store.undo();assert page.current_page()==before
    page.set_dashboard_settings(dict(compact=False,gap=20,density='compact'))
    assert page.active_canvas().gap==20
    page.layout_store.undo();assert page.current_page()==before
    page.reset_layout();page.layout_store.undo();assert page.current_page()==before
    ident=page.import_dashboard_data(dict(schemaVersion=2,dashboard=template))
    assert page.current_page()['id']==ident
    page.layout_store.undo();assert not any(d['id']==ident for d in page.store.state['pages'])


def test_real_menu_clicks_never_drag_and_control_z_redoes_exactly(page,monkeypatch):
    canvas=page.active_canvas();ident=page.current_page()['widgets'][0]['id'];frame=canvas.frames[ident]
    before=deepcopy(page.current_page());configured=[]
    monkeypatch.setattr(page,'configure_widget',lambda i,p:configured.append(i))
    def choose():frame.menu.actions()[0].trigger();frame.menu.close()
    QTimer.singleShot(0,choose)
    QTest.mouseClick(frame.menu_button,Qt.MouseButton.LeftButton)
    QTest.qWait(20)
    assert configured==[ident] and canvas.interaction is None and page.current_page()==before
    page.edit_layout.setChecked(True)
    header=frame.header
    QTest.keyClick(header,Qt.Key.Key_Space);QTest.keyClick(header,Qt.Key.Key_Right);QTest.keyClick(header,Qt.Key.Key_Return)
    after=deepcopy(page.current_page());assert before!=after
    QTest.keyClick(header,Qt.Key.Key_Z,Qt.KeyboardModifier.ControlModifier)
    assert page.current_page()==before
    QTest.keyClick(header,Qt.Key.Key_Z,Qt.KeyboardModifier.ControlModifier|Qt.KeyboardModifier.ShiftModifier)
    assert page.current_page()==after


def test_layout_commit_reuses_components_without_rendering_widget_data(page,monkeypatch):
    canvas=page.active_canvas();calls=[]
    for frame in canvas.frames.values():
        component=frame.component;render=component.render
        def counted(c=component,r=render):calls.append(c);r()
        monkeypatch.setattr(component,'render',counted)
    page.edit_layout.setChecked(True);header=next(iter(canvas.frames.values())).header
    QTest.keyClick(header,Qt.Key.Key_Space);QTest.keyClick(header,Qt.Key.Key_Right);QTest.keyClick(header,Qt.Key.Key_Return)
    assert page.layout_store.past and not calls
