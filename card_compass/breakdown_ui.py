"""Native spending rankings and charts for the saved transaction ledger."""
from PySide6.QtCore import Qt, QRectF
from PySide6.QtGui import QColor, QPainter
from PySide6.QtWidgets import QWidget, QVBoxLayout, QHBoxLayout, QScrollArea, QTabWidget, QTableWidgetItem, QPushButton, QMessageBox

from .breakdown import spending_breakdown
from .theme import color as theme_color
from .import_ui import amount, category_label, grid
from .ui import Metric, ScrollSafeComboBox, surface, text


class SpendBars(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.items = []
        self.setMinimumHeight(225)
        self.setAccessibleName('Top net spending; values also appear in the tables below')

    def set_items(self, items):
        self.items = [(name, value) for name, value in items if value > 0][:5]
        self.setAccessibleDescription('; '.join(f'{name}: {amount(value)}' for name, value in self.items))
        self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(QColor(theme_color('#4d6256')))
        if not self.items:
            painter.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter, 'No positive net spending in this selection.')
            return
        maximum = max(value for _, value in self.items)
        font = painter.font()
        font.setPixelSize(12)
        painter.setFont(font)
        width = max(1, self.width() - 4)
        for index, (name, value) in enumerate(self.items):
            top = index * 44
            money = amount(value)
            money_width = painter.fontMetrics().horizontalAdvance(money) + 12
            painter.setPen(QColor(theme_color('#233d38')))
            painter.drawText(QRectF(0, top, max(0, width - money_width), 20),
                             painter.fontMetrics().elidedText(name, Qt.TextElideMode.ElideRight, max(1, width - money_width)))
            painter.drawText(QRectF(width - money_width, top, money_width, 20), Qt.AlignmentFlag.AlignRight, money)
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QColor(theme_color('#e6f2e9')))
            painter.drawRoundedRect(QRectF(0, top + 25, width, 7), 3, 3)
            painter.setBrush(QColor(theme_color('#236347')))
            painter.drawRoundedRect(QRectF(0, top + 25, width * value / maximum, 7), 3, 3)


