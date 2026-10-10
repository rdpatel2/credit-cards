"""Independent widget definitions, native components and declarative configuration."""
from dataclasses import dataclass
from datetime import date
from contextvars import ContextVar
from PySide6.QtCore import Qt, Signal, Slot, QTimer
from PySide6.QtGui import QBrush, QColor
from PySide6.QtWidgets import (QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QStackedWidget,
    QTextEdit, QTableWidget, QTableWidgetItem, QHeaderView, QAbstractItemView, QLineEdit, QComboBox,
    QDialogButtonBox, QFormLayout, QSpinBox, QCheckBox, QListWidget, QListWidgetItem, QMenu, QLayout)
from PySide6.QtWidgets import QStyledItemDelegate
from ..investments import METRICS, RANGES, position, symbol
from ..market_news import SECTORS
from ..trading_calendar import earnings_date
from ..theme import color
from ..ui import FitDialog as QDialog

_DATA_CONTEXT=ContextVar('dashboard_data_layer')
WATCHLIST_MATCH_ROLE=Qt.ItemDataRole.UserRole+1


class EarningsDelegate(QStyledItemDelegate):
    """A visible watchlist marker that survives the table's theme stylesheet."""
    def paint(self,painter,option,index):
        super().paint(painter,option,index)
        if index.column()==0 and index.data(WATCHLIST_MATCH_ROLE):
            marker=option.rect.adjusted(2,5,0,-5);marker.setWidth(4)
            painter.fillRect(marker,QColor(color('#187148')))


def shared_data_layer():
    """Widget data context, independent of its frame, page or scene position."""
    return _DATA_CONTEXT.get()


def create_widget(definition,config,size,isEditing,onConfigChange,layer):
    token=_DATA_CONTEXT.set(layer)
    try:return definition.Component(config,size,isEditing,onConfigChange)
    finally:_DATA_CONTEXT.reset(token)


def money(value):
    return '—' if value is None else f"{'−' if value < 0 else ''}${abs(value):,.2f}"


def numeric(value):
    return '—' if value is None else f'{float(value):,.3f}'.rstrip('0').rstrip('.')


def percent(value):
    return '—' if value is None else f'{float(value):+.2f}%'


def price(value,currency='USD'):
    return money(value) if currency=='USD' else '—' if value is None else f'{currency} {numeric(value)}'


def requirements(kind, config, state):
    ticker = config.get('symbol', 'AAPL')
    listing = next((w for w in state['watchlists'] if w['id'] == config.get('watchlistId')), state['watchlists'][0])
    if kind == 'chart': return {('quote', ticker + '|' + config.get('range', '1mo'))}
    if kind == 'quote': return {('quote', ticker + '|1d')}
    if kind in ('metrics', 'analyst'):
        tickers = listing['symbols'] if kind == 'analyst' and config.get('analystScope') == 'watchlist' else [ticker]
        return {('modules', s) for s in tickers}
    if kind in ('news', 'headlines'): return {('news', 'market' if kind == 'headlines' or config.get('newsScope') == 'market' else ticker)}
    if kind == 'earnings': return {('earnings', earnings_date(config))}
    if kind in ('watchlist', 'positions', 'market', 'movers'):
        tickers = ['SPY','QQQ','DIA','IWM'] if kind == 'market' else listing['symbols']
        return {('quote', s + '|1d') for s in tickers}
    return set()


@dataclass(frozen=True)
class WidgetDefinition:
    type: str
    title: str
    icon: str
    description: str
    category: str
    defaultSize: tuple
    minSize: tuple
    configSchema: dict
    Component: object
    ConfigPanel: object
    dataRequirements: object


class WidgetRegistry:
    def __init__(self): self.definitions = {}
    def register(self, definition): self.definitions[definition.type] = definition
    def get(self, kind): return self.definitions.get(kind, self.definitions['unknown'])


