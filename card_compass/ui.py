"""Desktop presentation primitives: scalable card artwork and a shared theme."""
import hashlib

from PySide6.QtCore import Qt, QRectF, QSize, Signal, QPoint, QPointF, QDate, QEvent
from PySide6.QtGui import QColor, QFont, QPainter, QLinearGradient, QPen, QIcon, QPixmap, QPalette, QPixmapCache
from PySide6.QtSvg import QSvgRenderer
from PySide6.QtWidgets import QWidget, QFrame, QLabel, QVBoxLayout, QStyledItemDelegate, QStyle, QListWidget, QComboBox, QDoubleSpinBox, QDateEdit, QCompleter, QHBoxLayout, QPushButton, QSpinBox, QAbstractSpinBox, QDialog, QDialogButtonBox, QFormLayout, QLayout, QScrollArea, QSizePolicy, QAbstractItemView


class FitDialog(QDialog):
    """Keep form text readable and actions reachable on smaller screens."""
    def available_dialog_size(self):
        return self.screen().availableGeometry().adjusted(16,32,-16,-16).size()

    def prepare_text_layout(self):
        # A maximized window is sized by the window manager. Applying a smaller
        # screen cap here makes Qt continually undo and reapply that geometry.
        if self.isMaximized() or self.isFullScreen() or getattr(self,'_fitting_text',False):return
        self._fitting_text=True
        try:self._prepare_text_layout()
        finally:self._fitting_text=False

    def _prepare_text_layout(self):
        for form in self.findChildren(QFormLayout):
            if form.rowWrapPolicy()!=QFormLayout.RowWrapPolicy.WrapLongRows:
                form.setRowWrapPolicy(QFormLayout.RowWrapPolicy.WrapLongRows)
            alignment=Qt.AlignmentFlag.AlignLeft|Qt.AlignmentFlag.AlignVCenter
            if form.labelAlignment()!=alignment:form.setLabelAlignment(alignment)
        labels=[]
        for label in self.findChildren(QLabel):
            parent=label.parentWidget()
            while parent and parent is not self:
                if isinstance(parent,(QAbstractItemView,QComboBox,QAbstractSpinBox)) or parent.metaObject().className()=='WidgetFrame':break
                parent=parent.parentWidget()
            if parent is not self or label.maximumHeight()<16777215:continue
            if not label.wordWrap():label.setWordWrap(True)
            policy=label.sizePolicy()
            if policy.horizontalPolicy()!=QSizePolicy.Policy.Preferred or policy.verticalPolicy()!=QSizePolicy.Policy.Minimum:
                label.setSizePolicy(QSizePolicy.Policy.Preferred,QSizePolicy.Policy.Minimum)
            labels.append(label)
        layout=self.layout()
        if not layout:return
        layout.activate()
        for label in labels:
            required=max(0,label.heightForWidth(label.width()))
            if label.minimumHeight()!=required:label.setMinimumHeight(required)
        limit=self.available_dialog_size()
        # Explicit minimum sizes must not put dialog actions below the screen.
        self.setMinimumSize(min(self.minimumWidth(),limit.width()),min(self.minimumHeight(),limit.height()))
        minimum=layout.totalMinimumSize()
        if (minimum.width()>limit.width() or minimum.height()>limit.height()) and not hasattr(self,'text_scroll'):
            footer=None
            last=layout.itemAt(layout.count()-1)
            if last and (isinstance(last.widget(),QDialogButtonBox) or last.layout() and last.layout().parent() is layout):
                footer=layout.takeAt(layout.count()-1)
                if footer.layout():footer.layout().setParent(None)
            content=QWidget();content.setObjectName('scrollContent');content.setLayout(layout)
            layout.setSizeConstraint(QLayout.SizeConstraint.SetMinimumSize)
            self.text_scroll=QScrollArea();self.text_scroll.setWidgetResizable(True);self.text_scroll.setWidget(content)
            self.text_scroll.setMinimumSize(0,0)
            root=QVBoxLayout(self);root.addWidget(self.text_scroll,1)
            if footer:
                if footer.widget():root.addWidget(footer.widget())
                else:root.addLayout(footer.layout())
            self.setMinimumSize(0,0)
        target=self.size().expandedTo(self.minimumSizeHint()).boundedTo(limit)
        if self.size()!=target:self.resize(target)

    def showEvent(self,event):
        parent=self.parentWidget()
        while parent is not None:
            if getattr(parent,'is_demo',False):
                if not hasattr(self,'demo_banner') and self.layout() is not None:
                    self.demo_banner=QLabel('DEMO · Synthetic, disposable records. Your personal profile is separate.')
                    self.demo_banner.setObjectName('muted');self.demo_banner.setWordWrap(True)
                    layout=self.layout()
                    if hasattr(layout,'insertWidget'):layout.insertWidget(0,self.demo_banner)
                    else:layout.addWidget(self.demo_banner)
                    self.setWindowTitle('DEMO · '+self.windowTitle())
                break
            parent=parent.parentWidget()
        self.prepare_text_layout();super().showEvent(event)
        if self.isMaximized() or self.isFullScreen():return
        bounds=self.screen().availableGeometry()
        frame=self.frameGeometry()
        dx=max(bounds.left()-frame.left(),min(0,bounds.right()-frame.right()))
        dy=max(bounds.top()-frame.top(),min(0,bounds.bottom()-frame.bottom()))
        if dx or dy:self.move(self.pos()+QPoint(dx,dy))

    def event(self,event):
        if event.type()==QEvent.Type.LayoutRequest and self.isVisible():self.prepare_text_layout()
        return super().event(event)


