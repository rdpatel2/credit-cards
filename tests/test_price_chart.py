import os
os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
from datetime import datetime, timezone
from copy import deepcopy
import pytest
from PySide6.QtCore import QPoint, QPointF, Qt
from PySide6.QtTest import QTest
from PySide6.QtGui import QInputDevice
from PySide6.QtWidgets import QApplication
from card_compass.app import MainWindow
from card_compass.dashboard.price_chart import PriceChart


@pytest.fixture
def chart():
    app=QApplication.instance() or QApplication([])
    points=[(datetime(2026,10,day,tzinfo=timezone.utc).timestamp(),value) for day,value in [(1,100),(2,110),(6,105),(9,120)]]
    widget=PriceChart(points);widget.resize(600,260);widget.show();QTest.qWait(5)
    yield widget
    widget.close();widget.deleteLater();app.processEvents()


def test_hover_snaps_to_actual_date_and_price_then_clears(chart):
    # Nonuniform dates select the nearest timestamp, rather than equal index spacing.
    point=chart.point_position(2);QTest.mouseMove(chart,point.toPoint())
    assert chart.hover_index==2
    assert 'Oct 06, 2026' in chart.hover_text and '$105.00' in chart.hover_text
    assert chart.toolTip()==chart.hover_text and chart.accessibleDescription()==chart.hover_text
    QTest.mouseMove(chart,QPoint(5,5));assert chart.hover_index is None


def test_keyboard_range_navigation_and_intraday_exchange_timezone(chart):
    chart.setFocus();QTest.keyClick(chart,Qt.Key.Key_Right);assert chart.hover_index==0
    QTest.keyClick(chart,Qt.Key.Key_Right);assert chart.hover_index==1
    QTest.keyClick(chart,Qt.Key.Key_End);assert chart.hover_index==3
    QTest.keyClick(chart,Qt.Key.Key_Home);assert chart.hover_index==0
    QTest.keyClick(chart,Qt.Key.Key_Escape);assert chart.hover_index is None
    start=datetime(2026,10,9,14,30,tzinfo=timezone.utc).timestamp()
    chart.set_data([(start,10),(start+300,11)],'CAD','America/New_York');chart.select_point(1)
    assert '10:35 EDT' in chart.hover_text and 'CAD 11' in chart.hover_text
    chart.set_data(chart.points,'CAD','America/New_York');assert chart.hover_index==1
    chart.set_data([(start,12)],'CAD');assert chart.hover_index is None


def test_empty_flat_single_unsorted_duplicate_and_tiny_charts_are_safe(chart):
    for points in ([],[(1,100)],[(1,100),(2,100)],[(2,100),(1,100),(2,102)]):
        chart.set_data(points);chart.resize(150,120);chart.grab()
        assert chart.points==sorted(dict(points).items())
        if points:chart.inspect_at(chart.plot_rect().center());assert chart.hover_text
        if points and len(set(p[1] for p in points))==1:assert chart.point_position(0).y()==pytest.approx(chart.plot_rect().center().y())
    with pytest.raises(ValueError):chart.set_data([(1,float('nan'))])


def test_touch_can_inspect_price_without_a_mouse(chart):
    device=QTest.createTouchDevice(QInputDevice.DeviceType.TouchScreen)
    point=chart.point_position(1).toPoint()
    QTest.touchEvent(chart,device).press(0,point,chart).commit();QApplication.processEvents()
    assert chart.hover_index==1 and '$110.00' in chart.hover_text
    QTest.touchEvent(chart,device).release(0,point,chart).commit();QApplication.processEvents()


def test_hover_routes_through_dashboard_viewport_without_layout_changes(tmp_path):
    app=QApplication.instance() or QApplication([])
    owner=MainWindow(tmp_path,auto_refresh=False);owner.resize(1280,900);owner.show();owner.navigate('stocks')
    page=owner.stocks_page;canvas=page.active_canvas()
    key=next(w['id'] for w in page.current_page()['widgets'] if w['type']=='chart')
    page.market_result('quote','AAPL|1mo',dict(points=[(1,100),(86401,120),(172801,110)],currency='USD',demo=False))
    widget=canvas.frames[key].component.chart;QTest.qWait(10)
    before=deepcopy(canvas.current_layout)
    local=widget.point_position(1);point=canvas.items[key].pos()+QPointF(widget.mapTo(canvas.frames[key],local.toPoint()))
    canvas.ensureVisible(point.x(),point.y(),1,1);QTest.qWait(5)
    QTest.mouseMove(canvas.viewport(),canvas.mapFromScene(point));QTest.qWait(5)
    assert widget.hover_index==1 and '$120.00' in widget.hover_text
    assert canvas.current_layout==before and canvas.interaction is None
    page.edit_layout.setChecked(True);widget.select_point(None)
    QTest.mouseMove(canvas.viewport(),canvas.mapFromScene(point)+QPoint(3,0))
    assert widget.hover_index is None and canvas.interaction is None
    owner.close();owner.deleteLater();app.processEvents()