class DataWidget(QStackedWidget):
    footerChanged = Signal(str)
    crashed = Signal(str)
    def __init__(self, config, size, isEditing, onConfigChange):
        super().__init__()
        definition=REGISTRY.get(self.widget_type);layer=shared_data_layer()
        self.definition, self.config, self.onConfigChange, self.layer = definition, dict(config), onConfigChange, layer
        self.available_size=size
        self.isEditing = isEditing
        self.subscription=id(self)
        self.destroyed.connect(lambda obj=None,key=self.subscription:layer.unsubscribe(key))
        self.footer_text = ''
        self.setMinimumSize(0,0)
        self.layout().setSizeConstraint(QLayout.SizeConstraint.SetNoConstraint)
        self.content = QWidget()
        self.layout_body = QVBoxLayout(self.content)
        self.layout_body.setContentsMargins(0,0,0,0)
        self.layout_body.setSizeConstraint(QLayout.SizeConstraint.SetNoConstraint)
        self.layout_body.setSpacing(4)
        self.table = None
        self.editor = None
        self.chart = None
        self.controls = None
        self.selection_actions=[]
        self.message_page = QWidget()
        messages = QVBoxLayout(self.message_page)
        messages.setContentsMargins(8,8,8,8)
        messages.setSizeConstraint(QLayout.SizeConstraint.SetNoConstraint)
        self.message_label = QLabel()
        self.message_label.setWordWrap(True)
        messages.addWidget(self.message_label,1)
        retry = QPushButton('Refresh')
        self.retry_button=retry
        retry.clicked.connect(self.refresh)
        messages.addWidget(retry)
        self.addWidget(self.content)
        self.addWidget(self.message_page)
        kind = definition.type
        if kind == 'notes':
            self.editor = QTextEdit()
            self.editor.setMinimumSize(0,0)
            self.editor.setPlaceholderText('Your thesis, questions, and next steps…')
            self.editor.setPlainText(layer.state['notes'].get(config.get('_noteKey',''),''))
            self.editor.textChanged.connect(lambda: onConfigChange({'note':self.editor.toPlainText()}))
            self.layout_body.addWidget(self.editor)
        elif kind == 'chart':
            from .price_chart import PriceChart
            self.chart = PriceChart([])
            self.chart.setMinimumSize(0,0)
            self.layout_body.addWidget(self.chart)
        elif kind != 'unknown':
            if kind in ('positions', 'ledger', 'headlines'):
                self.controls = QWidget()
                controls = QHBoxLayout(self.controls)
                controls.setContentsMargins(0,0,0,0)
                controls.setSizeConstraint(QLayout.SizeConstraint.SetNoConstraint)
                if kind == 'positions':
                    self.watchlists = QComboBox()
                    self.watchlists.currentIndexChanged.connect(lambda: onConfigChange({'watchlistId':self.watchlists.currentData()}))
                    controls.addWidget(self.watchlists,1)
                    for label, action in [('Add ticker','add_symbol'),('Record trade','record_trade'),('Sell','sale_selected')]:
                        button=QPushButton(label);button.setObjectName('rowAction')
                        button.clicked.connect(lambda checked=False,a=action:self.handle_action(a));controls.addWidget(button)
                        if action=='sale_selected':self.selection_actions.append(button)
                    actions=QPushButton('Actions');actions.setObjectName('rowAction');actions.setProperty('essential',True)
                    menu=QMenu(actions)
                    for title,action in [('Add ticker','add_symbol'),('Record trade','record_trade'),('Sell selected','sale_selected'),('Remove selected ticker','remove_symbol'),('New watchlist','new_watchlist'),('Rename watchlist','rename_watchlist'),('Delete watchlist','delete_watchlist')]:
                        item=menu.addAction(title,lambda checked=False,a=action:self.handle_action(a))
                        if action in ('sale_selected','remove_symbol'):self.selection_actions.append(item)
                    actions.setMenu(menu);controls.addWidget(actions)
                elif kind == 'ledger':
                    self.filter = QLineEdit();self.filter.setPlaceholderText('Filter ticker')
                    self.filter.textChanged.connect(lambda: self.safe_render())
                    controls.addWidget(self.filter,1)
                    for label, action in [('Record trade','record_trade'),('Edit','edit_trade'),('Delete','delete_trade')]:
                        button=QPushButton(label);button.setObjectName('rowAction')
                        button.clicked.connect(lambda checked=False,a=action:self.handle_action(a));controls.addWidget(button)
                        if action in ('edit_trade','delete_trade'):self.selection_actions.append(button)
                else:
                    self.filter = QLineEdit();self.filter.setPlaceholderText('Search headlines')
                    self.filter.textChanged.connect(lambda:self.safe_render())
                    controls.addWidget(self.filter,1)
                self.layout_body.addWidget(self.controls)
            self.table = QTableWidget()
            if kind=='earnings':self.table.setItemDelegate(EarningsDelegate(self.table))
            self.table.setMinimumSize(0,0)
            self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
            self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
            self.table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
            self.table.verticalHeader().hide()
            self.table.horizontalHeader().setMinimumSectionSize(30)
            self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
            self.table.horizontalHeader().setSectionsMovable(True)
            self.table.horizontalHeader().sectionMoved.connect(self.save_column_order)
            self.table.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
            self.table.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
            self.table.itemDoubleClicked.connect(self.open_row)
            self.table.itemSelectionChanged.connect(self.update_selection_actions)
            self.layout_body.addWidget(self.table,1)
            self.update_selection_actions()
        layer.changed.connect(self.on_data_changed)
        self.set_config(config)
        if kind=='earnings':
            self.session_timer=QTimer(self);self.session_timer.setInterval(60000)
            self.session_timer.timeout.connect(self.update_earnings_session);self.session_timer.start()

    def update_earnings_session(self):
        if self.keys!=self.definition.dataRequirements(self.config,self.layer.state):self.set_config(self.config)

    def set_config(self, config):
        self.config = dict(config)
        self.keys = self.definition.dataRequirements(config, self.layer.state)
        self.layer.subscribe(self.keys,self.subscription)
        self.safe_render()

    @Slot(str)
    def on_data_changed(self, key):
        note_changed=self.definition.type=='notes' and key=='note:'+self.config.get('_noteKey','')
        if key == 'workspace' or note_changed or any(key == k + ':' + v for k,v in self.keys):
            if key=='workspace':
                self.keys=self.definition.dataRequirements(self.config,self.layer.state)
                self.layer.subscribe(self.keys,self.subscription)
            self.safe_render()

    def safe_render(self):
        try:
            self.render()
            self.last_error = None
        except Exception as exc:
            self.last_error = str(exc)
            self.crashed.emit(str(exc))

    def refresh(self):
        if self.definition.type=='earnings':self.update_earnings_session()
        self.layer.refresh(self.keys)
        self.safe_render()

    def update_selection_actions(self):
        for action in self.selection_actions:action.setEnabled(self.table.currentRow()>=0)

    def handle_action(self,action):
        row=self.table.currentRow() if self.table else -1
        ticker=self.table.item(row,0).text() if row>=0 and self.table.item(row,0) else None
        if action=='sale_selected':
            if ticker:self.layer.action('sale_scenario',ticker)
        elif action=='remove_symbol':
            if ticker:self.layer.action(action,self.config.get('watchlistId'),ticker)
        elif action in ('add_symbol','rename_watchlist','delete_watchlist'):self.layer.action(action,self.config.get('watchlistId'))
        elif action=='new_watchlist':
            ident=self.layer.action(action)
            if ident:self.onConfigChange({'watchlistId':ident})
        elif action in ('edit_trade','delete_trade'):
            if 0<=row<len(self.trade_rows):self.layer.action(action,self.trade_rows[row])
        elif action=='record_trade':self.layer.action(action,ticker if self.definition.type=='positions' else None,None,self.config.get('watchlistId') if self.definition.type=='positions' else None)

    def state_message(self, message):
        self.message_label.setText(message)
        self.setCurrentWidget(self.message_page)

    def render(self):
        kind=self.definition.type;config=self.config;ticker=config.get('symbol','AAPL')
        if kind=='unknown':
            self.retry_button.hide()
            self.state_message('This widget type is unavailable. Its saved configuration is preserved. Remove it or install its component to continue.')
            return
        if kind=='notes':
            note=self.layer.state['notes'].get(config.get('_noteKey',''),'')
            if self.editor.toPlainText()!=note:
                cursor=self.editor.textCursor();caret=cursor.position();offset=self.editor.verticalScrollBar().value()
                self.editor.blockSignals(True);self.editor.setPlainText(note);cursor=self.editor.textCursor();cursor.setPosition(min(caret,len(note)));self.editor.setTextCursor(cursor);self.editor.blockSignals(False)
                self.editor.verticalScrollBar().setValue(offset)
            self.setCurrentWidget(self.content);self.set_footer('Notes · saved on this device');return
        statuses=[self.layer.status(k,v) for k,v in self.keys]
        loaded=[self.layer.data(k,v) for k,v in sorted(self.keys)]
        sources=[f"{v.get('source','Public data')} · {v.get('asOf','')[:19]}" for v in loaded if v]
        self.set_footer(('Cached · ' if 'stale' in statuses else '') + (sources[0] if sources else 'Refresh to load data' if self.keys else 'Saved on this device'))
        if 'loading' in statuses:self.set_footer(('Cached data · ' if any(v and v.get('stale') for v in loaded) else '')+'Loading data…'+(' · '+sources[0] if sources else ''))
        elif 'error' in statuses or any(k+':'+v in self.layer.failures for k,v in self.keys):
            failure=next((self.layer.failures[k+':'+v] for k,v in self.keys if k+':'+v in self.layer.failures),'Data unavailable')
            self.set_footer(('Cached data · ' if any(loaded) else '')+failure+' · Refresh to retry')
        if self.keys and not any(loaded) and kind not in ('watchlist','positions','market','movers'):
            if 'loading' in statuses:
                self.state_message('Loading '+self.definition.title.lower()+'…');return
            if 'error' in statuses:
                errors=[self.layer.failures.get(k+':'+v,'') for k,v in self.keys]
                self.state_message('Could not load '+self.definition.title.lower()+'. '+next((e for e in errors if e),'')+' Use Refresh to retry.');return
            self.state_message('No data loaded for '+ticker+'. Refresh to load '+self.definition.title.lower()+'.');return
        self.setCurrentWidget(self.content)
        listing=next((w for w in self.layer.state['watchlists'] if w['id']==config.get('watchlistId')),self.layer.state['watchlists'][0])
        limit=max(1,min(120,int(config.get('limit',8))))
        if kind=='chart':
            data=self.layer.data('quote',ticker+'|'+config.get('range','1mo')) or {}
            self.chart.set_data(data.get('points',[]),data.get('currency','USD'),data.get('timezone','UTC'));return
        if kind in ('quote','metrics','analyst'):
            data=self.layer.data('quote',ticker+'|1d') if kind=='quote' else self.layer.data('modules',ticker)
            data=data or {}
            if kind=='quote':
                rows=[['Price',price(data.get('price'),data.get('currency','USD'))],['Day change',percent(data.get('changePercent'))],['Volume',numeric(data.get('volume'))],['Day high / low',numeric(data.get('high'))+' / '+numeric(data.get('low'))],['52-week high / low',numeric(data.get('yearHigh'))+' / '+numeric(data.get('yearLow'))]]
            elif kind=='metrics':
                percentages={'dividendYield','profitMargins','operatingMargins','returnOnEquity','revenueGrowth','earningsGrowth','debtToEquity'}
                monetary={'trailingEps','forwardEps','marketCap','enterpriseValue','totalRevenue','freeCashflow'}
                rows=[]
                for k in config.get('metrics',list(METRICS)[:8]):
                    value=data.get('values',{}).get(k)
                    currency=data.get('financialCurrency') if k in ('totalRevenue','freeCashflow') else data.get('currency')
                    formatted=f'{float(value)*100:.2f}%' if k in percentages and value is not None else price(value,currency or 'Currency unavailable') if k in monetary else numeric(value)
                    rows.append([METRICS.get(k,k),formatted])
                if not any(data.get('values',{}).get(k) is not None for k in config.get('metrics',list(METRICS)[:8])):
                    self.state_message('The provider has not supplied the selected fundamentals for '+ticker+'. Choose other metrics in Configure or Refresh to retry.');return
            else:
                periods=data.get('periods',[])
                if config.get('analystScope')=='watchlist':
                    totals={}
                    covered=0
                    for s in listing['symbols']:
                        coverage=self.layer.data('modules',s) or {}
                        if coverage.get('periods'):covered+=1
                        for period in coverage.get('periods',[]):
                            totals.setdefault(period['period'],[0]*5)
                            totals[period['period']]=[a+b for a,b in zip(totals[period['period']],period['counts'])]
                    periods=[dict(period=k,counts=v) for k,v in totals.items()]
                    self.set_footer(self.footer_text.removeprefix(listing['name']+' · ')+f' · Coverage: {covered} of {len(listing["symbols"])} tickers')
                rows=[]
                for period in periods:
                    counts=period['counts'];total=sum(counts);score=sum(n*(index+1) for index,n in enumerate(counts))/total if total else None
                    rows.append([period['period'],' / '.join(str(n) for n in counts)+(' · score '+numeric(score) if score is not None else '')])
                if not rows:
                    self.state_message('No analyst coverage is available for '+ticker+'. Refresh or choose another ticker in Configure.');return
            self.rows(['Period' if kind=='analyst' else 'Metric','Sell+ / Sell / Hold / Buy / Buy+' if kind=='analyst' else 'Value'],rows[:limit] if kind=='analyst' else rows)
            if kind=='metrics':
                for row,key in enumerate(config.get('metrics',list(METRICS)[:8])):
                    if data.get('values',{}).get(key) is None:self.table.item(row,1).setToolTip('Not supplied by the data provider. Refresh or choose another metric.')
            if kind=='analyst':self.table.horizontalHeaderItem(1).setToolTip('Strong sell / Sell / Hold / Buy / Strong buy recommendation counts')
        elif kind in ('watchlist','positions','market','movers'):
            if kind=='positions':
                self.watchlists.blockSignals(True);self.watchlists.clear()
                for w in self.layer.state['watchlists']:self.watchlists.addItem(w['name'],w['id'])
                self.watchlists.setCurrentIndex(max(0,self.watchlists.findData(listing['id'])));self.watchlists.blockSignals(False)
            tickers=['SPY','QQQ','DIA','IWM'] if kind=='market' else list(listing['symbols'])
            if kind=='movers' or kind!='positions' and config.get('sort') in ('price','change'):
                field='price' if kind!='movers' and config.get('sort')=='price' else 'changePercent'
                descending=kind!='movers' or config.get('direction','gainers')=='gainers'
                def sort_key(s):
                    value=(self.layer.data('quote',s+'|1d') or {}).get(field)
                    return (value is None,(-value if descending else value) if value is not None else 0)
                tickers.sort(key=sort_key)
            elif kind!='positions':tickers.sort()
            rows=[]
            self.symbols=tickers[:limit] if kind!='positions' else tickers
            for s in self.symbols:
                q=self.layer.data('quote',s+'|1d') or {};p=position(self.layer.state['trades'].get(s,[]))
                if kind=='positions':rows.append([s,q.get('name','—'),price(q.get('price'),q.get('currency','USD')),percent(q.get('changePercent')),numeric(p['quantity']),money(p['averageCost']),money(p['basis'])])
                else:
                    row=[s,price(q.get('price'),q.get('currency','USD')),percent(q.get('changePercent'))]
                    if config.get('showPositions'):row.extend([numeric(p['quantity']),money(p['averageCost'])])
                    rows.append(row)
            self.rows(['Ticker','Company','Price','Day %','Shares','Average cost','Remaining basis'] if kind=='positions' else ['Ticker','Price','Day %']+(['Shares','Average cost'] if config.get('showPositions') else []),rows)
            covered=sum(bool(self.layer.data('quote',s+'|1d')) for s in self.symbols)
            self.set_footer(self.footer_text+f' · Quotes: {covered} of {len(self.symbols)}')
            for row,s in enumerate(self.symbols):
                q=self.layer.data('quote',s+'|1d') or {};status=self.layer.status('quote',s+'|1d')
                detail=s+' · '+status.title()+' · '+self.layer.failures.get('quote:'+s+'|1d',q.get('asOf','Use Refresh to load this quote.'))
                for col in range(self.table.columnCount()):self.table.item(row,col).setToolTip(detail+'\n'+self.table.item(row,col).text())
        elif kind in ('trades','ledger'):
            query=self.filter.text().upper() if kind=='ledger' else ''
            self.trade_rows=sorted([(s,t) for s,trades in self.layer.state['trades'].items() if query in s for t in trades],key=lambda pair:pair[1]['date'],reverse=True)
            rows=[[t['date'],s,t['side'].title(),numeric(t['quantity']),money(t['price']),money(t['fees'])] for s,t in self.trade_rows]
            keys=[t['id'] for s,t in self.trade_rows]
            self.rows(['Date','Ticker','Side','Shares','Price','Fees'],rows if kind=='ledger' else rows[:limit],keys if kind=='ledger' else keys[:limit])
        elif kind in ('news','headlines','earnings'):
            req=next(iter(self.keys));data=self.layer.data(*req) or {}
            query=self.filter.text().casefold() if kind=='headlines' else ''
            sector=config.get('_sector',config.get('sector','all'))
            items=[i for i in data.get('items',[]) if kind=='earnings' or (query in i['title'].casefold() and (sector=='all' or sector in i.get('sectors',[])))]
            watched={s.upper() for w in self.layer.state['watchlists'] for s in w['symbols']}
            if kind=='earnings':items.sort(key=lambda i:i.get('symbol','').upper() not in watched)
            self.articles=items if kind=='headlines' else items[:limit]
            if not items:
                if kind=='headlines':
                    self.rows(['Headline','Published','Sector tags'],[])
                    self.set_footer('No matching headlines · change search/sector or Refresh')
                    return
                self.state_message('No '+('earnings events for this date. Configure another date or refresh.' if kind=='earnings' else 'matching headlines. Change the search/sector or refresh.'));return
            rows=[[i.get('symbol',''),i.get('name',''),'—' if i.get('epsForecast') in (None,'') else i['epsForecast'],i.get('reportTime','Not supplied')] if kind=='earnings' else [i['title'],i.get('date',''),', '.join(i.get('sectors',[]))] for i in self.articles]
            self.rows(['Ticker','Company','EPS forecast','Report time'] if kind=='earnings' else ['Headline','Published','Sector tags'],rows)
            if kind=='earnings':
                matches=sum(i.get('symbol','').upper() in watched for i in items)
                self.set_footer(self.footer_text+f' · {matches} watchlist match'+('es' if matches!=1 else ''))
                for row,item in enumerate(self.articles):
                    details='\n'.join(f'{label}: {item.get(key) or "Not supplied"}' for key,label in [('date','Date'),('reportTime','Report time'),('fiscalQuarterEnding','Fiscal quarter'),('noOfEsts','Estimates'),('lastYearEPS','Last year EPS'),('lastYearRptDt','Last year report date'),('marketCap','Market cap')])
                    matched=item.get('symbol','').upper() in watched
                    for col in range(4):
                        cell=self.table.item(row,col)
                        cell.setData(WATCHLIST_MATCH_ROLE,matched)
                        cell.setToolTip(('On your watchlist\n' if matched else '')+details)
                        if matched:
                            cell.setBackground(QBrush(QColor(color('#e6f2e9'))))
                            font=cell.font();font.setBold(True);cell.setFont(font)
        self.on_size(self.size())

    def rows(self, headers, rows, keys=None):
        selected=self.table.item(self.table.currentRow(),0)
        selected=selected.data(Qt.ItemDataRole.UserRole) if selected else None
        offset=self.table.verticalScrollBar().value()
        self.table.setCurrentCell(-1,-1);self.table.clearSelection()
        self.table.setColumnCount(len(headers));self.table.setHorizontalHeaderLabels(headers)
        self.table.setRowCount(len(rows))
        for i,row in enumerate(rows):
            key=keys[i] if keys is not None else str(row[0])
            for j,value in enumerate(row):
                item=QTableWidgetItem(str(value))
                item.setToolTip(str(value))
                if j==0:item.setData(Qt.ItemDataRole.UserRole,key)
                self.table.setItem(i,j,item)
            if selected is not None and key==selected:self.table.selectRow(i)
        self.table.verticalScrollBar().setValue(offset)
        group={'positions':'watchlist','ledger':'trades','headlines':'news'}.get(self.definition.type)
        header=self.table.horizontalHeader()
        header.blockSignals(True)
        for visual,logical in enumerate(self.layer.state.get('viewSettings',{}).get('order',{}).get(group,list(range(len(headers))))):
            current=header.visualIndex(logical)
            if current!=visual:header.moveSection(current,visual)
        header.blockSignals(False)

    def save_column_order(self,*args):
        group={'positions':'watchlist','ledger':'trades','headlines':'news'}.get(self.definition.type)
        if group:
            header=self.table.horizontalHeader()
            self.layer.state.setdefault('viewSettings',{}).setdefault('order',{})[group]=[header.logicalIndex(i) for i in range(header.count())]
            self.layer.schedule_local()

    def set_footer(self, value):
        if self.definition.type=='earnings':
            prefix=earnings_date(self.config)+(' · Next trading day' if not self.config.get('earningsDate') else '')
            if not value.startswith(prefix+' · '):value=prefix+' · '+value
        if self.definition.type in ('chart','quote','metrics','analyst'):
            prefix=self.config.get('symbol','AAPL')
            if self.definition.type=='analyst' and self.config.get('analystScope')=='watchlist':
                listing=next((w for w in self.layer.state['watchlists'] if w['id']==self.config.get('watchlistId')),self.layer.state['watchlists'][0]);prefix=listing['name']
            value=prefix+' · '+value
        self.footer_text=value;self.footerChanged.emit(value)

    def on_size(self,size):
        self.available_size=size
        if self.table:
            columns=self.table.columnCount()
            visible=2 if size.width()<320 else 3 if size.width()<480 else columns
            group={'positions':'watchlist','ledger':'trades','headlines':'news'}.get(self.definition.type)
            selected=self.layer.state.get('viewSettings',{}).get('columns',{}).get(group,list(range(columns)))
            shown=selected[:visible]
            for column in range(columns):self.table.setColumnHidden(column,column not in shown)
            self.table.verticalHeader().setDefaultSectionSize(self.layer.state.get('viewSettings',{}).get('rowHeight',32 if size.width()<320 else 42))
        if self.controls:
            for button in self.controls.findChildren(QPushButton):button.setVisible(bool(button.property('essential')) or size.width()>=480)

    def content_height(self):
        if self.table:return min(900,self.table.rowCount()*42+48+(40 if self.controls else 0))
        if self.editor:return max(140,min(800,self.editor.document().size().height()+24))
        return 230

    def open_row(self,item):
        kind=self.definition.type
        if kind in ('watchlist','positions','movers','market'):
            self.layer.action('open_symbol',self.table.item(item.row(),0).text())
        elif kind=='earnings':self.layer.action('open_symbol',self.articles[item.row()]['symbol'])
        elif kind in ('news','headlines') and self.articles[item.row()].get('url'):
            self.layer.action('open_url',self.articles[item.row()]['url'])


