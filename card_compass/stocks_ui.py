"""Native investment shell; dashboard rendering lives in dashboard modules."""
from copy import deepcopy
from datetime import date
import json
from pathlib import Path
import uuid
from PySide6.QtCore import Qt, QDate, QTimer, QUrl
from PySide6.QtGui import QDesktopServices, QKeySequence, QShortcut
from PySide6.QtWidgets import (QWidget, QVBoxLayout, QHBoxLayout, QPushButton, QLineEdit,
 QTabWidget, QDialogButtonBox, QFormLayout, QDateEdit, QSpinBox, QFileDialog,
 QInputDialog, QMessageBox, QCheckBox, QLabel, QMenu, QFrame, QSizePolicy, QTextEdit)
from .catalog import write_json
from .investments import WIDGETS, METRICS, RANGES, widget, page, symbol, position, simulate_sale, validate_workspace, VIEW_COLUMNS, validate_view_settings
from .investment_tax import estimate_tax, coverage as tax_coverage
from .finance_ui import dollars
from .ui import Metric, text, ScrollSafeComboBox, ScrollSafeDoubleSpinBox
from .market_news import SECTORS
from .dashboard.grid_canvas import GridCanvas
from .dashboard.layout_engine import ENGINE, dashboard_layout
from .dashboard.layout_store import LayoutStore
from .dashboard.data_layer import DataLayer
from .dashboard.widget_registry import REGISTRY, create_widget, money
from .dashboard.data_health import DataHealthDialog
from .education import TERMS, definition as term_definition
from .dashboard.gallery import WidgetGallery
from .dashboard.tools import DashboardSettings, STARTERS, starter
from .dashboard.schema import normalize_dashboard
from .ui import FitDialog as QDialog


class StatusLabel(QLabel):
    """Status changes cannot resize the canvas during a pointer gesture."""
    def __init__(self,message):
        super().__init__(message)
        self.setObjectName('muted');self.setFixedHeight(32);self.setMinimumWidth(0)
        self.setSizePolicy(QSizePolicy.Policy.Ignored,QSizePolicy.Policy.Fixed)
        self.setToolTip(message)
    def setText(self,message):
        super().setText(message);self.setToolTip(message)


class ViewSettingsDialog(QDialog):
    def __init__(self, owner):
        super().__init__(owner)
        self.setWindowTitle('Customize investment views')
        layout = QVBoxLayout(self)
        settings = owner.store.state.get('viewSettings', {})
        self.metrics = []
        for index, title in enumerate(('Remaining cost basis', 'Realized gain / loss', 'Market value')):
            check = QCheckBox(title)
            check.setChecked(settings.get('metrics', [True] * 3)[index])
            layout.addWidget(check)
            self.metrics.append(check)
        form = QFormLayout()
        self.height = QSpinBox()
        self.height.setRange(24, 96)
        self.height.setValue(settings.get('rowHeight', 58))
        form.addRow('Table row height', self.height)
        layout.addLayout(form)
        tabs = QTabWidget()
        self.columns = {}
        for name, titles in VIEW_COLUMNS.items():
            panel = QWidget()
            fields = QVBoxLayout(panel)
            self.columns[name] = []
            selected = settings.get('columns', {}).get(name, list(range(len(titles))))
            for index, title in enumerate(titles):
                check = QCheckBox(title)
                check.setChecked(index in selected)
                self.columns[name].append(check)
                fields.addWidget(check)
            fields.addStretch()
            tabs.addTab(panel, name.title())
        layout.addWidget(tabs)
        layout.addWidget(text('Drag table headers to reorder columns. Dashboards, stock tabs, and sector research all support adding, removing, moving, resizing, and configuring widgets.', 'muted', True))
        self.error = text('', 'muted', True)
        layout.addWidget(self.error)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
        self.resize(460, 580)

    def values(self):
        return dict(metrics=[check.isChecked() for check in self.metrics],
                    rowHeight=self.height.value(),
                    columns={name: [i for i, check in enumerate(fields) if check.isChecked()]
                             for name, fields in self.columns.items()})

