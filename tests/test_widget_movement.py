"""Native end-to-end block gestures, persistence and bounded drag regressions."""
import os
os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
from copy import deepcopy
import pytest
from PySide6.QtCore import QPoint, QPointF, Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication
from card_compass.app import MainWindow
from card_compass.investments import InvestmentStore, page
from card_compass.dashboard.layout_engine import ENGINE, intersects


@pytest.fixture
def window(tmp_path):
    app=QApplication.instance() or QApplication([])
    owner=MainWindow(tmp_path,auto_refresh=False);owner.resize(1280,900);owner.show();owner.navigate('stocks');QTest.qWait(20)
    yield owner
    owner.close();owner.deleteLater();app.processEvents()


def point(canvas,ident,x=70,y=24):
    return canvas.mapFromScene(canvas.items[ident].pos()+QPointF(x,y))


def begin(canvas,ident):
    QTest.mousePress(canvas.viewport(),Qt.MouseButton.LeftButton,pos=point(canvas,ident))


def move(canvas,destination):
    QTest.mouseMove(canvas.viewport(),canvas.mapFromScene(destination))


def end(canvas,destination):
    QTest.mouseRelease(canvas.viewport(),Qt.MouseButton.LeftButton,pos=canvas.mapFromScene(destination))


def valid(layout):
    assert not any(intersects(a,b) for i,a in enumerate(layout) for b in layout[i+1:])


@pytest.mark.parametrize('context',['home','custom','shared','stock','news'])
def test_pointer_drop_persists_after_restart_for_each_dashboard_context(window,context):
    p=window.stocks_page
    if context in ('shared','stock'):
        p.open_symbol('MSFT')
        if context=='stock':p.customize_stock(True)
    elif context=='custom':
        state=deepcopy(p.store.state);state['pages'].append(page('custom','My dashboard',['notes','chart','quote']));p.page_id='custom';p.commit(state)
    elif context=='news':p.tabs.setCurrentWidget(p.news_page)
    p.set_dashboard_settings({'compact':False,'reducedMotion':True});p.edit_layout.setChecked(True);QTest.qWait(20)
    canvas=p.active_canvas();before=deepcopy(canvas.current_layout);source=before[0];frames=dict(canvas.frames)
    destination=canvas.mapToScene(QPoint(round(8+canvas.column_width*3+70),canvas.viewport().height()-65))
    begin(canvas,source['i']);move(canvas,destination)
    assert canvas.interaction['active'] and canvas.placeholder.isVisible()
    assert p.current_page()['layouts'][canvas.size_key]==before
    end(canvas,destination);after=deepcopy(canvas.current_layout)
    assert after!=before;valid(after);assert canvas.frames==frames
    QTest.qWait(650)
    assert p.saved.text()=='Saved'
    key=canvas.size_key;dashboard_id=p.current_page()['id'];directory=window.directory
    window.close()
    reopened=MainWindow(directory,auto_refresh=False);reopened.resize(1280,900);reopened.show();reopened.navigate('stocks')
    q=reopened.stocks_page
    if context in ('stock','shared'):q.open_symbol('MSFT')
    elif context=='custom':q.page_id='custom';q.reload()
    elif context=='news':q.tabs.setCurrentWidget(q.news_page)
    QTest.qWait(20)
    assert q.current_page()['id']==dashboard_id
    assert q.current_page()['layouts'][key]==after
    assert q.active_canvas().current_layout==after
    reopened.close();reopened.deleteLater()


def test_beside_drop_creates_columns_and_between_drop_inserts(window):
    p=window.stocks_page;p.edit_layout.setChecked(True);canvas=p.active_canvas();before=deepcopy(canvas.current_layout)
    source,target=before[:2];rect=canvas.rect_for(target)
    destination=QPointF(rect.left()+rect.width()*.9,rect.top()+100)
    begin(canvas,source['i']);move(canvas,destination)
    assert canvas.indicator.isVisible()
    end(canvas,destination)
    a=next(g for g in canvas.current_layout if g['i']==source['i']);b=next(g for g in canvas.current_layout if g['i']==target['i'])
    assert a['y']==b['y'] and a['x']==b['x']+b['w'];valid(canvas.current_layout)
    p.layout_store.undo();assert canvas.current_layout==before
    rect=canvas.rect_for(target);destination=QPointF(rect.center().x(),rect.top()+50)
    begin(canvas,source['i']);move(canvas,destination);end(canvas,destination)
    a=next(g for g in canvas.current_layout if g['i']==source['i']);b=next(g for g in canvas.current_layout if g['i']==target['i'])
    assert a['x']==b['x'] and a['y']+a['h']<=b['y'];valid(canvas.current_layout)


