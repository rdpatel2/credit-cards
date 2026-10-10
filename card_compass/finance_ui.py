"""Finance overview and monthly budgeting screens."""
from PySide6.QtCore import QDate, Qt, QRectF
from PySide6.QtGui import QColor, QPainter
from PySide6.QtWidgets import (QWidget, QVBoxLayout, QHBoxLayout, QFormLayout,
    QDialogButtonBox, QLineEdit, QCheckBox, QDateEdit, QPushButton, QTableWidgetItem,
    QScrollArea, QMessageBox, QTabWidget, QHeaderView, QAbstractItemView, QLayout)
from .finance import FinanceStore
from .ui import Metric, text, surface, ScrollSafeComboBox, ScrollSafeDoubleSpinBox, ScrollSafeDateEdit, MonthPicker
from .import_ui import grid, amount, category_label
from .breakdown_ui import SpendBars
from .theme import color
from .ui import FitDialog as QDialog

CATEGORIES = {'rent': 'Rent / mortgage', 'utilities': 'Utilities', 'insurance': 'Insurance',
              'subscriptions': 'Subscriptions', 'health': 'Health', 'transport': 'Transportation',
              'savings': 'Savings contributions', 'other': 'Other', 'salary': 'Salary',
              'freelance': 'Freelance', 'other_income': 'Other income'}
INCOME_CATEGORIES = {'salary', 'freelance', 'other_income'}


def caption(key):
    return CATEGORIES.get(key, category_label(key))


def dollars(value=0):
    field = ScrollSafeDoubleSpinBox()
    field.setRange(0, 1_000_000_000)
    field.setDecimals(2)
    field.setPrefix('$ ')
    field.setValue(value)
    return field


class EntryDialog(QDialog):
    def __init__(self, owner, month, row=None, monthly_income=False):
        super().__init__(owner)
        self.setWindowTitle('Edit cash flow' if row else 'Add monthly income' if monthly_income else 'Add income or expense')
        self.resize(500, 420)
        layout = QVBoxLayout(self)
        layout.addWidget(text('Track money outside your imported card statements.', 'muted', True))
        form = QFormLayout()
        self.name = QLineEdit((row or {}).get('name', 'Monthly income' if monthly_income else ''))
        self.kind = ScrollSafeComboBox()
        self.kind.addItem('Income', 'income')
        self.kind.addItem('Expense', 'expense')
        self.kind.setCurrentIndex(0 if (row or {}).get('kind', 'income' if monthly_income else 'expense') == 'income' else 1)
        self.category = ScrollSafeComboBox()
        self.category.setEditable(True)
        self.category_keys = {
            'income': set(INCOME_CATEGORIES),
            'expense': (set(CATEGORIES) | set(owner.profile.get('spend', {}))) - INCOME_CATEGORIES,
        }
        for entry in owner.finance_store.entries(month):
            self.category_keys[entry['kind']].add(entry['category'])
        if row:
            self.category_keys[row['kind']].add(row['category'])
        self.category_choices = {}
        self.category_kind = None
        self.update_categories()
        if row:
            self.category.setCurrentIndex(self.category.findData(row['category']))
        self.kind.currentIndexChanged.connect(self.update_categories)
        self.value = dollars((row or {}).get('amount_cents', 0) / 100)
        self.start = MonthPicker(QDate.fromString(month + '-01', 'yyyy-MM-dd'))
        self.recurring = QCheckBox('Repeat every month from this month')
        self.recurring.setChecked(bool((row or {}).get('recurring', monthly_income)))
        self.has_end = QCheckBox('End after this month')
        self.end = MonthPicker(QDate.fromString(((row or {}).get('end_month') or month) + '-01', 'yyyy-MM-dd'))
        self.has_end.setChecked(bool((row or {}).get('end_month')))
        for label, field in [('Name', self.name), ('Type', self.kind), ('Category', self.category), ('Amount', self.value), ('Start month', self.start), ('', self.recurring), ('', self.has_end), ('End month', self.end)]:
            form.addRow(label, field)
        layout.addLayout(form)
        self.scope = ScrollSafeComboBox()
        self.scope.addItem('This month and future months', 'future')
        self.scope.addItem('This month only', 'month')
        self.scope.addItem('Entire series, including earlier months', 'all')
        if row:
            for field in (self.recurring, self.has_end, self.end):
                form.setRowVisible(field, False)
            form.labelForField(self.start).setText('Change from')
            self.start.setMinimumDate(QDate.fromString(row['start_month'] + '-01', 'yyyy-MM-dd'))
            if row.get('end_month'):
                self.start.setMaximumDate(QDate.fromString(row['end_month'] + '-01', 'yyyy-MM-dd'))
            if row['recurring']:
                form.addRow('Apply change to', self.scope)
                layout.addWidget(text('Earlier months keep their original amounts. To stop this entry, use “End after this month” in Income & bills.', 'muted', True))
            else:
                self.start.setEnabled(False)
        self.end.setEnabled(self.has_end.isChecked())
        self.has_end.toggled.connect(self.end.setEnabled)
        self.received = QCheckBox('Already received / paid this month')
        self.received.setVisible(row is None)
        layout.addWidget(self.received)
        layout.addWidget(text('Card purchases come from Statements. Do not enter them again here. Card payments are transfers and should not be entered as expenses. Savings contributions are planned cash outflows.', 'muted', True))
        self.error = text('', 'muted', True)
        layout.addWidget(self.error)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def update_categories(self, *_):
        if self.category_kind:
            self.category_choices[self.category_kind] = self.category.currentText()
        kind = self.kind.currentData()
        self.category.clear()
        for key in sorted(self.category_keys[kind], key=caption):
            self.category.addItem(caption(key), key)
        previous = self.category_choices.get(kind)
        if previous:
            self.category.setCurrentText(previous)
        else:
            self.category.setCurrentIndex(self.category.findData('salary' if kind == 'income' else 'rent'))
        self.category_kind = kind

    def values(self):
        index = self.category.findText(self.category.currentText())
        category = self.category.itemData(index) if index >= 0 else self.category.currentText().strip()
        return dict(name=self.name.text(), kind=self.kind.currentData(), category=category,
                    amount=self.value.value(), month=self.start.date().toString('yyyy-MM'),
                    recurring=self.recurring.isChecked(),
                    end_month=self.end.date().toString('yyyy-MM') if self.has_end.isChecked() else None)


