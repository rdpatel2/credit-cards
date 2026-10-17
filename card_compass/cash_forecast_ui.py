"""Editable, local monthly cash scenarios; no inferred account balances or forecasts."""
from copy import deepcopy
from datetime import date
from decimal import Decimal, ROUND_HALF_UP
import sqlite3
from PySide6.QtCore import QDate
from PySide6.QtWidgets import (QWidget, QVBoxLayout, QHBoxLayout, QFormLayout,
    QLineEdit, QPushButton, QCheckBox, QTabWidget, QTableWidgetItem,
    QDialogButtonBox, QScrollArea, QTextEdit, QHeaderView)
from .ui import FitDialog, text, MonthPicker
from .finance_ui import dollars
from .planning_ui import dated, choices, money
from .finance import shift_month
from .import_ui import grid
from .cash_forecast import defaults, forecast, validate_settings


def cash_field(cents=0):
    field = dollars()
    field.setRange(0, 1_000_000_000_000)
    field.setValue(cents / 100)
    field.setProperty('initialValue', field.value())
    field.setProperty('initialCents', cents)
    return field


def cash_value(field):
    if field.value() == field.property('initialValue'):
        return field.property('initialCents')
    return int((Decimal(str(field.value())) * 100).quantize(Decimal('1'), rounding=ROUND_HALF_UP))