def test_preview_and_edge_scroll_never_extend_page_and_escape_restores(window):
    p=window.stocks_page;p.edit_layout.setChecked(True);canvas=p.active_canvas();before=deepcopy(canvas.current_layout)
    extent=canvas.sceneRect();maximum=canvas.verticalScrollBar().maximum();source=before[0]['i'];frames=dict(canvas.frames)
    begin(canvas,source)
    bottom=QPoint(canvas.viewport().width()//2,canvas.viewport().height()-8)
    QTest.mouseMove(canvas.viewport(),bottom)
    for _ in range(35):
        canvas.auto_scroll()
        assert canvas.sceneRect()==extent and canvas.verticalScrollBar().maximum()==maximum
        assert canvas.frames==frames and p.current_page()['layouts'][canvas.size_key]==before
        valid(canvas.interaction['preview'])
    assert canvas.verticalScrollBar().value()>0
    QTest.keyClick(canvas.frames[source].header,Qt.Key.Key_Escape)
    assert canvas.current_layout==before and canvas.interaction is None
    assert not canvas.placeholder.isVisible() and not canvas.edge_scroll.isActive()
    assert canvas.items[source].graphicsEffect() is None


def test_market_status_changes_cannot_resize_or_cancel_active_drag(window):
    p=window.stocks_page;p.edit_layout.setChecked(True);QTest.qWait(20)
    canvas=p.active_canvas();source=canvas.current_layout[0]['i'];extent=canvas.sceneRect();size=canvas.viewport().size()
    begin(canvas,source);move(canvas,QPointF(280,160))
    p.data_layer.message.emit('Long provider message '*50);QTest.qWait(40)
    assert canvas.interaction and canvas.interaction['active']
    assert canvas.viewport().size()==size and canvas.sceneRect()==extent
    QTest.keyClick(canvas.frames[source].header,Qt.Key.Key_Escape)


def test_live_summary_changes_cannot_resize_or_cancel_active_drag(window):
    p=window.stocks_page;p.edit_layout.setChecked(True);QTest.qWait(20)
    canvas=p.active_canvas();source=canvas.current_layout[0]['i'];extent=canvas.sceneRect();size=canvas.viewport().size()
    begin(canvas,source);move(canvas,QPointF(280,160))
    for metric in p.metrics:
        metric.value.setText('$123,456,789.99');metric.caption.setText('PARTIAL MARKET VALUE')
        metric.description.setText('1 of 20 positions priced · Missing: MSFT, AAPL, NVDA '*12)
    p.valuation_details.show();QTest.qWait(40)
    assert canvas.interaction and canvas.interaction['active']
    assert canvas.viewport().size()==size and canvas.sceneRect()==extent
    QTest.keyClick(canvas.frames[source].header,Qt.Key.Key_Escape)


@pytest.mark.parametrize('cancel',['tab','focus','resize','escape','outside'])
def test_interrupted_drag_restores_original_without_saving(window,cancel):
    p=window.stocks_page;p.edit_layout.setChecked(True);canvas=p.active_canvas();before=deepcopy(canvas.current_layout)
    source=before[0]['i'];begin(canvas,source);move(canvas,QPointF(280,160))
    assert canvas.interaction['active']
    if cancel=='tab':p.tabs.setCurrentWidget(p.watchlist_page)
    elif cancel=='focus':p.ticker.setFocus()
    elif cancel=='resize':window.resize(1380,960)
    elif cancel=='escape':QTest.keyClick(canvas.frames[source].header,Qt.Key.Key_Escape)
    else:QTest.mouseRelease(canvas.viewport(),Qt.MouseButton.LeftButton,pos=QPoint(40,canvas.viewport().height()+500))
    QApplication.instance().processEvents()
    assert canvas.interaction is None and canvas.current_layout==before
    assert not canvas.placeholder.isVisible()


@pytest.mark.parametrize('kind',['right','bottom','corner'])
def test_edge_corner_resize_and_double_click_fit(window,kind):
    p=window.stocks_page;p.edit_layout.setChecked(True);canvas=p.active_canvas();g=deepcopy(canvas.current_layout[0]);frame=canvas.frames[g['i']]
    handle=frame.handles[kind];start=canvas.mapFromScene(canvas.items[g['i']].pos()+QPointF(handle.geometry().center()))
    delta=QPoint(round(canvas.column_width) if kind!='bottom' else 0,20 if kind!='right' else 0)
    QTest.mousePress(canvas.viewport(),Qt.MouseButton.LeftButton,pos=start);QTest.mouseMove(canvas.viewport(),start+delta)
    QTest.mouseRelease(canvas.viewport(),Qt.MouseButton.LeftButton,pos=start+delta)
    resized=next(item for item in canvas.current_layout if item['i']==g['i'])
    assert resized['w']==g['w']+(kind!='bottom') and resized['h']==g['h']+(2 if kind!='right' else 0)
    valid(canvas.current_layout)
    QTest.mouseDClick(handle,Qt.MouseButton.LeftButton,pos=handle.rect().center())
    fitted=next(item for item in canvas.current_layout if item['i']==g['i'])
    assert fitted['h']>=fitted['minH'];valid(canvas.current_layout)
    assert frame.body.geometry().bottom()<frame.footer.parentWidget().geometry().top()


def test_repeated_drag_undo_does_not_grow_page_or_recreate_twenty_widgets(window):
    import time
    p=window.stocks_page
    for _ in range(16):p.add_block('notes')
    p.set_dashboard_settings({'reducedMotion':True});p.edit_layout.setChecked(True)
    canvas=p.active_canvas();before=deepcopy(canvas.current_layout);frames=dict(canvas.frames);extent=canvas.sceneRect();source,target=before[:2]
    rect=canvas.rect_for(target);destination=QPointF(rect.center().x(),rect.top()+60)
    durations=[]
    for _ in range(20):
        begin(canvas,source['i'])
        tick=time.perf_counter();move(canvas,destination);durations.append(time.perf_counter()-tick)
        assert canvas.sceneRect()==extent and canvas.frames==frames
        end(canvas,destination);valid(canvas.current_layout)
        p.layout_store.undo()
        assert canvas.current_layout==before and canvas.sceneRect()==extent and canvas.frames==frames
    assert max(durations)<.1, durations


def test_matches_has_visible_padding_between_card_images(window):
    window.spend_widgets['dining'].setValue(200);window.recalculate();window.navigate('matches');QTest.qWait(20)
    table=window.recommendations
    assert table.rowCount()>1
    assert table.visualItemRect(table.item(1,0)).top()-table.visualItemRect(table.item(0,0)).top()>=104
    assert table.rowHeight(0)-80>=24