class ScrollSafeComboBox(QComboBox):
    def wheelEvent(self, event):
        event.ignore()

    def paintEvent(self, event):
        super().paintEvent(event)
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(QPen(self.palette().color(QPalette.ColorRole.Text), 1.6))
        x, y = self.width() - 16, self.height() / 2
        painter.drawLine(QPointF(x - 4, y - 2), QPointF(x, y + 2))
        painter.drawLine(QPointF(x, y + 2), QPointF(x + 4, y - 2))


class ScrollSafeDoubleSpinBox(QDoubleSpinBox):
    def wheelEvent(self, event):
        event.ignore()


class ScrollSafeDateEdit(QDateEdit):
    def wheelEvent(self, event):
        event.ignore()


class MonthPicker(QWidget):
    """Month dropdown and year with visible previous/next controls."""
    dateChanged = Signal(QDate)

    def __init__(self, date, parent=None):
        super().__init__(parent)
        self._date = QDate()
        self.minimum = QDate(1900, 1, 1)
        self.maximum = QDate(2200, 12, 1)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(4)
        self.previous = QPushButton('‹')
        self.next = QPushButton('›')
        for button, name, offset in ((self.previous, 'Previous month', -1), (self.next, 'Next month', 1)):
            button.setObjectName('monthArrow')
            button.setFixedWidth(32)
            button.setAccessibleName(name)
            button.setToolTip(name)
            button.clicked.connect(lambda checked=False, step=offset: self.setDate(self.date().addMonths(step)))
        self.months = ScrollSafeComboBox()
        self.months.setAccessibleName('Month')
        for month in range(1, 13):
            self.months.addItem(QDate(2000, month, 1).toString('MMMM'), month)
        self.year = QSpinBox()
        self.year.setRange(1900, 2200)
        self.year.setButtonSymbols(QAbstractSpinBox.ButtonSymbols.NoButtons)
        self.year.setAccessibleName('Year')
        self.year.setFixedWidth(80)
        for field in (self.previous, self.months, self.year, self.next):
            layout.addWidget(field)
        self.months.currentIndexChanged.connect(self.selected)
        self.year.valueChanged.connect(self.selected)
        self.setDate(date if date.isValid() else QDate.currentDate())

    def selected(self, *_):
        self.setDate(QDate(self.year.value(), self.months.currentData(), 1))

    def date(self):
        return self._date

    def setDate(self, date):
        if not date.isValid():
            return
        date = max(self.minimum, min(self.maximum, QDate(date.year(), date.month(), 1)))
        changed = date != self._date
        self._date = date
        self.months.blockSignals(True)
        self.year.blockSignals(True)
        self.months.setCurrentIndex(date.month() - 1)
        self.year.setValue(date.year())
        self.months.blockSignals(False)
        self.year.blockSignals(False)
        self.previous.setEnabled(date > self.minimum)
        self.next.setEnabled(date < self.maximum)
        if changed:
            self.dateChanged.emit(date)

    def setMinimumDate(self, date):
        self.minimum = QDate(date.year(), date.month(), 1)
        self.setDate(self.date())

    def setMaximumDate(self, date):
        self.maximum = QDate(date.year(), date.month(), 1)
        self.setDate(self.date())


