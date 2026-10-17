"""Desktop lifecycle, explicit folder consent and visible automation history."""
from copy import deepcopy
from datetime import datetime,timezone
import json
from pathlib import Path
import sqlite3
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import uuid
from PySide6.QtCore import QObject,QThread,QTimer,Signal,QTime,Qt
from PySide6.QtWidgets import (QWidget,QVBoxLayout,QHBoxLayout,QFormLayout,QLineEdit,
    QPushButton,QCheckBox,QSpinBox,QTimeEdit,QDialogButtonBox,QFileDialog,QMessageBox,
    QTabWidget,QScrollArea,QTableWidgetItem,QTextEdit)
from .ui import FitDialog,text
from .planning_ui import choices
from .import_ui import grid
from .finance import FinanceStore
from .planning import PlanningStore
from .automations import AutomationStore,KINDS,validate_job,reviewed_directory,execute,latest_slot
from .reports import DATASETS


class AutomationWorker(QThread):
    outcome=Signal(str,str)
    def __init__(self,owner,store,job,manual):
        super().__init__(owner)
        self.directory=owner.directory;self.state=deepcopy(owner.investment_store.state)
        self.store=store;self.job=deepcopy(job);self.manual=manual

    def run(self):
        try:
            # One consistent SQLite snapshot for report/rule reads; the mutable
            # investment workspace is separately captured when the job starts.
            with TemporaryDirectory(prefix='compass-report-snapshot-') as directory:
                directory=Path(directory)
                with sqlite3.connect(self.directory/'transactions.sqlite3') as source,sqlite3.connect(directory/'transactions.sqlite3') as destination:source.backup(destination)
                owner=SimpleNamespace(directory=self.directory,finance_store=FinanceStore(directory),
                    planning_store=PlanningStore(directory),investment_store=SimpleNamespace(state=self.state))
                if self.isInterruptionRequested():return
                ident=execute(owner,self.store,self.job,datetime.now(timezone.utc),self.manual)
                self.outcome.emit(ident or '','')
        except (ValueError,OSError,sqlite3.Error) as exc:self.outcome.emit('',str(exc))


class AutomationEngine(QObject):
    changed=Signal()
    def __init__(self,owner):
        super().__init__(owner);self.owner=owner;self.worker=None;self.stopping=False;self.error=''
        self.store=AutomationStore(owner.directory)
        self.store.recover_interrupted(datetime.now(timezone.utc))
        self.timer=QTimer(self);self.timer.setInterval(60000);self.timer.timeout.connect(self.check)
        if not getattr(owner,'is_demo',False):self.timer.start()
        QTimer.singleShot(0,self.check)

    @property
    def busy(self):return self.worker is not None and self.worker.isRunning()

    def check(self):
        if self.stopping or self.busy or getattr(self.owner,'is_demo',False):return
        try:
            now=datetime.now(timezone.utc);seen={(r['job_id'],r['slot']) for r in self.store.history()}
            for job in self.store.jobs():
                slot=latest_slot(job,now)
                if job['enabled'] and slot and (job['id'],slot.isoformat(timespec='seconds')) not in seen:
                    self.start(job);return
        except (ValueError,OSError,KeyError,sqlite3.Error) as exc:self.error=str(exc);self.changed.emit()

    def start(self,job,manual=False):
        if self.stopping or self.busy:raise ValueError('A job is running; wait for its result.')
        if getattr(self.owner,'is_demo',False):raise ValueError('Automation execution is disabled in the disposable demo; use your local profile to review a schedule.')
        if not manual and not job['enabled']:return
        # Flush notes/layout before capturing investment records. A failed save
        # must not produce a report that appears to reflect persisted records.
        self.owner.stocks_page.data_layer.flush_local()
        if not self.owner.stocks_page.layout_store.flush():raise OSError('Save pending workspace changes before running reports.')
        self.error='';self.worker=AutomationWorker(self.owner,self.store,job,manual)
        self.worker.outcome.connect(self.result);self.worker.finished.connect(self.done)
        self.worker.start();self.changed.emit()

    def result(self,ident,error):
        self.error=error
        if ident:
            run=next((r for r in self.store.history() if r['id']==ident),None)
            if run and run['status']=='triggered':self.owner.statusBar().showMessage('Finance reminder: '+run['message'],15000)
        self.changed.emit()

    def done(self):
        worker=self.worker;self.worker=None
        if worker:worker.deleteLater()
        self.changed.emit()
        if self.stopping:self.owner.close()
        else:QTimer.singleShot(0,self.check)

    def shutdown(self):
        self.stopping=True;self.timer.stop()
        if self.busy:self.worker.requestInterruption();return False
        return True


