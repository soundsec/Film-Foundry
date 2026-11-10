"""Shared read-only preview histogram and executed operation inspection."""
import json
import math
from dataclasses import asdict

from film_foundry.ui.qt import QtCore, QtWidgets
from film_foundry.ui.curve_editor import text
from film_foundry.ui.histogram import Histogram
from film_foundry.contracts.material_recipe import KEY as RECIPE_KEY,STATUS_KEY


class MaterialInspector(QtWidgets.QWidget):
    def __init__(self, owner, operation_labels):
        super().__init__();self.owner=owner;self.labels=operation_labels;self._identity=None
        layout=QtWidgets.QVBoxLayout(self);layout.setContentsMargins(0,0,0,0)
        from film_foundry.ui.material_edit_tools import MaterialEditTools
        edits=QtWidgets.QHBoxLayout();layout.addLayout(edits)
        owner.edit_tools=MaterialEditTools(owner,edits)
        bar=QtWidgets.QHBoxLayout();layout.addLayout(bar)
        for label,callback in ((text('适合窗口','Fit'),owner.viewer.fit),('100%',owner.viewer.actual_size)):
            button=QtWidgets.QToolButton();button.setText(label);button.clicked.connect(callback);bar.addWidget(button)
        bar.addStretch()
        self.histogram_button=QtWidgets.QToolButton();self.histogram_button.setText(text('直方图','Histogram'));self.histogram_button.setCheckable(True);bar.addWidget(self.histogram_button)
        self.history_button=QtWidgets.QToolButton();self.history_button.setText(text('工艺记录','Process history'));self.history_button.setCheckable(True);bar.addWidget(self.history_button)
        self.panels=QtWidgets.QStackedWidget();layout.addWidget(self.panels);self.panels.hide()
        self.histogram=Histogram();self.histogram.setFixedHeight(150);self.panels.addWidget(self.histogram)
        self.histogram.setToolTip(text('当前屏幕预览的RGB/亮度分布，包含当前白边或诊断视图；大图均匀抽样至约100万像素。左键切换RGB/亮度，右键切换线性/对数。不表示RAW曝光余量。',
            'Distribution of the displayed preview, including borders or diagnostic maps. Large previews are uniformly sampled to about 1 MP. Click: RGB/brightness; right-click: linear/log. Not RAW headroom.'))
        self.history=QtWidgets.QTreeWidget();self.history.setHeaderLabels([text('工序 / 参数','Operation / parameter'),text('已执行值','Executed value')]);self.history.setFixedHeight(170)
        self.history.setColumnWidth(0,200);self.panels.addWidget(self.history)
        self.history.setToolTip(text('只读的已执行记录，优先使用完整配方；不会重算、撤销或推进工艺。旧材料仅显示实际保存的历史。',
            'Read-only executed history, preferring the complete recipe. Does not recalculate, undo or advance processing. Older materials show only recorded events.'))
        self.histogram_button.toggled.connect(lambda checked:self.toggle(0,checked))
        self.history_button.toggled.connect(lambda checked:self.toggle(1,checked))
        owner.viewer.pixels_changed.connect(self.set_pixels)

    def toggle(self,index,checked):
        if checked:
            other=self.history_button if index==0 else self.histogram_button
            other.blockSignals(True);other.setChecked(False);other.blockSignals(False)
            self.panels.setCurrentIndex(index)
        self.panels.setVisible(self.histogram_button.isChecked() or self.history_button.isChecked())

    def set_pixels(self,pixels):
        # Statistics only: never feed this sampling into formation or export.
        stride=max(1,math.ceil(math.sqrt(pixels.shape[0]*pixels.shape[1]/1_000_000)))
        self.histogram.set_pixels(pixels[::stride,::stride])

    def sync(self):
        session=self.owner.session
        identity=session.state if session.state is not None else session.group if session.group is not None else session.image
        if identity is self._identity:return
        self._identity=identity;self.history.clear()
        if identity is None:
            self.histogram.bins=None;self.histogram.update();return
        if session.state is not None:
            process=json.loads(session.state.process_json)
            material=asdict(session.state.material)
        elif session.group is not None:
            process=json.loads(session.group.provenance_json).get('process',{})
            material=process.get(RECIPE_KEY,{}).get('material',{})
        else:
            return
        if material:
            parent=QtWidgets.QTreeWidgetItem(self.history,[text('材料定义','Material definition'),''])
            for name,value in material.items():QtWidgets.QTreeWidgetItem(parent,[name,self.value(value)])
        recipe=process.get(RECIPE_KEY)
        if not recipe and STATUS_KEY in process:
            status=process[STATUS_KEY]
            reasons={
                'missing_history':('起点缺少完整历史','The starting state has no complete history'),
                'nonportable_parameters':('操作包含配方不能保存的参数','An operation uses parameters not supported by portable recipes'),
                'recipe_limit':('完整记录超过配方容量','The complete record exceeds recipe limits'),
                'inconsistent_history':('历史与当前材料不一致','History does not match the current material')}
            reason=text(*reasons.get(status['reason'],('记录不完整','Incomplete history')))
            row=QtWidgets.QTreeWidgetItem(self.history,[text('配方不完整','Incomplete recipe'),status['operation']])
            row.setToolTip(0,reason);row.setToolTip(1,reason)
        events=recipe['steps'] if recipe else process.get('events',[])
        for index,event in enumerate(events,1):
            operation=event.get('operation','')
            label=text(*self.labels.get(operation,(operation,operation)))
            parent=QtWidgets.QTreeWidgetItem(self.history,[f'{index} · {label}',''])
            parameters=event.get('parameters',{}) if recipe else {k:v for k,v in event.items() if k!='operation'}
            for name,value in parameters.items():QtWidgets.QTreeWidgetItem(parent,[name,self.value(value)])
            parent.setToolTip(0,text('操作配方中的实际参数','Actual parameters from the operation recipe') if recipe else text('已有事件；不推测缺失步骤或参数','Recorded event; missing steps or parameters are not inferred'))

    @staticmethod
    def value(value):
        return json.dumps(value,ensure_ascii=False) if isinstance(value,(dict,list,tuple,bool)) or value is None else str(value)