class SearchableCategoryBox(ScrollSafeComboBox):
    """Search existing labels; partial or unknown text is never a category."""
    selectionChanged = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setEditable(True)
        self.setInsertPolicy(QComboBox.InsertPolicy.NoInsert)
        self.lineEdit().setPlaceholderText('Type to search categories…')
        completer = QCompleter(self.model(), self)
        completer.setCaseSensitivity(Qt.CaseSensitivity.CaseInsensitive)
        completer.setFilterMode(Qt.MatchFlag.MatchContains)
        completer.setCompletionMode(QCompleter.CompletionMode.PopupCompletion)
        self.setCompleter(completer)
        completer.activated[str].connect(self.select_completion)
        # Editable text and selecting the same item do not necessarily change
        # the index. Notify consumers whenever the selected value can change.
        self.currentIndexChanged.connect(lambda _: self.selectionChanged.emit())
        self.currentTextChanged.connect(lambda _: self.selectionChanged.emit())
        self.activated.connect(lambda _: self.selectionChanged.emit())

    def select_completion(self, value):
        index = self.findText(value, Qt.MatchFlag.MatchExactly)
        if index >= 0:
            self.setCurrentIndex(index)
            self.setEditText(self.itemText(index))
            self.selectionChanged.emit()

    def currentData(self, role=Qt.ItemDataRole.UserRole):
        index = self.selection_index()
        return self.itemData(index, role) if index >= 0 else None

    def selection_index(self):
        return self.findText(self.currentText(), Qt.MatchFlag.MatchFixedString)

    def selection_valid(self):
        return self.selection_index() >= 0

