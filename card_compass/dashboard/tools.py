"""Dashboard settings and reusable, data-independent starter templates."""
from copy import deepcopy
from PySide6.QtWidgets import QDialog, QVBoxLayout, QFormLayout, QCheckBox, QComboBox, QSpinBox, QDialogButtonBox
from ..investments import page
from .widget_registry import REGISTRY
from .layout_engine import ENGINE

STARTERS = {
    'Investment overview': ('Portfolio, market context and upcoming events.', ['watchlist','market','analyst','news','earnings','notes']),
    'Trader view': ('Price action, recent trades and market movers.', ['chart','quote','movers','trades','news','notes']),
    'Research view': ('Fundamentals, analyst coverage and your thesis.', ['chart','metrics','analyst','news','earnings','notes']),
}


def starter(name):
    dashboard = page('template', name, STARTERS[name][1])
    geometry = []
    for index, block in enumerate(dashboard['widgets']):
        definition = REGISTRY.get(block['type'])
        geometry.append(dict(i=block['id'], x=(index%2)*6, y=(index//2)*32,
                             w=6, h=32, minW=definition.minSize[0], minH=definition.minSize[1]))
    dashboard['layouts'] = {key: ENGINE.derive(geometry,key) for key in ('lg','md','sm','xs')}
    return dashboard


class DashboardSettings(QDialog):
    def __init__(self, settings, parent=None):
        super().__init__(parent)
        self.setWindowTitle('Dashboard settings')
        self.settings = deepcopy(settings)
        layout=QVBoxLayout(self);form=QFormLayout();layout.addLayout(form)
        self.compact=QCheckBox('Float blocks up to close gaps')
        self.compact.setChecked(settings.get('compact',True));form.addRow('Vertical compaction',self.compact)
        self.density=QComboBox();self.density.addItems(['comfortable','compact']);self.density.setCurrentText(settings.get('density','comfortable'));form.addRow('Density',self.density)
        self.gap=QSpinBox();self.gap.setRange(4,24);self.gap.setSuffix(' px');self.gap.setValue(settings.get('gap',12));form.addRow('Space between blocks',self.gap)
        self.motion=QCheckBox('Reduce animation');self.motion.setChecked(settings.get('reducedMotion',False));form.addRow('Motion',self.motion)
        buttons=QDialogButtonBox(QDialogButtonBox.StandardButton.Save|QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self.accept);buttons.rejected.connect(self.reject);layout.addWidget(buttons)
    def values(self):
        return dict(self.settings,compact=self.compact.isChecked(),density=self.density.currentText(),gap=self.gap.value(),reducedMotion=self.motion.isChecked())