class ExpensePie(QWidget):
    """All positive category totals, with a readable legend in both themes."""
    COLORS = ['#39b99b', '#659ee8', '#bd8ae0', '#efb65f', '#e57f91', '#7ebf69', '#55becd', '#a9a2e8']

    def __init__(self):
        super().__init__()
        self.items = []
        self.setMinimumHeight(165)
        self.setAccessibleName('Expense distribution by category')

    def set_items(self, items):
        self.items = sorted([(name, value) for name, value in items if value > 0], key=lambda item: item[1], reverse=True)
        self.setMinimumHeight(max(165, len(self.items) * 27 + 20))
        self.setAccessibleDescription('; '.join(f'{name}: {amount(value)}' for name, value in self.items))
        self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(QColor(color('#233d38')))
        if not self.items:
            painter.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter, 'No expenses yet. Add a bill or select Planned expenses.')
            return
        diameter = min(210, self.height() - 20, self.width() * 0.38)
        circle = QRectF(5, (self.height() - diameter) / 2, diameter, diameter)
        total = sum(value for _, value in self.items)
        start = 90 * 16
        for i, (name, value) in enumerate(self.items):
            shade = QColor(self.COLORS[i % len(self.COLORS)])
            span = round(value / total * 360 * 16) if i < len(self.items) - 1 else start + 270 * 16
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(shade)
            painter.drawPie(circle, start, -span)
            start -= span
            x, y = diameter + 28, 10 + i * 27
            painter.drawRoundedRect(QRectF(x, y + 5, 10, 10), 2, 2)
            painter.setPen(QColor(color('#233d38')))
            label = f'{name} · {amount(value)} · {value / total:.1%}'
            available = max(1, int(self.width() - x - 22))
            painter.drawText(QRectF(x + 20, y, available, 24), Qt.AlignmentFlag.AlignVCenter,
                             painter.fontMetrics().elidedText(label, Qt.TextElideMode.ElideRight, available))