class AutomationEditor(FitDialog):
    def __init__(self,owner,record=None):
        super().__init__(owner);self.owner=owner;self.original=deepcopy(record);self.job=None
        self.setWindowTitle('Review local automation');self.resize(850,700)
        layout=QVBoxLayout(self)
        layout.addWidget(text('Schedules run only while this desktop is open. Times are UTC; a missed schedule catches up once. CSV files contain plaintext financial records. No emails, uploads, trade execution or transaction edits.','muted',True))
        scroll=QScrollArea();scroll.setWidgetResizable(True);body=QWidget();form=QFormLayout(body);scroll.setWidget(body);layout.addWidget(scroll,1)
        self.name=QLineEdit();self.name.setMaxLength(120)
        self.kind=choices(list(KINDS.items()));self.enabled=QCheckBox('Enable this reviewed schedule')
        self.frequency=choices([('daily','Daily'),('weekly','Weekly'),('monthly','Monthly')])
        self.clock=QTimeEdit(QTime(12,0));self.clock.setDisplayFormat('HH:mm')
        self.weekday=choices([(i,name) for i,name in enumerate(('Monday','Tuesday','Wednesday','Thursday','Friday','Saturday','Sunday'))])
        self.month_day=QSpinBox();self.month_day.setRange(1,28)
        self.dataset=choices(list(DATASETS.items()));self.range=choices([('previous_month','Previous complete month'),('trailing_30','Trailing 30 days through today'),('year_to_date','Calendar year through today')])
        self.folder=QLineEdit();self.folder.setReadOnly(True)
        folder_row=QWidget();folder_layout=QHBoxLayout(folder_row);folder_layout.setContentsMargins(0,0,0,0);folder_layout.addWidget(self.folder,1)
        choose=QPushButton('Choose folder…');choose.clicked.connect(self.choose_folder);folder_layout.addWidget(choose)
        self.budget=choices([(key,key) for key in sorted(owner.finance_store.summary(datetime.now().strftime('%Y-%m'))['categories'])])
        self.goal=choices([(str(r['id']),r['name']) for r in owner.planning_store.goals()])
        self.account=choices([(str(r['id']),r['name']) for r in owner.planning_store.accounts()])
        self.threshold=QSpinBox();self.threshold.setRange(1,1000);self.threshold.setValue(100);self.threshold.setSuffix(' %')
        self.age=QSpinBox();self.age.setRange(1,3650);self.age.setValue(30);self.age.setSuffix(' days')
        fields=[('Name',self.name),('Action',self.kind),('',self.enabled),('Check frequency',self.frequency),('UTC time',self.clock),('Weekly day',self.weekday),('Monthly day',self.month_day),('CSV dataset',self.dataset),('Recorded date range',self.range),('Local CSV folder',folder_row),('Budget category',self.budget),('Goal',self.goal),('Account to review',self.account),('Spending / goal threshold',self.threshold),('Maximum goal / balance age',self.age)]
        for title,field in fields:form.addRow(title,field)
        layout.addWidget(text('Budget rules compare recorded spending with max(category target, scheduled costs), not bank cash or forecast costs. Goal rules need fresh recorded progress. Continuous conditions announce once until a valid no-match rearms them. A failed check never implies recovery. Report dates use the device calendar; due times stay UTC across daylight-saving changes.','muted',True))
        self.error=text('','muted',True);layout.addWidget(self.error)
        buttons=QDialogButtonBox(QDialogButtonBox.StandardButton.Save|QDialogButtonBox.StandardButton.Cancel);buttons.accepted.connect(self.confirm);buttons.rejected.connect(self.reject);layout.addWidget(buttons)
        if record:
            for field,key in [(self.kind,'kind'),(self.frequency,'frequency'),(self.weekday,'weekday'),(self.dataset,'dataset'),(self.range,'range')]:field.setCurrentIndex(field.findData(record[key]))
            self.name.setText(record['name']);self.enabled.setChecked(record['enabled']);self.clock.setTime(QTime(record['hour'],record['minute']));self.month_day.setValue(record['monthDay']);self.folder.setText(record['directory']);self.threshold.setValue(record['threshold']);self.age.setValue(record['maxAgeDays'])
            field={'budget':self.budget,'goal':self.goal,'stale_balance':self.account}.get(record['kind'])
            if field:
                index=field.findData(record['target'])
                if index<0:field.addItem('Missing recorded target — choose a replacement',record['target']);index=field.count()-1
                field.setCurrentIndex(index)
        self.kind.currentIndexChanged.connect(self.update_fields);self.frequency.currentIndexChanged.connect(self.update_fields);self.update_fields()

    def update_fields(self,*_):
        kind=self.kind.currentData();frequency=self.frequency.currentData()
        for field in (self.dataset,self.range,self.folder):field.setEnabled(kind=='report')
        self.budget.setEnabled(kind=='budget');self.goal.setEnabled(kind=='goal');self.account.setEnabled(kind=='stale_balance')
        self.threshold.setEnabled(kind in ('budget','goal'));self.age.setEnabled(kind in ('goal','stale_balance'))
        self.weekday.setEnabled(frequency=='weekly');self.month_day.setEnabled(frequency=='monthly')

    def choose_folder(self):
        selected=QFileDialog.getExistingDirectory(self,'Choose reviewed plaintext export folder',self.folder.text())
        if selected:
            try:self.folder.setText(str(reviewed_directory(selected,self.owner.directory)));self.error.clear()
            except (ValueError,OSError) as exc:self.error.setText(str(exc))

    def confirm(self):
        try:
            kind=self.kind.currentData();target={'budget':self.budget,'goal':self.goal,'stale_balance':self.account}.get(kind)
            now=datetime.now(timezone.utc)
            self.job=dict(version=1,id=self.original['id'] if self.original else uuid.uuid4().hex,name=self.name.text().strip(),kind=kind,enabled=self.enabled.isChecked(),activatedAt=now.isoformat(),frequency=self.frequency.currentData(),hour=self.clock.time().hour(),minute=self.clock.time().minute(),weekday=self.weekday.currentData(),monthDay=self.month_day.value(),dataset=self.dataset.currentData(),range=self.range.currentData(),directory=self.folder.text(),target=target.currentData() if target else '',threshold=self.threshold.value(),maxAgeDays=self.age.value())
            validate_job(self.job)
            if kind=='report':reviewed_directory(self.job['directory'],self.owner.directory)
            elif kind in ('goal','stale_balance') and not any(str(r['id'])==self.job['target'] for r in (self.owner.planning_store.goals() if kind=='goal' else self.owner.planning_store.accounts())):raise ValueError('Choose an existing recorded target.')
            self.accept()
        except (ValueError,OSError,TypeError) as exc:self.job=None;self.error.setText(str(exc))


