"""Shared operations for all seven experimental material pages."""
from film_foundry.app.edit_actions import ActionRegistry
from film_foundry.app.material_history import capture,changed,restore,new_history
from film_foundry.app.material_scan import render_material_scan
from film_foundry.ui.qt import QtWidgets
from film_foundry.ui.curve_editor import text
from film_foundry.ui.comparison_view import comparison_choice


class MaterialEditTools:
    def __init__(self,owner,layout):
        self.owner=owner;self.history=None;self.restoring=False;self.actions=ActionRegistry();self.buttons={};self._source_key=None
        for key,zh,en,callback in (
            ('edit.undo','撤回','Undo',lambda:self.travel(False)),('edit.redo','重做','Redo',lambda:self.travel(True)),
            ('image.rotate_left','左转','Rotate left',lambda:self.rotate(-1)),('image.rotate_right','右转','Rotate right',lambda:self.rotate(1))):
            self.actions.register(key,callback,lambda k=key:self.available(k))
            button=QtWidgets.QToolButton();button.setText(text(zh,en));button.clicked.connect(lambda _=False,k=key:self.actions.invoke(k))
            button.setToolTip(text('本页最多撤回3次材料操作，保留当前进程内历史；不删除已保存文件。旋转只作用于观看和图像导出。','Undo up to 3 material actions in this session; saved files remain. Rotation affects observation and image export only.'))
            layout.addWidget(button);self.buttons[key]=button
        self.comparison=comparison_choice(owner.viewer);layout.addWidget(self.comparison)
        self.quick=QtWidgets.QToolButton();self.quick.setText(text('快速配置','Quick settings'));self.quick.setPopupMode(QtWidgets.QToolButton.ToolButtonPopupMode.InstantPopup)
        menu=QtWidgets.QMenu(self.quick)
        menu.addAction(text('扫描与成片','Scan & output'),owner.open_scan_controls)
        menu.addAction(text('材料标靶预览','Material target preview'),owner.open_target_preview)
        menu.addAction(text('载入材料定义','Load material definition'),owner.load_definition)
        self.quick.setMenu(menu);layout.addWidget(self.quick);layout.addStretch();self.sync()

    def available(self,key):
        if self.owner.busy or self.history is None:return False
        if key=='edit.undo':return self.history.can_undo
        if key=='edit.redo':return self.history.can_redo
        return self.owner.session.image is not None or self.owner.session.state is not None or self.owner.session.group is not None

    def sync(self):
        for key,button in self.buttons.items():button.setEnabled(self.available(key))
        self.quick.setEnabled(not self.owner.busy)
        self.comparison.setEnabled(self.owner.session.image is not None)

    def before_submit(self):
        if self.history is None:self.history=new_history(self.owner.session)
        self.sync()

    def completed(self):
        current=capture(self.owner.session)
        if not self.restoring and self.history is not None and changed(self.history.items[self.history.position],current):self.history.push(current)
        self.restoring=False;self.sync_source();self.sync()

    def sync_source(self):
        session=self.owner.session;turns=session.scan_adjustments.get('rotation_quarters',0)
        key=(id(session.image),turns)
        if key==self._source_key:return
        self._source_key=key
        self.owner.viewer.set_original(None if session.image is None else render_material_scan(session.image.copy(),{'rotation_quarters':turns}))

    def travel(self,redo):
        snapshot=self.history.redo() if redo else self.history.undo();self.restoring=True
        self.owner._submit(lambda:restore(self.owner.session,snapshot),text('恢复材料状态…','Restoring material…'),restore_controls=True,body_index=0)

    def rotate(self,direction):
        session=self.owner.session
        turns=(session.scan_adjustments.get('rotation_quarters',0)+direction)%4
        def apply():session.scan_adjustments={**session.scan_adjustments,'rotation_quarters':turns}
        self.owner._submit(apply,text('旋转观察图像…','Rotating observation…'))
