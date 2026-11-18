"""Shared operations for all seven experimental material pages."""
from film_foundry.app.edit_actions import ActionRegistry
from film_foundry.app.material_history import capture,changed,restore
from film_foundry.app.material_scan import render_material_scan
from film_foundry.ui.qt import QtWidgets
from film_foundry.ui.curve_editor import text
from film_foundry.ui.comparison_view import comparison_choice


class MaterialEditTools:
    def __init__(self,owner,layout,options):
        self.owner=owner;self.history=None;self.restoring=False;self.actions=ActionRegistry();self.buttons={};self._source_key=None
        for key,zh,en,callback in (
            ('edit.undo','撤回','Undo',lambda:self.travel(False)),('edit.redo','重做','Redo',lambda:self.travel(True)),
            ('image.rotate_left','左转','Rotate left',lambda:self.rotate(-1)),('image.rotate_right','右转','Rotate right',lambda:self.rotate(1))):
            self.actions.register(key,callback,lambda k=key:self.available(k))
            button=QtWidgets.QToolButton();button.setText(text(zh,en));button.clicked.connect(lambda _=False,k=key:self.actions.invoke(k))
            button.setToolTip(text('本页最多撤回3次材料操作，保留当前进程内历史；不删除已保存文件。旋转只作用于观看和图像导出。','Undo up to 3 material actions in this session; saved files remain. Rotation affects observation and image export only.'))
            if key.startswith('image.'):
                button.setParent(owner);button.hide()
            else:layout.addWidget(button)
            self.buttons[key]=button
        self.comparison=comparison_choice(owner.viewer);options.addWidget(self.comparison)
        self.trials=QtWidgets.QToolButton();self.trials.setText(text('试条…','Test strip…'))
        from film_foundry.ui.material_test_strip import open_material_strip
        self.trials.clicked.connect(lambda:open_material_strip(owner));options.addWidget(self.trials)
        from film_foundry.ui.work_preview_history import install_material
        from film_foundry.ui.work_shortcuts import WorkShortcuts,annotate
        install_material(owner,options)
        owner.closed.connect(owner.preview_history.dispose)
        owner.work_shortcuts=WorkShortcuts(owner)
        annotate(self)
        self.quick=QtWidgets.QToolButton();self.quick.setText(text('快速配置','Quick settings'));self.quick.setPopupMode(QtWidgets.QToolButton.ToolButtonPopupMode.InstantPopup)
        menu=QtWidgets.QMenu(self.quick)
        for key in ('image.rotate_left','image.rotate_right'):
            button=self.buttons[key]
            menu.addAction(button.text(),button.click)
        from film_foundry.ui.formal_region import open_material_region
        menu.addAction(text('局部正式尺度预览…','Formal-scale region preview…'),lambda:open_material_region(owner))
        from film_foundry.ui.material_checkpoints import open_checkpoints
        menu.addAction(text('工序检查点…','Process checkpoints…'),lambda:open_checkpoints(owner))
        from film_foundry.ui.parameter_pins import open_pins
        menu.addAction(text("钉选参数…","Pinned parameters…"),lambda:open_pins(owner,film=False))
        menu.addAction(text('扫描与成片','Scan & output'),owner.open_scan_controls)
        menu.addAction(text('材料标靶预览','Material target preview'),owner.open_target_preview)
        menu.addAction(text('载入材料定义','Load material definition'),owner.load_definition)
        self.quick.setMenu(menu);options.addWidget(self.quick);options.addStretch();self.sync()

    def available(self,key):
        if self.owner.busy or self.history is None:return False
        if key=='edit.undo':return self.history.can_undo
        if key=='edit.redo':return self.history.can_redo
        return self.owner.session.image is not None or self.owner.session.state is not None or self.owner.session.group is not None

    def sync(self):
        for key,button in self.buttons.items():button.setEnabled(self.available(key))
        self.quick.setEnabled(not self.owner.busy)
        self.trials.setEnabled(not self.owner.busy and (self.owner.session.image is not None or self.owner.session.state is not None or self.owner.session.group is not None))
        self.comparison.setEnabled(self.owner.session.image is not None)

    def before_submit(self):
        if not self.restoring:self.record_draft()
        self.sync()

    def snapshot(self):
        from film_foundry.ui.material_workspace_state import capture as draft
        return {**capture(self.owner.session),'draft':draft(self.owner)}

    def record_draft(self):
        from film_foundry.app.edit_actions import SnapshotHistory
        current=self.snapshot()
        if self.history is None:self.history=SnapshotHistory(current,limit=4)
        else:
            prior=self.history.items[self.history.position]
            if changed(prior,current) or any(prior['draft'][key]!=current['draft'][key] for key in ('values','view')):self.history.push(current)
        self.sync()

    def completed(self):
        if self.restoring:
            from film_foundry.ui.material_workspace_state import restore as restore_draft
            restore_draft(self.owner,self.history.items[self.history.position]['draft'])
            self.owner.request_preview()
        else:self.record_draft()
        self.restoring=False;self.sync_source();self.sync()

    def sync_source(self,*,as_result=False):
        session=self.owner.session
        turns=session.scan_adjustments.get('rotation_quarters',0)
        key=(id(session.image),turns)
        viewer=self.owner.viewer
        if key==self._source_key:
            if as_result:viewer.set_original(viewer.original,as_result=True)
            return
        source=None if session.image is None else render_material_scan(session.image.copy(),{'rotation_quarters':turns})
        viewer.set_original(source,as_result=as_result)
        self._source_key=key

    def travel(self,redo):
        self.record_draft()
        snapshot=self.history.redo() if redo else self.history.undo();self.restoring=True
        self.owner._submit(lambda:restore(self.owner.session,snapshot),text('恢复材料状态…','Restoring material…'),body_index=snapshot['draft']['body_index'])

    def rotate(self,direction):
        session=self.owner.session
        turns=(session.scan_adjustments.get('rotation_quarters',0)+direction)%4
        def apply():session.scan_adjustments={**session.scan_adjustments,'rotation_quarters':turns}
        self.owner._submit(apply,text('旋转观察图像…','Rotating observation…'))
