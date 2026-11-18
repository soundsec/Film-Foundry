"""Desktop buttons call data actions; configuration history contains no pixels."""
from dataclasses import asdict
from film_foundry.app.edit_actions import ActionRegistry,SnapshotHistory
from film_foundry.model.config import DarkroomConfig
from film_foundry.ui.qt import QtCore,QtWidgets
from film_foundry.ui.curve_editor import text
from film_foundry.ui.comparison_view import comparison_choice


class FilmEditTools:
    def __init__(self,owner,layout):
        self.owner=owner;self.restoring=False;self.history=None;self.actions=ActionRegistry()
        self.timer=QtCore.QTimer(owner.root.qt);self.timer.setSingleShot(True);self.timer.setInterval(250);self.timer.timeout.connect(self.record)
        self.buttons={}
        for key,zh,en,callback in (
            ('edit.undo','撤回','Undo',lambda:self.travel(False)),('edit.redo','重做','Redo',lambda:self.travel(True)),
            ('image.rotate_left','左转','Rotate left',lambda:self.rotate(-1)),('image.rotate_right','右转','Rotate right',lambda:self.rotate(1))):
            self.actions.register(key,callback,lambda k=key:self.available(k))
            button=QtWidgets.QToolButton();button.setText(text(zh,en));button.clicked.connect(lambda _=False,k=key:self.actions.invoke(k))
            button.setToolTip(text('撤回/重做本页参数；旋转作用于成片导出，不改底片或原文件。','Undo/redo page settings; rotation affects exported images, never the material or source file.'))
            self.buttons[key]=button;layout.addWidget(button)
        self.comparison=comparison_choice(owner.result_view);layout.addWidget(self.comparison)
        self.comparison.currentIndexChanged.connect(lambda:owner.preview_tabs.setCurrentWidget(owner.result_view))
        from film_foundry.ui.work_preview_history import install_film
        install_film(owner)
        from film_foundry.ui.work_shortcuts import annotate
        annotate(self)
        self.quick=QtWidgets.QToolButton();self.quick.setText(text('正式质量','Output quality'));self.quick.setPopupMode(QtWidgets.QToolButton.ToolButtonPopupMode.InstantPopup)
        menu=QtWidgets.QMenu(self.quick)
        self.quality_actions=[]
        for quality,zh,en in (('draft','低质量','Low quality'),('standard','中质量','Medium quality'),('high','高质量','High quality')):
            self.quality_actions.append(menu.addAction(text(zh,en),lambda q=quality:self.set_quality(q)))
        self.quick.setToolTip(text('正式任务的质量等级。交互预览使用屏幕草稿。','Quality level for formal jobs. Interactive previews use the display draft.'))
        self.quick.setMenu(menu);layout.addStretch()

    def sync_task(self):
        mode=self.owner.pipeline_mode.get()
        for action in self.quality_actions:action.setVisible(mode!='scan')
        self.quick.setVisible(mode!='scan')

    def initialize(self):
        self.owner.preview_history.clear()
        self.timer.stop();self.history=SnapshotHistory(self.capture());self.sync();self.sync_task()

    def capture(self):
        return {'config':asdict(self.owner._config()),'frame_exposure_offsets':dict(self.owner._frame_exposure_offsets),'scan_interpretation':self.owner.scan_interpretation.get(),
                'link_scanner':self.owner.link_scanner.get(),'auto_develop':self.owner.auto_develop.get(),
                'presets':{bank:getattr(self.owner,bank+'_preset').get() for bank in ('film','develop','scanner')}}

    def apply(self,snapshot):
        from copy import deepcopy
        before=asdict(self.owner._config());after=deepcopy(snapshot['config'])
        before['output']['rotation_quarters']=after['output']['rotation_quarters']
        preview_needed=before!=after or self.owner.scan_interpretation.get()!=snapshot['scan_interpretation']
        offsets = dict(snapshot.get('frame_exposure_offsets', {}))
        preview_needed = preview_needed or offsets != self.owner._frame_exposure_offsets
        self.owner._frame_exposure_offsets = offsets
        ready=self.owner._auto_preview_ready;self.owner._auto_preview_ready=False
        baselines={key:deepcopy(value) for key,value in self.owner.control_resets.baselines.items()}
        try:
            for bank,value in snapshot['presets'].items():getattr(self.owner,bank+'_preset').set(value)
            self.owner._apply_loaded_config(DarkroomConfig.from_dict(snapshot['config']))
            self.owner.scan_interpretation.set(snapshot['scan_interpretation'])
            self.owner.link_scanner.set(snapshot.get('link_scanner',False));self.owner.auto_develop.set(snapshot.get('auto_develop',False))
        finally:
            self.owner._auto_preview_ready=ready
            self.owner.control_resets.baselines=baselines
        if preview_needed:self.owner._preview_edited()

    def available(self,key):
        if self.history is None:return False
        if self.owner._active_roll is not None:return False
        if key=='edit.undo':return self.history.can_undo or self.timer.isActive()
        if key=='edit.redo':return self.history.can_redo and not self.timer.isActive()
        return True

    def sync(self):
        for key,button in self.buttons.items():button.setEnabled(self.available(key))

    def schedule(self):
        if self.history is not None and not self.restoring:self.timer.start();self.sync()

    def record(self):
        self.timer.stop()
        if self.history is None or self.restoring:return
        try:snapshot=self.capture()
        except (ValueError,TypeError):return
        if snapshot!=self.history.items[self.history.position]:self.history.push(snapshot)
        self.sync()

    def travel(self,redo):
        self.record();self.restoring=True
        try:
            snapshot=self.history.redo() if redo else self.history.undo()
            self.apply(snapshot)
        finally:self.restoring=False;self.sync()

    def rotate(self,direction):
        self.record();snapshot=self.capture();output=snapshot['config']['output']
        output['rotation_quarters']=(output['rotation_quarters']+direction)%4
        self.apply(snapshot);self.record()

    def set_quality(self,quality):
        self.record();self.owner.quality_mode.set(quality);self.schedule()
