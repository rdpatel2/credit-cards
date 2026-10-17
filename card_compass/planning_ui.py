"""Manual accounts, balance history, savings allocations and debt comparisons."""
from datetime import date
import sqlite3
from PySide6.QtCore import Qt, QDate, QRectF, QPointF
from PySide6.QtGui import QPainter, QColor, QPen
from PySide6.QtWidgets import QWidget, QVBoxLayout, QHBoxLayout, QFormLayout, QLineEdit, QPushButton, QDialogButtonBox, QTableWidgetItem, QTabWidget, QMessageBox
from .ui import FitDialog, Metric, text, ScrollSafeComboBox, ScrollSafeDoubleSpinBox, ScrollSafeDateEdit
from .import_ui import grid
from .planning import ACCOUNT_TYPES, CURRENCIES, debt_scenario
from .finance_ui import dollars
from .theme import color


def money(value, currency='USD'):
    return 'Not recorded' if value is None else f'{currency} {value / 100:,.2f}'


def dated(value=None):
    field = ScrollSafeDateEdit()
    field.setCalendarPopup(True)
    field.setDisplayFormat('yyyy-MM-dd')
    field.setDate(QDate.fromString(value, 'yyyy-MM-dd') if value else QDate.currentDate())
    return field


def choices(values, current=None):
    field = ScrollSafeComboBox()
    for key, title in values:
        field.addItem(title, key)
    if current is not None:
        field.setCurrentIndex(max(0, field.findData(current)))
    return field


class AccountDialog(FitDialog):
    def __init__(self, owner, row=None):
        super().__init__(owner)
        row = row or {}
        self.setWindowTitle('Edit manual account' if row else 'Add manual account or asset')
        self.resize(550, 550)
        layout = QVBoxLayout(self)
        layout.addWidget(text('Enter a balance from your statement or a reasonable asset estimate. Investment balances here are separate from recorded trades; do not add the same account twice.', 'muted', True))
        form = QFormLayout()
        self.name = QLineEdit(row.get('name', ''))
        self.kind = choices([(key, title) for key, (title, side) in ACCOUNT_TYPES.items()], row.get('kind'))
        self.currency = choices([(key, key) for key in CURRENCIES], row.get('currency', 'USD'))
        self.currency.setEnabled(not row)
        self.owner_name = QLineEdit(row.get('owner', 'Me'))
        self.balance = dollars((row.get('amount_cents') or 0) / 100)
        self.as_of = dated(row.get('as_of'))
        self.as_of.setMaximumDate(QDate.currentDate())
        self.apr = ScrollSafeDoubleSpinBox()
        self.apr.setRange(0, 100)
        self.apr.setSuffix(' %')
        self.apr.setValue(row.get('apr_bps', 0) / 100)
        self.minimum = dollars(row.get('minimum_payment_cents', 0) / 100)
        for title, widget in [('Name', self.name), ('Account / asset type', self.kind), ('Currency', self.currency), ('Owner / household member', self.owner_name), ('Asset value or debt owed', self.balance), ('Balance as of', self.as_of), ('Debt APR', self.apr), ('Minimum monthly payment', self.minimum)]:
            form.addRow(title, widget)
        layout.addLayout(form)
        self.kind.currentIndexChanged.connect(self.update_kind)
        self.currency.currentIndexChanged.connect(self.update_currency)
        self.update_kind()
        self.update_currency()
        self.error = text('', 'muted', True)
        layout.addWidget(self.error)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def update_kind(self, *_):
        debt = ACCOUNT_TYPES[self.kind.currentData()][1] == 'liability'
        self.apr.setEnabled(debt)
        self.minimum.setEnabled(debt)

    def update_currency(self, *_):
        for field in (self.balance, self.minimum):
            field.setPrefix(self.currency.currentData()+' ')

    def values(self):
        debt = ACCOUNT_TYPES[self.kind.currentData()][1] == 'liability'
        return dict(name=self.name.text(), kind=self.kind.currentData(), currency=self.currency.currentData(),
                    owner=self.owner_name.text(), balance=self.balance.value(), as_of=self.as_of.date().toString('yyyy-MM-dd'),
                    apr=self.apr.value() if debt else 0, minimum_payment=self.minimum.value() if debt else 0)


