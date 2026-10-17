"""Review local destinations and exact selected records before a service copy."""
import json
from decimal import Decimal
from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QVBoxLayout,QFormLayout,QTabWidget,QWidget,QTextEdit,
    QCheckBox,QDialogButtonBox,QTableWidgetItem)
from .ui import FitDialog,text
from .planning_ui import choices
from .import_ui import grid
from .shared_sync import PlanningCopies,investment_preview


class SharedCopyReview(FitDialog):
    def __init__(self,owner,origin,identity,username,record):
        super().__init__(owner);self.owner=owner;self.origin=origin;self.identity=identity;self.record=record;self.preview=None;self.confirmed=None
        self.setWindowTitle('Review downloaded records for your desktop');self.resize(960,740)
        layout=QVBoxLayout(self)
        notice=text(f"Download {record['payload']['name']} · service version {record['version']} from {origin} as {username}. Service update: {record['updated']}. This is a recorded snapshot, not a live bank verification.",'muted',True);notice.setTextFormat(Qt.TextFormat.PlainText);layout.addWidget(notice)
        self.planning=PlanningCopies(owner.planning_store) if record['kind']!='investment_account' else None
        options=[(None,'Create a separate new local record')]
        if record['kind']=='investment_account':options.extend((row['id'],row['name']) for row in owner.investment_store.state['investmentAccounts'] if row['id']!='legacy')
        else:
            rows=owner.planning_store.accounts() if record['kind']=='manual_account' else owner.planning_store.goals()
            options.extend((row['id'],row['name']+' · '+row['currency']) for row in rows)
        self.destination=choices(options);form=QFormLayout();form.addRow('Local destination',self.destination);layout.addLayout(form)
        self.tabs=QTabWidget();layout.addWidget(self.tabs,1)
        summary=QWidget();summary_layout=QVBoxLayout(summary);self.changes=grid(['Field / record coverage','Local before','Incoming snapshot']);summary_layout.addWidget(self.changes,1);self.tabs.addTab(summary,'Changes')
        self.incoming=QTextEdit();self.incoming.setReadOnly(True);self.incoming.setPlainText(json.dumps(record,indent=2,ensure_ascii=False));self.tabs.addTab(self.incoming,'Exact incoming records')
        self.before=QTextEdit();self.before.setReadOnly(True);self.tabs.addTab(self.before,'Local records / mapping')
        note=text('A local copy remains on your device if the publisher later withdraws access. Copying does not edit the service. Accounts/goals keep other dated values and local goal links/categories; the reviewed date can replace an existing value. Updating an investment account replaces its complete selected trades, values, flows and dividends; omitted records and undocumented fields are removed. Other accounts, dashboards and private research notes remain.','muted',True);note.setTextFormat(Qt.TextFormat.PlainText);layout.addWidget(note)
        self.reviewed=QCheckBox('I reviewed these exact records, source dates and local destination');layout.addWidget(self.reviewed)
        self.replace=QCheckBox('I intend to update the reviewed existing local record / replace the selected investment history');layout.addWidget(self.replace)
        self.duplicates=QCheckBox('I reviewed same-name records and intend to create another copy despite possible double counting');layout.addWidget(self.duplicates)
        self.status=text('','muted',True);self.status.setTextFormat(Qt.TextFormat.PlainText);layout.addWidget(self.status)
        buttons=QDialogButtonBox(QDialogButtonBox.StandardButton.Save|QDialogButtonBox.StandardButton.Cancel);self.save_button=buttons.button(QDialogButtonBox.StandardButton.Save);self.save_button.setText('Verify source and copy reviewed records');buttons.accepted.connect(self.confirm);buttons.rejected.connect(self.reject);layout.addWidget(buttons)
        self.destination.currentIndexChanged.connect(self.rebuild);self.rebuild()
        if self.preview and self.preview.value['binding']:
            ident=self.preview.value['target'];self.destination.blockSignals(True);self.destination.setCurrentIndex(self.destination.findData(ident));self.destination.blockSignals(False);self.destination.setEnabled(False)

    def rebuild(self,*_):
        self.preview=None;self.confirmed=None;self.save_button.setEnabled(False)
        for field in (self.reviewed,self.replace,self.duplicates):field.setChecked(False)
        try:
            target=self.destination.currentData()
            self.preview=investment_preview(self.owner.investment_store,self.origin,self.identity,self.record,target) if self.record['kind']=='investment_account' else self.planning.preview(self.origin,self.identity,self.record,target)
            value=self.preview.value;self.before.setPlainText(json.dumps(dict(local=value['local'],mapping=value['binding']),indent=2,ensure_ascii=False));self.render(value)
            self.replace.setVisible(value['target'] is not None and not value['unchanged']);self.duplicates.setVisible(bool(value['possibleDuplicates']))
            self.status.setText('Already copied at this version; local records will be preserved.' if value['unchanged'] else 'Local edits differ from the last copy. Review before explicitly replacing them.' if value['conflict'] else 'Same-name local records: '+', '.join(row['name'] for row in value['possibleDuplicates']) if value['possibleDuplicates'] else 'Review ready. The service snapshot will be fetched again before local saving; a changed version or permission stops this copy.')
            self.save_button.setEnabled(True)
        except (ValueError,OSError) as exc:self.status.setText(str(exc));self.before.clear();self.changes.setRowCount(0)

    def render(self,value):
        payload=value['record']['payload'];local=value['local'];rows=[]
        if value['record']['kind']=='investment_account':
            def counts(record):
                if not record:return ['—']*4
                workspace=record['workspace'];return [sum(len(items) for items in workspace['trades'].values()),len(workspace['performanceRecords']['valuations']),len(workspace['performanceRecords']['flows']),len(workspace['dividendEvents'])]
            before=counts(local);after=counts(payload)
            rows=[(label,str(a),str(b)) for label,a,b in zip(('Complete trade history','Whole-account values','External funding flows','Dividend records'),before,after)]
            current=next((row for row in local['workspace']['investmentAccounts'] if row['id']=='shared'),{}) if local else {}
            incoming=next(row for row in payload['workspace']['investmentAccounts'] if row['id']=='shared')
            rows.extend((title,current.get(field,'—'),incoming[field]) for title,field in (('Name','name'),('Account tax treatment','kind'),('Recorded owner','owner')))
        else:
            before=local['record'] if local else {};history=local['history'] if local else []
            field='amount_cents' if value['record']['kind']=='manual_account' else 'saved_cents'
            previous=next((row[field] for row in history if row['date']==payload['asOf']),None)
            rows=[('Name',before.get('name','—'),payload['name']),('Currency',before.get('currency','—'),payload['currency']),('Value at '+payload['asOf'],'—' if previous is None else f'{Decimal(previous)/100:,.2f}',f'{Decimal(payload["amountCents"])/100:,.2f}'),('Other dated values kept',str(sum(row['date']!=payload['asOf'] for row in history)),'Preserved')]
            if value['record']['kind']=='goal':rows.extend([('Goal target','—' if not before else f'{Decimal(before["target_cents"])/100:,.2f}',f'{Decimal(payload["targetCents"])/100:,.2f}'),('Target date',before.get('target_date','—'),payload['targetDate'])])
            else:
                rows.extend([('Account type',before.get('kind','—'),payload['accountType']),('Recorded owner',before.get('owner','—'),payload['owner'])])
                from .planning import ACCOUNT_TYPES
                if ACCOUNT_TYPES[payload['accountType']][1]=='liability':rows.extend([('Debt APR (%)','—' if not before else str(Decimal(before['apr_bps'])/100),str(Decimal(payload['aprBps'])/100)),('Minimum monthly payment','—' if not before else str(Decimal(before['minimum_payment_cents'])/100),str(Decimal(payload['minimumPaymentCents'])/100))])
        self.changes.setRowCount(len(rows))
        for index,row in enumerate(rows):
            for col,content in enumerate(row):item=QTableWidgetItem(str(content));item.setToolTip(str(content));self.changes.setItem(index,col,item)

    def confirm(self):
        if not self.preview:self.status.setText('Build a valid review first.');return
        value=self.preview.value
        if not self.reviewed.isChecked():self.status.setText('Review the exact source, records and destination before copying.');return
        if value['target'] is not None and not value['unchanged'] and not self.replace.isChecked():self.status.setText('Confirm the reviewed update / full replacement of this existing local record.');return
        if value['possibleDuplicates'] and not self.duplicates.isChecked():self.status.setText('Review possible double counting before creating another local record.');return
        self.confirmed=(self.preview,self.replace.isChecked(),self.duplicates.isChecked());self.accept()
