"""Persistent alert rules/activity, evaluated only while the desktop app is open."""
from copy import deepcopy
from PySide6.QtCore import QObject,Signal,QTimer,Qt
from PySide6.QtWidgets import (QVBoxLayout,QHBoxLayout,QFormLayout,QLineEdit,QPushButton,QSpinBox,
 QDialogButtonBox,QTableWidgetItem,QTabWidget,QWidget,QCheckBox,QMessageBox,QHeaderView)
from .ui import FitDialog,text,ScrollSafeDoubleSpinBox
from .planning_ui import choices
from .import_ui import grid
from .investments import symbol
from .market_alerts import initial_alerts,rule_values,validate_alerts,requirements,evaluate,is_quiet
from .dashboard.data_health import readable_time


class MarketAlertEngine(QObject):
    changed=Signal()
    status=Signal(str)
    def __init__(self,owner):
        super().__init__(owner)
        self.owner=owner;self.keys=set();self.checking=False;self.stopping=False;self.error=''
        self.subscription='market-alert-rules'
        self.timer=QTimer(self);self.timer.setInterval(60000);self.timer.timeout.connect(self.check);self.timer.start()
        owner.data_layer.changed.connect(self.data_changed)
        QTimer.singleShot(0,self.check)

    def state(self):return self.owner.store.state.get('marketAlerts',initial_alerts())

    def persist(self,alerts):
        validate_alerts(alerts)
        if not self.owner.layout_store.flush():raise OSError('Workspace save failed; alert changes are not saved. Retry the workspace save first.')
        candidate=deepcopy(self.owner.store.state);candidate['marketAlerts']=alerts
        self.owner.store.save(candidate);self.error='';self.changed.emit()

    def data_changed(self,key):
        if any(key==':'.join(request) for request in self.keys):self.check()

    def check(self):
        if self.checking or self.stopping:return
        self.checking=True
        try:
            alerts=self.state();self.keys=requirements(alerts)
            self.owner.data_layer.subscribe(self.keys,self.subscription)
            candidate,new=evaluate(alerts,self.owner.data_layer.data,self.owner.data_layer.health())
            if candidate!=alerts:self.persist(candidate)
            if new and not is_quiet(candidate):self.status.emit(f'{len(new)} new in-app market alert(s). Open Tools → Market alerts to review sources and details.')
        except (ValueError,OSError) as exc:
            self.error=str(exc);self.status.emit('Market alerts unavailable: '+str(exc));self.changed.emit()
        finally:self.checking=False

    def shutdown(self):
        self.stopping=True;self.timer.stop();self.owner.data_layer.unsubscribe(self.subscription)
        try:self.owner.data_layer.changed.disconnect(self.data_changed)
        except (RuntimeError,TypeError):pass


class AlertRuleDialog(FitDialog):
    def __init__(self,parent,row=None):
        super().__init__(parent)
        self.row=row or {}
        self.setWindowTitle('Edit market condition' if row else 'Add market condition')
        layout=QVBoxLayout(self)
        layout.addWidget(text('Choose your own condition. Price checks use usable public USD quotes and notify when a level is met, then rearm after it stops being met. Earnings reminders cover today through the chosen calendar-day window, skipping closed exchange sessions. Data alerts cover currently watched feeds after a grace period.', 'muted',True))
        form=QFormLayout()
        self.name=QLineEdit(self.row.get('name',''))
        self.kind=choices([('price','Price level'),('earnings','Earnings reminder'),('stale','Watched-feed problem')],self.row.get('kind','price'))
        self.ticker=QLineEdit(self.row.get('symbol',parent.owner.selected_symbol));self.ticker.setMaxLength(10)
        self.direction=choices([('above','At or above'),('below','At or below')],self.row.get('direction','above'))
        self.threshold=ScrollSafeDoubleSpinBox();self.threshold.setDecimals(6);self.threshold.setRange(.000001,1e9);self.threshold.setPrefix('USD ');self.threshold.setValue(float(self.row.get('threshold',100)))
        self.original_threshold=self.threshold.value()
        self.lookahead=QSpinBox();self.lookahead.setRange(0,7);self.lookahead.setValue(self.row.get('lookahead',3));self.lookahead.setSuffix(' calendar days')
        self.delay=QSpinBox();self.delay.setRange(1,240);self.delay.setValue(self.row.get('delayMinutes',5));self.delay.setSuffix(' minutes')
        self.enabled=QCheckBox('Enabled while the app is open');self.enabled.setChecked(self.row.get('enabled',True))
        for label,control in [('Name',self.name),('Condition',self.kind),('Ticker',self.ticker),('Price direction',self.direction),('Threshold',self.threshold),('Earnings window',self.lookahead),('Problem grace period',self.delay)]:form.addRow(label,control)
        layout.addLayout(form);layout.addWidget(self.enabled)
        self.error=text('','muted',True);layout.addWidget(self.error)
        buttons=QDialogButtonBox(QDialogButtonBox.StandardButton.Save|QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self.accept);buttons.rejected.connect(self.reject);layout.addWidget(buttons)
        self.kind.currentIndexChanged.connect(self.update_kind);self.update_kind();self.resize(620,620)

    def update_kind(self,*_):
        kind=self.kind.currentData()
        self.ticker.setEnabled(kind in ('price','earnings'))
        for control in (self.threshold,self.direction):control.setEnabled(kind=='price')
        self.lookahead.setEnabled(kind=='earnings');self.delay.setEnabled(kind=='stale')

    def values(self):
        kind=self.kind.currentData();config={}
        if kind in ('price','earnings'):config['symbol']=symbol(self.ticker.text())
        if kind=='price':config.update(direction=self.direction.currentData(),threshold=self.row['threshold'] if 'threshold' in self.row and self.threshold.value()==self.original_threshold else str(self.threshold.value()))
        elif kind=='earnings':config['lookahead']=self.lookahead.value()
        else:config['delayMinutes']=self.delay.value()
        row=rule_values(self.name.text(),kind,self.enabled.isChecked(),**config)
        if self.row:row['id']=self.row['id']
        return row


