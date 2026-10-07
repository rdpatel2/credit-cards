"""Native statement review and merchant memory screens."""
from __future__ import annotations

from collections import defaultdict
from datetime import date
import hashlib
from pathlib import Path

from PySide6.QtCore import Qt, QThread, Signal, QTimer, QDate
from PySide6.QtWidgets import (QWidget, QDialog, QVBoxLayout, QHBoxLayout, QFormLayout, QLabel,
    QPushButton, QComboBox, QCheckBox, QDateEdit, QFileDialog, QTabWidget, QTableWidget,
    QTableWidgetItem, QHeaderView, QAbstractItemView, QMessageBox, QDialogButtonBox)

from .engine import DEFAULT_CATEGORIES, canonical, label
from .ledger import Ledger
from .statements import extract_pdf_text, parse_statement_text
from .ui import text, surface, icon

EXTRA_CATEGORIES = {'auto_services': 'Auto services', 'medical': 'Healthcare',
                    'recreation': 'Recreation / golf', 'general_retail': 'General retail', 'rent': 'Rent'}


def amount(cents):
    return f"{'−' if cents < 0 else ''}${abs(cents) / 100:,.2f}"


def category_label(key):
    return EXTRA_CATEGORIES.get(key, label(key))


def category_picker(categories, value=None, placeholder=True):
    combo = QComboBox()
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
            self.result.emit(extract_pdf_text(self.path))
        except Exception as exc:
            self.failed.emit(str(exc))