STYLE = """
QWidget { color: #233d38; font-size: 13px; }
QMainWindow, QWidget#canvas { background: #f5f7f6; }
QWidget#sidebar { background: #102e29; }
QLabel { background: transparent; }
QLabel#brand { color: #ffffff; font-size: 18px; font-weight: 700; }
QLabel#brandTag { color: #b4cdc4; font-size: 11px; }
QLabel#navSection { color: #b4cdc4; font-size: 10px; font-weight: 600; }
QLabel#sideStatus { color: #b4cdc4; font-size: 11px; }
QPushButton#nav { color: #b4cdc4; background: transparent; border: none; border-radius: 8px; text-align: left; padding: 13px 16px; font-size: 13px; }
QPushButton#nav:hover { background: #1a3e34; color: #ffffff; }
QPushButton#nav:checked { background: #254b3e; color: #ccf5df; font-weight: 600; }
QPushButton#nav[navLevel="child"] { padding: 7px 12px; }
QPushButton#sideRefresh { background: #1a3e34; color: #d9eee5; border: 1px solid #355348; }
QLabel#pageTitle { font-size: 29px; font-weight: 700; color: #183a2d; }
QLabel#sectionTitle { font-size: 17px; font-weight: 600; color: #183a2d; }
QLabel#muted { color: #4d6256; }
QLabel#eyebrow { color: #4d6256; font-size: 10px; font-weight: 600; }
QLabel#statValue { font-size: 28px; font-weight: 600; color: #183a2d; }
QLabel#heroTitle { font-size: 23px; font-weight: 600; color: #183a2d; }
QLabel#heroValue { font-size: 33px; font-weight: 600; color: #187148; }
QLabel#identityTitle { color: #233d38; font-size: 14px; font-weight: 600; }
QLabel#identityIssuer { color: #52665a; font-size: 11px; }
QLabel#pill { color: #236349; background: #e6f2e9; border-radius: 5px; padding: 4px 9px; font-size: 11px; }
QFrame#surface, QFrame#stat { background: #ffffff; border: 1px solid #e1e8e2; border-radius: 12px; }
QFrame#hero { background: #edf4ed; border: 1px solid #dce8dd; border-radius: 14px; }
QFrame#empty { background: #ffffff; border: 1px solid #e1e8e2; border-radius: 14px; }
QPushButton { background: #ffffff; border: 1px solid #d8e1da; border-radius: 7px; padding: 10px 17px; color: #334e40; font-weight: 500; }
QPushButton#monthArrow { padding: 3px; font-size: 24px; font-weight: 600; }
QPushButton#rowAction { padding: 6px 10px; }
QPushButton:hover { background: #eef4ef; border-color: #b5cbbb; }
QPushButton#categoryTile { background: #ffffff; border: 1px solid #d8e1da; border-radius: 12px; text-align: left; padding: 0; }
QPushButton#categoryTile:hover { background: #eef5ef; border-color: #7b9e89; }
QPushButton#categoryTile:focus { border: 2px solid #398161; }
QPushButton:pressed { background: #dceadf; }
QPushButton#primary { background: #236347; color: #ffffff; border: 1px solid #236347; }
QPushButton#primary:hover { background: #194f38; }
QPushButton#primary:disabled { background: #dce6df; color: #52665a; border-color: #dce6df; }
QPushButton#quiet { background: transparent; border: none; color: #236347; padding: 8px; }
QPushButton:disabled { color: #52665a; background: #edf1ee; border-color: #edf1ee; }
QPushButton:focus, QLineEdit:focus, QComboBox:focus, QDoubleSpinBox:focus, QSpinBox:focus, QDateEdit:focus { border: 1px solid #398161; }
QLineEdit, QComboBox, QDoubleSpinBox, QSpinBox, QDateEdit { color: #233d38; background: #ffffff; border: 1px solid #dce4dd; border-radius: 7px; padding: 10px 12px; min-height: 20px; selection-background-color: #236347; selection-color: #ffffff; }
QAbstractSpinBox QLineEdit, QComboBox QLineEdit { padding: 0; border: none; border-radius: 0; min-height: 0; background: transparent; }
QComboBox QAbstractItemView { color: #233d38; background: #ffffff; selection-background-color: #236347; selection-color: #ffffff; border: 1px solid #7b9484; }
QComboBox::drop-down { border: none; width: 28px; }
QComboBox::down-arrow { image: none; }
QDoubleSpinBox { min-width: 85px; }
QTableWidget { background: #ffffff; alternate-background-color: #ffffff; border: 1px solid #e1e8e2; border-radius: 10px; gridline-color: #edf1ee; }
QTableWidget::item { padding: 12px 16px; border-bottom: 1px solid #edf1ee; }
QTableWidget::item:hover { background: #f5f9f6; }
QTableWidget::item:selected { background: #edf5ee; color: #183a2d; }
QHeaderView { background: #ffffff; }
QHeaderView::section { background: #fafcf9; color: #4d6256; font-size: 11px; padding: 13px 16px; border: none; border-bottom: 1px solid #e6ece7; }
QTableCornerButton::section { background: white; border: none; }
QListWidget { background: transparent; border: none; outline: none; }
QListWidget::item { border-radius: 12px; background: #ffffff; border: 1px solid #e1e8e2; margin-bottom: 12px; }
QListWidget::item:selected { background: #eef5ef; border: 1px solid #98b6a1; }
QTextBrowser { color: #233d38; border: none; background: #ffffff; padding: 8px; font-size: 13px; }
QDialog { background: #f5f7f6; }
QTabWidget::pane { background: #ffffff; border: 1px solid #e1e8e2; border-radius: 8px; top: -1px; }
QTabBar::tab { color: #52665a; background: transparent; padding: 12px 16px; border-bottom: 2px solid transparent; }
QTabBar::tab:selected { color: #194f38; background: #e6f2e9; border-bottom: 2px solid #236347; }
QScrollArea { border: none; background: transparent; }
QSplitter::handle { background: #dce8dd; border: 1px solid #7b9484; border-radius: 4px; margin: 2px 30px; }
QSplitter::handle:hover { background: #236347; }
QScrollBar:vertical { background: transparent; width: 7px; margin: 3px 0; }
QScrollBar::handle:vertical { background: #7b9484; border-radius: 3px; min-height: 35px; }
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0; }
QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical { background: transparent; }
QCheckBox:disabled { color: #52665a; }
QCheckBox { spacing: 10px; padding: 7px 0; }
QCheckBox::indicator { width: 17px; height: 17px; }
QProgressBar { max-height: 4px; border: none; border-radius: 2px; background: #254b3e; }
QProgressBar::chunk { background: #89c69d; border-radius: 2px; }
QStatusBar { background: #f5f7f6; color: #52665a; }
QToolTip { color: #e5f4ea; background: #183a2d; border: none; padding: 8px; }
"""