class QuickStartDialog(QDialog):
    """A short, optional plan builder; all amounts are entered by the user."""
    def __init__(self, page):
        super().__init__(page)
        self.page = page
        self.setWindowTitle('Your first monthly plan')
        self.resize(520, 440)
        layout = QVBoxLayout(self)
        layout.addWidget(text('Start with what you know', 'heroTitle'))
        layout.addWidget(text('About 3 minutes · Skip any amount you do not know. You can change everything later. No bank connection is needed.', 'muted', True))
        form = QFormLayout()
        self.fields = {}
        for key, title in [('income', 'Monthly take-home income'), ('rent', 'Rent / mortgage'), ('groceries', 'Grocery budget'), ('transport', 'Transport budget')]:
            field = dollars()
            self.fields[key] = field
            form.addRow(title, field)
        layout.addLayout(form)
        layout.addWidget(text('Income and rent repeat monthly as scheduled, starting in the selected month. Grocery and transport budgets repeat too. Mark income received and bills paid when the money moves.', 'muted', True))
        self.error = text('', 'muted', True)
        layout.addWidget(self.error)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel)
        buttons.button(QDialogButtonBox.StandardButton.Save).setText('Create my plan')
        buttons.accepted.connect(self.save)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def save(self):
        values = {key: round(field.value() * 100) for key, field in self.fields.items()}
        if not any(values.values()):
            self.error.setText('Enter at least one amount, or cancel to explore first.')
            return
        month = self.page.period()
        # One transaction prevents a partially created plan on a write failure.
        try:
            with self.page.store.connection() as db:
                for key, kind, name in [('income', 'income', 'Monthly income'), ('rent', 'expense', 'Rent / mortgage')]:
                    if values[key]:
                        db.execute('INSERT INTO cash_entries(name,kind,category,amount_cents,start_month,recurring) VALUES (?,?,?,?,?,1)',
                                   (name, kind, 'salary' if key == 'income' else key, values[key], month))
                for key in ('groceries', 'transport'):
                    if values[key]:
                        db.execute('INSERT OR REPLACE INTO recurring_targets VALUES (?,?,?)', (key, month, values[key]))
        except Exception as exc:
            self.error.setText(f'Could not save your plan: {exc}')
            return
        self.page.owner.profile['setup_complete'] = True
        self.page.owner.save_profile()
        self.page.refresh_all()
        self.accept()