class ImportDialog(QDialog):
    def __init__(self, path, ledger, cards, categories, parent=None, extracted_text=None):
        super().__init__(parent)
        self.setWindowTitle('Import a statement · Card Compass')
        self.resize(1080, 830)
        self.setMinimumSize(900, 690)
        self.path = Path(path)
        self.ledger, self.cards, self.categories = ledger, cards, categories
        self.extracted_text = extracted_text
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
        layout.addWidget(text(self.path.name + ' · Your PDF stays on this device.', 'muted'))
        form = QHBoxLayout()
        self.card = QComboBox()
        self.card.addItem('Select the card for this statement…', None)
        for card in cards:
            self.card.addItem(card['name'] + ' · ' + card['issuerId'].replace('-', ' ').title(), card['id'])
        self.card.setMinimumWidth(330)
        form.addWidget(self.card, 1)
        form.addWidget(text('Statement closing date', 'muted'))
        self.closing = QDateEdit(QDate.currentDate())
        self.closing.setCalendarPopup(True)
        self.closing.setDisplayFormat('MMM d, yyyy')
        self.closing.setMinimumHeight(40)
        form.addWidget(self.closing)
        self.reparse = QPushButton('Read dates again')
        self.reparse.clicked.connect(lambda: self.parse(use_selected_date=True))
        form.addWidget(self.reparse)
        layout.addLayout(form)
        self.summary = text('Reading PDF text locally…', 'sectionTitle', True)
        layout.addWidget(self.summary)
        self.warning = text('', 'muted', True)
        layout.addWidget(self.warning)
        self.tabs = QTabWidget()
        self.merchants = grid(['MERCHANT NAME / LINK TO SAVED MERCHANT', 'COUNT', 'CATEGORY', 'REMEMBER FOR'])
        for col, width in ((1, 70), (2, 230), (3, 165)):
            self.merchants.horizontalHeader().setSectionResizeMode(col, QHeaderView.ResizeMode.Fixed)
            self.merchants.setColumnWidth(col, width)
        self.tabs.addTab(self.merchants, 'New merchants')
        self.transactions = grid(['DESCRIPTION', 'DATE', 'AMOUNT', 'TYPE', 'IMPORT', 'CATEGORY'])
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
        self.card.currentIndexChanged.connect(self.preview)
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
            self.parse()

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
        expected_issuer = {'bofa': 'bank-of-america', 'amex': 'american-express'}[self.statement.issuer]
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
            name = QComboBox()
            name.setEditable(True)
            name.addItem(key.title())
            for rule in saved:
                if name.findText(rule['display_name']) < 0:
                    name.addItem(rule['display_name'])
            name.setToolTip('\n'.join(p['description'] + (' · source label: ' + p['issuer_category'] if p['issuer_category'] else '') for p in purchases))
            category = category_picker(self.categories)
            scope = QComboBox()
            scope.addItem('All my cards', 'global')
            scope.addItem('This issuer only', f'issuer:{self.statement.issuer}')
            scope.addItem('This card only', f'card:{card_id}')
            self.merchants.setCellWidget(index, 0, name)
            self.merchants.setItem(index, 1, QTableWidgetItem(str(len(purchases))))
            self.merchants.setCellWidget(index, 2, category)
            self.merchants.setCellWidget(index, 3, scope)
            self.merchant_widgets[key] = (name, category, scope)
            category.currentIndexChanged.connect(self.refresh_categories)
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
                category.currentIndexChanged.connect(lambda unused, item=row, combo=category: self.override_changed(item, combo))
                self.transactions.setCellWidget(index, 5, category)
                self.row_categories.append(category)
            else:
                self.transactions.setItem(index, 5, QTableWidgetItem('Excluded from spending'))
                self.row_categories.append(None)
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
        ready = bool(self.preview_valid and included and not unresolved and self.card.currentData() and dates_match and self.confirm.isChecked())
        self.save.setEnabled(ready)
        self.readiness.setText(f'Choose categories for {len(unresolved)} new merchants.' if unresolved else
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
        import_button = QPushButton('Import statement PDF')
        import_button.setObjectName('primary')
        import_button.setIcon(icon('plus', '#ffffff', 16))
        import_button.clicked.connect(self.import_pdf)
        actions.addWidget(import_button)
        layout.addLayout(actions)
        layout.addWidget(text('BOFA and Amex PDFs with selectable text. You choose each new merchant’s category; the app remembers it.', 'muted', True))
        self.tabs = QTabWidget()
        self.transactions = grid(['MERCHANT / DESCRIPTION', 'DATE', 'AMOUNT', 'CATEGORY', 'CARD'])
        self.transactions.itemDoubleClicked.connect(self.edit_transaction)
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
        footer = QHBoxLayout()
        edit = QPushButton('Edit selected category')
        self.tabs.currentChanged.connect(lambda index: edit.setText('Remove selected import' if index == 3 else 'Change linked card' if index == 2 else 'Edit selected category'))
        edit.clicked.connect(self.edit_selected)
        footer.addWidget(edit)
        footer.addStretch()
        apply = QPushButton('Use imported spending')
        apply.clicked.connect(self.apply_spending)
        footer.addWidget(apply)
        layout.addLayout(footer)
        self.status = text('', 'muted', True)
        layout.addWidget(self.status)
        self.reload()

    def categories(self):
        result = set(DEFAULT_CATEGORIES) | set(EXTRA_CATEGORIES) | set(self.owner.profile.get('spend', {}))
        result |= {canonical(c) for card in self.owner.cards for rate in card.get('earningRates', []) for c in rate.get('eligibleCategories', [])}
        result -= {'choice_category', 'top_spend_category', 'second_highest_spend_category'}
        return result

    def reload(self):
        self.history = self.ledger.transactions()
        self.saved_rules = self.ledger.rules()
        self.saved_accounts = self.ledger.accounts()
        self.saved_imports = self.ledger.imports()
        self.transactions.setRowCount(len(self.history))
        for index, row in enumerate(self.history):
            card = getattr(self.owner, 'by_id', {}).get(row['card_id'], {})
            values = [row['description'], row['date'], amount(row['amount_cents']),
                      category_label(row['category']) + (' · override' if row['manual_override'] else '') if row['category'] else row['kind'].title() + ' · excluded',
                      card.get('name', row['card_id'])]
            for col, value in enumerate(values):
                self.transactions.setItem(index, col, QTableWidgetItem(value))
                self.transactions.item(index, col).setToolTip(row['filename'] + f" · page {row['page']} · {row['kind']}")
        self.merchants.setRowCount(len(self.saved_rules))
        for index, rule in enumerate(self.saved_rules):
            scope = rule['scope']
            values = [rule['display_name'], category_label(rule['category']), 'All cards' if scope == 'global' else scope]
            for col, value in enumerate(values):
                self.merchants.setItem(index, col, QTableWidgetItem(value))
        self.summary.setText(f'{len(self.history)} transactions · {len(self.saved_rules)} remembered merchants')
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
        self.status.setText('Double-click a purchase for a one-time override, or a saved merchant to change its default. Payments and interest do not count as spending.')

    def import_pdf(self):
        cards = [card for card in self.owner.cards if card['id'] in self.owner.profile['wallet']]
        if not cards:
            QMessageBox.information(self, 'Add your card first', 'Add the statement’s card to your wallet, then import its PDF.')
            self.owner.navigate('explore')
            return
        path, _ = QFileDialog.getOpenFileName(self, 'Choose a statement PDF', '', 'PDF statements (*.pdf)')
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
            'Remove the transactions originally saved from this file? Saved merchant categories and account links will remain. You can import the PDF again. Spending inputs stay as they are until you choose Use imported spending.',
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
        choices = QComboBox()
        expected = {'bofa': 'bank-of-america', 'amex': 'american-express'}[account['issuer']]
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

    def edit_transaction(self, *args):
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
        if not months:
            self.status.setText('Import a statement first to calculate monthly spending.')
            return
        dialog = QDialog(self)
        dialog.setWindowTitle('Use statement-based monthly spending')
        layout = QVBoxLayout(dialog)
        layout.addWidget(text('Use your imported spending?', 'sectionTitle'))
        layout.addWidget(text('This replaces the current spending inputs with each card’s average across its imported statement closing months, then adds the card averages together. Use complete statements for a representative average.', 'muted', True))
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

    def set_spending(self, spending):
        for category in spending:
            if category not in self.owner.spend_widgets:
                self.owner.add_spend_row(category)
        for category, widget in self.owner.spend_widgets.items():
            widget.setValue(spending.get(category, 0))
        self.owner.recalculate()
        self.owner.navigate('spending')