def light_palette():
    """Keep native controls consistent with our light surfaces on dark desktops."""
    palette = QPalette()
    colors = {
        'Window': '#f5f7f6', 'WindowText': '#233d38',
        'Base': '#ffffff', 'AlternateBase': '#eef4ef',
        'Text': '#233d38', 'Button': '#ffffff', 'ButtonText': '#233d38',
        'Highlight': '#236347', 'HighlightedText': '#ffffff',
        'ToolTipBase': '#183a2d', 'ToolTipText': '#e5f4ea',
        'Link': '#236347', 'LinkVisited': '#51436b', 'PlaceholderText': '#52665a',
        'Light': '#ffffff', 'Midlight': '#e1e8e2', 'Mid': '#7b9484',
        'Dark': '#52665a', 'Shadow': '#233d38',
    }
    for name, value in colors.items():
        palette.setColor(getattr(QPalette.ColorRole, name), QColor(value))
    for role in (QPalette.ColorRole.Text, QPalette.ColorRole.WindowText, QPalette.ColorRole.ButtonText):
        palette.setColor(QPalette.ColorGroup.Disabled, role, QColor(theme_color('#52665a')))
    palette.setColor(QPalette.ColorGroup.Disabled, QPalette.ColorRole.Button, QColor(theme_color('#edf1ee')))
    palette.setColor(QPalette.ColorGroup.Disabled, QPalette.ColorRole.Base, QColor(theme_color('#edf1ee')))
    return palette


ICONS = {
    "compass": '<circle cx="12" cy="12" r="9"/><path d="m16 8-3 5-5 3 3-5z"/>',
    "matches": '<path d="m12 3 2.4 5.9 6.4.5-4.9 4.1 1.5 6.2-5.4-3.4-5.4 3.4 1.5-6.2-4.9-4.1 6.4-.5z"/>',
    "wallet": '<rect x="3" y="5" width="18" height="14" rx="3"/><path d="M21 10h-6v5h6M3 8h17"/><path d="M17 12.5h.1"/>',
    "spending": '<path d="M4 20h16M6 16v-5M12 16V4M18 16V8"/>',
    "statement": '<path d="M6 3h8l4 4v14H6zM14 3v5h4M9 12h6M9 16h6"/>',
    "coverage": '<path d="m12 3 8 3v5c0 5-8 10-8 10S4 16 4 11V6zM8 12l3 3 5-6"/>',
    "explore": '<circle cx="10" cy="10" r="6"/><path d="m15 15 6 6"/>',
    "settings": '<path d="M4 7h16M4 17h16"/><circle cx="9" cy="7" r="3" fill="currentColor"/><circle cx="15" cy="17" r="3" fill="currentColor"/>',
    "refresh": '<path d="M20 8a8 8 0 0 0-14-2L3 9M3 3v6h6M4 16a8 8 0 0 0 14 2l3-3M15 15h6v6"/>',
    "arrow": '<path d="M4 12h16m-6-6 6 6-6 6"/>',
    "close": '<path d="m6 6 12 12M6 18 18 6"/>',
    "plus": '<path d="M12 5v14M5 12h14"/>',
}


def icon(name, color="#a4c9b8", size=20):
    svg = f'<svg xmlns="http://www.w3.org/2000/svg" width="24" height="24" viewBox="0 0 24 24" fill="none" color="{color}" stroke="{color}" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round">{ICONS[name]}</svg>'
    renderer = QSvgRenderer(svg.encode())
    pixmap = QPixmap(size * 2, size * 2)
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)
    renderer.render(painter)
    painter.end()
    pixmap.setDevicePixelRatio(2)
    return QIcon(pixmap)


def text(value, kind=None, wrap=False):
    widget = QLabel(value)
    if kind:
        widget.setObjectName(kind)
    widget.setWordWrap(wrap)
    return widget


def surface(kind="surface"):
    widget = QFrame()
    widget.setObjectName(kind)
    return widget


