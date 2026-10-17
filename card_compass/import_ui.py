"""Native statement review and merchant memory screens."""
from __future__ import annotations

from collections import defaultdict
from datetime import date
import hashlib
import json
from pathlib import Path

from PySide6.QtCore import Qt, QThread, Signal, QTimer, QDate
from PySide6.QtWidgets import (QWidget, QVBoxLayout, QHBoxLayout, QFormLayout, QLabel,
    QPushButton, QCheckBox, QLineEdit, QScrollArea, QFileDialog, QTabWidget, QTableWidget,
    QTableWidgetItem, QHeaderView, QAbstractItemView, QMessageBox, QDialogButtonBox)

from .engine import DEFAULT_CATEGORIES, canonical, label
from .ui import FitDialog as QDialog
from .ledger import Ledger
from .statements import extract_pdf_text, parse_statement_text
from .csv_statements import read_csv_text, csv_table, suggested_columns, parse_csv_text, FIELDS
from .ui import ScrollSafeComboBox, ScrollSafeDateEdit, SearchableCategoryBox, text, surface, icon

EXTRA_CATEGORIES = {'auto_services': 'Auto services', 'medical': 'Healthcare',
                    'recreation': 'Recreation / golf', 'general_retail': 'General retail', 'rent': 'Rent'}


def amount(cents):
    return f"{'−' if cents < 0 else ''}${abs(cents) / 100:,.2f}"


def category_label(key):
    return EXTRA_CATEGORIES.get(key, label(key))


def category_picker(categories, value=None, placeholder=True):
    combo = SearchableCategoryBox()
    if placeholder:
        combo.addItem('Choose a category…', None)
    for key in sorted(categories, key=category_label):
        combo.addItem(category_label(key), key)
    index = combo.findData(value)
    combo.setCurrentIndex(max(0, index))
    return combo


def grid(headers):
    widget = QTableWidget(0, len(headers))
    widget.setHorizontalHeaderLabels(headers)
    widget.horizontalHeader().setTextElideMode(Qt.TextElideMode.ElideRight)
    for column, title in enumerate(headers):widget.horizontalHeaderItem(column).setToolTip(title)
    widget.verticalHeader().hide()
    widget.verticalHeader().setDefaultSectionSize(58)
    widget.setShowGrid(False)
    widget.setWordWrap(False)
    widget.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
    widget.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
    widget.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
    widget.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
    return widget


class ExtractThread(QThread):
    result = Signal(str)
    failed = Signal(str)

    def __init__(self, path, parent):
        super().__init__(parent)
        self.path = path

    def run(self):
        try:
            self.result.emit(read_csv_text(self.path) if Path(self.path).suffix.lower() == '.csv' else extract_pdf_text(self.path))
        except Exception as exc:
            self.failed.emit(str(exc))


class CsvMappingDialog(QDialog):
    def __init__(self, value, parent=None):
        super().__init__(parent)
        self.setWindowTitle('Choose CSV columns · Card Compass')
        self.resize(820, 700)
        columns, records = csv_table(value)
        guesses = suggested_columns(columns)
        layout = QVBoxLayout(self)
        layout.addWidget(text('Match your CSV columns.', 'sectionTitle'))
        layout.addWidget(text('Choose the amount convention using a known purchase. Categories from the bank are hints; your saved merchant rules still apply.', 'muted', True))
        content = QWidget()
        form = QFormLayout(content)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setWidget(content)
        self.convention = ScrollSafeComboBox()
        for title, key in [('Choose amount convention…', None), ('Purchases are positive; payments/refunds negative', 'positive'),
                           ('Purchases are negative; payments/refunds positive', 'negative'),
                           ('Separate debit (purchase) and credit columns', 'debit_credit')]:
            self.convention.addItem(title, key)
        form.addRow('Amount convention', self.convention)
        self.columns = {}
        self.required_form = form
        self.optional_box = QWidget()
        optional_form = QFormLayout(self.optional_box)
        self.optional_toggle = QCheckBox('Show optional columns and account details')
        form.addRow(self.optional_toggle)
        self.optional_toggle.toggled.connect(self.optional_box.setVisible)
        for field in FIELDS:
            combo = ScrollSafeComboBox()
            combo.addItem('Not supplied', None)
            for column in columns:
                combo.addItem(column, column)
            combo.setCurrentIndex(max(0, combo.findData(guesses[field])))
            label = field.replace('_', ' ').title()
            (form if field in ('date', 'description', 'amount', 'debit', 'credit') else optional_form).addRow(label + (' (required)' if field in ('date', 'description') else ''), combo)
            self.columns[field] = combo
        self.ending = QLineEdit()
        self.ending.setMaxLength(5)
        self.ending.setPlaceholderText('Optional: last 4–5 digits, if no account column')
        optional_form.addRow('Account ending', self.ending)
        form.addRow(self.optional_box)
        self.optional_box.hide()
        self.interpretation = text('Choose how amounts are signed to preview purchases and refunds.', 'muted', True)
        form.addRow(self.interpretation)
        self.csv_text = value
        self.convention.currentIndexChanged.connect(self.update_example)
        for combo in self.columns.values():
            combo.currentIndexChanged.connect(self.update_example)
        layout.addWidget(scroll, 1)
        preview = grid(columns)
        preview.setMaximumHeight(170)
        preview.setRowCount(min(3, len(records)))
        for row, record in enumerate(records[:3]):
            for col, cell in enumerate(record[:len(columns)]):
                preview.setItem(row, col, QTableWidgetItem(cell))
        layout.addWidget(preview)
        self.error = text('', 'muted', True)
        layout.addWidget(self.error)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self.validate)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
        self.update_example()

    def update_example(self, *_):
        convention = self.convention.currentData()
        split = convention == 'debit_credit'
        for field in ('amount', 'debit', 'credit'):
            self.required_form.setRowVisible(self.columns[field], (field != 'amount') == split)
            self.required_form.labelForField(self.columns[field]).setText(field.title() + ' (required)')
        if not convention:
            self.interpretation.setText('Choose the convention using a purchase you recognize.')
            return
        try:
            statement = parse_csv_text(self.csv_text, **self.config())
            examples = statement.transactions[:3]
            self.interpretation.setText('How these rows will be read:\n' + '\n'.join(f'{row.description}: {row.kind.title()} · {amount(row.amount_cents)}' for row in examples))
        except ValueError as exc:
            self.interpretation.setText('Preview: ' + str(exc))

    def config(self):
        return {'mapping': {field: combo.currentData() for field, combo in self.columns.items()},
                'convention': self.convention.currentData(), 'account_ending': self.ending.text().strip()}

    def validate(self):
        config = self.config()
        required = ['date', 'description'] + (['debit', 'credit'] if config['convention'] == 'debit_credit' else ['amount'])
        if not config['convention'] or any(not config['mapping'][field] for field in required):
            self.error.setText('Choose the amount convention and all required date, description, and amount columns.')
        elif config['account_ending'] and not (config['account_ending'].isdigit() and len(config['account_ending']) in (4, 5)):
            self.error.setText('Account ending must contain only the last 4 or 5 digits.')
        else:
            self.accept()