class CashForecastDialog(FitDialog):
    def __init__(self, owner):
        super().__init__(owner)
        self.owner = owner
        self.store = owner.cash_forecast_store
        self.setWindowTitle('Cash runway / variable income')
        self.resize(1000, 730)
        load_error = ''
        try:
            self.settings = deepcopy(self.store.load())
        except (ValueError, TypeError, sqlite3.Error, OSError) as exc:
            self.settings = defaults()
            load_error = 'Saved assumptions could not be read; the original record is preserved. Review these defaults before saving. ' + str(exc)
        self.result = None
        self.overrides = deepcopy(self.settings['overrides'])
        layout = QVBoxLayout(self)
        layout.addWidget(text('Explore how your cash could change under different income assumptions.', 'sectionTitle'))
        layout.addWidget(text('Uses current available USD cash and the remaining monthly budget. Monthly totals cannot show whether a bill is affordable on a particular day. No accounts are linked and no money is moved.', 'muted', True))
        self.tabs = QTabWidget()
        layout.addWidget(self.tabs, 1)
        settings_scroll = QScrollArea()
        settings_scroll.setWidgetResizable(True)
        content = QWidget()
        controls = QVBoxLayout(content)
        form = QFormLayout()
        self.opening = cash_field(self.settings['openingCash'])
        self.reserve = cash_field(self.settings['reserve'])
        self.as_of = dated(self.settings['cashAsOf'])
        self.as_of.setMaximumDate(QDate.currentDate())
        self.source = QLineEdit(self.settings['source'])
        self.source.setMaxLength(120)
        self.months = choices([(n, str(n) + ' months') for n in (1, 3, 6, 12, 18, 24)], self.settings['months'])
        if self.months.findData(self.settings['months']) < 0:
            self.months.addItem(str(self.settings['months']) + ' months', self.settings['months'])
            self.months.setCurrentIndex(self.months.count()-1)
        self.mode = choices([('scheduled', 'Recorded monthly income plan'), ('range', 'Entered monthly take-home ranges')], self.settings['incomeMode'])
        self.low = cash_field(self.settings['incomeLow'])
        self.typical = cash_field(self.settings['incomeTypical'])
        self.high = cash_field(self.settings['incomeHigh'])
        self.buffer = cash_field(self.settings['expenseBuffer'])
        for title, field in [('Current available cash (USD)', self.opening), ('Cash as of (must be today)', self.as_of),
                             ('Cash source / counted accounts', self.source), ('Protected cash reserve', self.reserve),
                             ('Horizon', self.months), ('Income assumptions', self.mode), ('Low monthly income', self.low),
                             ('Typical monthly income', self.typical), ('High monthly income', self.high),
                             ('Extra monthly spending buffer', self.buffer)]:
            form.addRow(title, field)
        controls.addLayout(form)
        controls.addWidget(text('Income ranges replace the full monthly income plan; they are not extra earnings. Amounts already marked received are subtracted from remaining receipts. Enter take-home income after taxes/withholding.', 'muted', True))
        self.savings = QCheckBox('Savings contributions leave the cash counted above')
        self.savings.setChecked(self.settings['savingsConsumeCash'])
        controls.addWidget(self.savings)
        controls.addWidget(text('Uncheck only when transfers in the “savings” category stay within counted cash. Credit-card purchases use purchase dates; repayments, interest and statement fees are excluded from the budget. Avoid counting purchases and repayments twice, and include any missing costs in the buffer.', 'muted', True))
        account_row = QHBoxLayout()
        self.cash_account = choices([('', 'Choose a manual account balance recorded today')])
        self.cash_accounts = {}
        for row in owner.planning_store.accounts():
            if row['kind'] in ('checking', 'savings') and row['currency'] == 'USD' and row['as_of'] == date.today().isoformat():
                self.cash_accounts[row['id']] = row
                self.cash_account.addItem(row['name'] + ' · ' + money(row['amount_cents']), row['id'])
        account_row.addWidget(self.cash_account, 1)
        copy = QPushButton('Use this balance')
        copy.clicked.connect(self.use_balance)
        account_row.addWidget(copy)
        controls.addLayout(account_row)
        controls.addWidget(text('This replaces current cash with one balance. It never sums accounts or subtracts the reserve twice. To count several accounts, enter their reviewed available total and name them in the source.', 'muted', True))
        settings_scroll.setWidget(content)
        self.tabs.addTab(settings_scroll, 'Assumptions')

        seasonal = QWidget()
        season_layout = QVBoxLayout(seasonal)
        season_layout.addWidget(text('Optional full-month take-home ranges for seasonal income. Overrides replace either income mode for that month. They do not add a cash transaction.', 'muted', True))
        override_form = QFormLayout()
        self.override_month = MonthPicker(QDate.currentDate())
        self.override_low = cash_field()
        self.override_typical = cash_field()
        self.override_high = cash_field()
        for title, field in [('Month', self.override_month), ('Low', self.override_low), ('Typical', self.override_typical), ('High', self.override_high)]:
            override_form.addRow(title, field)
        season_layout.addLayout(override_form)
        override_actions = QHBoxLayout()
        for title, handler in [('Set monthly override', self.set_override), ('Remove selected override', self.remove_override)]:
            button = QPushButton(title)
            button.clicked.connect(handler)
            override_actions.addWidget(button)
        season_layout.addLayout(override_actions)
        self.override_table = grid(['Month', 'Low income', 'Typical income', 'High income'])
        self.override_table.itemSelectionChanged.connect(self.select_override)
        season_layout.addWidget(self.override_table, 1)
        self.tabs.addTab(seasonal, 'Seasonal overrides')

        history = QWidget()
        history_layout = QVBoxLayout(history)
        history_layout.addWidget(text('Recorded income for the last 12 months. A zero or missing record does not establish that you earned nothing. This is monthly received status, not verified bank deposits. Use it to review your assumptions.', 'muted', True))
        self.history = grid(['Month', 'Income planned', 'Marked received', 'Entries'])
        history_layout.addWidget(self.history, 1)
        self.tabs.addTab(history, 'Income history')

        results = QWidget()
        result_layout = QVBoxLayout(results)
        self.scenario = choices([(n, title) for n, title in enumerate(('Low income', 'Typical income', 'High income'))], 1)
        self.scenario.currentIndexChanged.connect(self.show_result)
        result_layout.addWidget(self.scenario)
        self.result_summary = text('Review assumptions and calculate to see a scenario.', 'muted', True)
        result_layout.addWidget(self.result_summary)
        self.table = grid(['Month', 'Opening cash', 'Remaining receipts', 'Remaining outflow', 'Closing cash', 'Above reserve'])
        self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Fixed)
        self.table.setColumnWidth(0, 95)
        result_layout.addWidget(self.table, 1)
        self.details = QTextEdit()
        self.details.setReadOnly(True)
        self.details.setMaximumHeight(155)
        result_layout.addWidget(self.details)
        self.tabs.addTab(results, 'Scenarios')
        self.error = text(load_error, 'muted', True)
        layout.addWidget(self.error)
        actions = QHBoxLayout()
        calculate = QPushButton('Calculate scenarios')
        calculate.setObjectName('primary')
        calculate.clicked.connect(self.calculate)
        actions.addWidget(calculate)
        save = QPushButton('Save assumptions')
        save.clicked.connect(self.save)
        actions.addWidget(save)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        buttons.rejected.connect(self.reject)
        actions.addWidget(buttons)
        layout.addLayout(actions)
        self.mode.currentIndexChanged.connect(self.update_mode)
        self.update_mode()
        self.refresh_overrides()
        self.refresh_history()
        for field in (self.opening, self.reserve, self.low, self.typical, self.high, self.buffer):
            field.valueChanged.connect(self.invalidate)
        self.source.textChanged.connect(self.invalidate)
        self.as_of.dateChanged.connect(self.invalidate)
        self.months.currentIndexChanged.connect(self.invalidate)
        self.mode.currentIndexChanged.connect(self.invalidate)
        self.savings.toggled.connect(self.invalidate)

    def update_mode(self, *_):
        for field in (self.low, self.typical, self.high):
            field.setEnabled(self.mode.currentData() == 'range')

    def values(self):
        settings = defaults()
        settings.update(openingCash=cash_value(self.opening), reserve=cash_value(self.reserve),
                        cashAsOf=self.as_of.date().toString('yyyy-MM-dd'), source=self.source.text().strip(),
                        months=self.months.currentData(), incomeMode=self.mode.currentData(),
                        incomeLow=cash_value(self.low), incomeTypical=cash_value(self.typical), incomeHigh=cash_value(self.high),
                        expenseBuffer=cash_value(self.buffer), savingsConsumeCash=self.savings.isChecked(),
                        overrides=deepcopy(self.overrides))
        return validate_settings(settings)

    def invalidate(self, *_):
        self.result = None
        self.table.setRowCount(0)
        self.details.clear()
        self.result_summary.setText('Assumptions changed. Calculate again to see current scenarios.')

    def use_balance(self):
        row = self.cash_accounts.get(self.cash_account.currentData())
        if not row:
            self.error.setText('Choose a USD checking/savings balance recorded today, or enter reviewed current cash manually.')
            return
        # Re-read: a balance could be edited in another desktop window while this dialog is open.
        fresh = next((r for r in self.owner.planning_store.accounts() if r['id'] == row['id']), None)
        if not fresh or fresh['as_of'] != date.today().isoformat():
            self.error.setText('This balance is no longer current; review it in Accounts first.')
            return
        self.opening.setValue(fresh['amount_cents'] / 100)
        self.opening.setProperty('initialValue', self.opening.value())
        self.opening.setProperty('initialCents', fresh['amount_cents'])
        self.source.setText('Manual account: ' + fresh['name'])
        self.as_of.setDate(QDate.currentDate())
        self.error.clear()

    def set_override(self):
        try:
            month = self.override_month.date().toString('yyyy-MM')
            updated = deepcopy(self.overrides)
            updated[month] = [cash_value(field) for field in (self.override_low, self.override_typical, self.override_high)]
            candidate = self.values()
            candidate['overrides'] = updated
            validate_settings(candidate)
            self.overrides = updated
            self.refresh_overrides()
            self.invalidate()
            self.error.clear()
        except ValueError as exc:
            self.error.setText(str(exc))

    def select_override(self):
        row = self.override_table.currentRow()
        if row < 0 or not self.override_table.item(row, 0):
            return
        month = self.override_table.item(row, 0).text()
        self.override_month.setDate(QDate.fromString(month + '-01', 'yyyy-MM-dd'))
        for field, value in zip((self.override_low, self.override_typical, self.override_high), self.overrides[month]):
            field.setValue(value / 100)
            field.setProperty('initialValue', field.value())
            field.setProperty('initialCents', value)

    def remove_override(self):
        row = self.override_table.currentRow()
        if row >= 0:
            self.overrides.pop(self.override_table.item(row, 0).text(), None)
            self.refresh_overrides()
            self.invalidate()

    def refresh_overrides(self):
        self.override_table.blockSignals(True)
        self.override_table.setRowCount(len(self.overrides))
        for index, (month, values) in enumerate(sorted(self.overrides.items())):
            for col, value in enumerate([month] + [money(n) for n in values]):
                self.override_table.setItem(index, col, QTableWidgetItem(value))
        self.override_table.blockSignals(False)

    def refresh_history(self):
        self.history.setRowCount(12)
        current = date.today().strftime('%Y-%m')
        for row in range(12):
            month = shift_month(current, row - 11)
            summary = self.owner.finance_store.summary(month)
            values = [month, money(summary['income']), money(summary['received']), str(sum(r['kind'] == 'income' for r in summary['entries']))]
            for col, value in enumerate(values):
                self.history.setItem(row, col, QTableWidgetItem(value))

    def calculate(self):
        try:
            self.result = forecast(self.owner.finance_store, self.values())
            self.show_result()
            self.tabs.setCurrentIndex(3)
            self.error.clear()
        except (ValueError, sqlite3.Error, OSError) as exc:
            self.invalidate()
            self.error.setText(str(exc))

    def show_result(self, *_):
        if self.result is None:
            return
        scenario = self.result['scenarios'][self.scenario.currentData()]
        self.result_summary.setText(scenario['name'] + ' · first closing cash shortfall: ' + (scenario['firstShortfall'] or 'none within horizon') + ' · first below reserve: ' + (scenario['firstBelowReserve'] or 'none within horizon'))
        self.table.setRowCount(len(scenario['rows']))
        for row, values in enumerate(scenario['rows']):
            for col, value in enumerate([values['month']] + [money(values[k]) for k in ('opening', 'remainingReceipts', 'remainingOutflow', 'closing', 'aboveReserve')]):
                self.table.setItem(row, col, QTableWidgetItem(value))
        lines = ['Cash source: ' + self.result['source'] + ' · as of ' + self.result['asOf'],
                 'Protected reserve: ' + money(self.result['reserve']), self.result['assumptions'],
                 'Remaining expenses per category are the larger of unspent budget and still-unpaid scheduled bills. The extra buffer is prorated for the current month. Missing categories, repayments, taxes and fees must be reviewed explicitly.']
        for row in scenario['rows']:
            lines.append(row['month'] + ': ' + row['source'] + ' ' + money(row['incomeAssumption']) + '; received ' + money(row['recordedReceipts']) + '; recorded spending ' + money(row['recordedSpending']) + '; additional buffer ' + money(row['expenseBuffer']))
        lines.extend(scenario['warnings'])
        self.details.setPlainText('\n\n'.join(lines))

    def save(self):
        try:
            settings = self.values()
            self.store.save(settings)
            self.settings = deepcopy(settings)
            self.error.setText('Saved locally and included in portable backups. Scenarios update only when you calculate.')
        except (ValueError, sqlite3.Error, OSError) as exc:
            self.error.setText('Could not save assumptions. ' + str(exc))