class MarketAlertsDialog(FitDialog):
    def __init__(self,owner):
        super().__init__(owner)
        self.owner=owner;self.engine=owner.alert_engine
        self.setWindowTitle('Market alerts · local rules and activity')
        layout=QVBoxLayout(self)
        layout.addWidget(text('Checks run while Finance Compass is open, usually every minute and when a watched quote/calendar arrives. Alerts stay on this device; no emails or orders. Demo, cached and non-USD data do not fire price reminders. Earnings dates may change. Quiet hours suppress in-app status announcements; activity still records.', 'muted',True))
        self.tabs=QTabWidget();layout.addWidget(self.tabs,1)
        events=QWidget();event_layout=QVBoxLayout(events)
        self.events=grid(['Created (device time)','Read','Condition','Source / as of','Details'])
        self.events.horizontalHeader().setSectionResizeMode(0,QHeaderView.ResizeMode.Fixed);self.events.setColumnWidth(0,150)
        event_layout.addWidget(self.events,1)
        controls=QHBoxLayout()
        for title,handler in [('Mark selected read',self.mark_selected),('Mark all read',self.mark_all),('Clear read activity',self.clear_read)]:
            button=QPushButton(title);button.clicked.connect(handler);controls.addWidget(button)
        event_layout.addLayout(controls);self.tabs.addTab(events,'Activity')
        rules=QWidget();rule_layout=QVBoxLayout(rules)
        self.rules=grid(['Name','Kind / condition','Enabled','State'])
        self.rules.horizontalHeader().setSectionResizeMode(0,QHeaderView.ResizeMode.ResizeToContents)
        self.rules.itemDoubleClicked.connect(lambda *_:self.edit_rule())
        rule_layout.addWidget(self.rules,1)
        actions=QHBoxLayout()
        for title,handler in [('Add condition',lambda:self.edit_rule(True)),('Edit selected',self.edit_rule),('Delete selected',self.delete_rule),('Check now',self.engine.check)]:
            button=QPushButton(title);button.clicked.connect(handler);actions.addWidget(button)
        rule_layout.addLayout(actions);self.tabs.addTab(rules,'Rules')
        quiet=QHBoxLayout();self.quiet=QCheckBox('Quiet hours (device time)')
        self.start=QSpinBox();self.end=QSpinBox()
        for control,value in [(self.start,22),(self.end,7)]:control.setRange(0,23);control.setValue(value)
        quiet.addWidget(self.quiet);quiet.addWidget(self.start);quiet.addWidget(text('to'));quiet.addWidget(self.end)
        save=QPushButton('Save quiet hours');save.clicked.connect(self.save_quiet);quiet.addWidget(save)
        layout.addLayout(quiet)
        self.status=text('','muted',True);layout.addWidget(self.status)
        buttons=QDialogButtonBox(QDialogButtonBox.StandardButton.Close);buttons.rejected.connect(self.reject);layout.addWidget(buttons)
        self.engine.changed.connect(self.reload);self.resize(1120,750)
        quiet_hours=self.engine.state()['quietHours']
        self.quiet.setChecked(quiet_hours is not None)
        if quiet_hours:self.start.setValue(quiet_hours['start']);self.end.setValue(quiet_hours['end'])
        self.reload()

    @staticmethod
    def fill(table,rows,keys):
        selected=table.item(table.currentRow(),0)
        selected=selected.data(Qt.ItemDataRole.UserRole) if selected else None
        table.clearSelection();table.setCurrentCell(-1,-1);table.setRowCount(len(rows))
        for i,row in enumerate(rows):
            for j,value in enumerate(row):
                item=QTableWidgetItem(str(value));item.setToolTip(str(value))
                if j==0:item.setData(Qt.ItemDataRole.UserRole,keys[i])
                table.setItem(i,j,item)
            if keys[i]==selected:table.selectRow(i)

    def reload(self):
        alerts=self.engine.state();self.event_rows=list(reversed(alerts['events']));self.rule_rows=alerts['rules']
        self.fill(self.events,[[readable_time(row['created']),'Read' if row['read'] else 'Unread',row['title'],row['source']+' · '+row['asOf'],row['details']] for row in self.event_rows],[row['id'] for row in self.event_rows])
        values=[]
        for row in self.rule_rows:
            condition=row['symbol']+' '+row['direction']+' USD '+str(row['threshold']) if row['kind']=='price' else row['symbol']+f" · next {row['lookahead']} days" if row['kind']=='earnings' else f"Watched feed problems ≥ {row['delayMinutes']} minutes"
            values.append([row['name'],condition,'Yes' if row['enabled'] else 'Paused','Paused' if not row['enabled'] else 'Condition active' if row['runtime']['active'] else 'Watching'])
        self.fill(self.rules,values,[row['id'] for row in self.rule_rows])
        self.status.setText(f"{sum(not row['read'] for row in alerts['events'])} unread · {len(alerts['rules'])} rules. "+('No conditions yet: add your own price, earnings or data-quality condition.' if not alerts['rules'] else '')+(' Capacity reached: clear read activity to allow more notifications; nothing was silently removed.' if alerts['capacityWarning'] else '')+(' Save/check error: '+self.engine.error if self.engine.error else ''))

    def save(self,alerts):
        try:self.engine.persist(alerts);self.engine.check();return True
        except (ValueError,OSError) as exc:self.status.setText('Alert changes not saved: '+str(exc));return False

    def edit_rule(self,new=False):
        row=None
        if not new:
            i=self.rules.currentRow()
            if not 0<=i<len(self.rule_rows):return
            row=deepcopy(self.rule_rows[i])
        dialog=AlertRuleDialog(self,row)
        while dialog.exec()==dialog.DialogCode.Accepted:
            try:
                changed=dialog.values();alerts=deepcopy(self.engine.state())
                index=next((i for i,old in enumerate(alerts['rules']) if old['id']==changed['id']),None)
                if index is None:alerts['rules'].append(changed)
                else:
                    current=alerts['rules'][index]
                    if {k:v for k,v in current.items() if k not in ('name','enabled','runtime')}=={k:v for k,v in changed.items() if k not in ('name','enabled','runtime')}:
                        changed['runtime']=current['runtime']
                    alerts['rules'][index]=changed
                validate_alerts(alerts)
                if self.save(alerts):return
                dialog.error.setText(self.status.text())
            except ValueError as exc:dialog.error.setText(str(exc))

    def delete_rule(self):
        i=self.rules.currentRow()
        if not 0<=i<len(self.rule_rows):return
        ident=self.rule_rows[i]['id']
        if QMessageBox.question(self,'Delete alert condition','Delete this condition? Recorded activity is retained.')!=QMessageBox.StandardButton.Yes:return
        alerts=deepcopy(self.engine.state());alerts['rules']=[row for row in alerts['rules'] if row['id']!=ident];self.save(alerts)

    def mark_selected(self):
        i=self.events.currentRow()
        if not 0<=i<len(self.event_rows):return
        ident=self.event_rows[i]['id'];alerts=deepcopy(self.engine.state())
        for row in alerts['events']:
            if row['id']==ident:row['read']=True
        self.save(alerts)

    def mark_all(self):
        alerts=deepcopy(self.engine.state())
        for row in alerts['events']:row['read']=True
        self.save(alerts)

    def clear_read(self):
        alerts=deepcopy(self.engine.state());count=sum(row['read'] for row in alerts['events'])
        if not count:return
        if QMessageBox.question(self,'Clear read activity',f'Remove {count} read notifications? Unread activity and rules are retained.')!=QMessageBox.StandardButton.Yes:return
        alerts['events']=[row for row in alerts['events'] if not row['read']];self.save(alerts)

    def save_quiet(self):
        alerts=deepcopy(self.engine.state());alerts['quietHours']=dict(start=self.start.value(),end=self.end.value()) if self.quiet.isChecked() else None
        self.save(alerts)

    def done(self,result):
        try:self.engine.changed.disconnect(self.reload)
        except (RuntimeError,TypeError):pass
        super().done(result)