class ImportDialog(QDialog):
    def __init__(self, path, ledger, cards, categories, parent=None, extracted_text=None, csv_config=None):
        super().__init__(parent)
        self.setWindowTitle('Import a statement · Card Compass')
        self.resize(1080, 830)
        self.setMinimumSize(900, 690)
        self.path = Path(path)
        self.ledger, self.cards, self.categories = ledger, cards, categories
        self.extracted_text = extracted_text
        self.csv_config = csv_config
        self.statement = None
        self.preview_valid = False
        self.rows, self.merchant_widgets = [], {}
        self.worker = None
        self.cancelled = False
        self.imported_count = 0
        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 24, 24, 24)
        layout.setSpacing(14)
        layout.addWidget(text('Teach your wallet once.', 'heroTitle'))
        layout.addWidget(text(self.path.name + ' · Your file stays on this device.', 'muted'))
        form = QHBoxLayout()
        self.card = ScrollSafeComboBox()
        self.card.addItem('Select the card for this statement…', None)
        for card in cards:
            self.card.addItem(card['name'] + ' · ' + card['issuerId'].replace('-', ' ').title(), card['id'])
        self.card.setMinimumWidth(330)
        form.addWidget(self.card, 1)
        form.addWidget(text('Statement closing date', 'muted'))
        self.closing = ScrollSafeDateEdit(QDate.currentDate())
        self.closing.setCalendarPopup(True)
        self.closing.setDisplayFormat('MMM d, yyyy')
        self.closing.setMinimumHeight(40)
        form.addWidget(self.closing)
        self.reparse = QPushButton('Read dates again')
        self.reparse.clicked.connect(lambda: self.parse(use_selected_date=True))
        form.addWidget(self.reparse)
        layout.addLayout(form)
        self.summary = text('Reading statement locally…', 'sectionTitle', True)
        layout.addWidget(self.summary)
        self.warning = text('', 'muted', True)
        layout.addWidget(self.warning)
        self.tabs = QTabWidget()
        self.merchants = grid(['MERCHANT / MATCH', 'COUNT', 'CATEGORY', 'REMEMBER FOR'])
        self.merchants.horizontalHeaderItem(0).setToolTip('Merchant name / link to a saved merchant')
        self.merchants.horizontalHeader().setSectionResizeMode(0,QHeaderView.ResizeMode.Interactive)
        self.merchants.setColumnWidth(0,350)
        for col, width in ((1, 90), (2, 230), (3, 165)):
            self.merchants.horizontalHeader().setSectionResizeMode(col, QHeaderView.ResizeMode.Fixed)
            self.merchants.setColumnWidth(col, width)
        self.tabs.addTab(self.merchants, 'New merchants')
        self.transactions = grid(['DESCRIPTION', 'DATE', 'AMOUNT', 'TYPE', 'IMPORT', 'CATEGORY', 'ONE-OFF'])
        self.transactions.horizontalHeader().setSectionResizeMode(0,QHeaderView.ResizeMode.Interactive)
        self.transactions.setColumnWidth(0,240)
        for col, width in ((1, 110), (2, 105), (3, 90), (4, 145), (5, 230)):
            self.transactions.horizontalHeader().setSectionResizeMode(col, QHeaderView.ResizeMode.Fixed)
            self.transactions.setColumnWidth(col, width)
        self.tabs.addTab(self.transactions, 'Review purchases')
        layout.addWidget(self.tabs, 1)
        self.confirm = QCheckBox('I checked the closing date, extracted purchases, and any reconciliation warnings.')
        layout.addWidget(self.confirm)
        self.readiness = text('', 'muted', True)
        layout.addWidget(self.readiness)
        actions = QHBoxLayout()
        cancel = QPushButton('Cancel')
        cancel.clicked.connect(self.reject)
        actions.addWidget(cancel)
        actions.addStretch()
        self.save = QPushButton('Save statement and merchant choices')
        self.save.setObjectName('primary')
        self.save.clicked.connect(self.commit)
        self.save.setEnabled(False)
        actions.addWidget(self.save)
        layout.addLayout(actions)
        self.confirm.toggled.connect(self.validate)
        self.card.currentIndexChanged.connect(self.card_changed)
        self.closing.dateChanged.connect(self.date_changed)
        if extracted_text is not None:
            self.parse()
        else:
            QTimer.singleShot(0, self.extract)

    def extract(self):
        if self.cancelled or self.worker is not None:
            return
        self.worker = ExtractThread(self.path, self)
        self.worker.result.connect(self.extracted)
        self.worker.failed.connect(self.failed)
        self.worker.finished.connect(self.extract_finished)
        self.worker.start()

    def extracted(self, value):
        if not self.cancelled:
            self.extracted_text = value
            if self.path.suffix.lower() == '.csv' and self.csv_config is None:
                try:
                    mapping = CsvMappingDialog(value, self)
                    if mapping.exec() != QDialog.DialogCode.Accepted:
                        self.reject()
                        return
                    self.csv_config = mapping.config()
                except ValueError as exc:
                    self.failed(str(exc))
                    return
            self.parse()

    def card_changed(self, *args):
        if self.path.suffix.lower() == '.csv':
            self.parse(use_selected_date=self.statement is not None)
        else:
            self.preview()

    def failed(self, message):
        self.summary.setText('This statement needs attention.')
        self.warning.setText(message)
        self.save.setEnabled(False)

    def extract_finished(self):
        if self.cancelled:
            super().reject()

    def reject(self):
        if self.worker and self.worker.isRunning():
            self.cancelled = True
            self.hide()
        else:
            super().reject()

    def closeEvent(self, event):
        if self.worker and self.worker.isRunning():
            self.reject()
            event.ignore()
        else:
            event.accept()

    def date_changed(self):
        if self.statement:
            self.confirm.setChecked(False)
            self.save.setEnabled(False)
            self.readiness.setText('Closing date changed. Click Read dates again to update transaction years.')

    def parse(self, use_selected_date=False):
        if not self.extracted_text:
            return
        self.statement = None
        self.preview_valid = False
        self.confirm.setChecked(False)
        try:
            closing = self.closing.date().toString('yyyy-MM-dd') if use_selected_date else None
            if self.path.suffix.lower() == '.csv':
                if self.csv_config is None:
                    self.extracted(self.extracted_text)
                    return
                card = next((c for c in self.cards if c['id'] == self.card.currentData()), None)
                issuer = {'bank-of-america': 'bofa', 'american-express': 'amex'}.get(card['issuerId'], card['issuerId']) if card else 'csv'
                self.statement = parse_csv_text(self.extracted_text, **self.csv_config, issuer=issuer, closing_date=closing)
            else:
                self.statement = parse_statement_text(self.extracted_text, closing_date=closing)
            self.statement.filename = self.path.name
            self.statement.source_hash = hashlib.sha256(self.path.read_bytes()).hexdigest() if self.path.exists() else hashlib.sha256(self.extracted_text.encode()).hexdigest()
            self.closing.blockSignals(True)
            self.closing.setDate(QDate.fromString(self.statement.closing_date, 'yyyy-MM-dd'))
            self.closing.blockSignals(False)
            self.warning.setText('\n'.join(self.statement.warnings))
            remembered = self.ledger.remembered_card(self.statement)
            if remembered:
                index = self.card.findData(remembered)
                self.card.setCurrentIndex(max(0, index))
            self.preview()
        except Exception as exc:
            self.failed(str(exc))

    def preview(self, *args):
        self.preview_valid = False
        self.save.setEnabled(False)
        self.confirm.setChecked(False)
        if not self.statement:
            return
        card_id = self.card.currentData()
        if not card_id:
            self.summary.setText(f'{len(self.statement.transactions)} transactions found. Select their card to continue.')
            self.readiness.setText('Link this statement to a card in your wallet. The account will be remembered.')
            return
        card = next(c for c in self.cards if c['id'] == card_id)
        expected_issuer = {'bofa': 'bank-of-america', 'amex': 'american-express'}.get(self.statement.issuer, self.statement.issuer)
        if card['issuerId'] not in (expected_issuer, 'custom'):
            self.failed('The selected card issuer does not match this statement.')
            return
        try:
            self.rows = self.ledger.preview(self.statement, card_id)
        except ValueError as exc:
            self.failed(str(exc))
            return
        self.preview_valid = True
        self.warning.setText('\n'.join(self.statement.warnings))
        groups = defaultdict(list)
        for row in self.rows:
            if row['kind'] in ('purchase', 'refund') and row['include'] and not row['category']:
                groups[row['merchant_key']].append(row)
        self.merchant_widgets = {}
        self.merchants.setRowCount(len(groups))
        saved = self.ledger.rules()
        for index, (key, purchases) in enumerate(groups.items()):
            name = ScrollSafeComboBox()
            name.setEditable(True)
            name.addItem(key.title())
            for rule in saved:
                if name.findText(rule['display_name']) < 0:
                    name.addItem(rule['display_name'])
            name.setToolTip('\n'.join(p['description'] + (' · source label: ' + p['issuer_category'] if p['issuer_category'] else '') for p in purchases))
            category = category_picker(self.categories)
            scope = ScrollSafeComboBox()
            scope.addItem('All my cards', 'global')
            scope.addItem('This issuer only', f'issuer:{self.statement.issuer}')
            scope.addItem('This card only', f'card:{card_id}')
            self.merchants.setCellWidget(index, 0, name)
            self.merchants.setItem(index, 1, QTableWidgetItem(str(len(purchases))))
            self.merchants.setCellWidget(index, 2, category)
            self.merchants.setCellWidget(index, 3, scope)
            self.merchant_widgets[key] = (name, category, scope)
            category.selectionChanged.connect(self.refresh_categories)
            name.currentTextChanged.connect(lambda value, combo=category: self.link_saved(value, combo))
        self.tabs.setTabText(0, f'New merchants ({len(groups)})')
        self.transactions.setRowCount(len(self.rows))
        self.row_categories, self.include_widgets = [], []
        for index, row in enumerate(self.rows):
            self.transactions.setItem(index, 0, QTableWidgetItem(row['description']))
            self.transactions.item(index, 0).setToolTip(row['description'] + f"\nSource page {row['page']} · posted {row['posting_date']}")
            for col, value in enumerate([row['date'], amount(row['amount_cents']), row['kind'].title()], 1):
                self.transactions.setItem(index, col, QTableWidgetItem(value))
            include = QCheckBox('Skip duplicate' if row['duplicate'] else 'Include')
            include.setChecked(row['include'])
            include.setToolTip('Checking a detected duplicate explicitly keeps it as another purchase.' if row['duplicate'] else 'Uncheck to omit this transaction.')
            include.toggled.connect(lambda checked, item=row: self.include_changed(item, checked))
            self.transactions.setCellWidget(index, 4, include)
            self.include_widgets.append(include)
            if row['kind'] in ('purchase', 'refund'):
                category = category_picker(self.categories, placeholder=False)
                category.insertItem(0, 'Use merchant rule', None)
                category.setCurrentIndex(0)
                category.selectionChanged.connect(lambda item=row, combo=category: self.override_changed(item, combo))
                self.transactions.setCellWidget(index, 5, category)
                self.row_categories.append(category)
            else:
                self.transactions.setItem(index, 5, QTableWidgetItem('Excluded from spending'))
                self.row_categories.append(None)
            one_off = QCheckBox('One-off')
            one_off.setChecked(row.get('one_off', False))
            one_off.setEnabled(row['kind'] in ('purchase', 'refund'))
            one_off.setToolTip('Keep in history, but exclude from recommendation spending. Mark a matching refund separately.')
            one_off.toggled.connect(lambda checked, item=row: item.update(one_off=checked))
            self.transactions.setCellWidget(index, 6, one_off)
        duplicates = sum(row['duplicate'] for row in self.rows)
        self.summary.setText(f'{len(self.rows)} transactions · {len(groups)} new merchants · {duplicates} duplicates · {amount(self.statement.purchases_cents)} purchases')
        if not groups:
            self.tabs.setCurrentIndex(1)
        self.refresh_categories()

    def link_saved(self, name, combo):
        for rule in self.ledger.rules():
            if rule['display_name'] == name:
                index = combo.findData(rule['category'])
                if index >= 0:
                    combo.setCurrentIndex(index)
                break
        self.validate()

    def mappings(self):
        return {key: {'name': name.currentText(), 'category': category.currentData(), 'scope': scope.currentData()}
                for key, (name, category, scope) in self.merchant_widgets.items() if category.currentData() and name.currentText().strip()}

    def refresh_categories(self, *args):
        mappings = self.mappings()
        for row, combo in zip(self.rows, getattr(self, 'row_categories', [])):
            if combo and not row['manual_override']:
                selected = mappings.get(row['merchant_key'], {}).get('category') or row.get('category')
                combo.setItemText(0, 'Use merchant rule' + (' · ' + category_label(selected) if selected else ' · choose above'))
        self.validate()

    def include_changed(self, row, checked):
        row['include'] = checked
        self.validate()

    def override_changed(self, row, combo):
        row['manual_override'] = combo.currentData() is not None
        row['override_category'] = combo.currentData()
        self.validate()

    def validate(self, *args):
        mappings = self.mappings()
        included = [row for row in self.rows if row['include']]
        unresolved = {row['merchant_key'] for row in included if row['kind'] in ('purchase', 'refund') and
                      not (row.get('override_category') or mappings.get(row['merchant_key'], {}).get('category') or row.get('category'))}
        dates_match = self.statement and self.statement.closing_date == self.closing.date().toString('yyyy-MM-dd')
        unfinished_search = any(row['include'] and combo and not combo.selection_valid()
                                for row, combo in zip(self.rows, getattr(self, 'row_categories', [])))
        ready = bool(self.preview_valid and included and not unresolved and not unfinished_search and self.card.currentData() and dates_match and self.confirm.isChecked())
        self.save.setEnabled(ready)
        self.readiness.setText(f'Choose categories for {len(unresolved)} new merchants.' if unresolved else
                               'Select a matching category to finish your search.' if unfinished_search else
                               'No new transactions to save. Duplicates are skipped.' if not included else
                               'Check the date and review confirmation to save.' if not ready else f'Ready to save {len(included)} transactions locally.')

    def commit(self):
        self.validate()
        if not self.save.isEnabled():
            return
        try:
            rows = [{**row, 'category': row['override_category'] if row['manual_override'] else row['category']} for row in self.rows]
            self.statement.date_confirmed = True
            choices = self.mappings()
            # Save rules only for included transactions that use them.
            used = {r['merchant_key'] for r in self.rows if r['include'] and not r['manual_override']}
            self.imported_count = self.ledger.commit(self.statement, self.card.currentData(), rows,
                                                    {key: value for key, value in choices.items() if key in used})
            self.accept()
        except Exception as exc:
            QMessageBox.warning(self, 'Statement could not be saved', str(exc))


