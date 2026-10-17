"""Explicit review of heuristic charge patterns, without altering imported purchases."""
import sqlite3
from PySide6.QtWidgets import (QWidget, QVBoxLayout, QHBoxLayout, QPushButton,
    QTableWidgetItem, QTabWidget, QTextEdit, QDialogButtonBox, QHeaderView, QMessageBox,
    QLabel, QSizePolicy)
from .ui import FitDialog, text
from .import_ui import grid
from .planning_ui import money


class RecurringDialog(FitDialog):
    def __init__(self, owner):
        super().__init__(owner)
        self.owner = owner
        self.store = owner.recurring_store
        self.setWindowTitle('Review recurring card charges')
        self.resize(1020, 700)
        self.candidates = []
        self.saved = []
        layout = QVBoxLayout(self)
        layout.setSpacing(8)
        layout.addWidget(text('Recognize regular charges in imported history', 'sectionTitle'))
        layout.addWidget(text('Suggestions require at least three adjacent months on the same card, roughly matching dates and amounts. They can include bills as well as subscriptions. Review each match; the app cannot confirm an active contract or cancel a service.', 'muted', True))
        self.tabs = QTabWidget()
        layout.addWidget(self.tabs, 1)
        suggestions = QWidget()
        suggestion_layout = QVBoxLayout(suggestions)
        self.summary = text('', 'muted', True)
        suggestion_layout.addWidget(self.summary)
        self.table = grid(['Merchant', 'Card account', 'Months', 'Latest charge', 'Amount', 'Review'])
        self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Fixed)
        self.table.setColumnWidth(0, 170)
        self.table.setMinimumHeight(210)
        self.table.itemSelectionChanged.connect(self.show_candidate)
        suggestion_layout.addWidget(self.table, 1)
        self.details = QTextEdit()
        self.details.setReadOnly(True)
        self.details.setMaximumHeight(140)
        suggestion_layout.addWidget(self.details)
        actions = QHBoxLayout()
        for title, handler in [('Confirm selected pattern', self.confirm_selected), ('Dismiss suggestion', self.dismiss_selected), ('Open purchases', self.open_purchases)]:
            button = QPushButton(title)
            button.clicked.connect(handler)
            actions.addWidget(button)
        suggestion_layout.addLayout(actions)
        self.tabs.addTab(suggestions, 'Suggestions')
        reviewed = QWidget()
        review_layout = QVBoxLayout(reviewed)
        review_layout.addWidget(text('Saved decisions retain the evidence you reviewed. Changed or missing imported history needs another review; an old confirmation does not prove a charge is still active. Removing a decision does not change purchases or cancel anything.', 'muted', True))
        self.saved_table = grid(['Merchant', 'Decision', 'Review date', 'Latest reviewed amount', 'Current evidence'])
        self.saved_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Fixed)
        self.saved_table.setColumnWidth(0, 170)
        self.saved_table.itemSelectionChanged.connect(self.show_saved)
        review_layout.addWidget(self.saved_table, 1)
        self.saved_details = QTextEdit()
        self.saved_details.setReadOnly(True)
        self.saved_details.setMaximumHeight(150)
        review_layout.addWidget(self.saved_details)
        remove = QPushButton('Remove selected review decision…')
        remove.clicked.connect(self.remove_selected)
        review_layout.addWidget(remove)
        self.tabs.addTab(reviewed, 'Reviewed patterns')
        self.error = text('', 'muted', True)
        layout.addWidget(self.error)
        footer = QHBoxLayout()
        refresh = QPushButton('Refresh imported history')
        refresh.clicked.connect(self.reload)
        footer.addWidget(refresh)
        footer.addStretch()
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        buttons.rejected.connect(self.reject)
        footer.addWidget(buttons)
        layout.addLayout(footer)
        self.reload()
        for label in self.findChildren(QLabel):
            label.setSizePolicy(label.sizePolicy().horizontalPolicy(), QSizePolicy.Policy.Maximum)

    @staticmethod
    def evidence_text(candidate):
        lines = [candidate['name'] + ' · card account ' + candidate['account_key'],
                 'Source: ' + candidate['source'],
                 'Observed latest amount: ' + money(candidate['amount_cents']) + ' · typical: ' + money(candidate['typical_cents']),
                 'Estimated next purchase date: ' + candidate['estimated_next'] + ' (heuristic, not a bill due date).',
                 'Observed purchases:']
        lines.extend(r['date'] + ' · ' + money(r['amount_cents']) + ' · transaction #' + str(r['id']) for r in candidate['history'])
        if candidate['refunds']:
            lines.append('Matching refunds:')
            lines.extend(r['date'] + ' · ' + money(r['amount_cents']) for r in candidate['refunds'])
        lines.extend(candidate['warnings'])
        lines.append('Only imported purchases are covered; one-off-marked rows, daily/weekly/annual charges, variable bills beyond the amount tolerance and multiple same-month charges are outside this suggestion model. Gaps in imports can hide patterns.')
        lines.append('Confirmation saves a local review snapshot. It never changes categories, transactions or budget entries. Card spending already contributes to monthly actuals; avoid adding it again as a non-card bill.')
        return '\n\n'.join(lines)

    def reload(self, *_):
        try:
            candidates = self.store.scan()
            saved = self.store.reviews()
        except (ValueError, TypeError, sqlite3.Error, OSError) as exc:
            self.error.setText('Could not load reviews. Existing records are preserved. ' + str(exc))
            self.table.setRowCount(0)
            self.saved_table.setRowCount(0)
            self.candidates = []
            self.saved = []
            self.details.clear()
            self.saved_details.clear()
            return
        self.candidates, self.saved = candidates, saved
        decisions = {r['candidate']['id']: r for r in saved}
        current = {r['id']: r for r in candidates}
        self.table.blockSignals(True)
        self.table.setRowCount(len(candidates))
        for row, candidate in enumerate(candidates):
            decision = decisions.get(candidate['id'])
            state = 'Not reviewed' if decision is None else decision['status'].title() if decision['candidate']['fingerprint'] == candidate['fingerprint'] else 'Evidence changed'
            values = [candidate['name'], candidate['account_key'], str(len(candidate['history'])), candidate['last_date'], money(candidate['amount_cents']), state]
            for col, value in enumerate(values):
                item = QTableWidgetItem(value)
                item.setToolTip(value)
                self.table.setItem(row, col, item)
        self.table.blockSignals(False)
        self.saved_table.blockSignals(True)
        self.saved_table.setRowCount(len(saved))
        for row, record in enumerate(saved):
            candidate = record['candidate']
            observed = current.get(candidate['id'])
            state = 'No recent pattern' if observed is None else 'Same evidence' if observed['fingerprint'] == candidate['fingerprint'] else 'Changed; review again'
            values = [candidate['name'], record['status'].title(), record['reviewed_at'][:10], money(candidate['amount_cents']), state]
            for col, value in enumerate(values):
                self.saved_table.setItem(row, col, QTableWidgetItem(value))
        self.saved_table.blockSignals(False)
        self.summary.setText(f'{len(candidates)} possible monthly patterns from imported card history. No suggestions does not mean there are no recurring bills. Review status includes prior dismissals; nothing is silently added to your plan.')
        self.error.clear()
        self.details.clear()
        self.saved_details.clear()
        if candidates:
            self.table.selectRow(0)
            self.show_candidate()
        if saved:
            self.saved_table.selectRow(0)
            self.show_saved()

    def selected(self):
        row = self.table.currentRow()
        return self.candidates[row] if 0 <= row < len(self.candidates) else None

    def show_candidate(self):
        candidate = self.selected()
        self.details.setPlainText(self.evidence_text(candidate) if candidate else 'Choose a suggestion to inspect its source transactions.')

    def show_saved(self):
        row = self.saved_table.currentRow()
        record = self.saved[row] if 0 <= row < len(self.saved) else None
        self.saved_details.setPlainText(record['status'].title() + ' · ' + record['reviewed_at'] + '\n\n' + self.evidence_text(record['candidate']) if record else '')

    def decide(self, status):
        candidate = self.selected()
        if candidate is None:
            self.error.setText('Select a suggestion and inspect its evidence first.')
            return
        try:
            self.store.review(candidate, status)
            self.reload()
            self.error.setText('Saved review locally; imported transactions and budgets are unchanged.')
        except (ValueError, sqlite3.Error, OSError) as exc:
            self.error.setText('Could not save review. ' + str(exc))

    def confirm_selected(self):
        self.decide('confirmed')

    def dismiss_selected(self):
        self.decide('dismissed')

    def remove_selected(self):
        row = self.saved_table.currentRow()
        if not 0 <= row < len(self.saved):
            return
        record = self.saved[row]
        if QMessageBox.question(self, 'Remove review decision?', 'Remove the saved ' + record['status'] + ' decision for ' + record['candidate']['name'] + '? Imported purchases remain unchanged.') != QMessageBox.StandardButton.Yes:
            return
        try:
            self.store.remove_review(record['candidate']['id'])
            self.reload()
        except (sqlite3.Error, OSError) as exc:
            self.error.setText('Could not remove review. ' + str(exc))

    def open_purchases(self):
        candidate = self.selected()
        if candidate is None:
            self.error.setText('Select a suggested merchant first.')
            return
        page = self.owner.imports_page
        page.clear_filters()
        page.drill_filter = ('account_key', candidate['account_key'], candidate['name'] + ' · ' + candidate['account_key'])
        page.search.setText(candidate['name'])
        page.reload()
        self.owner.navigate('imports')
        self.accept()