class BreakdownPage(QWidget):
    def __init__(self, owner):
        super().__init__()
        self.owner = owner
        self.ledger = owner.imports_page.ledger
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        content = QWidget()
        content.setObjectName('scrollContent')
        layout = QVBoxLayout(content)
        layout.setContentsMargins(0, 0, 10, 0)
        layout.setSpacing(10)
        filters = QHBoxLayout()
        filters.addWidget(text('Period', 'muted'))
        self.period = ScrollSafeComboBox()
        self.period.setMinimumWidth(160)
        filters.addWidget(self.period)
        filters.addSpacing(12)
        filters.addWidget(text('Card', 'muted'))
        self.card = ScrollSafeComboBox()
        filters.addWidget(self.card, 1)
        layout.addLayout(filters)
        self.period.setToolTip('Breakdown uses its own date selection, separate from your monthly budget.')
        metrics = QHBoxLayout()
        self.net = Metric('Net spending', 'Purchases minus refunds')
        self.purchases = Metric('Purchases', 'Before refunds')
        self.refunds = Metric('Refunds', 'Deducted from spending')
        for metric in (self.net, self.purchases, self.refunds):
            metric.set_compact()
            metrics.addWidget(metric, 1)
        layout.addLayout(metrics)
        self.summary = text('', 'muted', True)
        layout.addWidget(self.summary)
        chart_panel = QWidget()
        charts = QHBoxLayout(chart_panel)
        self.category_bars, self.merchant_bars = SpendBars(), SpendBars()
        for title, chart in [('Top spending categories', self.category_bars), ('Top merchants', self.merchant_bars)]:
            frame = surface()
            inside = QVBoxLayout(frame)
            inside.setContentsMargins(16, 16, 16, 10)
            inside.addWidget(text(title, 'sectionTitle'))
            inside.addWidget(chart)
            charts.addWidget(frame, 1)
        tabs = self.tabs = QTabWidget()
        self.tables = {}
        for key, title, heading in [('categories', 'Categories', 'CATEGORY'), ('merchants', 'Merchants', 'MERCHANT'),
                                    ('cards', 'Cards', 'CARD'), ('months', 'Monthly trend', 'MONTH')]:
            table = grid([heading, 'NET SPEND', 'PURCHASES', 'REFUNDS', 'SHARE OF PURCHASES'])
            table.setMinimumHeight(130)
            tabs.addTab(table, title)
            self.tables[key] = table
            table.itemDoubleClicked.connect(lambda item, k=key: self.open_purchases(k, item.row()))
            table.itemSelectionChanged.connect(self.update_drill_action)
        tabs.addTab(chart_panel, 'Charts')
        layout.addWidget(tabs, 1)
        self.drill_button = QPushButton('View purchases in selected group')
        self.drill_button.setEnabled(False)
        self.drill_button.clicked.connect(self.open_selected)
        tabs.currentChanged.connect(self.update_drill_action)
        actions = QHBoxLayout()
        actions.addWidget(self.drill_button, 1)
        help_button = QPushButton('How totals work…')
        help_button.clicked.connect(lambda: QMessageBox.information(self, 'Spending breakdown', 'These are actual purchases minus refunds by transaction date, within the filters above. Payments, fees, interest, transfers and reward credits are excluded. Share is the portion of purchases before refunds. Missing months are not estimated.\n\nDouble-click a group to see its purchases.\n\nSaved locally: ' + str(self.ledger.path)))
        actions.addWidget(help_button)
        layout.addLayout(actions)
        scroll.setWidget(content)
        outer.addWidget(scroll)
        self.period.currentIndexChanged.connect(self.render)
        self.card.currentIndexChanged.connect(self.render)
        self.reload()

    def card_name(self, ident):
        card = getattr(self.owner, 'by_id', {}).get(ident, {})
        return card.get('name', ident)

    def reload(self):
        self.rows = self.ledger.breakdown_rows()
        purchases = [row for row in self.rows if row['kind'] in ('purchase', 'refund')]
        for combo, options, placeholder in (
            (self.period, [(m, m) for m in sorted({r['date'][:7] for r in purchases}, reverse=True)], 'All imported dates'),
            (self.card, [(self.card_name(c), c) for c in sorted({r['card_id'] for r in purchases})], 'All cards'),
        ):
            selected = combo.currentData()
            combo.blockSignals(True)
            combo.clear()
            combo.addItem(placeholder, None)
            for title, key in options:
                combo.addItem(title, key)
            combo.setCurrentIndex(max(0, combo.findData(selected)))
            combo.blockSignals(False)
        self.render()

    def render(self, *args):
        self.report = spending_breakdown(self.rows, month=self.period.currentData(), card_id=self.card.currentData())
        data = self.report
        self.net.value.setText(amount(data['net_cents']))
        self.purchases.value.setText(amount(data['purchases_cents']))
        self.refunds.value.setText(amount(data['refunds_cents']))
        if not data['count']:
            self.summary.setText('No purchases or refunds in this selection. Import a statement on the Statements page to get started.')
        else:
            top = next((row for row in data['categories'] if row['net_cents'] > 0), None)
            detail = f"Largest category: {category_label(top['key'])} · {amount(top['net_cents'])}." if top else 'Refunds equal or exceed purchases in every category.'
            self.summary.setText(f"{data['count']} purchases and refunds · {detail}")
        self.category_bars.set_items([(category_label(row['key']), row['net_cents']) for row in data['categories']])
        self.merchant_bars.set_items([(row['name'], row['net_cents']) for row in data['merchants']])
        for key, table in self.tables.items():
            items = list(reversed(data[key])) if key == 'months' else data[key]
            table.setRowCount(len(items))
            for index, row in enumerate(items):
                name = category_label(row['key']) if key == 'categories' else self.card_name(row['key']) if key == 'cards' else row['name']
                values = [name, amount(row['net_cents']), amount(row['purchases_cents']), amount(row['refunds_cents']), f"{row['purchase_share']:.1%}"]
                for col, value in enumerate(values):
                    item = QTableWidgetItem(value)
                    item.setData(Qt.ItemDataRole.UserRole, row['key'])
                    item.setToolTip(name + f" · {row['count']} purchases and refunds")
                    table.setItem(index, col, item)
        self.update_drill_action()

    def update_drill_action(self, *_):
        if hasattr(self, 'drill_button'):
            table = self.tabs.currentWidget()
            self.drill_button.setEnabled(table in self.tables.values() and table.currentRow() >= 0)

    def open_selected(self):
        key = next((key for key, table in self.tables.items() if table is self.tabs.currentWidget()), None)
        if key:
            self.open_purchases(key, self.tables[key].currentRow())

    def open_purchases(self, key, row):
        item = self.tables[key].item(row, 0)
        if item:
            self.owner.imports_page.show_group(key, item.data(Qt.ItemDataRole.UserRole), item.text(), self.period.currentData(), self.card.currentData())
