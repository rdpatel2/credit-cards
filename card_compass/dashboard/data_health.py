"""A live view of subscribed feeds, retrieval history and actionable failures."""
from datetime import datetime
from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QTableWidget, QTableWidgetItem, QHeaderView, QAbstractItemView
from ..ui import FitDialog

FEED_NAMES = {'quote': 'Quote / chart', 'modules': 'Fundamentals / analysts', 'news': 'News', 'earnings': 'Earnings'}


def readable_time(value):
    try:
        return datetime.fromisoformat(value).astimezone().strftime('%b %d, %Y\n%H:%M %Z')
    except (ValueError, TypeError):
        return value or 'Not recorded'


class DataHealthDialog(FitDialog):
    def __init__(self, owner):
        super().__init__(owner)
        self.owner, self.layer = owner, owner.data_layer
        self.setWindowTitle('Market data health')
        self.resize(980, 600)
        layout = QVBoxLayout(self)
        explanation = QLabel('Public feeds may be delayed or unavailable. For quotes, “As of” is the provider’s price timestamp; for other feeds it is the retrieval timestamp, not the financial reporting period. “Last loaded” records a successful retrieval by this app. Cached and demo quotes are excluded from personal market-value totals.')
        explanation.setWordWrap(True)
        layout.addWidget(explanation)
        self.summary = QLabel()
        self.summary.setWordWrap(True)
        layout.addWidget(self.summary)
        self.table = QTableWidget(0, 5)
        self.table.setHorizontalHeaderLabels(['Feed / ticker or date', 'State', 'Source / as of', 'Last loaded', 'Recovery'])
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
        self.table.setColumnWidth(0, 145)
        self.table.setColumnWidth(1, 85)
        self.table.setColumnWidth(3, 125)
        self.table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        self.table.horizontalHeader().setSectionResizeMode(4, QHeaderView.ResizeMode.Stretch)
        self.table.verticalHeader().setDefaultSectionSize(100)
        layout.addWidget(self.table, 1)
        actions = QHBoxLayout()
        self.retry_selected = QPushButton('Retry selected feed')
        self.retry_selected.clicked.connect(self.retry)
        actions.addWidget(self.retry_selected)
        retry_all = QPushButton('Retry failed / cached feeds')
        retry_all.clicked.connect(self.retry_problems)
        actions.addWidget(retry_all)
        actions.addStretch()
        close = QPushButton('Close')
        close.clicked.connect(self.accept)
        actions.addWidget(close)
        layout.addLayout(actions)
        self.table.itemSelectionChanged.connect(self.update_selection)
        self.layer.changed.connect(self.reload)
        self.timer = QTimer(self)
        self.timer.setInterval(60000)
        self.timer.timeout.connect(self.reload)
        self.timer.start()
        self.reload()

    def reload(self, *_):
        selected = self.selected_request()
        rows = self.layer.health()
        self.table.setRowCount(len(rows))
        self.table.clearSelection()
        self.table.setCurrentCell(-1, -1)
        for index, row in enumerate(rows):
            state = row['mode'] + '\n' + row['status'].title()
            recovery = row['error'] or ('Illustrative data only; switch to Public market data for actual feeds.' if row['mode'] == 'Demo' else 'Refresh to retry.' if row['status'] in ('error', 'stale', 'empty') else 'Public data may be delayed.')
            values = [FEED_NAMES.get(row['kind'], row['kind'])+'\n'+row['key'], state,
                      row['source']+'\nAs of: '+readable_time(row['as_of']),
                      readable_time(row['retrieved_at']), recovery]
            for column, value in enumerate(values):
                item = QTableWidgetItem(value)
                item.setToolTip(value)
                if column == 2:
                    item.setToolTip(row['source']+'\nAs of: '+(row['as_of'] or 'Not supplied'))
                if column == 3:
                    item.setToolTip(row['retrieved_at'] or 'No successful retrieval recorded')
                if column == 0:
                    item.setData(Qt.ItemDataRole.UserRole, (row['kind'], row['key']))
                self.table.setItem(index, column, item)
            if (row['kind'], row['key']) == selected:
                self.table.selectRow(index)
        problems = sum(row['status'] in ('error', 'stale', 'empty') for row in rows)
        self.summary.setText(f'{len(rows)} subscribed feeds · {problems} need attention. ' + self.owner.metrics[2].description.text())
        self.update_selection()

    def selected_request(self):
        item = self.table.item(self.table.currentRow(), 0)
        return tuple(item.data(Qt.ItemDataRole.UserRole)) if item else None

    def update_selection(self):
        self.retry_selected.setEnabled(self.selected_request() is not None)

    def retry(self):
        request = self.selected_request()
        if request:
            self.layer.refresh({request})

    def retry_problems(self):
        requests = {(row['kind'], row['key']) for row in self.layer.health() if row['status'] in ('error', 'stale', 'empty')}
        if requests:
            self.layer.refresh(requests)
