"""Qt scene rendering: independent transformed panes, one page scroll owner."""
from copy import deepcopy
import os
from PySide6.QtCore import Qt, Signal, QPointF, QRectF, QTimer, QPropertyAnimation, QEasingCurve
from PySide6.QtGui import QColor, QPen, QPainter
from PySide6.QtWidgets import QGraphicsView, QGraphicsScene, QGraphicsRectItem, QGraphicsLineItem, QApplication, QStyle
from .layout_engine import ENGINE, ROW_UNIT, breakpoint, dashboard_layout
from .widget_frame import WidgetFrame


class GridCanvas(QGraphicsView):
    layoutChanged = Signal(str, object)
    breakpointChanged = Signal(str)
    command = Signal(str, str)
    announced = Signal(str)
    widgetDropped = Signal(str, str, int, int)

    def __init__(self, component_factory, parent=None, engine=ENGINE):
        super().__init__(parent)
        self.engine = engine
        self.factory = component_factory
        self.setScene(QGraphicsScene(self))
        self.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self.setRenderHint(QPainter.RenderHint.Antialiasing)
        self.setFrameShape(self.Shape.NoFrame)
        self.setAcceptDrops(True)
        self.viewport().setAttribute(Qt.WidgetAttribute.WA_AcceptTouchEvents)
        self.frames, self.items, self.animations = {}, {}, {}
        self.dashboard = None
        self.size_key = breakpoint(self.viewport().width())
        self.editing = False
        self.interaction = None
        self.current_layout = []
        self.margin = 8
        self.gap = 12
        self.reduced_motion = False
        self.gallery_drop = None
        self.placeholder = QGraphicsRectItem()
        self.placeholder.setZValue(90)
        self.placeholder.setPen(QPen(QColor('#4a9eff'), 2, Qt.PenStyle.DashLine))
        self.placeholder.setBrush(QColor(74, 158, 255, 25))
        self.placeholder.hide()
        self.scene().addItem(self.placeholder)
        self.indicator = QGraphicsLineItem()
        self.indicator.setPen(QPen(QColor('#4a9eff'), 4))
        self.indicator.setZValue(200)
        self.indicator.hide()
        self.scene().addItem(self.indicator)
        self.edge_scroll = QTimer(self)
        self.edge_scroll.setInterval(30)
        self.edge_scroll.timeout.connect(self.auto_scroll)

    @property
    def column_width(self):
        return max(1, (self.viewport().width() - 2 * self.margin + self.gap) / 12)

    def rect_for(self, g):
        return QRectF(self.margin + g['x'] * self.column_width, self.margin + g['y'] * ROW_UNIT,
                      max(20, g['w'] * self.column_width - self.gap), max(50, g['h'] * ROW_UNIT - self.gap))

    def set_dashboard(self, dashboard):
        if self.interaction:
            self.cancel()
        changed = not self.dashboard or self.dashboard['id'] != dashboard['id']
        self.dashboard = deepcopy(dashboard)
        settings = dashboard.get('settings', {})
        self.gap = settings.get('gap', 12)
        self.reduced_motion = settings.get('reducedMotion', False) or bool(os.getenv('QT_REDUCED_MOTION')) or not self.style().styleHint(QStyle.StyleHint.SH_Widget_Animate)
        self.size_key = breakpoint(self.viewport().width())
        layout = dashboard_layout(self.dashboard, self.size_key)
        self.current_layout = deepcopy(layout)
        blocks = {w['id']: w for w in dashboard['widgets']}
        for ident in list(self.items):
            if ident not in blocks or changed or self.frames[ident].block['type'] != blocks[ident]['type']:
                proxy = self.items.pop(ident)
                self.frames.pop(ident)
                self.scene().removeItem(proxy)
                proxy.deleteLater()
        for ident, block in blocks.items():
            if ident not in self.items:
                frame = WidgetFrame(block, self.factory)
                proxy = self.scene().addWidget(frame)
                proxy.setAcceptTouchEvents(True)
                self.items[ident] = proxy
                self.frames[ident] = frame
                frame.command.connect(self.command)
                self.bind_handle(ident, frame.header)
                for handle in frame.handles.values():
                    self.bind_handle(ident, handle)
            else:
                frame = self.frames[ident]
                if frame.block != block:
                    frame.block = deepcopy(block)
                    if frame.component and hasattr(frame.component, 'set_config'):
                        frame.guarded(lambda f=frame, b=block: f.component.set_config(b.get('config', {})))
        self.apply_layout(layout)
        self.set_editing(self.editing)
        if changed:
            self.verticalScrollBar().setValue(0)

    def bind_handle(self, ident, handle):
        handle.started.connect(lambda point, kind, i=ident: self.begin(i, point, kind))
        handle.moved.connect(self.pointer_move)
        handle.finished.connect(self.finish)
        handle.cancelled.connect(self.cancel)
        handle.fitted.connect(lambda i=ident: self.auto_fit(i))
        handle.keyed.connect(lambda key, modifiers, i=ident: self.key_move(i, key, modifiers))

    def set_editing(self, editing):
        if not editing and self.interaction:
            self.cancel()
        self.editing = editing
        geometry = {g['i']: g for g in self.current_layout}
        for ident, frame in self.frames.items():
            frame.set_editing(editing, geometry.get(ident, {}).get('locked', False))
        self.viewport().update()

    def apply_layout(self, layout, animate=False, skip=None, extent=True):
        self.current_layout = deepcopy(layout)
        for g in layout:
            ident = g['i']
            if ident == skip or ident not in self.items:
                continue
            proxy = self.items[ident]
            rect = self.rect_for(g)
            proxy.resize(rect.size())
            self.frames[ident].set_density(self.dashboard.get('settings',{}).get('density','comfortable'))
            destination = rect.topLeft()
            old_animation = self.animations.pop(ident, None)
            if old_animation:
                old_animation.stop()
            if animate and not self.reduced_motion and proxy.pos() != destination:
                animation = QPropertyAnimation(proxy, b'pos', self)
                animation.setDuration(100)
                animation.setEasingCurve(QEasingCurve.Type.OutCubic)
                animation.setStartValue(proxy.pos())
                animation.setEndValue(destination)
                self.animations[ident] = animation
                animation.start()
            else:
                proxy.setPos(destination)
        if extent:
            bottom = max((self.rect_for(g).bottom() for g in layout), default=200)
            self.setSceneRect(0, 0, self.viewport().width(), max(self.viewport().height(), bottom + 28))

    def scene_point(self, global_point):
        return self.mapToScene(self.viewport().mapFromGlobal(global_point.toPoint()))

    def begin(self, ident, global_point, kind='move', keyboard=False):
        if not self.editing:
            return
        g = next(g for g in self.current_layout if g['i'] == ident)
        if g.get('locked'):
            return
        if self.interaction:
            self.cancel()
        for animation in self.animations.values():
            animation.stop()
        self.animations.clear()
        self.apply_layout(self.current_layout)
        self.interaction = dict(ident=ident, kind=kind, original=deepcopy(self.current_layout),
                                origin=QPointF(global_point), last=QPointF(global_point), geometry=deepcopy(g),
                                offset=self.scene_point(global_point) - self.items[ident].pos(), active=keyboard,
                                preview=deepcopy(self.current_layout), extent=self.sceneRect(), keyboard=keyboard)
        if keyboard:
            self.lift()

    def lift(self):
        i = self.interaction
        proxy = self.items[i['ident']]
        proxy.setZValue(100)
        proxy.setScale(1.015 if i['kind'] == 'move' and not self.reduced_motion else 1)
        self.placeholder.show()
        self.frames[i['ident']].announce('Picked up ' + self.frames[i['ident']].block['title'])
        if not i['keyboard']:
            self.edge_scroll.start()

    def pointer_move(self, global_point):
        i = self.interaction
        if not i:
            return
        i['last'] = QPointF(global_point)
        delta = i['last'] - i['origin']
        if not i['active'] and abs(delta.x()) + abs(delta.y()) < 5:
            return
        if not i['active']:
            i['active'] = True
            self.lift()
        point = self.scene_point(i['last'])
        g = deepcopy(i['geometry'])
        if i['kind'] == 'move':
            destination = point - i['offset']
            g.update(x=round((destination.x() - self.margin) / self.column_width),
                     y=round((destination.y() - self.margin) / ROW_UNIT))
            # Bound a gesture to the existing page plus one viewport, never feedback from reflow.
            g['y'] = min(g['y'], round((i['extent'].height() + self.viewport().height()) / ROW_UNIT))
            self.items[i['ident']].setPos(destination)
            self.preview(g, point)
        else:
            if i['kind'] in ('right', 'corner'):
                g['w'] += round(delta.x() / self.column_width)
            if i['kind'] in ('bottom', 'corner'):
                g['h'] += round(delta.y() / ROW_UNIT)
            self.preview(g)

    def preview(self, geometry, point=None):
        i = self.interaction
        compact = self.dashboard.get('settings', {}).get('compact', True)
        self.indicator.hide()
        target = None
        if point is not None:
            target = next((g for g in i['original'] if g['i'] != i['ident'] and self.rect_for(g).contains(point)), None)
        if target:
            rect = self.rect_for(target)
            fraction_x = (point.x() - rect.left()) / rect.width()
            fraction_y = (point.y() - rect.top()) / rect.height()
            if fraction_x < .22 or fraction_x > .78:
                side = 'left' if fraction_x < .22 else 'right'
                layout = self.engine.split(i['original'], i['ident'], target['i'], side, compact)
                x = rect.left() if side == 'left' else rect.right()
                self.indicator.setLine(x, rect.top(), x, rect.bottom())
            else:
                after = fraction_y >= .5
                geometry.update(x=target['x'], w=target['w'], y=target['y'] + target['h'] if after else target['y'])
                layout = self.engine.move(i['original'], i['ident'], geometry, compact)
                y = rect.bottom() if after else rect.top()
                self.indicator.setLine(rect.left(), y, rect.right(), y)
            self.indicator.show()
        else:
            layout = self.engine.move(i['original'], i['ident'], geometry, compact)
        i['preview'] = layout
        self.apply_layout(layout, animate=True, skip=i['ident'] if i['kind'] == 'move' else None, extent=False)
        placed = next(g for g in layout if g['i'] == i['ident'])
        self.placeholder.setRect(self.rect_for(placed))
        self.announce_position(placed)

    def announce_position(self, g):
        message = f"Column {g['x'] + 1}, row {g['y'] + 1}; width {g['w']}, height {g['h']}."
        self.announced.emit(message)
        self.frames[g['i']].announce(message)

    def finish(self, point=None):
        i = self.interaction
        if not i:
            return
        if point is not None and i['active'] and not i['keyboard']:
            self.pointer_move(point)
        active = i['active']
        layout = deepcopy(i['preview'])
        self.clear_interaction()
        self.apply_layout(layout if active else i['original'])
        if active and layout != i['original']:
            self.layoutChanged.emit(self.size_key, layout)
        self.frames[i['ident']].announce('Dropped ' + self.frames[i['ident']].block['title'])

    def cancel(self):
        i = self.interaction
        if not i:
            return
        self.clear_interaction()
        self.apply_layout(i['original'])
        self.frames[i['ident']].announce('Move cancelled')

    def clear_interaction(self):
        i = self.interaction
        self.interaction = None
        self.edge_scroll.stop()
        self.placeholder.hide()
        self.indicator.hide()
        self.items[i['ident']].setScale(1)
        self.items[i['ident']].setZValue(0)
        self.frames[i['ident']].header.pressed = False
        for handle in self.frames[i['ident']].handles.values():
            handle.pressed = False

    def key_move(self, ident, key, modifiers):
        if key == Qt.Key.Key_Escape:
            self.cancel()
            return
        if key in (Qt.Key.Key_Space, Qt.Key.Key_Return, Qt.Key.Key_Enter):
            if self.interaction:
                self.finish()
            else:
                point = self.frames[ident].header.mapToGlobal(self.frames[ident].header.rect().center())
                self.begin(ident, QPointF(point), keyboard=True)
            return
        directions = {Qt.Key.Key_Left: (-1, 0), Qt.Key.Key_Right: (1, 0), Qt.Key.Key_Up: (0, -1), Qt.Key.Key_Down: (0, 1)}
        if key in directions and self.interaction and self.interaction['ident'] == ident:
            g = next(deepcopy(g) for g in self.interaction['preview'] if g['i'] == ident)
            dx, dy = directions[key]
            if modifiers & Qt.KeyboardModifier.ShiftModifier:
                g.update(w=g['w'] + dx, h=g['h'] + dy)
            else:
                g.update(x=g['x'] + dx, y=g['y'] + dy)
            self.preview(g)
            self.apply_layout(self.interaction['preview'], extent=False)

    def auto_fit(self, ident):
        frame = self.frames[ident]
        content_height = frame.component.sizeHint().height() if frame.component else 140
        if frame.component and hasattr(frame.component, 'content_height'):
            content_height = frame.component.content_height()
        g = next(g for g in self.current_layout if g['i'] == ident)
        layout = self.engine.move(self.current_layout, ident, dict(h=round((content_height + 90 + self.gap) / ROW_UNIT)), self.dashboard.get('settings', {}).get('compact', True))
        self.apply_layout(layout)
        self.layoutChanged.emit(self.size_key, layout)

    def auto_scroll(self):
        i = self.interaction
        if not i:
            return
        point = self.viewport().mapFromGlobal(i['last'].toPoint())
        direction = -1 if point.y() < 35 else 1 if point.y() > self.viewport().height() - 35 else 0
        if 0 <= point.x() <= self.viewport().width() and direction:
            bar = self.verticalScrollBar()
            bar.setValue(bar.value() + 15 * direction)
            self.pointer_move(i['last'])

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if not self.dashboard:
            return
        if self.interaction:
            self.cancel()
        key = breakpoint(self.viewport().width())
        if key != self.size_key:
            self.size_key = key
            self.current_layout = deepcopy(dashboard_layout(self.dashboard, key))
            self.breakpointChanged.emit(key)
        self.apply_layout(self.current_layout)

    def hideEvent(self,event):
        if self.interaction:self.cancel()
        super().hideEvent(event)

    def dragEnterEvent(self,event):
        from .gallery import WIDGET_MIME
        from .widget_registry import REGISTRY
        kind=bytes(event.mimeData().data(WIDGET_MIME)).decode(errors='replace')
        if kind in REGISTRY.definitions and kind!='unknown':
            self.gallery_drop=kind
            event.acceptProposedAction()
        else:event.ignore()

    def dragMoveEvent(self,event):
        if not self.gallery_drop:event.ignore();return
        from .widget_registry import REGISTRY
        point=self.mapToScene(event.position().toPoint())
        definition=REGISTRY.get(self.gallery_drop)
        g=dict(i='__gallery__',x=round((point.x()-self.margin)/self.column_width),y=max(0,round((point.y()-self.margin)/ROW_UNIT)),
               w=12 if self.size_key=='xs' else definition.defaultSize[0],h=definition.defaultSize[1],minW=definition.minSize[0],minH=definition.minSize[1])
        layout=self.engine.move([*self.current_layout,g],g['i'],g,self.dashboard.get('settings',{}).get('compact',True))
        self.placeholder.setRect(self.rect_for(next(g for g in layout if g['i']=='__gallery__')))
        self.placeholder.show()
        event.acceptProposedAction()

    def dragLeaveEvent(self,event):
        self.gallery_drop=None;self.placeholder.hide();event.accept()

    def dropEvent(self,event):
        if not self.gallery_drop:event.ignore();return
        point=self.mapToScene(event.position().toPoint());kind=self.gallery_drop
        self.gallery_drop=None;self.placeholder.hide()
        self.widgetDropped.emit(kind,self.size_key,round((point.x()-self.margin)/self.column_width),max(0,round((point.y()-self.margin)/ROW_UNIT)))
        event.acceptProposedAction()

    def drawBackground(self, painter, rect):
        super().drawBackground(painter, rect)
        if self.editing:
            painter.setPen(QPen(QColor(120, 150, 180, 40), 1))
            for x in range(13):
                pixel = self.margin + x * self.column_width
                painter.drawLine(QPointF(pixel, rect.top()), QPointF(pixel, rect.bottom()))