class ConfigPanel(QDialog):
    def __init__(self, definition, config, layer, parent=None):
        super().__init__(parent)
        self.setWindowTitle('Configure '+definition.title)
        self.definition,self.config=definition,dict(config)
        layout=QVBoxLayout(self);form=QFormLayout();layout.addLayout(form)
        self.fields={}
        for key,(label,kind) in definition.configSchema.items():
            if kind=='bool':field=QCheckBox();field.setChecked(bool(config.get(key,key=='followSymbol')))
            elif kind=='int':field=QSpinBox();field.setRange(1,120);field.setValue(config.get(key,8))
            elif kind=='watchlist':
                field=QComboBox()
                for w in layer.state['watchlists']:field.addItem(w['name'],w['id'])
                field.setCurrentIndex(max(0,field.findData(config.get(key))))
            elif kind=='metrics':
                field=QListWidget();field.setMaximumHeight(240)
                for metric,title in METRICS.items():
                    item=QListWidgetItem(title);item.setData(Qt.ItemDataRole.UserRole,metric)
                    item.setFlags(item.flags()|Qt.ItemFlag.ItemIsUserCheckable)
                    item.setCheckState(Qt.CheckState.Checked if metric in config.get(key,list(METRICS)[:8]) else Qt.CheckState.Unchecked)
                    field.addItem(item)
            elif isinstance(kind,tuple):field=QComboBox();field.addItems(kind);field.setCurrentText(config.get(key,kind[0]))
            elif isinstance(kind,dict):
                field=QComboBox()
                for value,title in kind.items():field.addItem(title,value)
                field.setCurrentIndex(max(0,field.findData(config.get(key))))
            else:
                field=QLineEdit(str(config.get(key,'')))
                if key=='earningsDate':
                    field.setPlaceholderText('Automatic: next trading day')
                    field.setToolTip('Leave blank to follow the next US trading day, or enter a fixed date as YYYY-MM-DD.')
            form.addRow(label,field);self.fields[key]=field
        self.error=QLabel();self.error.setWordWrap(True);layout.addWidget(self.error)
        if not self.fields:layout.addWidget(QLabel('Use the widget menu to rename this block, and Edit layout to move or resize it.'))
        buttons=QDialogButtonBox(QDialogButtonBox.StandardButton.Save|QDialogButtonBox.StandardButton.Cancel)
        buttons.button(QDialogButtonBox.StandardButton.Save).setEnabled(bool(self.fields))
        buttons.accepted.connect(self.accept);buttons.rejected.connect(self.reject);layout.addWidget(buttons)
    def values(self):
        config=dict(self.config)
        for key,field in self.fields.items():
            if isinstance(field,QListWidget):
                config[key]=[field.item(i).data(Qt.ItemDataRole.UserRole) for i in range(field.count()) if field.item(i).checkState()==Qt.CheckState.Checked]
                if not config[key]:raise ValueError('Choose at least one metric.')
                continue
            config[key]=field.isChecked() if isinstance(field,QCheckBox) else field.value() if isinstance(field,QSpinBox) else (field.currentData() if field.currentData() is not None else field.currentText()) if isinstance(field,QComboBox) else field.text()
        if 'symbol' in config:config['symbol']=symbol(config['symbol'])
        if config.get('earningsDate'):date.fromisoformat(config['earningsDate'])
        return config