class FinancePage(QWidget):
    def __init__(self, owner, budget=False):
        super().__init__()
        self.owner, self.budget = owner, budget
        self.store = owner.finance_store
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        content = QWidget()
        content.setObjectName('scrollContent')
        layout = QVBoxLayout(content)
        layout.setSizeConstraint(QLayout.SizeConstraint.SetMinAndMaxSize)
        layout.setContentsMargins(0, 0, 10, 0)
        layout.setSpacing(8)
        controls = QHBoxLayout()
        self.month = MonthPicker(QDate.fromString(owner.profile.get('finance_month', QDate.currentDate().toString('yyyy-MM')) + '-01', 'yyyy-MM-dd'))
        if not self.month.date().isValid():
            self.month.setDate(QDate.currentDate())
        self.month.dateChanged.connect(self.month_changed)
        controls.addWidget(self.month)
        controls.addStretch()
        self.manage_income = QPushButton('Manage income')
        self.manage_income.clicked.connect(self.show_income)
        controls.addWidget(self.manage_income)
        self.add_income = QPushButton('Add income')
        self.add_income.clicked.connect(lambda: self.edit_entry(monthly_income=True))
        controls.addWidget(self.add_income)
        add = QPushButton('Add a bill / expense')
        add.setObjectName('primary')
        add.clicked.connect(lambda: self.edit_entry())
        controls.addWidget(add)
        layout.addLayout(controls)
        self.welcome = surface('hero')
        welcome = QVBoxLayout(self.welcome)
        welcome.addWidget(text('Welcome. Let’s plan your first month.', 'sectionTitle'))
        welcome.addWidget(text('1. Add take-home income  →  2. Plan bills and spending  →  3. Mark money received or paid. Start with estimates; no statements required.', 'muted', True))
        self.quick_start = QPushButton('Build my first plan · about 3 minutes')
        self.quick_start.setObjectName('primary')
        self.quick_start.clicked.connect(self.start_setup)
        welcome.addWidget(self.quick_start)
        layout.addWidget(self.welcome)
        metrics = QHBoxLayout()
        self.metrics = [Metric('Expected income', 'Before you mark it received'), Metric('Planned outflow', 'Budgets and scheduled bills'),
                        Metric('Unallocated', 'Income left to plan'), Metric('Actual cash flow', 'Money received minus money spent')]
        for metric in self.metrics:
            metric.set_compact()
            metrics.addWidget(metric, 1)
        layout.addLayout(metrics)
        self.status = text('', 'muted', True)
        layout.addWidget(self.status)
        self.pending = text('', 'muted', True)
        layout.addWidget(self.pending)
        self.tabs = QTabWidget()
        layout.addWidget(self.tabs, 1)
        self.welcome_space = QWidget()
        layout.addWidget(self.welcome_space, 1)
        entries = QWidget()
        entries_layout = QVBoxLayout(entries)
        entries_layout.addWidget(text('Income and bills outside credit cards. Use the buttons on each row when money moves. Imported card purchases appear in your totals automatically.', 'muted', True))
        self.entries_table = grid(['Name', 'Type', 'Amount', 'Status', 'Actions'])
        self.entries_table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.entries_table.setMinimumHeight(130)
        self.entries_table.horizontalHeader().setMinimumSectionSize(70)
        for col, width in ((1, 100), (2, 120), (3, 120), (4, 222)):
            self.entries_table.horizontalHeader().setSectionResizeMode(col, QHeaderView.ResizeMode.Fixed)
            self.entries_table.setColumnWidth(col, width)
        self.entries_table.itemSelectionChanged.connect(self.update_actions)
        self.entries_table.itemDoubleClicked.connect(self.edit_selected)
        entries_layout.addWidget(self.entries_table)
        self.entries_empty = text('No income or bills yet. Add your income and a regular bill using the buttons above.', 'muted', True)
        entries_layout.addWidget(self.entries_empty)
        actions = QHBoxLayout()
        self.entry_actions = []
        for title, handler in [('Edit selected', self.edit_selected), ('Mark received / paid', self.toggle_posted), ('End after this month', self.end_selected), ('Delete series…', self.delete_selected)]:
            button = QPushButton(title)
            button.clicked.connect(handler)
            actions.addWidget(button)
            self.entry_actions.append(button)
        entries_layout.addLayout(actions)
        self.tabs.addTab(entries, 'Income && bills')
        plan = QWidget()
        plan_layout = QVBoxLayout(plan)
        plan_layout.addWidget(text('Budgets include scheduled bills. Available = planned amount minus actual spending.', 'muted', True))
        self.targets = grid(['Category', 'Budget', 'Scheduled', 'Actual', 'Available'])
        self.targets.setMinimumHeight(100)
        self.targets.horizontalHeader().setMinimumSectionSize(85)
        plan_layout.addWidget(self.targets)
        self.target_source = text('Select a category to see its budget source.', 'muted', True)
        target_source_row = QHBoxLayout()
        target_source_row.addWidget(self.target_source, 1)
        plan_layout.addLayout(target_source_row)
        if budget:
            tools = QHBoxLayout()
            self.category = ScrollSafeComboBox()
            self.category.setEditable(True)
            tools.addWidget(self.category, 1)
            self.target = dollars()
            tools.addWidget(self.target)
            self.repeat_target = QCheckBox('Repeat monthly')
            self.repeat_target.setChecked(True)
            tools.addWidget(self.repeat_target)
            save = QPushButton('Save budget')
            save.clicked.connect(self.set_target)
            tools.addWidget(save)
            plan_layout.addLayout(tools)
            copy = QPushButton('Copy last month…')
            copy.setObjectName('rowAction')
            copy.clicked.connect(self.copy_targets)
            target_source_row.addWidget(copy)
            self.targets.itemSelectionChanged.connect(self.select_target)
            self.category.currentIndexChanged.connect(self.category_target)
        else:
            edit = QPushButton('Edit category budgets')
            edit.clicked.connect(lambda: (owner.navigate('budget'), owner.budget_page.tabs.setCurrentIndex(1)))
            plan_layout.addWidget(edit)
        self.tabs.addTab(plan, 'Category budgets')
        chart = QWidget()
        chart_layout = QVBoxLayout(chart)
        self.pie_mode = ScrollSafeComboBox()
        self.pie_mode.addItem('Actual expenses', 'actual')
        self.pie_mode.addItem('Planned expenses', 'planned')
        self.pie_mode.currentIndexChanged.connect(self.update_pie)
        chart_layout.addWidget(self.pie_mode)
        self.pie = ExpensePie()
        chart_layout.addWidget(self.pie)
        chart_layout.addWidget(text('Actual includes paid bills and net card purchases. Savings are cash outflows; income, card payments and refund-only categories are omitted from the pie.', 'muted', True))
        self.tabs.addTab(chart, 'Spending chart')
        if not budget:
            self.investment_metric = Metric('Investments · cost basis', 'What your remaining positions cost')
            self.card_metric = Metric('Credit cards', 'Cards in your wallet')
            self.investment_metric.set_compact()
            self.card_metric.set_compact()
            accounts = QWidget()
            account_layout = QVBoxLayout(accounts)
            connected = QHBoxLayout()
            connected.addWidget(self.investment_metric, 1)
            connected.addWidget(self.card_metric, 1)
            account_layout.addLayout(connected)
            actions = QHBoxLayout()
            for title, key in [('Open investments', 'stocks'), ('Manage credit cards', 'wallet')]:
                button = QPushButton(title)
                button.clicked.connect(lambda checked=False, k=key: owner.navigate(k))
                actions.addWidget(button)
            account_layout.addLayout(actions)
            account_layout.addStretch()
            self.tabs.addTab(accounts, 'Cards and investments')
            self.bars = SpendBars()
            self.bars.hide()
        self.scroll.setWidget(content)
        outer.addWidget(self.scroll)
        self.reload()

    def start_setup(self):
        QuickStartDialog(self).exec()

    def period(self):
        return self.month.date().toString('yyyy-MM')

    def month_changed(self, date):
        if hasattr(self.owner, 'budget_page'):
            self.owner.set_finance_month(date)
        else:
            self.reload()

    def reload(self, *_):
        if not self.budget:
            portfolio = self.owner.investment_store.portfolio()
            self.investment_metric.value.setText(amount(float(portfolio['basis']) * 100))
            self.card_metric.value.setText(str(len(self.owner.profile.get('wallet', []))))
        summary = self.store.summary(self.period())
        self.summary = summary
        self.update_pie()
        first_use = not self.budget and not self.owner.profile.get('setup_complete') and not summary['entries'] and not self.store.target_values(self.period()) and not summary['card_total']
        self.welcome.setVisible(first_use)
        self.welcome_space.setVisible(first_use)
        self.tabs.setVisible(not first_use)
        for metric, key in zip(self.metrics, ('income', 'planned', 'remaining', 'cash_flow')):
            metric.value.setText(amount(summary[key]))
        self.status.setText(f"Received {amount(summary['received'])} · Spent {amount(summary['actual'])} · Includes {amount(summary['card_total'])} on cards" + (' · Your plan exceeds your income.' if summary['remaining'] < 0 else ''))
        pending = [row for row in summary['entries'] if not row['posted']]
        self.pending.setText(f"{len(pending)} scheduled item(s) still need marking received / paid below." if pending else 'All entries for this month are up to date.' if summary['entries'] else 'Start by adding income or building your first plan.')
        categories = {k: v for k, v in summary['categories'].items() if any(v.values()) or k in summary['target_sources']}
        self.targets.blockSignals(True)
        self.targets.setRowCount(len(categories))
        for i, (key, b) in enumerate(sorted(categories.items(), key=lambda pair: caption(pair[0]))):
            values = [caption(key), amount(b['target']), amount(b['scheduled']), amount(b['actual']), amount(b['planned']-b['actual'])]
            for j, value in enumerate(values):
                item = QTableWidgetItem(value)
                item.setData(Qt.ItemDataRole.UserRole, key)
                self.targets.setItem(i, j, item)
        self.targets.blockSignals(False)
        if self.budget:
            current = self.category.currentText()
            self.category.blockSignals(True)
            self.category.clear()
            from .engine import DEFAULT_CATEGORIES
            for key in sorted(set(CATEGORIES) | set(DEFAULT_CATEGORIES) | set(summary['categories']), key=caption):
                if key not in INCOME_CATEGORIES:
                    self.category.addItem(caption(key), key)
            if current:
                self.category.setCurrentText(current)
            self.category.blockSignals(False)
        self.rows = summary['entries']
        self.entries_table.blockSignals(True)
        self.entries_table.clearSelection()
        self.entries_table.setCurrentCell(-1, -1)
        self.entries_table.setRowCount(len(self.rows))
        for i, row in enumerate(self.rows):
            values = [row['name'], row['kind'].title(), amount(row['amount_cents']), 'Received' if row['posted'] and row['kind'] == 'income' else 'Paid' if row['posted'] else 'Scheduled']
            for j, value in enumerate(values):
                self.entries_table.setItem(i, j, QTableWidgetItem(value))
            controls = QWidget()
            tools = QHBoxLayout(controls)
            tools.setContentsMargins(2, 2, 2, 2)
            edit = QPushButton('Edit')
            edit.setObjectName('rowAction')
            edit.clicked.connect(lambda checked=False, r=row: self.edit_entry(r))
            tools.addWidget(edit)
            post = QPushButton(self.post_label(row))
            post.setObjectName('rowAction')
            post.clicked.connect(lambda checked=False, r=row: self.post_row(r))
            tools.addWidget(post)
            self.entries_table.setCellWidget(i, 4, controls)
        self.entries_table.blockSignals(False)
        self.entries_table.setVisible(bool(self.rows))
        self.entries_empty.setVisible(not self.rows)
        for button in self.entry_actions:
            button.setVisible(bool(self.rows))
        self.update_actions()
        self.scroll.widget().layout().activate()
        self.scroll.widget().updateGeometry()

    @staticmethod
    def post_label(row):
        return 'Undo received' if row['posted'] and row['kind'] == 'income' else 'Undo paid' if row['posted'] else 'Mark received' if row['kind'] == 'income' else 'Mark paid'

    def update_actions(self, *_):
        if not hasattr(self, 'entry_actions'):
            return
        row = self.selected()
        for button in self.entry_actions:
            button.setEnabled(row is not None)
        self.entry_actions[1].setText(self.post_label(row) if row else 'Mark received / paid')
        self.entry_actions[2].setEnabled(bool(row and row['recurring']))

    def refresh_all(self):
        for key in ('overview_page', 'budget_page'):
            page = getattr(self.owner, key, None)
            if page:
                page.reload()

    def selected(self):
        index = self.entries_table.currentRow()
        return self.rows[index] if hasattr(self, 'rows') and 0 <= index < len(self.rows) else None

    def update_pie(self, *_):
        if hasattr(self, 'summary'):
            key = self.pie_mode.currentData()
            self.pie.set_items([(caption(category), values[key]) for category, values in self.summary['categories'].items()])

    def show_income(self):
        dialog = QDialog(self)
        dialog.setWindowTitle('Manage income · ' + self.month.date().toString('MMMM yyyy'))
        dialog.resize(620, 360)
        layout = QVBoxLayout(dialog)
        layout.addWidget(text('Your take-home income sources', 'sectionTitle'))
        listing = grid(['Source', 'Monthly amount', 'Action'])
        listing.horizontalHeader().setSectionResizeMode(2,QHeaderView.ResizeMode.Fixed)
        layout.addWidget(listing)
        def reload():
            rows = [row for row in self.store.entries(self.period()) if row['kind'] == 'income']
            listing.setRowCount(len(rows))
            for i, row in enumerate(rows):
                listing.setItem(i, 0, QTableWidgetItem(row['name']))
                listing.setItem(i, 1, QTableWidgetItem(amount(row['amount_cents'])))
                button = QPushButton('Change details')
                button.setObjectName('rowAction')
                button.setAccessibleName('Change amount and details for '+row['name'])
                button.clicked.connect(lambda checked=False, r=row: (self.edit_entry(r), reload()))
                listing.setCellWidget(i, 2, button)
                button.ensurePolished()
                # Table item padding also surrounds embedded cell widgets.
                listing.setColumnWidth(2,max(listing.columnWidth(2),button.sizeHint().width()+40))
                listing.setRowHeight(i,max(58,button.sizeHint().height()+32))
            hint.setText('Select Change to edit an existing source. Add a source only for separate income.' if rows else 'No income yet. Add the amount you expect to receive each month.')
        hint = text('', 'muted', True)
        layout.addWidget(hint)
        add = QPushButton('Add another income source')
        add.clicked.connect(lambda: (self.edit_entry(monthly_income=True), reload()))
        layout.addWidget(add)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        buttons.rejected.connect(dialog.reject)
        layout.addWidget(buttons)
        reload()
        dialog.exec()

    def edit_entry(self, row=None, monthly_income=False):
        dialog = EntryDialog(self.owner, self.period(), row, monthly_income=monthly_income)
        while dialog.exec() == QDialog.DialogCode.Accepted:
            try:
                values = dialog.values()
                if row:
                    scope = dialog.scope.currentData()
                    if scope == 'all' and row['recurring'] and QMessageBox.question(self, 'Change earlier months too?', 'This also changes amounts in earlier received / paid months. Continue?', QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No, QMessageBox.StandardButton.No) != QMessageBox.StandardButton.Yes:
                        continue
                    self.store.edit_occurrences(row['id'], values['month'], scope, **values)
                else:
                    ident = self.store.save_entry(**values)
                    if dialog.received.isChecked():
                        self.store.post(ident, values['month'], True)
                self.refresh_all()
                return
            except (ValueError, OSError) as exc:
                dialog.error.setText(str(exc))

    def edit_selected(self, *_):
        row = self.selected()
        if row:
            self.edit_entry(row)

    def post_row(self, row):
        self.store.post(row['id'], self.period(), not row['posted'])
        self.refresh_all()

    def toggle_posted(self):
        row = self.selected()
        if row:
            self.post_row(row)

    def end_selected(self):
        row = self.selected()
        if row and QMessageBox.question(self, 'End this recurring entry?', 'Keep this month and earlier history, and stop all later occurrences?') == QMessageBox.StandardButton.Yes:
            self.store.end_entry(row['id'], self.period())
            self.refresh_all()

    def delete_selected(self):
        row = self.selected()
        if row and QMessageBox.question(self, 'Delete entire series?', 'Delete this entry and its received / paid history for all months? Use End after this month to preserve history.', QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No, QMessageBox.StandardButton.No) == QMessageBox.StandardButton.Yes:
            self.store.delete_entry(row['id'])
            self.refresh_all()

    def select_target(self):
        item = self.targets.item(self.targets.currentRow(), 0)
        if item:
            self.category.setCurrentIndex(self.category.findData(item.data(Qt.ItemDataRole.UserRole)))
            self.category_target()

    def category_target(self, *_):
        key = self.category.currentData()
        values = self.store.target_values(self.period())
        value, source = values.get(key, (0, 'No saved budget'))
        self.target.setValue(value / 100)
        self.repeat_target.setChecked(source != 'This month only')
        self.target_source.setText(f'{caption(key) if key else "Category"}: {source}.')
        self.target_source.setToolTip('A budget for this month only overrides a repeating budget for this month.')

    def set_target(self):
        index = self.category.findText(self.category.currentText())
        key = self.category.itemData(index) if index >= 0 else self.category.currentText().strip()
        try:
            setter = self.store.set_recurring_target if self.repeat_target.isChecked() else self.store.set_target
            setter(self.period(), key, self.target.value())
            self.refresh_all()
            self.category_target()
        except ValueError as exc:
            QMessageBox.information(self, 'Budget target', str(exc))

    def copy_targets(self):
        source = self.month.date().addMonths(-1).toString('yyyy-MM')
        values = self.store.target_values(source)
        if not values:
            QMessageBox.information(self, 'Copy budgets', 'There are no saved budgets in the previous month.')
            return
        dialog = QDialog(self)
        dialog.setWindowTitle('Review budgets before copying')
        dialog.resize(550, 400)
        layout = QVBoxLayout(dialog)
        layout.addWidget(text('These amounts replace matching budgets in this month only. Other categories stay as they are.', 'muted', True))
        listing = grid(['Category', 'Current', 'After copy'])
        listing.setRowCount(len(values))
        current = self.store.target_values(self.period())
        for i, (key, (value, _)) in enumerate(sorted(values.items())):
            for j, label in enumerate([caption(key), amount(current.get(key, (0, ''))[0]), amount(value)]):
                listing.setItem(i, j, QTableWidgetItem(label))
        layout.addWidget(listing)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Apply | QDialogButtonBox.StandardButton.Cancel)
        buttons.button(QDialogButtonBox.StandardButton.Apply).clicked.connect(dialog.accept)
        buttons.rejected.connect(dialog.reject)
        layout.addWidget(buttons)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            with self.store.connection() as db:
                for key, (value, _) in values.items():
                    db.execute('INSERT OR REPLACE INTO budget_targets VALUES (?,?,?)', (self.period(), key, value))
            self.refresh_all()
            self.target_source.setText(f'Copied {len(values)} budgets from {source} to {self.period()}.')