class Metric(QFrame):
    def __init__(self, caption, description):
        super().__init__()
        self.setObjectName("stat")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 16, 20, 16)
        layout.setSpacing(7)
        self.caption = text(caption.upper(), "eyebrow")
        layout.addWidget(self.caption)
        self.value = text("—", "statValue")
        layout.addWidget(self.value)
        self.description = text(description, "muted", True)
        layout.addWidget(self.description)

    def set_compact(self):
        self.layout().setContentsMargins(12, 8, 12, 8)
        self.layout().setSpacing(3)


def paint_artwork(painter, rect, pixmap):
    """Area-resample to physical pixels once, then draw on aligned pixel boundaries."""
    dpr = painter.device().devicePixelRatioF()
    width, height = max(1, round(rect.width() * dpr)), max(1, round(rect.height() * dpr))
    key = f'card-art:{pixmap.cacheKey()}:{width}:{height}:{dpr}'
    thumbnail = QPixmapCache.find(key)
    if thumbnail is None:
        thumbnail = pixmap.scaled(width, height, Qt.AspectRatioMode.KeepAspectRatio,
                                  Qt.TransformationMode.SmoothTransformation)
        thumbnail.setDevicePixelRatio(dpr)
        QPixmapCache.insert(key, thumbnail)
    x = round(rect.center().x() * dpr - thumbnail.width() / 2) / dpr
    y = round(rect.center().y() * dpr - thumbnail.height() / 2) / dpr
    # Resampling at paint time undersamples small logos and text. This pixmap
    # already matches the display's pixel density, including fractional scaling.
    painter.drawPixmap(QPointF(x, y), thumbnail)


class CardArt(QWidget):
    """Cached source artwork, with a scalable illustration while unavailable."""
    COLORS = [("#163c30", "#54805b"), ("#173440", "#507e88"), ("#34383c", "#777a79"), ("#584829", "#9c8550"), ("#433847", "#857084")]

    def __init__(self, card=None, width=220, height=136, artwork=None):
        super().__init__()
        self.card = card or {"name": "Card Compass", "issuerId": "YOUR NEXT CARD"}
        self.artwork = artwork
        if artwork is not None:
            artwork.imageReady.connect(lambda url: self.update())
        self.setFixedSize(width, height)
        self.setAccessibleName(f"Card artwork: {self.card['name']}")

    def set_card(self, card):
        self.card = card
        self.setAccessibleName(f"Card artwork: {card['name']}")
        self.update()

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        rect = QRectF(self.rect()).adjusted(1, 1, -1, -1)
        pixmap = self.artwork.pixmap(self.card) if self.artwork is not None else None
        if pixmap is not None:
            paint_artwork(p, rect, pixmap)
            p.end()
            return
        index = hashlib.sha256(self.card["issuerId"].encode()).digest()[0] % len(self.COLORS)
        first, second = self.COLORS[index]
        gradient = QLinearGradient(rect.topLeft(), rect.bottomRight())
        gradient.setColorAt(0, QColor(first))
        gradient.setColorAt(1, QColor(second))
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(gradient)
        p.drawRoundedRect(rect, 10, 10)
        p.setPen(QPen(QColor(255, 255, 255, 22), 1))
        p.setBrush(Qt.BrushStyle.NoBrush)
        for offset in (0, 22, 44, 66):
            p.drawEllipse(QRectF(self.width() * .55 + offset, -self.height() * .3, self.width(), self.height() * 1.8))
        small = self.width() < 130
        pad = 11 if small else 19
        p.setPen(QColor("#e2eddd"))
        font = QFont(self.font())
        font.setPixelSize(8 if small else 10)
        font.setWeight(QFont.Weight.Medium)
        p.setFont(font)
        issuer = self.card["issuerId"].replace("-", " ").upper()
        p.drawText(QRectF(pad, pad, self.width() - pad * 2, 18), Qt.AlignmentFlag.AlignLeft, p.fontMetrics().elidedText(issuer, Qt.TextElideMode.ElideRight, self.width() - pad * 2))
        p.setBrush(QColor("#cdbf88"))
        p.setPen(QPen(QColor("#9d986e"), .7))
        chip_y = 28 if small else 47
        p.drawRoundedRect(QRectF(pad, chip_y, 17 if small else 28, 12 if small else 20), 3, 3)
        p.drawLine(pad + (8 if small else 14), chip_y, pad + (8 if small else 14), chip_y + (12 if small else 20))
        if not small:
            font.setPixelSize(14)
            font.setWeight(QFont.Weight.DemiBold)
            p.setFont(font)
            p.setPen(QColor("#ffffff"))
            name = p.fontMetrics().elidedText(self.card["name"], Qt.TextElideMode.ElideRight, self.width() - pad * 2)
            p.drawText(QRectF(pad, self.height() - 45, self.width() - pad * 2, 23), Qt.AlignmentFlag.AlignLeft, name)
        p.end()