REGISTRY=WidgetRegistry()
TICKER=dict(symbol=('Ticker','text'),followSymbol=('Follow this stock','bool'))
WATCH=dict(watchlistId=('Watchlist','watchlist'),limit=('Row limit','int'))
WATCH_VIEW=dict(**WATCH,sort=('Sort',{'symbol':'Ticker','price':'Highest price','change':'Largest daily change'}),showPositions=('Show saved positions','bool'))
definitions=[
 ('watchlist','Watchlist','Portfolio','Saved tickers, prices and daily changes.',WATCH_VIEW),
 ('positions','Watchlists and positions','Portfolio','Holdings, cost basis and watchlist actions.',dict(watchlistId=WATCH['watchlistId'])),
 ('trades','Recent trades','Portfolio','Your most recent recorded transactions.',dict(limit=('Row limit','int'))),
 ('ledger','Trade history','Portfolio','Search and edit recorded trades.',{}),
 ('chart','Price chart','Research','Closing prices over the selected period.',dict(**TICKER,range=('Chart range',RANGES))),
 ('quote','Key statistics','Research','Price, volume and daily trading range.',TICKER),
 ('metrics','Advanced metrics','Research','Company fundamentals and valuation metrics.',dict(**TICKER,metrics=('Metrics','metrics'))),
 ('analyst','Analyst trend','Research','Analyst recommendation counts by period.',dict(**TICKER,**WATCH,analystScope=('Coverage scope',('stock','watchlist')))),
 ('movers','Daily movers','Market','Watchlist daily gains and losses.',dict(**WATCH,showPositions=('Show saved positions','bool'),direction=('Direction',{'gainers':'Biggest gainers','losers':'Biggest losers'}))),
 ('market','Market overview','Market','Benchmark ETF prices and daily changes.',dict(limit=WATCH['limit'],sort=WATCH_VIEW['sort'],showPositions=WATCH_VIEW['showPositions'])),
 ('news','News','Market','Company or market headlines.',dict(**TICKER,limit=('Row limit','int'),newsScope=('Headlines',('company','market')),sector=('Sector',SECTORS))),
 ('headlines','Market headlines','Market','Searchable sector-filtered market headlines.',{}),
 ('earnings','Earnings calendar','Market','Next trading day’s earnings, with watchlist matches highlighted.',dict(earningsDate=('Date (blank: next trading day)','text'),limit=('Row limit','int'))),
 ('notes','Research notes','Personal','Your saved thesis and research notes.',{}),
 ('unknown','Unavailable widget','Other','Preserved block whose component is unavailable.',{}),
]
for kind,title,category,description,schema in definitions:
    component=type(kind.title()+'Widget',(DataWidget,),dict(widget_type=kind))
    REGISTRY.register(WidgetDefinition(kind,title,'▦',description,category,(12 if kind in ('positions','ledger','headlines') else 6,32),(3,24 if kind in ('positions','ledger','headlines') else 18),schema,component,ConfigPanel,lambda config,state,k=kind:requirements(k,config,state)))
