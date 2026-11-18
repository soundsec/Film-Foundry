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
        self.setSizePolicy(QtWidgets.QSizePolicy.Policy.Ignored,QtWidgets.QSizePolicy.Policy.Preferred)
        layout=QtWidgets.QVBoxLayout(self);layout.setContentsMargins(0,0,0,0)
        from film_foundry.ui.material_edit_tools import MaterialEditTools
        from film_foundry.ui.flow_toolbar import FlowToolbar
        self.toolbar=bar=FlowToolbar();layout.addLayout(bar)
        owner.edit_tools=MaterialEditTools(owner,bar,bar)
        self.view_options=QtWidgets.QToolButton();self.view_options.setText(text('观看工具','View tools'))
        self.view_options.setPopupMode(QtWidgets.QToolButton.ToolButtonPopupMode.InstantPopup)
        menu=QtWidgets.QMenu(self.view_options);self.view_options.setMenu(menu);bar.addWidget(self.view_options)
        for label,callback in ((text('适合窗口','Fit'),owner.viewer.fit),('100%',owner.viewer.actual_size)):
            menu.addAction(label,callback)
        bar.addStretch()
        self.histogram_button=QtWidgets.QToolButton();self.histogram_button.setText(text('直方图','Histogram'));self.histogram_button.setCheckable(True);self.histogram_button.setParent(self);self.histogram_button.hide()
        self.history_button=QtWidgets.QToolButton();self.history_button.setText(text('工艺记录','Process history'));self.history_button.setCheckable(True);self.history_button.setParent(self);self.history_button.hide()
        for button in (self.histogram_button,self.history_button):
            action=menu.addAction(button.text(),button.click);action.setCheckable(True)
            button.toggled.connect(action.setChecked)
        from film_foundry.ui.material_probe import MaterialProbe
        self.probe=MaterialProbe(owner,menu,layout)
        self.panels=QtWidgets.QStackedWidget();self.panels.setMinimumHeight(112);self.panels.hide()
        self.panels.setSizePolicy(QtWidgets.QSizePolicy.Policy.Ignored,QtWidgets.QSizePolicy.Policy.Expanding)
        self.histogram_slot=QtWidgets.QWidget();slot=QtWidgets.QVBoxLayout(self.histogram_slot);slot.setContentsMargins(0,0,0,0)
        self.histogram=Histogram();slot.addWidget(self.histogram);self.panels.addWidget(self.histogram_slot)
        self.histogram.setToolTip(self.histogram.toolTip()+text(' 分布含白边/诊断视图，大图均匀抽样至约100万像素；裁切指示使用全部预览像素。',
            ' Distribution includes borders/diagnostic views, sampled uniformly to about 1 MP. Clipping indicators use all preview pixels.'))
        self.histogram.settings_changed.connect(self.sync_placement)
        owner.viewer.enable_clipping(self.histogram.settings)
        self.history=QtWidgets.QTreeWidget();self.history.setHeaderLabels([text('工序 / 参数','Operation / parameter'),text('已执行值','Executed value')]);self.history.setMinimumHeight(112)
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
        self.sync_placement()

    def sync_placement(self,*_):
        if not hasattr(self.owner,'histogram_sidebar_host'):return
        side=self.histogram.settings.values['layout']=='sidebar'
        shown=self.histogram_button.isChecked();history=self.history_button.isChecked()
        target=self.owner.histogram_sidebar_host if side else self.histogram_slot
        if self.histogram.parentWidget() is not target:target.layout().addWidget(self.histogram)
        self.owner.histogram_sidebar_host.setVisible(side and shown)
        self.histogram.setVisible(shown)
        self.panels.setCurrentIndex(1 if history else 0)
        self.panels.setVisible(history or shown and not side)
        placement=(side,shown,history)
        if getattr(self,'_placement',None)!=placement:
            self._placement=placement
            if hasattr(self.owner,'layout_memory'):
                self.owner.layout_memory.apply('histogram_sidebar',force=True)
                self.owner.layout_memory.apply('inspection')

    def set_pixels(self,pixels):
        # Statistics only: never feed this sampling into formation or export.
        stride=max(1,math.ceil(math.sqrt(pixels.shape[0]*pixels.shape[1]/1_000_000)))
        rgb=pixels.reshape(-1,3)
        endpoints=(rgb.min(axis=0)==0,rgb.max(axis=0)==255) if rgb.size else ([False]*3,[False]*3)
        self.histogram.set_pixels(pixels[::stride,::stride],endpoints=endpoints)

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
