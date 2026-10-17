"""Explicit tax scope and supported-rate coverage; unsupported jurisdictions stay visible."""
from datetime import date
from PySide6.QtWidgets import QVBoxLayout,QFormLayout,QSpinBox,QLineEdit,QCheckBox,QPushButton,QDialogButtonBox
from .ui import FitDialog,text
from .planning_ui import choices
from .cash_forecast_ui import cash_field,cash_value
from .investment_tax import STATUSES,FIELDS,DEDUCTIONS_BY_YEAR,NC_DEDUCTIONS,coverage,validate_profile


class TaxProfileDialog(FitDialog):
    def __init__(self,owner):
        super().__init__(owner)
        self.setWindowTitle('Tax year / jurisdiction / sale assumptions')
        existing=owner.store.state.get('taxProfile') or {}
        layout=QVBoxLayout(self)
        layout.addWidget(text('Set the scope before using a hypothetical sale estimate. Rates are reviewed for 2025/2026 US resident individuals, federal-only or full-year North Carolina. Other years/jurisdictions can be recorded, with estimates withheld.','muted',True))
        form=QFormLayout()
        self.year=QSpinBox();self.year.setRange(1900,2200);self.year.setValue(existing.get('year',date.today().year))
        self.country=QLineEdit(existing.get('country','US'));self.country.setMaxLength(2)
        self.state=choices([('FEDERAL','US federal only (state excluded)'),('NC','North Carolina'),('OTHER','Other jurisdiction')],existing.get('state','FEDERAL'))
        self.other_state=QLineEdit(existing.get('state','') if existing.get('state') not in ('NC','FEDERAL',None) else '')
        self.other_state.setMaxLength(40)
        if existing.get('state') not in ('NC','FEDERAL',None):self.state.setCurrentIndex(self.state.findData('OTHER'))
        self.status=choices(list(STATUSES.items()),existing.get('filingStatus','single'))
        self.resident=QCheckBox('Assumptions are for a US resident individual');self.resident.setChecked(existing.get('residentUS',existing.get('country','US')=='US'))
        self.nc_resident=QCheckBox('Full-year North Carolina resident');self.nc_resident.setChecked(existing.get('fullYearNC',existing.get('state')=='NC'))
        for title,field in [('Tax year',self.year),('Country code',self.country),('State / jurisdiction',self.state),('Other jurisdiction label',self.other_state),('Filing status',self.status),('',self.resident),('',self.nc_resident)]:form.addRow(title,field)
        self.fields={}
        deductions=DEDUCTIONS_BY_YEAR.get(self.year.value(),{})
        for key,title in FIELDS.items():
            value=existing.get(key,deductions.get(self.status.currentData(),0) if key=='deduction' else NC_DEDUCTIONS[self.status.currentData()] if key=='stateDeduction' and self.state.currentData()=='NC' else 0)
            self.fields[key]=cash_field(round(value*100));form.addRow(title,self.fields[key])
        layout.addLayout(form)
        defaults=QPushButton('Use reviewed basic deduction defaults')
        defaults.clicked.connect(self.use_defaults);layout.addWidget(defaults)
        layout.addWidget(text('Include recorded and outside-account realized income once in your inputs. Dividend records do not fill these assumptions automatically. Qualified dividends belong in existing preferential income; their qualified portion is already part of gross dividends. Basic defaults are not a deduction eligibility check; review dependent, spouse-itemizes, age/blindness and other circumstances.','muted',True))
        layout.addWidget(text('Gain-only incremental illustration. Loss histories, carryforwards, wash sales, AMT, credits, corporate actions, return-of-capital basis changes, treaties, special-rate gains and other state/country rules are not modeled. Federal-only totals exclude every state tax. No tax filing package or professional review is implied.','muted',True))
        self.error=text('','muted',True);layout.addWidget(self.error)
        buttons=QDialogButtonBox(QDialogButtonBox.StandardButton.Save|QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self.accept);buttons.rejected.connect(self.reject);layout.addWidget(buttons)
        self.state.currentIndexChanged.connect(self.update_scope);self.year.valueChanged.connect(self.update_scope)
        self.country.textChanged.connect(self.update_scope);self.other_state.textChanged.connect(self.update_scope)
        self.resident.toggled.connect(self.update_scope);self.nc_resident.toggled.connect(self.update_scope)
        self.update_scope()

    def values(self):
        return validate_profile(dict(year=self.year.value(),country=self.country.text().strip().upper(),
            state=self.other_state.text().strip() if self.state.currentData()=='OTHER' else self.state.currentData(),
            filingStatus=self.status.currentData(),residentUS=self.resident.isChecked(),fullYearNC=self.nc_resident.isChecked(),
            **{key:cash_value(field)/100 for key,field in self.fields.items()}))

    def update_scope(self,*_):
        self.other_state.setEnabled(self.state.currentData()=='OTHER')
        self.nc_resident.setEnabled(self.state.currentData()=='NC')
        self.fields['stateDeduction'].setEnabled(self.state.currentData()=='NC')
        try:self.error.setText(coverage(self.values()) or 'Reviewed-rate coverage available for this scope. Income and deductions still need your review.')
        except ValueError as exc:self.error.setText(str(exc))

    def use_defaults(self):
        values=DEDUCTIONS_BY_YEAR.get(self.year.value())
        if not values:self.error.setText('No reviewed deduction default for this year; enter an independently reviewed amount.');return
        self.fields['deduction'].setValue(values[self.status.currentData()])
        self.fields['stateDeduction'].setValue(NC_DEDUCTIONS[self.status.currentData()] if self.state.currentData()=='NC' else 0)
        self.update_scope()