class TradeDialog(QDialog):
    def __init__(self,parent,ticker,row=None,scenario=False):
        super().__init__(parent)
        self.setWindowTitle(f'{ticker} · '+('What if I sell?' if scenario else 'Edit trade' if row else 'Record trade'))
        self.row=row or dict(id=uuid.uuid4().hex,side='sell' if scenario else 'buy',date=date.today().isoformat(),quantity=1,price=0,fees=0)
        layout=QVBoxLayout(self)
        form=QFormLayout()
        self.side=ScrollSafeComboBox()
        for v in ('buy','sell'): self.side.addItem(v.title(),v)
        self.side.setCurrentIndex(self.side.findData(self.row['side']))
        self.side.setEnabled(not scenario)
        self.day=QDateEdit(QDate.fromString(self.row['date'],'yyyy-MM-dd'))
        self.day.setDisplayFormat('yyyy-MM-dd')
        self.day.setCalendarPopup(True)
        self.quantity=ScrollSafeDoubleSpinBox()
        self.quantity.setDecimals(8)
        self.quantity.setRange(.00000001,1e9)
        self.quantity.setValue(float(self.row['quantity']))
        self.price=dollars(float(self.row['price']))
        self.price.setDecimals(6)
        self.fees=dollars(float(self.row['fees']))
        self.original_values={key:control.value() for key,control in [('quantity',self.quantity),('price',self.price),('fees',self.fees)]}
        self.account=ScrollSafeComboBox()
        for account in parent.store.state['investmentAccounts']:
            self.account.addItem(account['name'],account['id'])
        self.account.setCurrentIndex(max(0,self.account.findData(self.row.get('accountId','legacy'))))
        form.addRow('Investment account',self.account)
        for title,entry in [('Side',self.side),('Date',self.day),('Shares',self.quantity),('Price per share (USD)',self.price),('Fees (USD)',self.fees)]:form.addRow(title,entry)
        layout.addLayout(form)
        layout.addWidget(text('FIFO uses the oldest available lots within the selected account. This records your history; it does not place a broker order. Enter execution prices manually.', 'muted',True))
        if scenario:
            self.tax_status = text('', 'muted', True)
            layout.addWidget(self.tax_status)
            tax = QPushButton('Set up / edit tax assumptions…')
            def update_tax():
                profile = parent.store.state.get('taxProfile')
                self.tax_status.setText(f"Tax scope saved · {profile['year']} · {profile.get('country','US')} / {profile['state']}. " + (tax_coverage(profile) or 'Reviewed-rate coverage available.') if profile else 'Optional: set tax assumptions to include a tax estimate. Your sale inputs stay here.')
            tax.clicked.connect(lambda: (parent.tax_settings(), update_tax()))
            layout.addWidget(tax)
            update_tax()
        self.error=text('','muted',True)
        layout.addWidget(self.error)
        buttons=QDialogButtonBox(QDialogButtonBox.StandardButton.Ok if scenario else QDialogButtonBox.StandardButton.Save)
        buttons.addButton(QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
    def values(self):
        numbers={key:self.row[key] if control.value()==self.original_values[key] else control.value()
                 for key,control in [('quantity',self.quantity),('price',self.price),('fees',self.fees)]}
        return dict(self.row,side=self.side.currentData(),date=self.day.date().toString('yyyy-MM-dd'),accountId=self.account.currentData(),**numbers)

class DashboardPanel(QWidget):
    def __init__(self, owner, ident):
        super().__init__()
        self.owner, self.dashboard_id = owner, ident
        layout=QVBoxLayout(self);layout.setContentsMargins(12,12,12,12)
        self.canvas=GridCanvas(lambda frame:owner.create_component(frame,self),self)
        self.canvas.layoutChanged.connect(lambda size,geometry:owner.layout_changed(self,size,geometry))
        self.canvas.command.connect(lambda ident,command:owner.try_widget_command(self,ident,command))
        self.canvas.announced.connect(owner.announce)
        self.canvas.widgetDropped.connect(lambda kind,size,x,y,hint:owner.try_add_block(kind,self,(size,x,y,hint)))
        self.canvas.addRequested.connect(owner.add_widget)
        self.canvas.templateRequested.connect(owner.apply_template)
        self.canvas.historyRequested.connect(lambda action:owner.layout_store.undo() if action=='undo' else owner.layout_store.redo())
        layout.addWidget(self.canvas)


class ResearchPanel(DashboardPanel):
    def __init__(self,owner,ticker):
        self.ticker=ticker
        super().__init__(owner,'stock-'+ticker)
        controls=QHBoxLayout()
        controls.addWidget(text(ticker+' · Research','sectionTitle'),1)
        self.customize=QCheckBox('Customize only this stock')
        self.customize.toggled.connect(owner.customize_stock)
        controls.addWidget(self.customize)
        for label,handler in [('Record trade',lambda:owner.record_trade(ticker)),('What if I sell?',lambda:owner.sale_scenario(ticker))]:
            button=QPushButton(label);button.setObjectName('rowAction');button.clicked.connect(handler);controls.addWidget(button)
        self.layout().insertLayout(0,controls)
        self.scope_label=text('','muted',True)
        self.layout().insertWidget(1,self.scope_label)


class StocksPage(QWidget):
    def __init__(self,owner):
        super().__init__()
        self.owner=owner;self.store=owner.investment_store
        self.network_enabled=owner.artwork.network_enabled
        self.selected_symbol='AAPL';self.page_id='home';self.news_sector='all'
        self.stock_view=False;self.news_view=False;self.research_tabs={};self.note_editors={}
        self.layout_store=LayoutStore(self.store,validate_workspace,self)
        self.data_layer=DataLayer(self.store,owner.directory,self.network_enabled,self)
        self.data_layer.persist=self.layout_store.schedule_save;self.data_layer.flush_persist=self.layout_store.flush
        self.layout_store.saveState.connect(self.data_layer.saved)
        self.cache=self.data_layer.cache;self.failures=self.data_layer.failures
        self.store.state=validate_workspace(self.store.state)
        layout=QVBoxLayout(self);layout.setContentsMargins(0,0,0,0);layout.setSpacing(8)
        toolbar=QHBoxLayout()
        self.ticker=QLineEdit();self.ticker.setPlaceholderText('Open a ticker · AAPL, MSFT');self.ticker.setMaximumWidth(250)
        self.ticker.returnPressed.connect(self.open_ticker);toolbar.addWidget(self.ticker,1)
        open_button=QPushButton('Open');open_button.setObjectName('rowAction');open_button.clicked.connect(self.open_ticker);toolbar.addWidget(open_button)
        self.refresh_button=QPushButton('Refresh market data');self.refresh_button.setObjectName('rowAction');self.refresh_button.clicked.connect(self.refresh_data);toolbar.addWidget(self.refresh_button)
        self.mode=ScrollSafeComboBox();self.mode.addItem('Public market data','auto');self.mode.addItem('Demo preview','demo')
        self.mode.setCurrentIndex(self.mode.findData(self.store.state.get('dataMode','auto')));self.mode.currentIndexChanged.connect(self.set_mode);toolbar.addWidget(self.mode)
        utilities=QPushButton('Tools');menu=QMenu(utilities)
        for label,handler in [('Market data health…',self.show_data_health),('Investing terms…',self.show_investing_terms),('Portfolio allocation…',self.show_allocation),('Account performance…',self.show_performance),('Monthly account risk / factors…',self.show_risk),('Compare / screen securities…',self.show_security_research),('Investment accounts…',self.manage_accounts),('Import broker CSV…',self.import_broker_csv),('Dividends / yearly review…',self.show_dividends),('View settings',self.configure_views),('Import investments…',self.import_workspace),('Export investments…',self.export_workspace),('Tax assumptions…',self.tax_settings)]:menu.addAction(label,handler)
        utilities.setMenu(menu);toolbar.addWidget(utilities);layout.addLayout(toolbar)
        self.alerts_menu_action=menu.addAction('Market alerts…',self.show_market_alerts)
        self.metrics=[Metric('Remaining cost basis','Across all saved positions'),Metric('Realized gain / loss','FIFO · recorded sales'),Metric('Market value','USD positions with usable public quotes')]
        self.summary_tiles=QWidget();row=QHBoxLayout(self.summary_tiles);row.setContentsMargins(0,0,0,0)
        self.summary_tiles.setFixedHeight(106)
        for metric in self.metrics:
            metric.caption.setToolTip(term_definition(metric.caption.text()))
            metric.set_compact();metric.setFixedHeight(106);metric.setMinimumWidth(0)
            metric.setSizePolicy(QSizePolicy.Policy.Ignored,QSizePolicy.Policy.Fixed)
            for label in (metric.caption,metric.value,metric.description):
                label.setMinimumWidth(0);label.setSizePolicy(QSizePolicy.Policy.Ignored,QSizePolicy.Policy.Preferred)
            metric.description.setFixedHeight(36)
            row.addWidget(metric,1)
        self.valuation_details=QPushButton('See unpriced positions');self.valuation_details.setObjectName('quiet');self.valuation_details.clicked.connect(self.show_unpriced);row.addWidget(self.valuation_details)
        layout.addWidget(self.summary_tiles)
        self.status=StatusLabel('Start with a watchlist or Record trade. Drag widget edges to resize; Edit layout to move.')
        self.status_row=QHBoxLayout();self.status_row.addWidget(self.status,1);layout.addLayout(self.status_row)
        self.health_button=QPushButton('Data health');self.health_button.setObjectName('rowAction')
        self.health_button.clicked.connect(self.show_data_health);self.status_row.addWidget(self.health_button)
        self.alerts_button=QPushButton('Alerts (0)');self.alerts_button.setObjectName('rowAction')
        self.alerts_button.setToolTip('Local market reminders; checks run while Finance Compass is open.')
        self.alerts_button.clicked.connect(self.show_market_alerts);self.status_row.addWidget(self.alerts_button)
        self.data_layer.changed.connect(self.update_data_health)
        controls=QHBoxLayout()
        self.dashboards=ScrollSafeComboBox();self.dashboards.setMinimumContentsLength(10);self.dashboards.setSizeAdjustPolicy(self.dashboards.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
        self.dashboards.currentIndexChanged.connect(self.choose_dashboard);controls.addWidget(self.dashboards,1)
        create=QPushButton('New dashboard');create.setObjectName('rowAction');create.clicked.connect(self.new_dashboard);controls.addWidget(create)
        self.dashboard_tools=QPushButton('Dashboard tools');self.dashboard_tools.setObjectName('rowAction');self.tools_menu=QMenu(self.dashboard_tools)
        for label,handler in [('Rename',self.rename_dashboard),('Duplicate dashboard',self.duplicate_dashboard),
            ('Move dashboard earlier',lambda:self.reorder_dashboard(-1)),('Move dashboard later',lambda:self.reorder_dashboard(1)),
            ('Dashboard settings…',self.dashboard_settings),('Reset layout',self.reset_layout),
            ('Export dashboard JSON…',self.export_dashboard),('Import dashboard JSON…',self.import_dashboard),
            ('Save as template…',self.save_template),('Apply template…',self.apply_template),('How to arrange widgets',self.layout_help),('Delete',self.delete_dashboard)]:self.tools_menu.addAction(label,handler)
        self.dashboard_tools.setMenu(self.tools_menu);controls.addWidget(self.dashboard_tools)
        add=QPushButton('Add widget');add.setObjectName('rowAction');add.clicked.connect(self.add_widget);controls.addWidget(add)
        self.edit_layout=QCheckBox('Edit layout');self.edit_layout.toggled.connect(self.set_editing);controls.addWidget(self.edit_layout)
        self.saved=QPushButton('Saved');self.saved.setObjectName('quiet');self.saved.setFixedWidth(116);self.saved.clicked.connect(self.layout_store.flush)
        layout.addLayout(controls)
        self.undo_button=QPushButton('Undo');self.undo_button.setObjectName('rowAction');self.undo_button.clicked.connect(self.layout_store.undo)
        self.redo_button=QPushButton('Redo');self.redo_button.setObjectName('rowAction');self.redo_button.clicked.connect(self.layout_store.redo)
        self.status_row.addWidget(self.undo_button);self.status_row.addWidget(self.redo_button)
        self.status_row.addWidget(self.saved)
        self.tabs=QTabWidget();self.tabs.setTabsClosable(True);self.tabs.tabBar().setMovable(True);self.tabs.tabCloseRequested.connect(self.close_research_tab);layout.addWidget(self.tabs,1)
        for attr,ident,label in [('dashboard_page','home','Home dashboard'),('watchlist_page','positions','Watchlists and positions'),('history_page','trade-history','Trade history'),('news_page','market-news','Market news')]:
            panel=DashboardPanel(self,ident);setattr(self,attr,panel);self.tabs.addTab(panel,label)
        self.sectors=ScrollSafeComboBox()
        for key,title in SECTORS.items():self.sectors.addItem(title,key)
        self.sectors.currentIndexChanged.connect(self.select_sector)
        self.news_page.layout().insertWidget(0,self.sectors)
        for index in range(4):
            for side in (self.tabs.tabBar().ButtonPosition.LeftSide,self.tabs.tabBar().ButtonPosition.RightSide):self.tabs.tabBar().setTabButton(index,side,None)
        self.tabs.currentChanged.connect(self.tab_changed)
        self.data_layer.actions=dict(open_symbol=self.open_symbol,open_url=lambda url:QDesktopServices.openUrl(QUrl(url)),record_trade=self.record_trade,import_broker_csv=self.import_broker_csv,add_symbol=self.add_symbol,sale_scenario=self.sale_scenario,edit_trade=self.edit_trade,delete_trade=self.delete_trade,remove_symbol=self.remove_symbol,new_watchlist=self.new_watchlist,rename_watchlist=self.rename_watchlist,delete_watchlist=self.delete_watchlist)
        self.data_layer.message.connect(self.status.setText)
        self.data_layer.changed.connect(lambda key:self.render_metrics() if key=='workspace' or key.startswith('quote:') and key.endswith('|1d') else None)
        self.layout_store.changed.connect(self.reload);self.layout_store.historyChanged.connect(self.update_history)
        self.layout_store.saveState.connect(self.saved.setText)
        self.layout_store.saveState.connect(lambda value:self.saved.setToolTip(getattr(self.layout_store,'last_error','') if value.startswith('Failed') else 'Layout saved to your workspace on this device'))
        redo=QShortcut(QKeySequence('Ctrl+Shift+Z'),self);redo.setContext(Qt.ShortcutContext.WidgetWithChildrenShortcut);redo.activated.connect(self.layout_store.redo)
        for sequence,handler in [(QKeySequence.StandardKey.Undo,self.layout_store.undo),(QKeySequence.StandardKey.Redo,self.layout_store.redo)]:
            shortcut=QShortcut(QKeySequence(sequence),self);shortcut.setContext(Qt.ShortcutContext.WidgetWithChildrenShortcut);shortcut.activated.connect(handler)
        self.toast=QFrame(self);self.toast.setObjectName('surface');toast_layout=QHBoxLayout(self.toast)
        toast_layout.addWidget(QLabel('Widget removed'))
        toast_undo=QPushButton('Undo');toast_undo.setObjectName('rowAction');toast_layout.addWidget(toast_undo)
        toast_undo.clicked.connect(lambda:(self.layout_store.undo(),self.toast.hide()))
        self.toast.hide();self.toast_timer=QTimer(self);self.toast_timer.setSingleShot(True);self.toast_timer.setInterval(8000);self.toast_timer.timeout.connect(self.toast.hide)
        self.ensure_news_page()
        self.reload()
        from .market_alerts_ui import MarketAlertEngine
        self.alert_engine=MarketAlertEngine(self)
        self.alert_engine.changed.connect(self.update_alert_count)
        self.alert_engine.status.connect(self.status.setText)
        self.update_alert_count()
        if self.store.error:self.status.setText(self.store.error)

    def panels(self):return [self.dashboard_page,self.watchlist_page,self.history_page,self.news_page,*self.research_tabs.values()]
    def dashboard_for(self,panel,state=None):
        state=state or self.store.state
        if isinstance(panel,ResearchPanel):return state['stockOverrides'].get(panel.ticker,state['stockTemplate'])
        if panel is self.news_page:return state['newsPages'].get(self.news_sector,next(p for p in state['pages'] if p['id']=='market-news'))
        ident=self.page_id if panel is self.dashboard_page else panel.dashboard_id
        return next((p for p in state['pages'] if p['id']==ident),state['pages'][0])
    def current_page(self,state=None):return self.dashboard_for(self.tabs.currentWidget(),state)
    def active_canvas(self):return self.tabs.currentWidget().canvas
    def create_component(self,frame,panel):
        definition=REGISTRY.get(frame.block['type']);config=frame.block['config']
        def change(values):
            if 'note' in values:self.data_layer.put_note(config['_noteKey'],values['note']);return
            def edit(state):
                block=next(w for w in self.dashboard_for(panel,state)['widgets'] if w['id']==frame.ident)
                block['config'].update(values)
            self.layout_store.mutate('Configure '+frame.block['title'],edit)
        component=create_widget(definition,config,frame.body.size(),self.edit_layout.isChecked(),change,self.data_layer)
        component.footerChanged.connect(frame.set_footer);frame.set_footer(component.footer_text)
        component.crashed.connect(frame.show_error)
        frame.refresh_button.setVisible(bool(definition.dataRequirements(config,self.store.state)))
        if frame.block['type']=='positions':self.watchlist_table=component.table;self.watchlists=component.watchlists;self.positions_component=component
        if frame.block['type']=='ledger':self.trade_table=component.table;self.trade_filter=component.filter;self.ledger_component=component
        if frame.block['type']=='headlines':self.news_table=component.table;self.news_search=component.filter
        if component.editor:self.note_editors[config['_noteKey']]=component.editor
        return component
    def render_panel(self,panel):
        dashboard=deepcopy(self.dashboard_for(panel))
        if isinstance(panel,ResearchPanel):
            separate=panel.ticker in self.store.state['stockOverrides']
            panel.customize.blockSignals(True);panel.customize.setChecked(separate);panel.customize.blockSignals(False)
            panel.scope_label.setText('Editing only '+panel.ticker if separate else 'Editing the shared stock layout · Customize only this stock for separate blocks.')
        for block in dashboard['widgets']:
            config=block['config']
            if isinstance(panel,ResearchPanel) and config.get('followSymbol',True):config['symbol']=panel.ticker
            note_context='stock-'+panel.ticker if isinstance(panel,ResearchPanel) else dashboard['id']
            config['_noteKey']=note_context+':'+block['id']
            if panel is self.news_page:config['_sector']=self.news_sector
        panel.canvas.set_dashboard(dashboard);panel.canvas.set_editing(self.edit_layout.isChecked())
    def reload(self):
        self.dashboards.blockSignals(True);self.dashboards.clear()
        for p in self.store.state['pages']:self.dashboards.addItem(p['name'],p['id'])
        for ticker in self.research_tabs:self.dashboards.addItem(ticker+' · Research','stock:'+ticker)
        selected='stock:'+self.selected_symbol if self.stock_view else 'market-news' if self.news_view else self.current_page()['id']
        self.dashboards.setCurrentIndex(max(0,self.dashboards.findData(selected)));self.dashboards.blockSignals(False)
        for panel in self.panels():self.render_panel(panel)
        for panel in (self.dashboard_page,self.watchlist_page,self.history_page,self.news_page):
            self.tabs.setTabText(self.tabs.indexOf(panel),self.dashboard_for(panel)['name'])
        domain={key:self.store.state.get(key) for key in ('trades','watchlists','notes','viewSettings','dataMode','investmentAccounts')}
        if domain!=getattr(self,'_domain',None):
            self._domain=domain;self.data_layer.notify_local();self.render_metrics()
        self.apply_view_settings();self.update_history()
    def render_dashboard(self):self.render_panel(self.tabs.currentWidget())
    def layout_changed(self,panel,size,layout):
        self.layout_store.mutate('Arrange blocks',lambda state:self.dashboard_for(panel,state)['layouts'].__setitem__(size,deepcopy(layout)))
    def set_editing(self,editing):
        for panel in self.panels():panel.canvas.set_editing(editing)
        self.status.setText('Edit mode · drag headers, resize edges, or use Space + arrows. Escape cancels.' if editing else 'Drag a widget edge or corner to resize. Edit layout to move widgets.')
    def layout_help(self):
        QMessageBox.information(self,'Arrange your dashboard',
            '1. Turn on Edit layout. Drag any widget header to move it.\n'
            '2. Drop near a widget’s left or right edge to create columns; drop above or below to insert. The blue preview shows the result.\n'
            '3. Resize any unlocked widget at any time: drag a right or bottom edge, or the corner. Double-click an edge to fit the content.\n'
            '4. Use ⋯ to configure, duplicate, move to another dashboard, expand, lock or remove a widget.\n'
            '5. Turn Edit layout off to use tables and notes. Changes save automatically; Undo and Redo recover previous layouts.\n\n'
            'Keyboard: focus a header, Space to pick up, arrows to move, Shift + arrows to resize, Enter to drop, Escape to cancel.\n'
            'To resize without Edit layout, focus a resize handle, press Space, use arrows, then Enter to apply or Escape to cancel.\n'
            'Dashboard tools → Dashboard settings lets you turn off gap closing for free placement.')
    def announce(self,message):self.status.setText(message)
    def update_history(self):
        self.undo_button.setEnabled(bool(self.layout_store.past));self.redo_button.setEnabled(bool(self.layout_store.future))
        self.undo_button.setToolTip('Undo '+self.layout_store.past[-1][0] if self.layout_store.past else 'Nothing to undo')
        if hasattr(self,'toast') and (not self.layout_store.past or self.layout_store.past[-1][0]!='Remove widget'):self.toast.hide()
    def commit(self,state,render=True):
        try:self.layout_store.replace(state);return self.layout_store.flush()
        except (ValueError,OSError) as exc:self.status.setText(str(exc));return False
    def flush_notes(self):
        self.data_layer.flush_local();self.layout_store.flush()
    def page_context(self):
        if self.news_view:return ('news',self.news_sector)
        if self.stock_view:return ('stock',self.selected_symbol) if self.selected_symbol in self.store.state['stockOverrides'] else ('template','')
        return ('dashboard',self.current_page()['id'])
    def widget_command(self,panel,ident,command):
        block=next(w for w in self.dashboard_for(panel)['widgets'] if w['id']==ident)
        if command=='refresh':
            frame=panel.canvas.frames[ident]
            if frame.component:frame.guarded(frame.component.refresh)
            else:frame.retry()
            return
        if command=='configure':self.configure_widget(ident,panel);return
        if command=='remove':self.remove_widget(ident,panel);return
        if command=='rename':
            title,ok=QInputDialog.getText(self,'Rename widget','Title',text=block['title'])
            if ok and title.strip():self.layout_store.mutate('Rename widget',lambda state:next(w for w in self.dashboard_for(panel,state)['widgets'] if w['id']==ident).__setitem__('title',title.strip()[:80]))
        elif command=='lock':
            def lock(state):
                for layout in self.dashboard_for(panel,state)['layouts'].values():
                    g=next(g for g in layout if g['i']==ident);g['locked']=not g.get('locked',False)
            self.layout_store.mutate('Lock position',lock)
        elif command=='duplicate':self.duplicate_widget(ident,panel)
        elif command in ('move','copy'):self.move_widget_to(ident,panel,command=='copy')
        elif command=='expand':self.expand_widget(ident,panel)
    def try_widget_command(self,panel,ident,command):
        try:self.widget_command(panel,ident,command)
        except (ValueError,OSError,StopIteration) as exc:self.status.setText(str(exc))
    def configure_widget(self,ident,panel=None):
        panel=panel or self.tabs.currentWidget();block=next(w for w in self.dashboard_for(panel)['widgets'] if w['id']==ident)
        definition=REGISTRY.get(block['type'])
        if definition.ConfigPanel is None:self.status.setText('This widget has no additional configuration. Use Rename title or Edit layout.');return
        dialog=definition.ConfigPanel(definition,block['config'],self.data_layer,self)
        while dialog.exec()==QDialog.DialogCode.Accepted:
            try:
                values=dialog.values()
                self.layout_store.mutate('Configure '+block['title'],lambda state:next(w for w in self.dashboard_for(panel,state)['widgets'] if w['id']==ident).__setitem__('config',values));return
            except ValueError as exc:dialog.error.setText(str(exc))
    def add_block(self,kind,panel=None,placement=None):
        panel=panel or self.tabs.currentWidget();definition=REGISTRY.get(kind);block=widget(kind)
        def add(state):
            dashboard=self.dashboard_for(panel,state)
            if len(dashboard['widgets'])>=30:raise ValueError('Limit: 30 widgets per dashboard.')
            block['config']['watchlistId']=state['watchlists'][0]['id'];dashboard['widgets'].append(block)
            for key,layout in dashboard['layouts'].items():
                width=12 if key=='xs' else definition.defaultSize[0]
                x,y=(placement[1],placement[2]) if placement and placement[0]==key else (0,0)
                geometry=dict(i=block['id'],x=x,y=y,w=width,h=definition.defaultSize[1],minW=definition.minSize[0],minH=definition.minSize[1])
                if placement and placement[0]==key:
                    hint=placement[3] if len(placement)>3 else None
                    if hint and hint.get('side'):
                        dashboard['layouts'][key]=ENGINE.split([*layout,geometry],block['id'],hint['target'],hint['side'],dashboard['settings']['compact'])
                    else:
                        if hint:
                            target=next(g for g in layout if g['i']==hint['target'])
                            geometry.update(x=target['x'],w=target['w'],y=target['y']+(target['h'] if hint['after'] else 0))
                        dashboard['layouts'][key]=ENGINE.move([*layout,geometry],block['id'],geometry,dashboard['settings']['compact'])
                else:
                    dashboard['layouts'][key]=ENGINE.insert(layout,ENGINE.first_free(layout,geometry),dashboard['settings']['compact'])
        self.layout_store.mutate('Add '+definition.title,add)
        return block['id']
    def add_widget(self):
        if not hasattr(self,'gallery'):
            self.gallery=WidgetGallery(self.data_layer,self)
            self.gallery.selected.connect(self.try_add_block)
            self.gallery.dragging.connect(lambda:self.edit_layout.setChecked(True))
        self.gallery.show();self.gallery.raise_();self.gallery.activateWindow()
    def try_add_block(self,kind,panel=None,placement=None):
        try:return self.add_block(kind,panel,placement)
        except ValueError as exc:self.status.setText(str(exc))
    def remove_widget(self,ident,panel=None):
        panel=panel or self.tabs.currentWidget()
        def remove(state):
            dashboard=self.dashboard_for(panel,state);dashboard['widgets']=[w for w in dashboard['widgets'] if w['id']!=ident]
            for key,layout in dashboard['layouts'].items():dashboard['layouts'][key]=ENGINE.repair([g for g in layout if g['i']!=ident],dashboard['settings']['compact'])
        self.layout_store.mutate('Remove widget',remove)
        self.status.setText('Widget removed · Undo restores the block and its notes.')
        self.toast.adjustSize();self.toast.move(max(8,self.width()-self.toast.width()-24),max(8,self.height()-self.toast.height()-24));self.toast.show();self.toast.raise_();self.toast_timer.start()
    def duplicate_widget(self,ident,panel=None):
        panel=panel or self.tabs.currentWidget();new_id=uuid.uuid4().hex
        def duplicate(state):
            dashboard=self.dashboard_for(panel,state)
            if len(dashboard['widgets'])>=30:raise ValueError('Limit: 30 widgets.')
            block=deepcopy(next(w for w in dashboard['widgets'] if w['id']==ident));block['id']=new_id;block['title']+=' copy';dashboard['widgets'].append(block)
            for key,layout in dashboard['layouts'].items():
                geometry=next(deepcopy(g) for g in layout if g['i']==ident);geometry['i']=new_id
                dashboard['layouts'][key]=ENGINE.insert(layout,geometry,dashboard['settings']['compact'])
            old_key=('stock-'+panel.ticker if isinstance(panel,ResearchPanel) else dashboard['id'])+':'+ident
            state['notes'][old_key[:-len(ident)]+new_id]=state['notes'].get(old_key,'')
        self.layout_store.mutate('Duplicate widget',duplicate);return new_id
    def move_widget_to(self,ident,panel=None,copy=False,target_id=None):
        panel=panel or self.tabs.currentWidget()
        targets=[p for p in self.store.state['pages'] if p['id']!=self.dashboard_for(panel)['id']]
        if target_id is None:
            title,ok=QInputDialog.getItem(self,'Copy to dashboard' if copy else 'Move to dashboard','Dashboard',[p['name'] for p in targets],0,False)
            if not ok:return
            target_id=next(p['id'] for p in targets if p['name']==title)
        def move(state):
            source=self.dashboard_for(panel,state);target=next(p for p in state['pages'] if p['id']==target_id)
            if len(target['widgets'])>=30:raise ValueError('Destination has 30 widgets.')
            block=deepcopy(next(w for w in source['widgets'] if w['id']==ident));new_id=uuid.uuid4().hex if copy else ident;block['id']=new_id
            if any(w['id']==new_id for w in target['widgets']):new_id=uuid.uuid4().hex;block['id']=new_id
            target['widgets'].append(block)
            for key,layout in target['layouts'].items():
                geometry=next(deepcopy(g) for g in source['layouts'][key] if g['i']==ident);geometry['i']=new_id
                target['layouts'][key]=ENGINE.insert(layout,geometry,target['settings']['compact'])
            old_key=('stock-'+panel.ticker if isinstance(panel,ResearchPanel) else source['id'])+':'+ident
            state['notes'][target['id']+':'+new_id]=state['notes'].get(old_key,'')
            if not copy:
                source['widgets']=[w for w in source['widgets'] if w['id']!=ident]
                for key,layout in source['layouts'].items():source['layouts'][key]=ENGINE.repair([g for g in layout if g['i']!=ident],source['settings']['compact'])
        self.layout_store.mutate('Copy widget' if copy else 'Move widget to dashboard',move)
    def expand_widget(self,ident,panel=None):
        panel=panel or self.tabs.currentWidget();block=deepcopy(next(w for w in self.dashboard_for(panel)['widgets'] if w['id']==ident))
        dialog=QDialog(self);dialog.setWindowTitle(block['title']);dialog.resize(1000,700);layout=QVBoxLayout(dialog)
        from .dashboard.widget_frame import WidgetFrame
        config=deepcopy(panel.canvas.frames[ident].block['config']);block['config']=config
        frame=WidgetFrame(block,lambda f:self.create_component(f,panel));layout.addWidget(frame)
        dialog.setWindowState(Qt.WindowState.WindowMaximized);dialog.exec();dialog.deleteLater()
    def new_dashboard(self):
        name,ok=QInputDialog.getText(self,'New dashboard','Name')
        if ok and name.strip():
            ident=uuid.uuid4().hex
            self.layout_store.mutate('Create dashboard',lambda state:state['pages'].append(page(ident,name.strip(),[])))
            self.page_id=ident;self.tabs.setCurrentWidget(self.dashboard_page);self.reload()
    def duplicate_dashboard(self):
        source=deepcopy(self.current_page());ident=uuid.uuid4().hex
        def duplicate(state):
            dashboard=deepcopy(source);dashboard.update(id=ident,name=source['name']+' copy')
            state['pages'].append(dashboard)
            for block in dashboard['widgets']:
                source_key=('stock-'+self.selected_symbol if self.stock_view else source['id'])+':'+block['id']
                state['notes'][ident+':'+block['id']]=state['notes'].get(source_key,'')
        self.layout_store.mutate('Duplicate dashboard',duplicate)
        self.page_id=ident;self.tabs.setCurrentWidget(self.dashboard_page);self.reload()
        return ident
    def reorder_dashboard(self,delta):
        ident=self.current_page()['id']
        def reorder(state):
            pages=state['pages'];index=next((i for i,p in enumerate(pages) if p['id']==ident),None)
            if index is None:return
            destination=max(0,min(len(pages)-1,index+delta));pages.insert(destination,pages.pop(index))
        self.layout_store.mutate('Reorder dashboards',reorder)
    def set_dashboard_settings(self,settings):
        def change(state):
            dashboard=self.current_page(state);dashboard['settings'].update(settings)
            for key,layout in dashboard['layouts'].items():dashboard['layouts'][key]=ENGINE.repair(layout,dashboard['settings']['compact'])
        self.layout_store.mutate('Dashboard settings',change)
    def dashboard_settings(self):
        dialog=DashboardSettings(self.current_page()['settings'],self)
        if dialog.exec()==QDialog.DialogCode.Accepted:self.set_dashboard_settings(dialog.values())
    def reset_layout(self):
        def reset(state):
            dashboard=self.current_page(state);geometry=[]
            for index,block in enumerate(dashboard['widgets']):
                definition=REGISTRY.get(block['type']);width=definition.defaultSize[0]
                geometry=ENGINE.insert(geometry,dict(i=block['id'],x=0,y=0,w=width,h=definition.defaultSize[1],minW=definition.minSize[0],minH=definition.minSize[1]),True)
            dashboard['layouts']={key:ENGINE.derive(geometry,key) for key in ('lg','md','sm','xs')}
        self.layout_store.mutate('Reset layout',reset)
    def export_dashboard(self):
        path,_=QFileDialog.getSaveFileName(self,'Export dashboard','dashboard.json','JSON files (*.json)')
        if path:
            try:write_json(Path(path),dict(schemaVersion=2,dashboard=self.current_page()))
            except OSError as exc:self.status.setText('Export failed: '+str(exc))
    def import_dashboard_data(self,raw):
        if raw.get('schemaVersion',2) not in (1,2):raise ValueError('Unsupported dashboard version.')
        dashboard=normalize_dashboard(raw.get('dashboard',raw),legacy=raw.get('schemaVersion')==1)
        if not dashboard['widgets']:raise ValueError('The file contains no widget blocks.')
        dashboard['id']=uuid.uuid4().hex
        self.layout_store.mutate('Import dashboard',lambda state:state['pages'].append(dashboard))
        self.page_id=dashboard['id'];self.tabs.setCurrentWidget(self.dashboard_page);self.reload()
        return dashboard['id']
    def import_dashboard(self):
        path,_=QFileDialog.getOpenFileName(self,'Import as new dashboard','','JSON files (*.json)')
        if path:
            try:
                if Path(path).stat().st_size>1_000_000:raise ValueError('Dashboard exceeds 1 MB.')
                self.import_dashboard_data(json.loads(Path(path).read_text()))
            except (ValueError,OSError,TypeError,KeyError) as exc:self.status.setText('Import failed: '+str(exc))
    def save_template(self):
        name,ok=QInputDialog.getText(self,'Save as template','Template name',text=self.current_page()['name'])
        if ok and name.strip():
            template=deepcopy(self.current_page());template['name']=name.strip()
            self.layout_store.mutate('Save template',lambda state:state['templates'].__setitem__(uuid.uuid4().hex,template))
    def apply_template_data(self,template):
        def apply(state):
            dashboard=self.current_page(state);ident,name=dashboard['id'],dashboard['name']
            dashboard.clear();dashboard.update(normalize_dashboard(template));dashboard.update(id=ident,name=name)
        self.layout_store.mutate('Apply template',apply)
    def apply_template(self):
        saved=list(self.store.state['templates'].values());names=[*STARTERS,*[p['name'] for p in saved]]
        name,ok=QInputDialog.getItem(self,'Apply template','Replaces blocks; Undo restores your previous layout.',names,0,False)
        if ok:self.apply_template_data(starter(name) if name in STARTERS else next(p for p in saved if p['name']==name))
    def rename_dashboard(self):
        dashboard=self.current_page();name,ok=QInputDialog.getText(self,'Rename dashboard','Name',text=dashboard['name'])
        if ok and name.strip():self.layout_store.mutate('Rename dashboard',lambda state:self.current_page(state).__setitem__('name',name.strip()))
    def delete_dashboard(self):
        ident=self.current_page()['id']
        if ident in ('home','positions','trade-history','market-news') or self.stock_view or self.news_view:self.status.setText('Primary pages are preserved. Remove their widgets or create a custom dashboard.');return
        self.page_id='home'
        self.layout_store.mutate('Delete dashboard',lambda state:state.__setitem__('pages',[p for p in state['pages'] if p['id']!=ident]))
    def choose_dashboard(self,*_):
        ident=self.dashboards.currentData()
        if not ident:return
        if ident.startswith('stock:'):
            self.tabs.setCurrentWidget(self.research_tabs[ident[6:]]);return
        reserved={'positions':self.watchlist_page,'trade-history':self.history_page,'market-news':self.news_page}
        if ident in reserved:self.tabs.setCurrentWidget(reserved[ident])
        else:self.page_id=ident;self.tabs.setCurrentWidget(self.dashboard_page)
        self.reload()
    def open_ticker(self):
        try:self.open_symbol(symbol(self.ticker.text()))
        except ValueError as exc:self.status.setText(str(exc))
    def open_symbol(self,ticker):
        ticker=symbol(ticker)
        if ticker not in self.research_tabs:
            if len(self.research_tabs)>=30:self.status.setText('Close a research tab before opening another stock (limit 30).');return
            panel=ResearchPanel(self,ticker);panel.return_panel=self.tabs.currentWidget()
            if isinstance(panel.return_panel,ResearchPanel):panel.return_panel=self.dashboard_page
            self.research_tabs[ticker]=panel;self.tabs.addTab(panel,ticker+' · Research')
        self.tabs.setCurrentWidget(self.research_tabs[ticker]);self.selected_symbol=ticker;self.ticker.setText(ticker);self.reload()
    def close_research_tab(self,index):
        panel=self.tabs.widget(index)
        if not isinstance(panel,ResearchPanel):return
        self.flush_notes();self.research_tabs.pop(panel.ticker,None)
        if self.tabs.currentWidget() is panel:self.tabs.setCurrentWidget(panel.return_panel)
        self.tabs.removeTab(index);panel.deleteLater();self.reload()
    def customize_stock(self,checked):
        if not self.stock_view:return
        ticker=self.selected_symbol
        def customize(state):
            if checked:state['stockOverrides'].setdefault(ticker,deepcopy(state['stockTemplate']))
            else:state['stockOverrides'].pop(ticker,None)
        self.layout_store.mutate('Change stock layout scope',customize)
    def tab_changed(self,index):
        active=self.tabs.widget(index);self.stock_view=isinstance(active,ResearchPanel);self.news_view=active is self.news_page
        if self.stock_view:self.selected_symbol=active.ticker;self.ticker.setText(active.ticker)
        self.reload()
    def ensure_news_page(self):
        if self.news_sector not in self.store.state['newsPages']:
            source=deepcopy(next(p for p in self.store.state['pages'] if p['id']=='market-news'));source.update(id='news-'+self.news_sector,name=SECTORS[self.news_sector]);self.store.state['newsPages'][self.news_sector]=source
        dashboard=self.store.state['newsPages'][self.news_sector]
        if not any(w['type']=='headlines' for w in dashboard['widgets']):
            block=widget('headlines');dashboard['widgets'].append(block)
            sizes=self.store.state.get('newsPanelSizes',[320,320])
            for key,layout in dashboard['layouts'].items():
                for g in layout:g['h']=max(18,min(120,round(sizes[1]/10)))
                geometry=dict(i=block['id'],x=0,y=0,w=12,h=max(18,min(120,round(sizes[0]/10))))
                dashboard['layouts'][key]=ENGINE.move([*layout,geometry],block['id'],geometry,dashboard['settings']['compact'])
    def select_sector(self,*_):
        self.news_sector=self.sectors.currentData() or 'all';self.ensure_news_page();self.render_panel(self.news_page)
    def widget_symbol(self,w):return self.selected_symbol if self.stock_view and w['config'].get('followSymbol') else w['config'].get('symbol','AAPL')
    @property
    def trade_rows(self):return self.ledger_component.trade_rows if hasattr(self,'ledger_component') else []
    def selected_ticker(self):
        row=self.watchlist_table.currentRow();return self.watchlist_table.item(row,0).text() if row>=0 else None
    def selected_watchlist(self,ident=None):
        if ident is None:
            try:ident=self.watchlists.currentData()
            except (AttributeError,RuntimeError):pass
        return next((w for w in self.store.state['watchlists'] if w['id']==ident),self.store.state['watchlists'][0])
    def new_watchlist(self):
        if len(self.store.state['watchlists'])>=20:self.status.setText('Limit: 20 watchlists.');return
        name,ok=QInputDialog.getText(self,'New watchlist','Name')
        if ok and name.strip():
            state=deepcopy(self.store.state);ident=uuid.uuid4().hex;state['watchlists'].append(dict(id=ident,name=name.strip(),symbols=[]))
            if self.commit(state):return ident
    def rename_watchlist(self,ident=None):
        listing=self.selected_watchlist(ident);name,ok=QInputDialog.getText(self,'Rename watchlist','Name',text=listing['name'])
        if ok and name.strip():
            state=deepcopy(self.store.state);next(w for w in state['watchlists'] if w['id']==listing['id'])['name']=name.strip();self.commit(state)
    def delete_watchlist(self,ident=None):
        if len(self.store.state['watchlists'])==1:self.status.setText('Keep at least one watchlist.');return
        listing=self.selected_watchlist(ident)
        if QMessageBox.question(self,'Delete watchlist','Delete '+listing['name']+'? Saved trades are retained.')==QMessageBox.StandardButton.Yes:
            state=deepcopy(self.store.state);state['watchlists']=[w for w in state['watchlists'] if w['id']!=listing['id']];self.commit(state)
    def add_symbol(self,listing_id=None):
        ticker,ok=QInputDialog.getText(self,'Add ticker','US ticker')
        if ok:
            try:
                ticker=symbol(ticker);state=deepcopy(self.store.state);listing=next(w for w in state['watchlists'] if w['id']==self.selected_watchlist(listing_id)['id'])
                if ticker not in listing['symbols']:listing['symbols'].append(ticker)
                self.commit(state)
            except ValueError as exc:self.status.setText(str(exc))
    def remove_symbol(self,listing_id=None,ticker=None):
        ticker=ticker or self.selected_ticker()
        if ticker:
            state=deepcopy(self.store.state);listing=next(w for w in state['watchlists'] if w['id']==self.selected_watchlist(listing_id)['id']);listing['symbols']=[s for s in listing['symbols'] if s!=ticker];self.commit(state)
    def sell_selected(self):
        ticker=self.selected_ticker()
        if ticker:self.sale_scenario(ticker)
    def data(self,kind,key):return self.data_layer.data(kind,key)
    def quote(self,ticker,period='1d'):return self.data('quote',ticker+'|'+period)
    def source_label(self,value,kind,key):return ('Cached · ' if value.get('stale') else '')+value.get('source','') if value else self.failures.get(kind+':'+key,'Not loaded · Refresh')
    def render_metrics(self):
        portfolio=self.store.portfolio();self.metrics[0].value.setText(money(portfolio['basis']));self.metrics[1].value.setText(money(portfolio['realized']))
        self.data_layer.subscribe({('quote',ticker+'|1d') for ticker,holding in portfolio['holdings'].items() if holding['quantity']},'portfolio-summary')
        total=0;covered=0;self.unpriced=[]
        for ticker,p in portfolio['holdings'].items():
            if not p['quantity']:continue
            value=self.data('quote',ticker+'|1d')
            if value and not value.get('demo') and not value.get('stale') and value.get('currency')=='USD':total+=float(p['quantity'])*value['price'];covered+=1
            else:
                reason = 'Demo quote' if value and value.get('demo') else 'Cached quote' if value and value.get('stale') else 'Non-USD quote' if value and value.get('currency')!='USD' else 'Quote unavailable'
                self.unpriced.append(ticker+' · '+reason+' · Excluded from market value; use Data health to inspect or retry.')
        self.metrics[2].value.setText(money(total) if covered else '—');self.metrics[2].caption.setText('PARTIAL MARKET VALUE' if self.unpriced else 'MARKET VALUE')
        self.metrics[2].description.setText((f'{covered} of {covered+len(self.unpriced)} positions priced'+(' · Missing: '+', '.join(s.split(' · ')[0] for s in self.unpriced[:3]) if self.unpriced else '')) if covered or self.unpriced else 'No positions yet · record a trade to start')
        for metric in self.metrics:
            metric.value.setToolTip(metric.value.text());metric.description.setToolTip(metric.description.text())
        self.valuation_details.setVisible(bool(self.unpriced))
        self.update_data_health()
    def update_data_health(self,*_):
        rows=self.data_layer.health()
        issues=sum(row['status'] in ('error','stale','empty') for row in rows)
        self.health_button.setText('Data health'+(f' · {issues} need attention' if issues else ''))
        self.health_button.setToolTip('Inspect sources, timestamps and failed feeds. Public data may be delayed; demo and cached quotes do not value holdings.')
    def show_data_health(self):
        if not getattr(self,'health_dialog',None):
            self.health_dialog=DataHealthDialog(self)
        self.health_dialog.reload();self.health_dialog.show();self.health_dialog.raise_();self.health_dialog.activateWindow()
    def show_investing_terms(self):
        dialog=QDialog(self);dialog.setWindowTitle('Investing terms');dialog.resize(640,640)
        layout=QVBoxLayout(dialog)
        search=QLineEdit();search.setPlaceholderText('Search a term · cost basis, EPS, P/E');search.setAccessibleName('Search investing terms');layout.addWidget(search)
        content=QTextEdit();content.setReadOnly(True);layout.addWidget(content)
        def update(query=''):
            entries=[(METRICS.get(key,key.title()),value) for key,value in TERMS.items()]
            content.setPlainText('\n\n'.join(name+'\n'+value for name,value in entries if query.casefold() in (name+' '+value).casefold()) or 'No matching terms. Try a different word.')
        search.textChanged.connect(update);update()
        close=QPushButton('Close');close.clicked.connect(dialog.accept);layout.addWidget(close)
        dialog.exec()
    def show_unpriced(self):QMessageBox.information(self,'Positions excluded from market value','\n'.join(self.unpriced))
    def view_tables(self):
        result={}
        for panel in self.panels():
            for frame in panel.canvas.frames.values():
                component=frame.component
                group={'positions':'watchlist','ledger':'trades','headlines':'news'}.get(frame.block['type'])
                if group and component and component.table:result[group]=component.table
        return result
    def apply_view_settings(self):
        settings=self.store.state.get('viewSettings',{})
        for index,metric in enumerate(self.metrics):metric.setVisible(settings.get('metrics',[True]*3)[index])
        for name,table in self.view_tables().items():
            table.verticalHeader().setDefaultSectionSize(settings.get('rowHeight',42))
            for column in range(table.columnCount()):table.setColumnHidden(column,column not in settings.get('columns',{}).get(name,list(range(table.columnCount()))))
    def configure_views(self):
        dialog=ViewSettingsDialog(self)
        if dialog.exec()==QDialog.DialogCode.Accepted:
            try:
                values=validate_view_settings(dialog.values());state=deepcopy(self.store.state);state.setdefault('viewSettings',{}).update(values);self.commit(state)
            except ValueError as exc:self.status.setText(str(exc))
    def requests(self):return sorted(self.data_layer.requested)
    def start(self):
        if self.network_enabled:self.refresh_data()
    def refresh_data(self,*_,missing_only=False):self.data_layer.refresh()
    def set_mode(self,*_):
        state=deepcopy(self.store.state);state['dataMode']=self.mode.currentData();self.commit(state);self.data_layer.refresh()
    def market_result(self,kind,key,value):self.data_layer.result(kind,key,value)
    def market_failed(self,kind,key,message):self.data_layer.failed(kind,key,message)
    def market_finished(self):self.data_layer.finished();self.render_metrics()
    def render_watchlist(self):self.positions_component.safe_render()
    def render_trades(self):self.ledger_component.safe_render()
    def render_news(self):self.data_layer.changed.emit('news:market')
    def import_workspace(self):
        path,_=QFileDialog.getOpenFileName(self,'Import investment workspace','','JSON files (*.json)')
        if path:
            try:
                if Path(path).stat().st_size>10_000_000:raise ValueError('Workspace exceeds 10 MB.')
                state=validate_workspace(json.loads(Path(path).read_text()))
                if QMessageBox.question(self,'Import investments','Replace investment data with this file?')!=QMessageBox.StandardButton.Yes:return
                self.page_id='home';self.tabs.setCurrentWidget(self.dashboard_page);self.commit(state)
            except (ValueError,OSError) as exc:self.status.setText('Could not import investments: '+str(exc))
    def export_workspace(self):
        self.flush_notes();path,_=QFileDialog.getSaveFileName(self,'Export investments','investments-workspace.json','JSON files (*.json)')
        if path:write_json(Path(path),self.store.state)
    def shutdown(self):self.alert_engine.shutdown();self.layout_store.flush();self.data_layer.shutdown()
    def manage_accounts(self):
        from .broker_import_ui import InvestmentAccountsDialog
        InvestmentAccountsDialog(self).exec()
    def import_broker_csv(self):
        from .broker_import_ui import BrokerImportDialog
        BrokerImportDialog(self).exec()
    def show_allocation(self):
        from .portfolio_analysis_ui import AllocationDialog
        AllocationDialog(self).exec()
    def show_performance(self):
        from .performance_ui import PerformanceDialog
        PerformanceDialog(self).exec()
    def show_security_research(self):
        from .security_research_ui import SecurityResearchDialog
        SecurityResearchDialog(self).exec()
    def show_market_alerts(self):
        from .market_alerts_ui import MarketAlertsDialog
        MarketAlertsDialog(self).exec()
    def update_alert_count(self):
        count=sum(not event['read'] for event in self.alert_engine.state()['events'])
        self.alerts_menu_action.setText(f'Market alerts… ({count} unread)')
        self.alerts_button.setText(f'Alerts ({count})')
    def record_trade(self,ticker=None,row=None,watchlist_id=None):
        if not isinstance(ticker,str):
            ticker,ok=QInputDialog.getText(self,'Record trade','Ticker',text=self.selected_symbol)
            if not ok:return
        try:ticker=symbol(ticker)
        except ValueError as exc:self.status.setText(str(exc));return
        dialog=TradeDialog(self,ticker,row)
        while dialog.exec()==QDialog.DialogCode.Accepted:
            try:
                self.store.trade(ticker,dialog.values(),watchlist_id=self.selected_watchlist(watchlist_id)['id']);self.reload();self.owner.refresh_investment_summary();return
            except (ValueError,OSError) as exc:dialog.error.setText(str(exc))
    def selected_trade(self):
        index=self.trade_table.currentRow()
        return self.trade_rows[index] if 0<=index<len(self.trade_rows) else None
    def edit_trade(self,row=None):
        row=row or self.selected_trade()
        if row:self.record_trade(*row)
    def delete_trade(self,row=None):
        row=row or self.selected_trade()
        if row and QMessageBox.question(self,'Delete trade','Delete this trade? Later sales must still have sufficient shares.')==QMessageBox.StandardButton.Yes:
            try:self.store.trade(*row,delete=True);self.reload();self.owner.refresh_investment_summary()
            except (ValueError,OSError) as exc:self.status.setText(str(exc))
    def tax_settings(self):
        from .investment_tax_ui import TaxProfileDialog
        dialog=TaxProfileDialog(self)
        while dialog.exec()==QDialog.DialogCode.Accepted:
            try:
                self.data_layer.flush_local()
                if not self.layout_store.flush():raise OSError('Pending workspace changes could not be saved. Retry before saving tax assumptions.')
                state=deepcopy(self.store.state);state['taxProfile']=dialog.values()
                self.store.save(state);return
            except (ValueError,OSError) as exc:dialog.error.setText(str(exc))

    def show_dividends(self):
        from .dividends_ui import DividendReviewDialog
        DividendReviewDialog(self).exec()

    def show_risk(self):
        from .risk_analysis_ui import RiskAnalysisDialog
        RiskAnalysisDialog(self).exec()
    def sale_scenario(self,ticker):
        dialog=TradeDialog(self,ticker,scenario=True)
        while dialog.exec()==QDialog.DialogCode.Accepted:
            try:
                trades=self.store.state['trades'].get(ticker,[]);sale=dialog.values();result=simulate_sale(trades,sale)
                message=f"Short-term gain: {money(result['shortGain'])}\nLong-term gain: {money(result['longGain'])}\n"
                profile=self.store.state.get('taxProfile')
                account=next(a for a in self.store.state['investmentAccounts'] if a['id']==sale['accountId'])
                has_losses=result['hasLoss'] or any(d['gain']<0 and d['accountId']==sale['accountId'] for d in position(trades)['disposals'])
                reason='Set tax assumptions first.' if not profile else tax_coverage(profile)
                if account['kind']!='brokerage':reason='Requires an explicitly taxable brokerage account; retirement and unknown account distributions need separate review.'
                elif profile and sale['date'][:4]!=str(profile['year']):reason='Hypothetical sale year must match the saved tax year.'
                elif has_losses:reason='Loss scenarios and recorded loss histories need separate review.'
                if not reason:
                    tax=estimate_tax(profile,result['shortGain'],result['longGain'])
                    state_line=f"Incremental NC: {money(tax['state'])}" if tax['stateCovered'] else 'State taxes excluded from this federal-only estimate.'
                    message+=f"\nIncremental federal (including NIIT): {money(tax['federal'])}\n{state_line}\nCovered incremental estimate: {money(tax['total'])}\nRate source: {tax['source']}\nRates reviewed: {tax['rateReviewed']}"
                else:message+='\nTax estimate unavailable: '+reason
                QMessageBox.information(self,'FIFO sale scenario',message);return
            except (ValueError,OSError) as exc:dialog.error.setText(str(exc))
