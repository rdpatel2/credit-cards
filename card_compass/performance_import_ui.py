"""Performance CSV account mapping and correction confirmation."""
from PySide6.QtWidgets import (QVBoxLayout,QPushButton,QFileDialog,QTableWidgetItem,QDialogButtonBox,QCheckBox,QHeaderView)
from .ui import FitDialog,text,ScrollSafeComboBox
from .import_ui import grid
from .performance_import import parse_file,preview_import,commit_import


class PerformanceImportDialog(FitDialog):
    def __init__(self,owner):
        super().__init__(owner)
        self.owner=owner;self.rows=[];self.mapping={};self.preview=None
        self.setWindowTitle('Import account valuations / funding CSV')
        layout=QVBoxLayout(self)
        layout.addWidget(text('Import a version 1 valuations or funding_flows CSV from Reports & export, or use its documented columns. Map each source account explicitly. Whole-account USD valuations include cash; contribution/withdrawal amounts cross the account boundary. Importing does not certify completeness or replace trade lots.', 'muted',True))
        choose=QPushButton('Choose CSV…');choose.clicked.connect(self.choose_file);layout.addWidget(choose)
        self.accounts=grid(['CSV account','Existing investment account']);self.accounts.setMaximumHeight(160)
        layout.addWidget(self.accounts)
        build=QPushButton('Preview mapped records');build.clicked.connect(self.build);layout.addWidget(build)
        self.table=grid(['Action','Account','Date','Kind','Previous record','New amount','Source'])
        self.table.horizontalHeader().setSectionResizeMode(0,QHeaderView.ResizeMode.Fixed);self.table.setColumnWidth(0,100)
        layout.addWidget(self.table,1)
        self.ack=QCheckBox('I reviewed these existing-record corrections and intend to replace those values.')
        self.ack.hide();layout.addWidget(self.ack)
        self.status=text('Nothing is saved until confirmation.','muted',True);layout.addWidget(self.status)
        buttons=QDialogButtonBox(QDialogButtonBox.StandardButton.Cancel)
        self.confirm=buttons.addButton('Confirm import',QDialogButtonBox.ButtonRole.AcceptRole)
        self.confirm.setEnabled(False);self.confirm.clicked.connect(self.save);buttons.rejected.connect(self.reject)
        layout.addWidget(buttons);self.resize(1050,700)

    def invalidate(self,*_):
        self.preview=None;self.confirm.setEnabled(False);self.table.setRowCount(0);self.ack.setChecked(False);self.ack.hide()

    def choose_file(self):
        path,_=QFileDialog.getOpenFileName(self,'Choose performance CSV','','CSV (*.csv)')
        if not path:return
        self.invalidate();self.rows=[]
        try:
            self.rows=parse_file(path)
            labels=sorted({row['account_id'] for row in self.rows})
            self.accounts.setRowCount(len(labels));self.mapping={}
            for i,label in enumerate(labels):
                self.accounts.setItem(i,0,QTableWidgetItem(label))
                combo=ScrollSafeComboBox();combo.addItem('Choose destination account',None)
                for account in self.owner.store.state['investmentAccounts']:
                    combo.addItem(account['name'],account['id'])
                    if account['id']==label:combo.setCurrentIndex(combo.count()-1)
                combo.currentIndexChanged.connect(self.invalidate)
                self.accounts.setCellWidget(i,1,combo);self.accounts.setRowHeight(i,48);self.mapping[label]=combo
            self.status.setText(f'{len(self.rows)} USD records loaded. Choose mappings and preview.')
        except (ValueError,OSError,UnicodeError) as exc:self.status.setText('CSV unavailable: '+str(exc))

    def build(self):
        self.invalidate()
        try:
            if not self.rows:raise ValueError('Choose a CSV first.')
            if not self.owner.layout_store.flush():raise ValueError('Save the workspace before previewing imports.')
            self.preview=preview_import(self.owner.store.state,self.rows,{label:combo.currentData() for label,combo in self.mapping.items()})
            accounts={row['id']:row['name'] for row in self.owner.store.state['investmentAccounts']}
            values=[]
            for kind,row in self.preview['added']:values.append(['Add',accounts[row['accountId']],row['date'],row.get('kind','valuation'),'—',row['amount'],row['source']])
            for kind,prior,row in self.preview['changed']:values.append(['Correct',accounts[row['accountId']],row['date'],row.get('kind','valuation'),prior['date']+' · '+prior.get('kind','valuation')+' · USD '+prior['amount']+' · '+prior['source'],row['amount'],row['source']])
            self.table.setRowCount(len(values))
            for i,row in enumerate(values):
                for j,value in enumerate(row):
                    item=QTableWidgetItem(str(value));item.setToolTip(str(value));self.table.setItem(i,j,item)
            self.ack.setVisible(bool(self.preview['changed']))
            self.status.setText(f"Add {len(self.preview['added'])} · correct {len(self.preview['changed'])} · skip {len(self.preview['skipped'])} identical records. Review dates, amounts, funding direction and account mapping.")
            self.confirm.setEnabled(True)
        except (ValueError,OSError) as exc:self.status.setText('Preview unavailable: '+str(exc))

    def save(self):
        if self.preview is None:return
        try:
            commit_import(self.owner.store,self.preview,self.ack.isChecked())
            self.accept()
        except (ValueError,OSError) as exc:self.status.setText('Import not saved: '+str(exc))