CARD_ROLE = Qt.ItemDataRole.UserRole + 1


from .theme import color as theme_color


class CardDelegate(QStyledItemDelegate):
    """Paint identities directly so long catalogs don't need hundreds of widgets."""
    def __init__(self, parent=None, artwork=None):
        super().__init__(parent)
        self.artwork = artwork
        if artwork is not None and parent is not None:
            artwork.imageReady.connect(lambda url: parent.viewport().update())

    def paint(self, painter, option, index):
        card = index.data(CARD_ROLE)
        if not card:
            return super().paint(painter, option, index)
        p = painter
        p.save()
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        rect = QRectF(option.rect)
        selected = bool(option.state & QStyle.StateFlag.State_Selected)
        hovered = bool(option.state & QStyle.StateFlag.State_MouseOver)
        background = QColor(theme_color('#edf5ee' if selected else '#f5f9f6' if hovered else '#ffffff'))
        if isinstance(option.widget, QListWidget):
            p.setBrush(background)
            p.setPen(QPen(QColor(theme_color('#98b6a1' if selected else '#e1e8e2')), 1))
            p.drawRoundedRect(rect.adjusted(1, 1, -1, -1), 12, 12)
        else:
            p.fillRect(rect, background)
            p.setPen(QPen(QColor(theme_color('#edf1ee')), 1))
            p.drawLine(rect.bottomLeft(), rect.bottomRight())
        art = QRectF(rect.left() + 18, rect.center().y() - 40, 128, 80)
        font = QFont(option.font)
        pixmap = self.artwork.pixmap(card) if self.artwork is not None else None
        if pixmap is not None:
            paint_artwork(p, art, pixmap)
        else:
            colors = CardArt.COLORS[hashlib.sha256(card['issuerId'].encode()).digest()[0] % len(CardArt.COLORS)]
            gradient = QLinearGradient(art.topLeft(), art.bottomRight())
            gradient.setColorAt(0, QColor(colors[0]))
            gradient.setColorAt(1, QColor(colors[1]))
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(gradient)
            p.drawRoundedRect(art, 7, 7)
            p.setBrush(QColor('#cdbf88'))
            p.drawRoundedRect(QRectF(art.left() + 10, art.top() + 24, 15, 11), 2, 2)
            font.setPixelSize(7)
            p.setFont(font)
            p.setPen(QColor('#e2eddd'))
            issuer = card['issuerId'].replace('-', ' ').upper()
            p.drawText(QRectF(art.left() + 10, art.top() + 8, 67, 12), Qt.AlignmentFlag.AlignLeft,
                       p.fontMetrics().elidedText(issuer, Qt.TextElideMode.ElideRight, 67))
        left = art.right() + 18
        width = max(0, int(rect.right() - left - 16))
        font.setPixelSize(14)
        font.setWeight(QFont.Weight.DemiBold)
        p.setFont(font)
        p.setPen(QColor(theme_color('#233d38')))
        p.drawText(QRectF(left, rect.center().y() - 20, width, 23), Qt.AlignmentFlag.AlignLeft,
                   p.fontMetrics().elidedText(card['name'], Qt.TextElideMode.ElideRight, width))
        font.setPixelSize(11)
        font.setWeight(QFont.Weight.Normal)
        p.setFont(font)
        p.setPen(QColor(theme_color('#52665a')))
        p.drawText(QRectF(left, rect.center().y() + 6, width, 20), Qt.AlignmentFlag.AlignLeft,
                   p.fontMetrics().elidedText(card['issuerId'].replace('-', ' ').title(), Qt.TextElideMode.ElideRight, width))
        p.restore()