class AutomationsDialog(FitDialog):
    def __init__(self,owner):
        super().__init__(owner);self.owner=owner;self.engine=owner.automation_engine
        self.setWindowTitle('Local automations and scheduled reports');self.resize(1050,720)
        layout=QVBoxLayout(self);layout.addWidget(text('Jobs run on this device while the app is open. Review filesystem destinations and source coverage before enabling them. Restored backups pause every job until you review it again.','muted',True))
        self.tabs=QTabWidget();layout.addWidget(self.tabs,1)
        jobs_page=QWidget();jobs_layout=QVBoxLayout(jobs_page)
        self.jobs=grid(['Name','Action','State','UTC schedule','Target / dataset']);jobs_layout.addWidget(self.jobs,1)
        self.tabs.addTab(jobs_page,'Reviewed jobs')
        actions=QHBoxLayout()
        for title,handler in [('Add…',self.add),('Review / edit…',self.edit),('Pause',self.pause),('Run now',self.run_now),('Remove…',self.remove)]:
            button=QPushButton(title);button.clicked.connect(handler);actions.addWidget(button)
        jobs_layout.addLayout(actions)
        history_page=QWidget();history_layout=QVBoxLayout(history_page)
        self.runs=grid(['Started (UTC)','Job','Result','Message']);history_layout.addWidget(self.runs,1)
        self.detail=QTextEdit();self.detail.setReadOnly(True);self.detail.setMaximumHeight(160);history_layout.addWidget(self.detail)
        clear=QPushButton('Clear finished run history…');clear.clicked.connect(self.clear_history);history_layout.addWidget(clear);self.tabs.addTab(history_page,'Run history and recovery')
        self.status=text('','muted',True);layout.addWidget(self.status)
        buttons=QDialogButtonBox(QDialogButtonBox.StandardButton.Close);buttons.rejected.connect(self.reject);layout.addWidget(buttons)
        self.runs.itemSelectionChanged.connect(self.show_details);self.engine.changed.connect(self.reload);self.reload()

    def selected(self):
        row=self.jobs.currentRow()
        if row<0:raise ValueError('Select a job first.')
        ident=self.jobs.item(row,0).data(Qt.ItemDataRole.UserRole)
        job=next((r for r in self.engine.store.jobs() if r['id']==ident),None)
        if not job:raise ValueError('This job was removed; refresh and select again.')
        return job

    def idle(self):
        if self.engine.busy:raise ValueError('A job is running; wait before changing jobs or history.')

    def reload(self):
        selected=self.jobs.item(self.jobs.currentRow(),0).data(Qt.ItemDataRole.UserRole) if self.jobs.currentRow()>=0 and self.jobs.item(self.jobs.currentRow(),0) else None
        try:
            jobs=self.engine.store.jobs();self.jobs.setRowCount(len(jobs))
            for row,job in enumerate(jobs):
                when=f"{job['frequency']} {job['hour']:02d}:{job['minute']:02d}"+ (f" · weekday {job['weekday']+1}" if job['frequency']=='weekly' else f" · day {job['monthDay']}" if job['frequency']=='monthly' else '')
                values=[job['name'],KINDS[job['kind']],'Enabled' if job['enabled'] else 'Paused',when,DATASETS[job['dataset']] if job['kind']=='report' else job['target']]
                for col,value in enumerate(values):
                    item=QTableWidgetItem(value);item.setToolTip(value);self.jobs.setItem(row,col,item)
                self.jobs.item(row,0).setData(Qt.ItemDataRole.UserRole,job['id'])
                if job['id']==selected:self.jobs.selectRow(row)
            history=self.engine.store.history();self.runs.setRowCount(len(history))
            for row,run in enumerate(history):
                details=json.loads(run['details']);values=[run['started'],details.get('jobName','Removed / interrupted job'),run['status'],run['message']]
                for col,value in enumerate(values):self.runs.setItem(row,col,QTableWidgetItem(value))
                self.runs.item(row,0).setData(Qt.ItemDataRole.UserRole,run['id'])
            self.status.setText(self.engine.error or ('Running one local job…' if self.engine.busy else f'{len(jobs)} jobs · {sum(j["enabled"] for j in jobs)} enabled · no checks while the desktop is closed.'))
        except (ValueError,OSError,KeyError,sqlite3.Error) as exc:self.status.setText('Could not load automation records: '+str(exc))

    def save_editor(self,job=None):
        try:
            self.idle();dialog=AutomationEditor(self.owner,job)
            if dialog.exec()!=dialog.DialogCode.Accepted:return
            self.engine.store.save(dialog.job);self.reload()
        except (ValueError,OSError,sqlite3.Error) as exc:self.status.setText(str(exc))

    def add(self):self.save_editor()
    def edit(self):
        try:self.save_editor(self.selected())
        except ValueError as exc:self.status.setText(str(exc))

    def pause(self):
        try:
            self.idle();job=self.selected();job['enabled']=False;self.engine.store.save(job);self.reload()
        except (ValueError,OSError,sqlite3.Error) as exc:self.status.setText(str(exc))

    def run_now(self):
        try:
            self.idle();job=self.selected()
            if QMessageBox.question(self,'Run reviewed job now?',f"Run {job['name']} now? "+(f"A plaintext CSV will be written to {job['directory']}." if job['kind']=='report' else 'Only the recorded condition is checked.'))!=QMessageBox.StandardButton.Yes:return
            self.engine.start(job,manual=True);self.tabs.setCurrentIndex(1)
        except (ValueError,OSError,sqlite3.Error) as exc:self.status.setText(str(exc))

    def remove(self):
        try:
            self.idle();job=self.selected()
            if QMessageBox.question(self,'Remove automation?',f"Remove {job['name']}? Existing files and run history remain.")!=QMessageBox.StandardButton.Yes:return
            self.engine.store.remove(job['id']);self.reload()
        except (ValueError,OSError,sqlite3.Error) as exc:self.status.setText(str(exc))

    def clear_history(self):
        try:
            self.idle()
            if QMessageBox.question(self,'Clear finished history?', 'This removes finished run evidence and condition latches. Due schedules can run again and active conditions can announce again. Existing exported files remain. Continue?')!=QMessageBox.StandardButton.Yes:return
            self.engine.store.clear_finished();self.detail.clear();self.reload()
        except (ValueError,OSError,sqlite3.Error) as exc:self.status.setText(str(exc))

    def show_details(self):
        row=self.runs.currentRow()
        if row<0 or not self.runs.item(row,0):self.detail.clear();return
        ident=self.runs.item(row,0).data(Qt.ItemDataRole.UserRole)
        run=next((r for r in self.engine.store.history() if r['id']==ident),None)
        self.detail.setPlainText(json.dumps(run,indent=2,ensure_ascii=False) if run else '')

    def done(self,result):
        try:self.engine.changed.disconnect(self.reload)
        except RuntimeError:pass
        super().done(result)
