"""User-controlled exports; files stay on the local device."""
from pathlib import Path
from PySide6.QtCore import QDate
from PySide6.QtWidgets import QWidget, QVBoxLayout, QFormLayout, QPushButton, QFileDialog, QMessageBox
from .ui import text
from .planning_ui import choices, dated
from .reports import DATASETS, rows_for, write_csv


class ReportsPage(QWidget):
    def __init__(self, owner):
        super().__init__()
        self.owner = owner
        layout = QVBoxLayout(self)
        layout.addWidget(text('Export your records', 'heroTitle'))
        layout.addWidget(text('CSV files are readable in spreadsheets and other tools. They include financial data in plain text; choose where to store them. Exports describe your recorded data, not a broker statement or a tax filing package.', 'muted',True))
        form = QFormLayout()
        self.dataset = choices(list(DATASETS.items()),'card_transactions')
        self.start = dated()
        self.start.setDate(QDate(QDate.currentDate().year(),1,1))
        self.end = dated()
        self.end.setMaximumDate(QDate.currentDate())
        form.addRow('Dataset',self.dataset)
        form.addRow('Start',self.start)
        form.addRow('End / lots as of',self.end)
        layout.addLayout(form)
        layout.addWidget(text('Monthly cash flow and budget exports include whole selected months because payment status has monthly precision. Remaining lots include all purchases up to the end date, regardless of start date. Other datasets filter their recorded dates. Amounts retain their own currency; existing trade/card ledgers are USD.', 'muted',True))
        export = QPushButton('Export CSV…')
        export.setObjectName('primary')
        export.clicked.connect(self.export)
        layout.addWidget(export)
        automation = QPushButton('Local automations / scheduled reports…')
        automation.clicked.connect(self.show_automations)
        layout.addWidget(automation)
        api = QPushButton('Optional read-only local API…')
        api.clicked.connect(self.show_api)
        layout.addWidget(api)
        services = QPushButton('Optional services / household sharing…')
        services.clicked.connect(self.show_connected)
        layout.addWidget(services)
        self.status = text('', 'muted',True)
        layout.addWidget(self.status)
        layout.addStretch()

    def show_automations(self):
        from .automations_ui import AutomationsDialog
        AutomationsDialog(self.owner).exec()

    def show_api(self):
        from .local_api_ui import LocalAPIDialog
        LocalAPIDialog(self.owner).exec()

    def show_connected(self):
        from .connected_ui import ConnectedServicesDialog
        ConnectedServicesDialog(self.owner).exec()

    def export(self):
        dataset = self.dataset.currentData()
        try:
            headers,rows = rows_for(self.owner,dataset,self.start.date().toString('yyyy-MM-dd'),self.end.date().toString('yyyy-MM-dd'))
            path,_ = QFileDialog.getSaveFileName(self,'Export '+DATASETS[dataset],'finance-compass-'+dataset+'.csv','CSV files (*.csv)')
            if not path:
                return
            destination = Path(path)
            if destination.resolve() in {(self.owner.directory/name).resolve() for name in ('profile.json','transactions.sqlite3','investments-workspace.json','market-cache.json')}:
                raise ValueError('Choose an export file outside the active data files.')
            if destination.suffix.lower() != '.csv':
                destination = Path(str(destination)+'.csv')
            count = write_csv(destination,headers,rows)
            self.status.setText(f'Exported {count} rows · CSV schema version 1 · '+str(destination))
        except (ValueError,OSError) as exc:
            QMessageBox.warning(self,'Could not export records',str(exc))