class BalanceDialog(FitDialog):
    def __init__(self, owner, row):
        super().__init__(owner)
        self.setWindowTitle('Record balance · '+row['name'])
        layout = QVBoxLayout(self)
        self.value = dollars((row['amount_cents'] or 0) / 100)
        self.value.setPrefix(row['currency']+' ')
        self.as_of = dated()
        self.as_of.setMaximumDate(QDate.currentDate())
        form = QFormLayout()
        form.addRow('Value / debt owed', self.value)
        form.addRow('As of', self.as_of)
        layout.addLayout(form)
        layout.addWidget(text('A balance on a new date adds history. Recording the same date corrects that snapshot. No income, expense or trade is created.', 'muted', True))
        self.error = text('', 'muted', True)
        layout.addWidget(self.error)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)


class GoalDialog(FitDialog):
    def __init__(self, owner, row=None):
        super().__init__(owner)
        row = row or {}
        self.store = owner.planning_store
        self.setWindowTitle('Edit savings goal' if row else 'Add savings goal')
        self.resize(550, 570)
        layout = QVBoxLayout(self)
        layout.addWidget(text('Record money you have set aside for this goal. Linking an account is a reference, not an automatic allocation of its full balance. Budget savings entries do not move money or update goals.', 'muted', True))
        form = QFormLayout()
        self.name = QLineEdit(row.get('name', ''))
        self.kind = choices([('emergency', 'Emergency fund'), ('savings', 'Savings goal')], row.get('kind'))
        self.currency = choices([(key, key) for key in CURRENCIES], row.get('currency', 'USD'))
        self.currency.setEnabled(not row)
        self.target = dollars(row.get('target_cents', 0) / 100)
        self.saved = dollars(row.get('saved_cents', 0) / 100)
        self.target_date = dated(row.get('target_date'))
        if not row:
            self.target_date.setDate(QDate.currentDate().addYears(1))
        self.as_of = dated()
        self.as_of.setMaximumDate(QDate.currentDate())
        self.account = ScrollSafeComboBox()
        self.category = QLineEdit(row.get('category', 'savings'))
        for title, widget in [('Name', self.name), ('Goal type', self.kind), ('Currency', self.currency), ('Target', self.target), ('Amount set aside', self.saved), ('Target date', self.target_date), ('Progress as of', self.as_of), ('Linked account (optional)', self.account), ('Budget category (optional)', self.category)]:
            form.addRow(title, widget)
        layout.addLayout(form)
        self.currency.currentIndexChanged.connect(self.update_accounts)
        self.update_accounts()
        self.account.setCurrentIndex(max(0, self.account.findData(row.get('account_id'))))
        self.error = text('', 'muted', True)
        layout.addWidget(self.error)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def update_accounts(self, *_):
        selected = self.account.currentData()
        self.account.clear()
        self.account.addItem('No linked account', None)
        for row in self.store.accounts():
            if row['side'] == 'asset' and row['currency'] == self.currency.currentData():
                self.account.addItem(row['name'], row['id'])
        self.account.setCurrentIndex(max(0, self.account.findData(selected)))
        for field in (self.saved, self.target):
            field.setPrefix(self.currency.currentData()+' ')

    def values(self):
        return dict(name=self.name.text(), kind=self.kind.currentData(), currency=self.currency.currentData(),
                    target=self.target.value(), target_date=self.target_date.date().toString('yyyy-MM-dd'),
                    saved=self.saved.value(), as_of=self.as_of.date().toString('yyyy-MM-dd'),
                    account_id=self.account.currentData(), category=self.category.text())


class NetWorthChart(QWidget):
    def __init__(self):
        super().__init__()
        self.points = []
        self.setMinimumHeight(190)
        self.setAccessibleName('Recorded net-worth history; exact values are in the table')

    def set_points(self, points):
        self.points = points
        self.setAccessibleDescription('; '.join(row['date']+': '+str(row['net_worth']/100) for row in points))
        self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(QColor(color('#233d38')))
        if len(self.points) < 2:
            painter.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter, 'Record balances on two dates to see a trend.')
            return
        bounds = QRectF(15, 15, self.width()-30, self.height()-50)
        values = [row['net_worth'] for row in self.points]
        days = [date.fromisoformat(row['date']).toordinal() for row in self.points]
        low, high = min(values), max(values)
        points = [QPointF(bounds.left()+(day-days[0])/max(1,days[-1]-days[0])*bounds.width(),
                         bounds.bottom()-(value-low)/max(1,high-low)*bounds.height()) for day,value in zip(days, values)]
        painter.setPen(QPen(QColor(color('#008a74')), 2))
        for first, last in zip(points, points[1:]):
            painter.drawLine(first, last)
        painter.setPen(QColor(color('#233d38')))
        painter.drawText(15, self.height()-10, self.points[0]['date']+'  →  '+self.points[-1]['date'])


