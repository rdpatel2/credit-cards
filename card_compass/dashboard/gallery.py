"""Searchable native block gallery with real previews and scene drag payloads."""
from copy import deepcopy
from PySide6.QtCore import Qt, QMimeData, Signal
from PySide6.QtGui import QDrag
from PySide6.QtWidgets import (QVBoxLayout, QHBoxLayout, QLineEdit, QListWidget,
    QListWidgetItem, QLabel, QPushButton, QWidget, QAbstractItemView)
from ..investments import widget
from ..ui import FitDialog as QDialog
from .widget_frame import WidgetFrame
from .widget_registry import REGISTRY, create_widget

WIDGET_MIME = 'application/x-finance-compass-widget'


class GalleryList(QListWidget):
    dragging = Signal()
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setDragEnabled(True)
        self.setDragDropMode(QAbstractItemView.DragDropMode.DragOnly)

    def startDrag(self, actions):
        item = self.currentItem()
        kind = item.data(Qt.ItemDataRole.UserRole) if item else None
        if not kind:
            return
        mime = QMimeData()
        mime.setData(WIDGET_MIME, kind.encode())
        drag = QDrag(self)
        drag.setMimeData(mime)
        self.dragging.emit()
        drag.exec(Qt.DropAction.CopyAction)


class WidgetGallery(QDialog):
    selected = Signal(str)
    dragging = Signal()
    def __init__(self, layer, parent=None):
        super().__init__(parent)
        self.layer = layer
        self.setWindowTitle('Add widget')
        self.setModal(False)
        self.resize(780, 520)
        layout = QVBoxLayout(self)
        self.search = QLineEdit()
        self.search.setPlaceholderText('Search widgets · news, notes, earnings…')
        self.search.setAccessibleName('Search widget gallery')
        self.search.textChanged.connect(self.populate)
        layout.addWidget(self.search)
        row = QHBoxLayout()
        self.list = GalleryList()
        self.list.setMaximumWidth(280)
        self.list.currentItemChanged.connect(self.show_preview)
        self.list.itemDoubleClicked.connect(self.choose)
        self.list.dragging.connect(self.dragging)
        self.list.dragging.connect(self.hide)
        row.addWidget(self.list, 1)
        self.preview = QWidget()
        self.preview_layout = QVBoxLayout(self.preview)
        self.preview_layout.setContentsMargins(0,0,0,0)
        row.addWidget(self.preview, 2)
        layout.addLayout(row, 1)
        self.hint = QLabel('Choose a widget, or drag it onto the dashboard. Preview uses your cached data.')
        self.hint.setWordWrap(True)
        layout.addWidget(self.hint)
        self.add_button = QPushButton('Add selected widget')
        self.add_button.clicked.connect(self.choose)
        layout.addWidget(self.add_button)
        self.populate()

    def populate(self, *args):
        query = self.search.text().casefold()
        self.list.clear()
        categories = sorted({d.category for d in REGISTRY.definitions.values() if d.type != 'unknown'})
        for category in categories:
            definitions = [d for d in REGISTRY.definitions.values() if d.category == category and d.type != 'unknown'
                           and query in (d.title+' '+d.description+' '+category).casefold()]
            if not definitions:
                continue
            heading = QListWidgetItem(category)
            heading.setFlags(Qt.ItemFlag.NoItemFlags)
            self.list.addItem(heading)
            for definition in definitions:
                item = QListWidgetItem(definition.icon+'  '+definition.title)
                item.setData(Qt.ItemDataRole.UserRole, definition.type)
                item.setToolTip(definition.description)
                self.list.addItem(item)
        first = next((i for i in range(self.list.count()) if self.list.item(i).data(Qt.ItemDataRole.UserRole)), -1)
        self.list.setCurrentRow(first)
        self.add_button.setEnabled(first >= 0)
        if first < 0:
            self.clear_preview()
            self.preview_layout.addWidget(QLabel('No matching widgets. Try another search.'))

    def clear_preview(self):
        while self.preview_layout.count():
            item = self.preview_layout.takeAt(0)
            if item.widget():
                item.widget().hide()
                item.widget().deleteLater()

    def show_preview(self, item, previous=None):
        if not item or not item.data(Qt.ItemDataRole.UserRole):
            return
        self.clear_preview()
        definition = REGISTRY.get(item.data(Qt.ItemDataRole.UserRole))
        description = QLabel(definition.description)
        description.setWordWrap(True)
        self.preview_layout.addWidget(description)
        block = widget(definition.type)
        block['config'].update(watchlistId=self.layer.state['watchlists'][0]['id'], _noteKey='gallery-preview')
        def factory(frame):
            component = create_widget(definition, deepcopy(block['config']), frame.size(), False, lambda values:None, self.layer)
            component.footerChanged.connect(frame.set_footer)
            component.crashed.connect(frame.show_error)
            frame.set_footer(component.footer_text)
            return component
        frame = WidgetFrame(block, factory)
        frame.command.connect(lambda ident, action: frame.guarded(frame.component.refresh) if action == 'refresh' and frame.component else None)
        frame.menu_button.hide()
        frame.body.setEnabled(False)
        self.preview_layout.addWidget(frame, 1)
        self.preview_layout.addWidget(QLabel(f'Default size: {definition.defaultSize[0]} of 12 columns × {definition.defaultSize[1]*10} px tall'))

    def choose(self, *args):
        item = self.list.currentItem()
        if item and item.data(Qt.ItemDataRole.UserRole):
            self.selected.emit(item.data(Qt.ItemDataRole.UserRole))
            self.hide()
