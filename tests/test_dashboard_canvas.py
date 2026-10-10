import os
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
from copy import deepcopy
import pytest
from PySide6.QtCore import Qt, QPointF
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QLabel
from card_compass.dashboard.grid_canvas import GridCanvas
from card_compass.dashboard.layout_engine import ENGINE, intersects


@pytest.fixture
def canvas():
    app = QApplication.instance() or QApplication([])
    canvas = GridCanvas(lambda frame: QLabel('Independent body'))
    canvas.resize(980, 650)
    layout = ENGINE.repair([dict(i='a', x=0, y=0, w=6, h=30), dict(i='b', x=6, y=0, w=6, h=50), dict(i='c', x=0, y=30, w=6, h=30)])
    dashboard = dict(id='test', widgets=[dict(id=i, type='notes', title=i, config={}) for i in ('a', 'b', 'c')], layouts={'md':layout},settings=dict(compact=False,reducedMotion=True))
    canvas.set_dashboard(dashboard)
    canvas.show()
    QTest.qWait(20)
    yield canvas
    canvas.close();canvas.deleteLater();app.processEvents()


def point(canvas, x, y):
    return canvas.mapFromScene(QPointF(x,y))


def test_scene_pointer_drag_free_placement_and_escape(canvas):
    canvas.set_editing(True)
    original=deepcopy(canvas.current_layout)
    changes=[]
    canvas.layoutChanged.connect(lambda key, layout: changes.append(layout))
    start=point(canvas,60,26)
    end=point(canvas,60,460)
    QTest.mousePress(canvas.viewport(),Qt.MouseButton.LeftButton,pos=start)
    QTest.mouseMove(canvas.viewport(),end)
    assert canvas.interaction and canvas.interaction['active']
    assert canvas.placeholder.isVisible()
    QTest.mouseRelease(canvas.viewport(),Qt.MouseButton.LeftButton,pos=end)
    assert changes and next(g for g in changes[-1] if g['i']=='a')['y']>0
    assert not any(intersects(a,b) for i,a in enumerate(canvas.current_layout) for b in canvas.current_layout[i+1:])
    header=canvas.frames['a'].header
    QTest.keyClick(header,Qt.Key.Key_Space)
    QTest.keyClick(header,Qt.Key.Key_Right)
    QTest.keyClick(header,Qt.Key.Key_Escape)
    assert canvas.current_layout==changes[-1]


def test_view_mode_menu_and_content_cannot_start_drag(canvas):
    original=deepcopy(canvas.current_layout)
    QTest.mousePress(canvas.viewport(),Qt.MouseButton.LeftButton,pos=point(canvas,60,26))
    QTest.mouseMove(canvas.viewport(),point(canvas,240,120))
    QTest.mouseRelease(canvas.viewport(),Qt.MouseButton.LeftButton,pos=point(canvas,240,120))
    assert canvas.interaction is None and canvas.current_layout==original
    canvas.set_editing(True)
    assert canvas.frames['a'].body.testAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
    assert not canvas.frames['a'].body.isEnabled()


def test_resize_keyboard_and_footer_are_separate(canvas):
    canvas.set_editing(True)
    header=canvas.frames['a'].header
    QTest.keyClick(header,Qt.Key.Key_Space)
    QTest.keyClick(header,Qt.Key.Key_Right,Qt.KeyboardModifier.ShiftModifier)
    QTest.keyClick(header,Qt.Key.Key_Down,Qt.KeyboardModifier.ShiftModifier)
    QTest.keyClick(header,Qt.Key.Key_Return)
    g=next(g for g in canvas.current_layout if g['i']=='a')
    assert g['w']==7 and g['h']==31
    frame=canvas.frames['a']
    assert frame.body.geometry().bottom() < frame.footer.geometry().top()
    assert frame.footer.geometry().bottom() < frame.height()


def test_widget_construction_error_is_isolated():
    app=QApplication.instance() or QApplication([])
    def factory(frame):
        if frame.ident=='a':raise RuntimeError('Example component crash')
        return QLabel('Still working')
    view=GridCanvas(factory)
    view.set_dashboard(dict(id='crash',widgets=[dict(id=i,type='notes',title=i,config={}) for i in ('a','b')],layouts={'xs':[dict(i='a',x=0,y=0,w=12,h=30),dict(i='b',x=0,y=30,w=12,h=30)]}))
    assert 'error' in view.frames['a'].footer.text()
    assert view.frames['b'].component.text()=='Still working'
    view.close();view.deleteLater();app.processEvents()
