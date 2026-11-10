"""Material combinations; delegates all parameter ownership to the shared app."""
from film_foundry.ui.qt import QtWidgets
from film_foundry.ui.curve_editor import text
from film_foundry.app.material_designs import DESIGNS,TRAITS,compose_material

class MaterialDesignDialog(QtWidgets.QDialog):
    def __init__(self,config,parent=None):
        super().__init__(parent);self.config=config;self.setWindowTitle(text('构建材料','Compose material'));self.resize(540,380)
        layout=QtWidgets.QVBoxLayout(self);form=QtWidgets.QFormLayout();layout.addLayout(form);self.choices={}
        labels={'design':('材料起点','Material starting point'),'emulsion':('乳剂','Emulsion'),
            'grain':('颗粒','Grain'),'support':('片基','Support'),'backing':('防光晕层','Antihalation'),'age':('材料状态','Material age')}
        for axis,options in {'design':DESIGNS,**TRAITS}.items():
            combo=QtWidgets.QComboBox()
            for key,names in options.items():combo.addItem(text(*names),key)
            self.choices[axis]=combo;form.addRow(text(*labels[axis]),combo)
        self.setToolTip(text('生成可继续编辑的材料参数，替换当前材料草稿；药水和扫描设置保留。红敏仅作用于已记录可见光，不生成红外；附加层采用现有统一池，尚非完整电影去碳工艺。',
            'Replace the material draft with editable parameters; retain chemistry and scanning. Red weighting uses recorded visible light only. Auxiliary backing uses the shared pool, not a full remjet process.'))
        buttons=QtWidgets.QDialogButtonBox(QtWidgets.QDialogButtonBox.StandardButton.Ok|QtWidgets.QDialogButtonBox.StandardButton.Cancel)
        layout.addStretch();layout.addWidget(buttons);buttons.accepted.connect(self.apply);buttons.rejected.connect(self.reject)

    def apply(self):
        values={axis:combo.currentData() for axis,combo in self.choices.items()}
        self.config=compose_material(self.config,**values);self.accept()