class PlanningPage(QWidget):
    def __init__(self, owner, goals=False):
        super().__init__()
        self.owner, self.store, self.goal_mode = owner, owner.planning_store, goals
        self.rows = []
        self.selection_actions = []
        layout = QVBoxLayout(self)
        toolbar = QHBoxLayout()
        self.currency = choices([(key, key) for key in CURRENCIES], 'USD')
        self.currency.currentIndexChanged.connect(self.reload)
        toolbar.addWidget(self.currency)
        toolbar.addStretch()
        retirement = QPushButton('Retirement what-if…')
        retirement.clicked.connect(self.show_retirement)
        toolbar.addWidget(retirement)
        self.add = QPushButton('Add goal' if goals else 'Add account / asset')
        self.add.setObjectName('primary')
        self.add.clicked.connect(self.add_item)
        toolbar.addWidget(self.add)
        layout.addLayout(toolbar)
        self.status = text('', 'muted', True)
        layout.addWidget(self.status)
        self.tabs = QTabWidget()
        layout.addWidget(self.tabs, 1)
        panel = QWidget()
        panel_layout = QVBoxLayout(panel)
        self.table = grid(['Goal', 'Target', 'Set aside', 'Progress', 'Target date', 'Monthly amount', 'Linked account', 'Source'] if goals else ['Account / asset', 'Type', 'Owner', 'Balance', 'As of', 'Source / freshness'])
        self.table.itemDoubleClicked.connect(lambda *_: self.edit_selected())
        panel_layout.addWidget(self.table)
        actions = QHBoxLayout()
        for title, handler in [('Edit selected', self.edit_selected), ('Delete selected', self.delete_selected)]:
            button = QPushButton(title)
            button.clicked.connect(handler)
            actions.addWidget(button)
            self.selection_actions.append(button)
        if not goals:
            self.record_button = QPushButton('Record balance')
            self.record_button.clicked.connect(self.record_balance)
            actions.addWidget(self.record_button)
            self.selection_actions.append(self.record_button)
        else:
            details = QPushButton('Progress details')
            details.clicked.connect(self.goal_details)
            actions.addWidget(details)
            self.selection_actions.append(details)
        panel_layout.addLayout(actions)
        self.tabs.addTab(panel, 'Savings goals' if goals else 'Manual accounts')
        self.table.itemSelectionChanged.connect(self.update_selection)
        if not goals:
            history = QWidget()
            history_layout = QVBoxLayout(history)
            self.chart = NetWorthChart()
            history_layout.addWidget(self.chart)
            self.history_table = grid(['Date', 'Assets', 'Liabilities', 'Recorded net worth', 'Balances included', 'Missing / old'])
            history_layout.addWidget(self.history_table)
            history_layout.addWidget(text('Last recorded balance is carried forward until a newer snapshot. Missing balances are not estimated. Net worth uses manual values, not trade cost basis. Each currency is shown separately.', 'muted', True))
            self.tabs.addTab(history, 'Net-worth history')
            debt = QWidget()
            debt_layout = QVBoxLayout(debt)
            debt_layout.addWidget(text('Educational payoff scenarios: fixed APR, monthly compounding and fixed minimum payments, no new borrowing or fees. Payments freed by paying off a debt stay in the monthly budget. Avalanche pays highest APR first; snowball pays smallest balance first. This is not a lender schedule or a guarantee.', 'muted', True))
            inputs = QHBoxLayout()
            inputs.addWidget(text('Extra monthly payment', 'muted'))
            self.extra = dollars()
            self.extra.valueChanged.connect(self.render_debt)
            inputs.addWidget(self.extra)
            debt_layout.addLayout(inputs)
            self.debt_table = grid(['Scenario', 'Monthly budget', 'Months', 'Interest', 'Remaining', 'Outcome'])
            debt_layout.addWidget(self.debt_table)
            self.debt_view = choices([('baseline', 'Minimums only'), ('avalanche', 'Avalanche + extra'), ('snowball', 'Snowball + extra')])
            self.debt_view.currentIndexChanged.connect(self.render_debt)
            debt_layout.addWidget(self.debt_view)
            self.debt_history = grid(['Month', 'Payment', 'Interest', 'Remaining balance'])
            debt_layout.addWidget(self.debt_history, 1)
            self.tabs.addTab(debt, 'Debt payoff scenarios')
        self.reload()

    @staticmethod
    def fill(table, rows):
        table.clearSelection()
        table.setCurrentCell(-1,-1)
        table.setRowCount(len(rows))
        for i, values in enumerate(rows):
            for j, value in enumerate(values):
                item = QTableWidgetItem(str(value))
                item.setToolTip(str(value))
                table.setItem(i, j, item)

    def reload(self, *_):
        currency = self.currency.currentData()
        if self.goal_mode:
            self.rows = [row for row in self.store.goals() if row['currency'] == currency]
            self.fill(self.table, [[row['name'], money(row['target_cents'],currency), money(row['saved_cents'],currency),
                                   f"{row['progress']:.0%}", row['target_date']+(' · overdue' if row['overdue'] else ''),
                                   money(row['monthly_needed_cents'],currency), row['account_name'] or 'None', self.source_summary(row)] for row in self.rows])
            warnings = sum(bool(row['warning']) for row in self.rows)
            self.status.setText(f'{len(self.rows)} goals · {warnings} allocation/balance warnings. Amounts are recorded allocations, including reviewed service copies, not automatic transfers. Monthly amounts divide remaining savings by time to target; no investment returns assumed.' if self.rows else 'Set an emergency-fund or savings target and record what you have set aside.')
            for i,row in enumerate(self.rows):
                for j in range(self.table.columnCount()):
                    self.table.item(i,j).setToolTip(self.table.item(i,j).text()+'\n'+row['warning']+'\n'+self.source_detail(row))
        else:
            self.rows = [row for row in self.store.accounts() if row['currency'] == currency]
            self.fill(self.table, [[row['name'], ACCOUNT_TYPES[row['kind']][0], row['owner'], money(row['amount_cents'],currency),
                                   row['as_of'] or 'Unknown', self.source_summary(row)+(' · old' if row['stale'] else '')] for row in self.rows])
            for i,row in enumerate(self.rows):self.table.item(i,5).setToolTip(self.source_detail(row))
            total = self.store.net_worth().get(currency, dict(net_worth=0, assets=0, liabilities=0, stale=0, missing=0))
            self.status.setText('Recorded net worth: '+money(total['net_worth'],currency)+' · Assets '+money(total['assets'],currency)+' · Debt '+money(total['liabilities'],currency)+f" · {total['stale']} old balances, {total['missing']} missing. No currency conversion or automatic brokerage valuation." if self.rows else 'Add checking, savings, investments, property, vehicles or debt to start your balance sheet.')
            history = self.store.history(currency)
            self.chart.set_points(history)
            self.fill(self.history_table, [[row['date'], money(row['assets'],currency), money(row['liabilities'],currency), money(row['net_worth'],currency),row['priced'],f"{row['missing']} missing / {row['stale']} old"] for row in history])
            self.extra.setPrefix(currency+' ')
            self.render_debt()
        self.update_selection()

    @staticmethod
    def source_summary(row):
        source=row.get('source_info')
        if source and source.get('source_type')=='institution_balance':return 'Institution '+source['environment'].upper()+(' · date unknown' if source['date_basis']=='user_reviewed_unknown_provider_date' else '')
        return 'Service v'+str(source['remote_version']) if source else 'Manual entry'

    @staticmethod
    def source_detail(row):
        source=row.get('source_info')
        if not source:return row.get('source','Recorded manual entry')
        if source.get('source_type')=='institution_balance':return row['source']+'\nRecorded '+source['date']+'; provider retrieved '+source['retrieved_at']+'; copied '+source['imported_at']+'\n'+source['origin']+' · '+source['balance_mode']+'\nProvider timestamp: '+(source['balance_updated_at'] or 'unavailable; date reviewed by user')+'\nA saved provider balance, not a live verified balance. Sandbox is synthetic.'
        return row['source']+'\nRecorded '+source['date']+'; copied '+source['imported_at']+'\n'+source['origin']+' · version '+str(source['remote_version'])+'\nA reviewed service copy, not a live verified bank balance.'

    def update_selection(self):
        for button in self.selection_actions:
            button.setEnabled(self.selected() is not None)

    def selected(self):
        row = self.table.currentRow()
        return self.rows[row] if 0 <= row < len(self.rows) else None

    def show_retirement(self):
        from .retirement_ui import RetirementDialog
        RetirementDialog(self.owner).exec()

    def add_item(self):
        self.edit_item()

    def edit_selected(self):
        if self.selected():
            self.edit_item(self.selected())

    def edit_item(self, row=None):
        dialog = GoalDialog(self.owner, row) if self.goal_mode else AccountDialog(self.owner, row)
        while dialog.exec() == FitDialog.DialogCode.Accepted:
            try:
                save = self.store.save_goal if self.goal_mode else self.store.save_account
                save(**dialog.values(), ident=row['id'] if row else None)
                self.owner.refresh_planning()
                return
            except (ValueError, OSError, sqlite3.Error) as exc:
                dialog.error.setText(str(exc))

    def record_balance(self):
        row = self.selected()
        if not row:
            return
        dialog = BalanceDialog(self.owner, row)
        while dialog.exec() == FitDialog.DialogCode.Accepted:
            try:
                self.store.record_balance(row['id'], dialog.value.value(), dialog.as_of.date().toString('yyyy-MM-dd'))
                self.owner.refresh_planning()
                return
            except (ValueError, OSError, sqlite3.Error) as exc:
                dialog.error.setText(str(exc))

    def delete_selected(self):
        row = self.selected()
        if not row:
            return
        message = 'Delete '+row['name']+' and its recorded history?'
        if not self.goal_mode:
            message += ' Linked savings goals keep their allocations but lose this account link.'
        if QMessageBox.question(self, 'Delete recorded item?', message, QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No, QMessageBox.StandardButton.No) != QMessageBox.StandardButton.Yes:
            return
        try:
            (self.store.delete_goal if self.goal_mode else self.store.delete_account)(row['id'])
            self.owner.refresh_planning()
        except (OSError, sqlite3.Error) as exc:
            QMessageBox.warning(self, 'Could not delete', str(exc))

    def goal_details(self):
        row = self.selected()
        if row:
            QMessageBox.information(self, row['name'], f"Set aside: {money(row['saved_cents'],row['currency'])}\nRemaining: {money(row['remaining_cents'],row['currency'])}\nProgress recorded: {row['as_of'] or 'Not recorded'}\nLinked account: {row['account_name'] or 'None'}\nBudget category: {row['category'] or 'None'}\n\n{row['warning'] or 'Allocations are entered manually and are not added to net worth again.'}")

    def render_debt(self, *_):
        if self.goal_mode or not hasattr(self, 'debt_history'):
            return
        currency = self.currency.currentData()
        accounts = [row for row in self.store.accounts() if row['currency'] == currency]
        scenarios = {'baseline': debt_scenario(accounts), 'avalanche': debt_scenario(accounts,self.extra.value(),'avalanche'),
                     'snowball': debt_scenario(accounts,self.extra.value(),'snowball')}
        has_debt = any(row['side']=='liability' and row['amount_cents'] for row in accounts)
        self.fill(self.debt_table, [[name.title(), money(row['budget_cents'],currency),row['months'],money(row['interest_cents'],currency),
                                    money(row['remaining_cents'],currency),'No debt balances recorded' if not has_debt else 'Repaid in scenario' if row['paid_off'] else row['reason']] for name,row in scenarios.items()])
        self.fill(self.debt_history, [[row['month'],money(row['payment_cents'],currency),money(row['interest_cents'],currency),
                                      money(row['remaining_cents'],currency)] for row in scenarios[self.debt_view.currentData()]['history']])