class ImportsPage(QWidget):
    def __init__(self, owner):
        super().__init__()
        self.owner = owner
        self.ledger = Ledger(owner.directory)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(16)
        actions = QHBoxLayout()
        self.summary = text('Import statements. Remember merchants.', 'sectionTitle')
        actions.addWidget(self.summary, 1)
        recurring = QPushButton('Recurring charges')
        recurring.clicked.connect(self.show_recurring)
        actions.addWidget(recurring)
        import_button = QPushButton('Import CSV or PDF')
        import_button.setObjectName('primary')
        import_button.setIcon(icon('plus', '#ffffff', 16))
        import_button.clicked.connect(self.import_statement)
        actions.addWidget(import_button)
        layout.addLayout(actions)
        layout.addWidget(text('CSV exports with mapped columns, or BOFA and Amex PDFs with selectable text. Choose new merchant categories once.', 'muted', True))
        self.filter_bar = QWidget()
        filters = QHBoxLayout(self.filter_bar)
        filters.setContentsMargins(0, 0, 0, 0)
        self.search = QLineEdit()
        self.search.setPlaceholderText('Search merchant or description')
        self.search.setClearButtonEnabled(True)
        self.period = ScrollSafeComboBox()
        self.card_filter = ScrollSafeComboBox()
        self.order = ScrollSafeComboBox()
        self.order.addItems(['Newest first', 'Oldest first', 'Largest amount', 'Merchant A–Z'])
        for control in (self.search, self.period, self.card_filter, self.order):
            filters.addWidget(control, 1)
        self.drill_filter = None
        self.filter_hint = text('', 'muted', True)
        clear = QPushButton('Clear filters')
        clear.clicked.connect(self.clear_filters)
        filters.addWidget(clear)
        layout.addWidget(self.filter_bar)
        layout.addWidget(self.filter_hint)
        self.tabs = QTabWidget()
        self.transactions = grid(['MERCHANT / DESCRIPTION', 'DATE', 'AMOUNT', 'CATEGORY', 'CARD', 'ONE-OFF'])
        self.transactions.itemDoubleClicked.connect(self.edit_transaction)
        self.transactions.itemChanged.connect(self.one_off_changed)
        self.transactions.horizontalHeaderItem(5).setToolTip('Exclude unusual purchases from imported monthly averages used for recommendations.')
        self.transactions.horizontalHeader().setMinimumSectionSize(100)
        self.transactions.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
        self.transactions.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        for col, width in ((0, 240), (1, 110), (2, 110), (3, 210), (4, 210), (5, 100)):
            self.transactions.setColumnWidth(col, width)
        self.transactions.setColumnHidden(4, True)
        self.transactions.setColumnHidden(5, True)
        self.tabs.addTab(self.transactions, 'Transactions')
        self.merchants = grid(['MERCHANT', 'DEFAULT CATEGORY', 'APPLIES TO'])
        self.merchants.itemDoubleClicked.connect(self.edit_rule)
        self.tabs.addTab(self.merchants, 'Saved merchants')
        self.accounts = grid(['STATEMENT ACCOUNT', 'LINKED WALLET CARD', 'STATEMENTS'])
        self.accounts.itemDoubleClicked.connect(self.edit_account)
        self.tabs.addTab(self.accounts, 'Accounts')
        self.imports = grid(['IMPORTED FILE', 'CLOSING DATE', 'SAVED TRANSACTIONS'])
        self.tabs.addTab(self.imports, 'Imported files')
        layout.addWidget(self.tabs, 1)
        self.extra_columns = QCheckBox('Show card and one-off columns')
        self.extra_columns.toggled.connect(lambda checked: [self.transactions.setColumnHidden(col, not checked) for col in (4, 5)])
        layout.addWidget(self.extra_columns)
        self.stale_button = QPushButton('Statement history changed · Update recommendation inputs…')
        self.stale_button.clicked.connect(self.apply_spending)
        layout.addWidget(self.stale_button)
        footer = QHBoxLayout()
        edit = self.edit_button = QPushButton('Edit selected category')
        self.tabs.currentChanged.connect(lambda index: edit.setText('Remove selected import' if index == 3 else 'Change linked card' if index == 2 else 'Edit selected category'))
        edit.clicked.connect(self.edit_selected)
        footer.addWidget(edit)
        self.details_button = QPushButton('View selected purchase')
        self.details_button.clicked.connect(self.show_transaction)
        footer.addWidget(self.details_button)
        footer.addStretch()
        apply = QPushButton('Use imported spending')
        apply.clicked.connect(self.apply_spending)
        footer.addWidget(apply)
        layout.addLayout(footer)
        self.status = text('', 'muted', True)
        layout.addWidget(self.status)
        self.search.textChanged.connect(self.reload)
        for control in (self.period, self.card_filter, self.order):
            control.currentIndexChanged.connect(self.reload)
        for table in (self.transactions, self.merchants, self.accounts, self.imports):
            table.itemSelectionChanged.connect(self.update_actions)
            table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.tabs.currentChanged.connect(self.update_actions)
        self.reload()

    def show_recurring(self):
        from .recurring_ui import RecurringDialog
        RecurringDialog(self.owner).exec()

    def categories(self):
        result = set(DEFAULT_CATEGORIES) | set(EXTRA_CATEGORIES) | set(self.owner.profile.get('spend', {}))
        result |= {canonical(c) for card in self.owner.cards for rate in card.get('earningRates', []) for c in rate.get('eligibleCategories', [])}
        result -= {'choice_category', 'top_spend_category', 'second_highest_spend_category'}
        return result

    def reload(self, *_):
        all_rows = self.ledger.breakdown_rows()
        for combo, options, title in (
            (self.period, [(m, m) for m in sorted({r['date'][:7] for r in all_rows}, reverse=True)], 'All months'),
            (self.card_filter, [(getattr(self.owner, 'by_id', {}).get(c, {}).get('name', c), c) for c in sorted({r['card_id'] for r in all_rows})], 'All cards'),
        ):
            previous = combo.currentData()
            combo.blockSignals(True)
            combo.clear()
            combo.addItem(title, None)
            for label, key in options:
                combo.addItem(label, key)
            # Preserve a drill-through filter even if it has no matches after an edit.
            if previous and combo.findData(previous) < 0:
                combo.addItem(previous, previous)
            combo.setCurrentIndex(max(0, combo.findData(previous)))
            combo.blockSignals(False)
        query = self.search.text().strip().casefold()
        self.history = [r for r in all_rows if (not query or query in (r['description'] + ' ' + r['merchant_name']).casefold())
                        and (not self.period.currentData() or r['date'].startswith(self.period.currentData()))
                        and (not self.card_filter.currentData() or r['card_id'] == self.card_filter.currentData())
                        and (not self.drill_filter or self.matches_drill(r))]
        order = self.order.currentIndex()
        self.history.sort(key=(lambda r: r['date']) if order < 2 else (lambda r: abs(r['amount_cents'])) if order == 2 else (lambda r: r['merchant_name'].casefold()), reverse=order in (0, 2))
        self.filter_hint.setText((self.drill_filter[2] + ' · ' if self.drill_filter else '') + f'{len(self.history)} of {len(all_rows)} transactions. Clear filters to see all history.')
        self.saved_rules = self.ledger.rules()
        self.saved_accounts = self.ledger.accounts()
        self.saved_imports = self.ledger.imports()
        self.transactions.blockSignals(True)
        self.transactions.setCurrentCell(-1, -1)
        self.transactions.setRowCount(len(self.history))
        for index, row in enumerate(self.history):
            card = getattr(self.owner, 'by_id', {}).get(row['card_id'], {})
            values = [row['description'], row['date'], amount(row['amount_cents']),
                      category_label(row['category']) + (' · override' if row['manual_override'] else '') if row['category'] else row['kind'].title() + ' · excluded',
                      card.get('name', row['card_id'])]
            for col, value in enumerate(values):
                item=QTableWidgetItem(value)
                if col==1:item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
                elif col==2:item.setTextAlignment(Qt.AlignmentFlag.AlignRight|Qt.AlignmentFlag.AlignVCenter)
                self.transactions.setItem(index, col, item)
                self.transactions.item(index, col).setToolTip(row['description'] + '\n' + card.get('name', row['card_id']) + '\n' + row['filename'] + f" · page {row['page']} · {row['kind']}")
            one_off = QTableWidgetItem('')
            one_off.setData(Qt.ItemDataRole.UserRole, row['id'])
            one_off.setFlags(Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable)
            if row['kind'] in ('purchase', 'refund'):
                one_off.setFlags(one_off.flags() | Qt.ItemFlag.ItemIsUserCheckable)
                one_off.setCheckState(Qt.CheckState.Checked if row['one_off'] else Qt.CheckState.Unchecked)
                one_off.setToolTip('Keep in history and Breakdown; exclude from imported recommendation averages. Mark matching refunds separately.')
            else:
                one_off.setText('—')
            self.transactions.setItem(index, 5, one_off)
        self.transactions.blockSignals(False)
        self.merchants.setRowCount(len(self.saved_rules))
        for index, rule in enumerate(self.saved_rules):
            scope = rule['scope']
            values = [rule['display_name'], category_label(rule['category']), 'All cards' if scope == 'global' else scope]
            for col, value in enumerate(values):
                self.merchants.setItem(index, col, QTableWidgetItem(value))
        self.summary.setText(f'{len(all_rows)} transactions · {len(self.saved_rules)} remembered merchants')
        self.imports.setRowCount(len(self.saved_imports))
        for index, imported in enumerate(self.saved_imports):
            for col, value in enumerate([imported['filename'], imported['closing_date'], str(imported['transaction_count'])]):
                self.imports.setItem(index, col, QTableWidgetItem(value))
        self.accounts.setRowCount(len(self.saved_accounts))
        for index, account in enumerate(self.saved_accounts):
            card = getattr(self.owner, 'by_id', {}).get(account['card_id'], {})
            values = [account['issuer'].upper() + ' · ending ' + (account['ending'] or 'not printed'),
                      card.get('name', account['card_id']), str(account['statement_count'])]
            for col, value in enumerate(values):
                self.accounts.setItem(index, col, QTableWidgetItem(value))
        self.status.setText('Check One-off for unusual purchases to exclude them from recommendation averages. Double-click a purchase to change its category. Payments and interest do not count as spending.')
        breakdown = getattr(self.owner, 'breakdown_page', None)
        if breakdown:
            breakdown.reload()
        self.update_stale()
        self.update_actions()

    def spending_signature(self):
        spending, months = self.ledger.monthly_spending()
        return hashlib.sha256(json.dumps([spending, months], sort_keys=True).encode()).hexdigest()

    def update_stale(self):
        stale = self.owner.profile.get('spending_source') == 'imports' and self.owner.profile.get('spending_signature') != self.spending_signature()
        self.owner.profile['spending_stale'] = stale
        self.stale_button.setVisible(stale)
        if hasattr(self.owner, 'matches_stale'):
            self.owner.matches_stale.setVisible(stale)
        if hasattr(self.owner, 'timer'):
            self.owner.save_profile()

    def update_actions(self, *_):
        if not hasattr(self, 'edit_button'):
            return
        index = self.tabs.currentIndex()
        table = (self.transactions, self.merchants, self.accounts, self.imports)[index]
        selected = table.currentRow() >= 0
        self.edit_button.setEnabled(selected)
        self.details_button.setEnabled(index == 0 and selected)
        self.filter_bar.setVisible(index == 0)
        self.filter_hint.setVisible(index == 0)
        self.extra_columns.setVisible(index == 0)

    def clear_filters(self):
        self.drill_filter = None
        self.search.clear()
        self.period.setCurrentIndex(0)
        self.card_filter.setCurrentIndex(0)
        self.reload()

    def matches_drill(self, row):
        field, value, _ = self.drill_filter
        return row['kind'] in ('purchase', 'refund') and ((row.get(field) or 'uncategorized') == value if field != 'date' else row['date'].startswith(value))

    def show_group(self, grouping, key, label, month=None, card=None):
        self.clear_filters()
        self.drill_filter = ({'categories': 'category', 'merchants': 'merchant_group', 'cards': 'card_id', 'months': 'date'}[grouping], key, label)
        if month:
            self.period.setCurrentIndex(self.period.findData(month))
        if card:
            self.card_filter.setCurrentIndex(self.card_filter.findData(card))
        self.tabs.setCurrentIndex(0)
        self.reload()
        self.owner.navigate('imports')

    def show_transaction(self):
        index = self.transactions.currentRow()
        if not 0 <= index < len(self.history):
            return
        row = self.history[index]
        dialog = QDialog(self)
        dialog.setWindowTitle('Purchase details')
        dialog.resize(520, 360)
        layout = QVBoxLayout(dialog)
        layout.addWidget(text(row['description'], 'sectionTitle', True))
        card = self.owner.by_id.get(row['card_id'], {}).get('name', row['card_id'])
        layout.addWidget(text(f"{row['date']} · {amount(row['amount_cents'])} · {row['kind'].title()}\n{card}\n{category_label(row['category']) if row['category'] else 'Excluded from spending'}", 'muted', True))
        one_off = QCheckBox('One-off purchase: exclude from recommendation averages')
        one_off.setChecked(bool(row['one_off']))
        one_off.setEnabled(row['kind'] in ('purchase', 'refund'))
        layout.addWidget(one_off)
        layout.addWidget(text('Still included in actual spending. Mark any matching refund separately.', 'muted', True))
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(dialog.accept)
        buttons.rejected.connect(dialog.reject)
        layout.addWidget(buttons)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            if one_off.isEnabled():
                self.ledger.set_one_off(row['id'], one_off.isChecked())
            self.reload()

    def import_statement(self):
        cards = [card for card in self.owner.cards if card['id'] in self.owner.profile['wallet']]
        if not cards:
            QMessageBox.information(self, 'Add your card first', 'Add the statement’s card to your wallet, then import its CSV or PDF.')
            self.owner.navigate('explore')
            return
        path, _ = QFileDialog.getOpenFileName(self, 'Choose a statement CSV or PDF', '', 'Statement files (*.csv *.pdf);;CSV exports (*.csv);;PDF statements (*.pdf)')
        if not path:
            return
        dialog = ImportDialog(path, self.ledger, cards, self.categories(), self)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            self.reload()
            self.status.setText(f'Saved {dialog.imported_count} new transactions. Merchant choices will be reused on future imports.')
            self.apply_spending()

    def choose_category(self, title, current, history=False):
        dialog = QDialog(self)
        dialog.setWindowTitle(title)
        layout = QVBoxLayout(dialog)
        layout.addWidget(text(title, 'sectionTitle'))
        category = category_picker(self.categories(), current, placeholder=False)
        layout.addWidget(category)
        apply = QCheckBox('Also update past purchases using this merchant rule')
        if history:
            layout.addWidget(apply)
            layout.addWidget(text('Single-purchase overrides are preserved.', 'muted'))
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(dialog.accept)
        buttons.rejected.connect(dialog.reject)
        layout.addWidget(buttons)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            return category.currentData(), apply.isChecked()
        return None

    def edit_rule(self, *args):
        index = self.merchants.currentRow()
        if index < 0:
            return
        rule = self.saved_rules[index]
        choice = self.choose_category('Change ' + rule['display_name'] + ' default', rule['category'], True)
        if choice:
            self.ledger.update_rule(rule['scope'], rule['merchant_key'], choice[0], choice[1])
            self.reload()

    def edit_selected(self):
        if self.tabs.currentIndex() == 0:
            self.edit_transaction()
        elif self.tabs.currentIndex() == 1:
            self.edit_rule()
        elif self.tabs.currentIndex() == 2:
            self.edit_account()
        else:
            self.remove_import()

    def remove_import(self):
        index = self.imports.currentRow()
        if index < 0:
            return
        imported = self.saved_imports[index]
        result = QMessageBox.question(self, 'Remove this import?',
            'Remove the transactions originally saved from this file? Saved merchant categories and account links will remain. You can import the file again. Spending inputs stay as they are until you choose Use imported spending.',
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No, QMessageBox.StandardButton.No)
        if result == QMessageBox.StandardButton.Yes:
            self.ledger.remove_import(imported['id'])
            self.reload()

    def edit_account(self, *args):
        index = self.accounts.currentRow()
        if index < 0:
            return
        account = self.saved_accounts[index]
        dialog = QDialog(self)
        dialog.setWindowTitle('Link this account to a wallet card')
        layout = QVBoxLayout(dialog)
        layout.addWidget(text('Change the linked card', 'sectionTitle'))
        choices = ScrollSafeComboBox()
        expected = {'bofa': 'bank-of-america', 'amex': 'american-express'}.get(account['issuer'], account['issuer'])
        for card in self.owner.cards:
            if card['id'] in self.owner.profile['wallet'] and card['issuerId'] in (expected, 'custom'):
                choices.addItem(card['name'], card['id'])
        choices.setCurrentIndex(max(0, choices.findData(account['card_id'])))
        layout.addWidget(choices)
        layout.addWidget(text('Existing purchases stay in history and will be associated with this card. Their categories are preserved.', 'muted', True))
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(dialog.accept)
        buttons.rejected.connect(dialog.reject)
        layout.addWidget(buttons)
        if dialog.exec() == QDialog.DialogCode.Accepted and choices.currentData():
            self.ledger.reassign_account(account['account_key'], choices.currentData())
            self.reload()

    def one_off_changed(self, item):
        if item.column() != 5 or not (item.flags() & Qt.ItemFlag.ItemIsUserCheckable):
            return
        ident = item.data(Qt.ItemDataRole.UserRole)
        one_off = item.checkState() == Qt.CheckState.Checked
        self.ledger.set_one_off(ident, one_off)
        for row in self.history:
            if row['id'] == ident:
                row['one_off'] = int(one_off)
        if self.owner.profile.get('spending_source') == 'imports':
            self.set_spending(self.ledger.monthly_spending()[0], navigate=False)
            self.status.setText('One-off setting saved. Imported monthly spending and recommendations updated; transaction history is unchanged.')
        else:
            self.status.setText('One-off setting saved. Choose Use imported spending to update recommendations; your manual inputs are unchanged.')

    def edit_transaction(self, *args):
        if args and isinstance(args[0], QTableWidgetItem) and args[0].column() == 5:
            return
        index = self.transactions.currentRow()
        if index < 0:
            return
        row = self.history[index]
        if row['kind'] not in ('purchase', 'refund'):
            return
        choice = self.choose_category('Override this purchase only', row['category'])
        if choice:
            self.ledger.override_transaction(row['id'], choice[0])
            self.reload()

    def apply_spending(self):
        spending, months = self.ledger.monthly_spending()
        if not months and not self.owner.profile.get('spending_stale'):
            self.status.setText('Import a statement first to calculate monthly spending.')
            return
        dialog = QDialog(self)
        dialog.setWindowTitle('Use statement-based monthly spending')
        layout = QVBoxLayout(dialog)
        layout.addWidget(text('Use your imported spending?', 'sectionTitle'))
        layout.addWidget(text('This replaces the current spending inputs with each card’s average across its imported statement closing months, then adds the card averages together. Use complete statements for a representative average.', 'muted', True))
        one_off_count = sum(bool(row['one_off']) for row in self.ledger.transactions())
        layout.addWidget(text(f'{one_off_count} one-off transaction(s) excluded. One-off purchases remain in history and Breakdown; imported months still count in the average.', 'muted', True))
        preview = grid(['CATEGORY', 'MONTHLY AVERAGE'])
        preview.setRowCount(len(spending))
        for index, (category, value) in enumerate(sorted(spending.items(), key=lambda pair: -pair[1])):
            preview.setItem(index, 0, QTableWidgetItem(category_label(category)))
            preview.setItem(index, 1, QTableWidgetItem(f'${value:,.2f}'))
        layout.addWidget(preview)
        layout.addWidget(text(f'{len(months)} cards · ' + ', '.join(str(count) + ' imported month(s)' for count in months.values()) + '. Refunds reduce spending; negative category totals are set to zero.', 'muted', True))
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Apply | QDialogButtonBox.StandardButton.Cancel)
        buttons.button(QDialogButtonBox.StandardButton.Apply).clicked.connect(dialog.accept)
        buttons.rejected.connect(dialog.reject)
        layout.addWidget(buttons)
        dialog.resize(650, 600)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            self.set_spending(spending)

    def set_spending(self, spending, navigate=True):
        for category in spending:
            if category not in self.owner.spend_widgets:
                self.owner.add_spend_row(category)
        self.owner.applying_imported_spending = True
        try:
            for category, widget in self.owner.spend_widgets.items():
                widget.setValue(spending.get(category, 0))
        finally:
            self.owner.applying_imported_spending = False
        self.owner.profile['spending_source'] = 'imports'
        self.owner.profile['spending_signature'] = self.spending_signature()
        self.owner.profile['spending_stale'] = False
        self.owner.recalculate()
        self.update_stale()
        if navigate:
            self.owner.navigate('spending')
