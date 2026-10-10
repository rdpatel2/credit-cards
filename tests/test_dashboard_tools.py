import os
os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
from copy import deepcopy
import pytest
from PySide6.QtCore import Qt, QPoint, QPointF, QMimeData
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
