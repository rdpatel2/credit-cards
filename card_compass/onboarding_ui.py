"""A persona-based, persistent checklist that links directly to existing actions."""
from pathlib import Path
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QWidget, QVBoxLayout, QHBoxLayout, QPushButton, QTableWidgetItem, QHeaderView
from .ui import text, ScrollSafeComboBox
from .import_ui import grid
from .onboarding import PATHS, TASKS


class OnboardingPage(QWidget):
    def __init__(self, owner):
        super().__init__()
        self.owner = owner
        layout = QVBoxLayout(self)
        layout.addWidget(text('Start with one useful task', 'heroTitle'))
        layout.addWidget(text('Choose the path that fits you. This checklist uses your local records to show progress; it does not recommend investments. Start with examples if you would rather explore before entering your own data.', 'muted', True))
        toolbar = QHBoxLayout()
        self.persona = ScrollSafeComboBox()
        for key,(title,tasks) in PATHS.items():
            self.persona.addItem(title,key)
        self.persona.setCurrentIndex(max(0,self.persona.findData(owner.profile.get('onboarding_persona','beginner'))))
        self.persona.currentIndexChanged.connect(self.choose_persona)
        toolbar.addWidget(self.persona,1)
        demo = QPushButton('Explore isolated demo')
        demo.setObjectName('primary')
        demo.clicked.connect(owner.start_demo)
        toolbar.addWidget(demo)
        template = QPushButton('Create starter research dashboard')
        template.clicked.connect(self.create_template)
        toolbar.addWidget(template)
        layout.addLayout(toolbar)
        self.progress = text('', 'muted', True)
        layout.addWidget(self.progress)
        self.table = grid(['Task', 'Why it helps', 'Progress', 'Action'])
        self.table.horizontalHeader().setSectionResizeMode(1,QHeaderView.ResizeMode.Stretch)
        self.table.horizontalHeader().setSectionResizeMode(0,QHeaderView.ResizeMode.ResizeToContents)
        self.table.horizontalHeader().setSectionResizeMode(2,QHeaderView.ResizeMode.ResizeToContents)
        self.table.horizontalHeader().setSectionResizeMode(3,QHeaderView.ResizeMode.Fixed)
        self.table.setColumnWidth(3,110)
        layout.addWidget(self.table,1)
        layout.addWidget(text('Day 1: explore and make one plan. Week 1: check your records and make a backup. Month 1: update balances and review goals. US investment/tax tools have limited coverage; no orders are placed by this app.', 'muted',True))
        self.reload()

    def choose_persona(self,*_):
        self.owner.profile['onboarding_persona']=self.persona.currentData()
        self.owner.save_profile()
        self.reload()

    def completion(self):
        owner = self.owner
        reviewed = set(owner.profile.get('onboarding_reviewed',[]))
        month = owner.profile.get('finance_month') or owner.overview_page.period()
        record = owner.profile.get('last_backup')
        try:
            backed_up = isinstance(record,dict) and isinstance(record.get('path'),str) and Path(record['path']).is_file()
        except (OSError,ValueError):
            backed_up = False
        return dict(plan=bool(owner.finance_store.entries(month) or owner.finance_store.target_values(month)) if month else False,
                    statements=bool(owner.finance_store.imports()),accounts=bool(owner.planning_store.accounts()),
                    goals=bool(owner.planning_store.goals()),trades=any(owner.investment_store.state['trades'].values()),
                    notes=any(owner.investment_store.state['notes'].values()),
                    backup=backed_up,**{key:key in reviewed for key in ('demo','terms','coverage','research','health','reports','allocation','performance','runway','retirement')})

    def reload(self,*_):
        tasks = PATHS[self.persona.currentData()][1]
        done = self.completion()
        self.table.setRowCount(len(tasks))
        for i,key in enumerate(tasks):
            title, description = TASKS[key]
            for j,value in enumerate([title,description,'Done' if done.get(key) else 'Next step']):
                item=QTableWidgetItem('Opened' if j==2 and done.get(key) and key in ('demo','terms','coverage','research','health','reports','allocation','performance','runway','retirement') else value)
                item.setToolTip(value)
                self.table.setItem(i,j,item)
            action=QPushButton('Open')
            action.setAccessibleName('Open '+title)
            action.clicked.connect(lambda checked=False,k=key:self.open_task(k))
            self.table.setCellWidget(i,3,action)
            self.table.setRowHeight(i,82)
        self.progress.setText(f"{sum(bool(done.get(key)) for key in tasks)} of {len(tasks)} tasks complete. This is a suggested sequence; you can use any feature whenever you want.")

    def open_task(self,key):
        owner=self.owner
        reviewed = set(owner.profile.get('onboarding_reviewed',[]))
        if key=='demo':owner.start_demo()
        elif key=='plan':owner.navigate('overview');owner.overview_page.start_setup()
        elif key=='statements':owner.navigate('imports');owner.imports_page.import_statement()
        elif key in ('accounts','goals'):owner.navigate(key);owner.pages.currentWidget().add_item()
        elif key=='terms':owner.stocks_page.show_investing_terms()
        elif key=='coverage':owner.overview_page.show_coverage()
        elif key=='runway':owner.overview_page.show_cash_forecast()
        elif key=='retirement':owner.accounts_page.show_retirement()
        elif key=='trades':owner.navigate('stocks');owner.stocks_page.record_trade()
        elif key in ('research','notes'):
            owner.navigate('stocks')
            owner.stocks_page.open_symbol(owner.stocks_page.selected_symbol)
        elif key=='health':owner.navigate('stocks');owner.stocks_page.show_data_health()
        elif key=='allocation':owner.navigate('stocks');owner.stocks_page.show_allocation()
        elif key=='performance':owner.navigate('stocks');owner.stocks_page.show_performance()
        elif key=='reports':owner.navigate('reports')
        elif key=='backup':owner.backup_data()
        if key in ('demo','terms','coverage','research','health','reports','allocation','performance','runway','retirement'):
            reviewed.add(key)
            owner.profile['onboarding_reviewed']=sorted(reviewed)
            owner.save_profile()
        self.reload()

    def create_template(self):
        from .dashboard.tools import starter
        name = 'Trader view' if self.persona.currentData()=='active' else 'Research view' if self.persona.currentData()=='advanced' else 'Investment overview'
        self.owner.stocks_page.import_dashboard_data(dict(schemaVersion=2,dashboard=starter(name)))
        self.owner.navigate('stocks')
