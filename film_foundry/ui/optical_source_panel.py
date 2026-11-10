"""In-page optical exposure controls shared by all material workspaces."""
from pathlib import Path
from film_foundry.ui.qt import QtCore, QtWidgets
from film_foundry.ui.curve_editor import text
from film_foundry.contracts.optical_cascade import OpticalRelay

BODY_LABELS={'film':('胶片','Film'),'wet_negative':('湿版玻璃负片','Wet glass negative'),
    'wet_positive':('湿版正像','Wet plate positive'),'paper_print':('相纸','Photographic paper'),
    'cyanotype_print':('蓝晒','Cyanotype'),'noble_print':('铂钯印相','Platinum / palladium'),
    'receiver':('接收片','Receiver'),'donor_residue':('供体残片','Donor residue'),
    'silver_negative':('银像负片','Silver negative'),'integral_print':('即显照片','Instant print'),
    'silver_plate':('银版','Silver plate')}


class OpticalSourcePanel(QtWidgets.QGroupBox):
    applied=QtCore.Signal()
    cancelled=QtCore.Signal()
    def __init__(self,path,choices,parent=None):
        super().__init__(text('光学来源 · ','Optical source · ')+Path(path).name,parent)
        if not choices:raise ValueError('No final material bodies')
        self.source_path=str(path);self.choices=choices;default=OpticalRelay()
        layout=QtWidgets.QVBoxLayout(self);row=QtWidgets.QHBoxLayout();layout.addLayout(row)
        left=QtWidgets.QFormLayout();right=QtWidgets.QFormLayout();row.addLayout(left,1);row.addLayout(right,1)
        self.body=QtWidgets.QComboBox()
        for index,item in enumerate(choices):self.body.addItem(text(*BODY_LABELS.get(item['role'],(f'片体 {index+1}',f'Body {index+1}'))),item['body_id'])
        left.addRow(text('来源片体','Source body'),self.body)
        self.mode=QtWidgets.QComboBox();self.mode.addItem(text('透射','Transmission'),'transmission');self.mode.addItem(text('反射','Reflection'),'reflection')
        left.addRow(text('光学路径','Optical path'),self.mode)
        def number(form,label,value,low,high):
            spin=QtWidgets.QDoubleSpinBox();spin.setDecimals(4);spin.setRange(low,high);spin.setValue(value);spin.setKeyboardTracking(False);form.addRow(label,spin);return spin
        self.ev=number(left,text('接收曝光 EV','Receiver exposure EV'),default.exposure_ev,-20,20)
        self.seconds=number(left,text('相对曝光时间 s','Relative exposure time s'),default.exposure_seconds,0,86400)
        self.filters=[number(right,text('滤色密度 '+channel,channel+' filter density'),default.filter_density_rgb[i],0,10) for i,channel in enumerate(('R','G','B'))]
        self.filters[1].setToolTip(text('阻挡绿光，提高可变反差纸反差；并非成片色调调整。','Filters green light, raising VC paper contrast. This is exposure filtration.'))
        self.filters[2].setToolTip(text('阻挡蓝光，降低可变反差纸反差。','Filters blue light, lowering VC paper contrast.'))
        self.setToolTip(text('直接观察最终介质后形成下一材料的曝光；不调用扫描反相、调色或归一化，不改来源。相对RGB剂量不是实测光谱或lux·s。',
            'Expose the next material by observing the final medium directly. No scan inversion, color grading or normalization; the source is unchanged. Relative RGB dose, not a measured spectrum or lux·s.'))
        self.body.currentIndexChanged.connect(self.body_changed);self.body_changed()
        actions=QtWidgets.QHBoxLayout();actions.addStretch();layout.addLayout(actions)
        cancel=QtWidgets.QPushButton(text('取消','Cancel'));cancel.clicked.connect(self.cancelled);actions.addWidget(cancel)
        apply=QtWidgets.QPushButton(text('载入曝光来源','Load exposure source'));apply.setObjectName('primaryAction');apply.clicked.connect(self.applied);actions.addWidget(apply)

    def body_changed(self):
        entry=self.choices[self.body.currentIndex()]
        self.mode.setCurrentIndex(self.mode.findData(entry['mode']));self.mode.setEnabled(entry['body_id'] is None)

    def selection(self):
        return self.body.currentData(),OpticalRelay(mode=self.mode.currentData(),exposure_ev=self.ev.value(),
            exposure_seconds=self.seconds.value(),filter_density_rgb=tuple(control.value() for control in self.filters))
