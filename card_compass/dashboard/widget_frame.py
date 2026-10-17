"""Native block chrome and unified mouse/touch/pen/keyboard input."""
from PySide6.QtCore import Qt, Signal, QEvent, QPointF
from PySide6 import QtGui
from PySide6.QtGui import QAccessible, QAccessibleEvent, QPainter, QPen, QPalette
from PySide6.QtWidgets import QWidget, QFrame, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QMenu, QLayout, QSizePolicy
from ..education import definition as term_definition


class PointerHandle(QWidget):
    started = Signal(object, str)
    moved = Signal(object)
    finished = Signal(object)
    cancelled = Signal()
    fitted = Signal()
    keyed = Signal(int, object)

    def __init__(self, kind='move', parent=None):
        super().__init__(parent)
        self.kind = kind
        self.enabled = False
        self.pressed = False
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setAttribute(Qt.WidgetAttribute.WA_AcceptTouchEvents)

    def begin(self, point):
        if self.enabled:
            self.pressed = True
            self.setFocus()
            self.started.emit(point, self.kind)

    def move(self, point):
        if self.pressed:
            self.moved.emit(point)

    def end(self, point):
        if self.pressed:
            self.pressed = False
            self.finished.emit(point)

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton and self.enabled:
            self.begin(event.globalPosition())
            event.accept()
        else:
            event.ignore()

    def mouseMoveEvent(self, event):
        self.move(event.globalPosition())
        event.accept() if self.pressed else event.ignore()

    def mouseReleaseEvent(self, event):
        self.end(event.globalPosition())
        event.accept()

    def mouseDoubleClickEvent(self, event):
        if self.enabled and self.kind != 'move':
            self.pressed = False
            self.cancelled.emit()
            self.fitted.emit()
            event.accept()

    def tabletEvent(self, event):
        if not self.enabled:
            event.ignore()
            return
        point = event.globalPosition()
        if event.type() == QEvent.Type.TabletPress:
            self.begin(point)
        elif event.type() == QEvent.Type.TabletMove:
            self.move(point)
        elif event.type() == QEvent.Type.TabletRelease:
            self.end(point)
        event.accept()

    def event(self, event):
        if event.type() in (QEvent.Type.TouchBegin, QEvent.Type.TouchUpdate, QEvent.Type.TouchEnd, QEvent.Type.TouchCancel):
            if not self.enabled:
                return False
            if event.type() == QEvent.Type.TouchCancel:
                self.pressed = False
                self.cancelled.emit()
            elif event.points():
                point = event.points()[0].globalPosition()
                if event.type() == QEvent.Type.TouchBegin:
                    self.begin(point)
                elif event.type() == QEvent.Type.TouchUpdate:
                    self.move(point)
                else:
                    self.end(point)
            event.accept()
            return True
        return super().event(event)

    def keyPressEvent(self, event):
        if self.enabled or (event.modifiers() & (Qt.KeyboardModifier.ControlModifier|Qt.KeyboardModifier.MetaModifier) and event.key() in (Qt.Key.Key_Z,Qt.Key.Key_Y)):
            self.keyed.emit(event.key(), event.modifiers())
            event.accept()
        else:
            super().keyPressEvent(event)

    def paintEvent(self,event):
        super().paintEvent(event)
        if self.kind=='move':
            if self.enabled and self.hasFocus():
                painter=QPainter(self);painter.setPen(QPen(self.palette().color(QPalette.ColorRole.Highlight),1.5));painter.drawRoundedRect(self.rect().adjusted(1,1,-1,-1),4,4)
            return
        if not self.enabled:return
        painter=QPainter(self);painter.setPen(QPen(self.palette().color(QPalette.ColorRole.Text),1.5))
        if self.kind=='corner':
            for length in (4,8,12):painter.drawLine(self.width()-length-3,self.height()-3,self.width()-3,self.height()-length-3)
        elif self.kind=='right':painter.drawLine(self.width()//2,self.height()//2-8,self.width()//2,self.height()//2+8)
        else:painter.drawLine(self.width()//2-8,self.height()//2,self.width()//2+8,self.height()//2)

    def hideEvent(self, event):
        if self.pressed:
            self.pressed = False
            self.cancelled.emit()
        super().hideEvent(event)

    def focusOutEvent(self,event):
        if self.pressed:
            self.pressed=False
            self.cancelled.emit()
        super().focusOutEvent(event)


class WidgetFrame(QFrame):
    command = Signal(str, str)
    def __init__(self, block, component_factory):
        super().__init__()
        self.block = block
        self.ident = block['id']
        self.editing = False
        self.setObjectName('surface')
        self.setMinimumSize(0, 0)
        self.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Ignored)
        layout = QVBoxLayout(self)
        layout.setSizeConstraint(QLayout.SizeConstraint.SetNoConstraint)
        layout.setContentsMargins(12, 6, 12, 12)
        layout.setSpacing(4)
        self.header = PointerHandle(parent=self)
        self.header.setFixedHeight(38)
        header = QHBoxLayout(self.header)
        header.setContentsMargins(0, 0, 0, 0)
        self.title = QLabel(block['title'])
        self.title.setObjectName('sectionTitle')
        self.title.setMinimumWidth(0)
        self.title.setSizePolicy(QSizePolicy.Policy.Ignored,QSizePolicy.Policy.Preferred)
        self.title.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        header.addWidget(self.title, 1)
        self.confidence = QLabel('')
        self.confidence.setObjectName('muted')
        self.confidence.setAccessibleName('Data mode')
        header.addWidget(self.confidence)
        self.menu_button = QPushButton('⋯')
        self.menu_button.setObjectName('quiet')
        self.menu_button.setFixedWidth(34)
        self.menu_button.setStyleSheet('QPushButton { padding: 0px; } QPushButton::menu-indicator { image: none; width: 0px; }')
        self.menu_button.setAccessibleName('Actions for ' + block['title'])
        self.menu = QMenu(self.menu_button)
        for label, command in [('Configure', 'configure'), ('Rename title', 'rename'), ('Duplicate', 'duplicate'),
                               ('Move to dashboard…', 'move'), ('Copy to dashboard…', 'copy'), ('Refresh', 'refresh'),
                               ('Full-screen / expand', 'expand'), ('Lock / unlock position', 'lock'), ('Remove', 'remove')]:
            self.menu.addAction(label, lambda checked=False, cmd=command: self.command.emit(self.ident, cmd))
        self.menu_button.setMenu(self.menu)
        header.addWidget(self.menu_button)
        layout.addWidget(self.header)
        self.body = QWidget()
        self.body.setMinimumSize(0, 0)
        self.body_layout = QVBoxLayout(self.body)
        self.body_layout.setSizeConstraint(QLayout.SizeConstraint.SetNoConstraint)
        self.body_layout.setContentsMargins(0, 0, 0, 0)
        self.body_layout.setSpacing(4)
        layout.addWidget(self.body, 1)
        footer_row = QWidget()
        footer_layout = QHBoxLayout(footer_row)
        # Leave the always-available corner grip clear of the Refresh button.
        footer_layout.setContentsMargins(0,0,16,0)
        self.footer = QLabel('')
        self.footer.setObjectName('muted')
        self.footer.setFixedHeight(28)
        self.footer.setWordWrap(False)
        footer_layout.addWidget(self.footer,1)
        self.refresh_button = QPushButton('Refresh')
        self.refresh_button.setObjectName('rowAction')
        self.refresh_button.setAccessibleName('Refresh ' + block['title'])
        self.refresh_button.clicked.connect(lambda: self.command.emit(self.ident, 'refresh'))
        footer_layout.addWidget(self.refresh_button)
        footer_row.setFixedHeight(32)
        layout.addWidget(footer_row)
        self.footer_text = ''
        self.component = None
        self.factory = component_factory
        self.build_component()
        self.handles = {}
        for kind in ('right', 'bottom', 'corner'):
            handle = PointerHandle(kind, self)
            handle.setAccessibleName('Resize ' + block['title'] + ' ' + kind)
            handle.setCursor(Qt.CursorShape.SizeHorCursor if kind == 'right' else Qt.CursorShape.SizeVerCursor if kind == 'bottom' else Qt.CursorShape.SizeFDiagCursor)
            handle.setToolTip('Drag to resize; double-click to fit content. Keyboard: Space, arrows, then Enter to apply or Escape to cancel.')
            self.handles[kind] = handle
        self.header.setAccessibleName('Move ' + block['title'])
        self.header.setToolTip('Edit layout to move. Space picks up; arrows move; Shift + arrows resize; Enter drops; Escape cancels.')
        self.set_editing(False)

    def build_component(self):
        try:
            self.component = self.factory(self)
            self.body_layout.addWidget(self.component)
            if hasattr(self.component,'retry_button') and not self.refresh_button.isHidden():self.component.retry_button.hide()
            if getattr(self.component, 'last_error', None):
                raise RuntimeError(self.component.last_error)
        except Exception as exc:
            self.show_error(exc)

    def guarded(self, callback):
        try:
            callback()
        except Exception as exc:
            self.show_error(exc)

    def show_error(self, exc):
        while self.body_layout.count():
            item = self.body_layout.takeAt(0)
            if item.widget():
                item.widget().hide()
                item.widget().deleteLater()
        self.component = None
        self.refresh_button.hide()
        error = QLabel('This widget could not load. Your other widgets are still available.')
        error.setWordWrap(True)
        error.setToolTip(str(exc))
        self.body_layout.addWidget(error)
        retry = QPushButton('Retry widget')
        retry.clicked.connect(self.retry)
        self.body_layout.addWidget(retry)
        self.set_footer('Widget error · retry to reload')

    def set_footer(self, message):
        self.footer_text = message
        badge = 'DEMO' if 'DEMO · illustrative only' in message else 'CACHED' if 'Cached data' in message else 'PUBLIC' if 'Public data · may be delayed' in message else 'NOT LOADED' if 'Not loaded' in message else ''
        self.confidence.setText(badge)
        self.confidence.setToolTip(message)
        self.confidence.setVisible(bool(badge))
        self.footer.setToolTip(message)
        self.footer.setText(self.footer.fontMetrics().elidedText(message, Qt.TextElideMode.ElideRight, max(30,self.footer.width())))

    def retry(self):
        while self.body_layout.count():
            item = self.body_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        self.build_component()

    def set_editing(self, editing, locked=False):
        self.editing = editing
        self.header.enabled = editing and not locked
        self.header.setFocusPolicy(Qt.FocusPolicy.StrongFocus if self.header.enabled else Qt.FocusPolicy.NoFocus)
        self.header.setCursor(Qt.CursorShape.OpenHandCursor if self.header.enabled else Qt.CursorShape.ArrowCursor)
        self.title.setText(('⠿  ' if self.header.enabled else '🔒  ' if locked else '') + self.block['title'])
        self.title.setToolTip(term_definition(self.block['title']) or self.block['title'])
        self.header.setAccessibleName('Move '+self.block['title'])
        self.menu_button.setAccessibleName('Actions for '+self.block['title'])
        self.body.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, editing)
        self.body.setEnabled(not editing)
        if self.component:
            self.component.isEditing = editing
        for handle in self.handles.values():
            handle.enabled = not locked
            handle.setFocusPolicy(Qt.FocusPolicy.StrongFocus if handle.enabled else Qt.FocusPolicy.NoFocus)
            handle.setVisible(handle.enabled)

    def set_density(self,density):
        padding=8 if density=='compact' else 12
        self.layout().setContentsMargins(padding,6,padding,12)
        self.header.setFixedHeight(32 if density=='compact' else 38)

    def announce(self, message):
        self.setAccessibleDescription(message)
        self.header.setAccessibleDescription(message)
        announcement=getattr(QtGui,'QAccessibleAnnouncementEvent',None)
        QAccessible.updateAccessibility(announcement(self.header,message) if announcement else QAccessibleEvent(self.header,QAccessible.Event.DescriptionChanged))

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self.set_footer(self.footer_text)
        if hasattr(self, 'handles'):
            self.handles['right'].setGeometry(self.width() - 12, 45, 12, max(0, self.height() - 58))
            self.handles['bottom'].setGeometry(8, self.height() - 12, max(0, self.width() - 32), 12)
            self.handles['corner'].setGeometry(self.width() - 24, self.height() - 24, 24, 24)
            for handle in self.handles.values():
                handle.raise_()
        if self.component and hasattr(self.component, 'on_size'):
            self.guarded(lambda: self.component.on_size(self.body.size()))
