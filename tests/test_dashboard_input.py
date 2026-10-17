"""Mouse, touch and pen use the same native scene interaction contract."""
import os
os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
from copy import deepcopy
import pytest
from PySide6.QtCore import Qt, QPoint, QPointF, QEvent
from PySide6.QtGui import QTabletEvent, QPointingDevice, QInputDevice
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QLabel
from card_compass.dashboard.grid_canvas import GridCanvas
from card_compass.dashboard.layout_engine import ENGINE


@pytest.fixture
def canvas():
    app=QApplication.instance() or QApplication([])
    view=GridCanvas(lambda frame:QLabel('Body'));view.resize(1000,700)
    view.set_dashboard(dict(id='input',widgets=[dict(id='a',type='notes',title='A',config={})],
        layouts={'md':ENGINE.repair([dict(i='a',x=0,y=0,w=6,h=30)])},settings=dict(compact=False,reducedMotion=True)))
    view.show();view.set_editing(True);QTest.qWait(20)
    yield view
    view.close();view.deleteLater();app.processEvents()


@pytest.mark.parametrize('kind,editing',[('move',True),('corner',True),('corner',False)])
def test_touch_routes_through_viewport_for_drag_and_resize(canvas,kind,editing):
    canvas.set_editing(editing)
    device=QTest.createTouchDevice(QInputDevice.DeviceType.TouchScreen)
    frame=canvas.frames['a'];handle=frame.header if kind=='move' else frame.handles[kind]
    start=canvas.mapFromScene(canvas.items['a'].pos()+QPointF(handle.geometry().center()))
    end=start+QPoint(round(canvas.column_width),20)
    QTest.touchEvent(canvas.viewport(),device).press(0,start,canvas.viewport()).commit();QApplication.processEvents()
    assert canvas.interaction and not canvas.interaction['active']
    QTest.touchEvent(canvas.viewport(),device).move(0,end,canvas.viewport()).commit();QApplication.processEvents()
    assert canvas.interaction['active'] and canvas.placeholder.isVisible()
    QTest.touchEvent(canvas.viewport(),device).release(0,end,canvas.viewport()).commit();QApplication.processEvents()
    assert canvas.interaction is None
    g=canvas.current_layout[0]
    assert (g['x'],g['y'])==(1,2) if kind=='move' else (g['w'],g['h'])==(7,32)


@pytest.mark.parametrize('kind,editing',[('move',True),('bottom',True),('bottom',False)])
def test_pen_routes_through_viewport_for_drag_and_resize(canvas,kind,editing):
    canvas.set_editing(editing)
    device=QPointingDevice('Test pen',12,QInputDevice.DeviceType.Stylus,QPointingDevice.PointerType.Pen,
        QInputDevice.Capability.Position|QInputDevice.Capability.Pressure,1,1)
    frame=canvas.frames['a'];handle=frame.header if kind=='move' else frame.handles[kind]
    start=canvas.mapFromScene(canvas.items['a'].pos()+QPointF(handle.geometry().center()))
    end=start+QPoint(round(canvas.column_width),20)
    for event_type,point in [(QEvent.Type.TabletPress,start),(QEvent.Type.TabletMove,end),(QEvent.Type.TabletRelease,end)]:
        event=QTabletEvent(event_type,device,QPointF(point),QPointF(canvas.viewport().mapToGlobal(point)),.5,0,0,0,0,0,
            Qt.KeyboardModifier.NoModifier,Qt.MouseButton.LeftButton,Qt.MouseButton.NoButton if event_type==QEvent.Type.TabletRelease else Qt.MouseButton.LeftButton)
        QApplication.sendEvent(canvas.viewport(),event)
    assert canvas.interaction is None
    g=canvas.current_layout[0]
    assert (g['x'],g['y'])==(1,2) if kind=='move' else (g['w'],g['h'])==(6,32)


def test_keyboard_activation_distance_locks_and_accessible_announcements(canvas):
    frame=canvas.frames['a'];before=deepcopy(canvas.current_layout)
    start=canvas.mapFromScene(canvas.items['a'].pos()+QPointF(60,24))
    QTest.mousePress(canvas.viewport(),Qt.MouseButton.LeftButton,pos=start)
    QTest.mouseMove(canvas.viewport(),start+QPoint(2,1));QTest.mouseRelease(canvas.viewport(),Qt.MouseButton.LeftButton,pos=start+QPoint(2,1))
    assert canvas.current_layout==before
    QTest.keyClick(frame.header,Qt.Key.Key_Space);QTest.keyClick(frame.header,Qt.Key.Key_Right)
    QTest.keyClick(frame.header,Qt.Key.Key_Down,Qt.KeyboardModifier.ShiftModifier)
    assert 'Column 2' in frame.header.accessibleDescription()
    QTest.keyClick(frame.header,Qt.Key.Key_Return)
    assert canvas.current_layout[0]['x']==1 and canvas.current_layout[0]['h']==31
    assert 'Dropped' in frame.header.accessibleDescription() and not canvas.animations
    dashboard=deepcopy(canvas.dashboard);dashboard['layouts']['md'][0]['locked']=True;canvas.set_dashboard(dashboard)
    QTest.keyClick(frame.header,Qt.Key.Key_Space)
    assert canvas.interaction is None and not frame.header.enabled


@pytest.mark.parametrize('kind',['right','bottom','corner'])
def test_keyboard_resize_without_edit_mode_supports_commit_cancel_and_locks(canvas,kind):
    canvas.set_editing(False)
    frame=canvas.frames['a'];handle=frame.handles[kind]
    original=deepcopy(canvas.current_layout)
    assert handle.enabled and handle.isVisible() and frame.body.isEnabled()
    assert not frame.header.enabled
    changes=[]
    canvas.layoutChanged.connect(lambda key,layout:changes.append(layout))
    QTest.keyClick(handle,Qt.Key.Key_Space)
    QTest.keyClick(handle,Qt.Key.Key_Right)
    QTest.keyClick(handle,Qt.Key.Key_Down)
    QTest.keyClick(handle,Qt.Key.Key_Escape)
    assert canvas.current_layout==original and not changes and canvas.interaction is None
    QTest.keyClick(handle,Qt.Key.Key_Space)
    QTest.keyClick(handle,Qt.Key.Key_Right)
    QTest.keyClick(handle,Qt.Key.Key_Down)
    QTest.keyClick(handle,Qt.Key.Key_Return)
    resized=canvas.current_layout[0]
    assert resized['w']==original[0]['w']+(kind!='bottom')
    assert resized['h']==original[0]['h']+(kind!='right')
    assert resized['x']==original[0]['x'] and resized['y']==original[0]['y']
    assert len(changes)==1 and canvas.interaction is None
    dashboard=deepcopy(canvas.dashboard)
    dashboard['layouts'][canvas.size_key]=deepcopy(canvas.current_layout)
    dashboard['layouts'][canvas.size_key][0]['locked']=True
    canvas.set_dashboard(dashboard)
    locked=deepcopy(canvas.current_layout)
    assert all(not handle.enabled and handle.isHidden() for handle in frame.handles.values())
    canvas.begin('a',QPointF(0,0),kind,keyboard=True)
    assert canvas.interaction is None and canvas.current_layout==locked
