"""One embedded desktop page over the frozen film continuation application API."""
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from pathlib import Path
from film_foundry.ui.qt import QtCore, QtWidgets
from film_foundry.ui.curve_editor import text
from film_foundry.app.control import CancelFlag, WorkflowControl
from film_foundry.app.film_process_work import start_work, continue_work, describe_work
from film_foundry.ui.roll_process_dialog import ACTIONS as BATH_ACTIONS

REACTIONS={**BATH_ACTIONS,'destroy_dye':('染料侵蚀','Dye destruction'),
    'remove_emulsion':('乳剂脱落','Emulsion removal'),'shear_emulsion':('乳剂剪切','Emulsion shear'),
    'age_latent':('潜影等待','Latent waiting'),'light_residual_halide':('残盐受光','Residual salt light exposure')}


class FilmProcessWorkspace(QtWidgets.QDialog):
    workspace_closed = QtCore.Signal()
    completed = QtCore.Signal(object, object, object)
    result_ready = QtCore.Signal(object)
    view_result = QtCore.Signal(str)

    def __init__(self, config_provider):
        super().__init__()
        self.config_provider=config_provider;self.path=None;self.info=None;self.final_path=None
        self.busy=False;self._closing=False;self.last_error=None;self._batch_cancel=None
        self.pool=ThreadPoolExecutor(max_workers=1,thread_name_prefix='film-reactions')
        self.completed.connect(self._completed)
        self.setWindowTitle(text('分段冲洗','Film reaction stages'))
        root=QtWidgets.QVBoxLayout(self);bar=QtWidgets.QHBoxLayout();root.addLayout(bar)
        self.prepare_button=QtWidgets.QPushButton(text('从潜影开始…','Start from latent…'))
        self.open_button=QtWidgets.QPushButton(text('打开冲洗存档…','Open process checkpoint…'))
        self.prepare_button.setToolTip(text('保存主反应池、表面与抑制剂余量，并在没有可见表面效果时保留热湿历史。尚未开始的药浴可以修改条件并另存分支，相关邻接和 DIR 准备按需更新。',
            'Saves reaction pools, surface and inhibitor inventories, including wet history when no visible surface effect is present. Eligible unentered baths can be revised into a separate checkpoint.'))
        bar.addWidget(self.prepare_button);bar.addWidget(self.open_button);bar.addStretch()
        self.prepare_button.clicked.connect(self.start_dialog);self.open_button.clicked.connect(self.open_dialog)
        self.steps=QtWidgets.QTreeWidget();self.steps.setColumnCount(4)
        self.steps.setHeaderLabels([text('步骤','Step'),text('关联药浴','Related bath'),text('反应','Reaction'),text('状态','State')]);root.addWidget(self.steps)
        self.steps.setToolTip(text('一浴可能包含多个耦合反应；这里的步骤不是额外的药浴，也不会在等待时自行推进。',
            'One bath may contain several coupled reactions. Steps are not extra baths, and waiting does not advance chemistry.'))
        baths=QtWidgets.QHBoxLayout();root.addLayout(baths)
        self.bath_target=QtWidgets.QComboBox();baths.addWidget(self.bath_target,1)
        self.edit_bath_button=QtWidgets.QPushButton(text('修改后续药浴','Edit future bath'));baths.addWidget(self.edit_bath_button)
        self.edit_bath_button.setToolTip(text('只编辑尚未开始的药浴，必要时更新未执行显影的邻接或 DIR 准备。已使用的场和已形成材料保持；另存分支，保留原存档。',
            'Edit an unentered bath, refreshing adjacency or DIR preparation only for future development when needed. Used fields and formed material stay unchanged; save a separate branch.'))
        self.bath_editor_host=QtWidgets.QVBoxLayout();root.addLayout(self.bath_editor_host);self.bath_editor=None
        self.edit_bath_button.clicked.connect(self.edit_bath)
        row=QtWidgets.QHBoxLayout();root.addLayout(row)
        self.target=QtWidgets.QComboBox();row.addWidget(self.target,1)
        self.advance_button=QtWidgets.QPushButton(text('执行并保存…','Run and save…'))
        self.finish_button=QtWidgets.QPushButton(text('完成冲洗…','Finish processing…'))
        self.cancel_button=QtWidgets.QPushButton(text('停止并保存','Stop and save'))
        self.view_button=QtWidgets.QPushButton(text('查看成片','View result'))
        for b in (self.advance_button,self.finish_button,self.cancel_button,self.view_button):row.addWidget(b)
        self.advance_button.clicked.connect(lambda:self.advance_dialog(False))
        self.finish_button.clicked.connect(lambda:self.advance_dialog(True))
        self.cancel_button.clicked.connect(lambda:self._batch_cancel.cancel() if self._batch_cancel else None)
        self.view_button.clicked.connect(lambda:self.view_result.emit(str(self.final_path)))
        self.status=QtWidgets.QLabel();self.status.setWordWrap(True);root.addWidget(self.status)
        self._sync()

    def _sync(self):
        self.prepare_button.setEnabled(not self.busy);self.open_button.setEnabled(not self.busy)
        self.target.setEnabled(not self.busy and self.info is not None)
        self.advance_button.setEnabled(not self.busy and self.target.count()>0)
        self.finish_button.setEnabled(not self.busy and self.info is not None)
        self.cancel_button.setEnabled(self.busy and self._batch_cancel is not None)
        self.view_button.setEnabled(not self.busy and self.final_path is not None)
        self.bath_target.setEnabled(not self.busy)
        self.edit_bath_button.setEnabled(not self.busy and self.bath_target.currentData() is not None)
        if self.bath_editor is not None:self.bath_editor.setEnabled(not self.busy)

    def edit_bath(self):
        option=self.bath_target.currentData()
        if option is None or self.busy:return
        from film_foundry.ui.bath_override_dialog import BathOverrideDialog
        from types import SimpleNamespace
        if self.bath_editor is not None:self.bath_editor.deleteLater()
        editor=BathOverrideDialog(option['bath'],option['row'],SimpleNamespace(**option['defaults']),self)
        editor.setWindowFlags(QtCore.Qt.WindowType.Widget);editor.enabled.hide()
        self.bath_editor=editor;self.bath_editor_host.addWidget(editor);editor.show()
        def accepted():
            if editor.value is None:return
            output,_=QtWidgets.QFileDialog.getSaveFileName(self,text('另存药浴分支','Save bath branch'),
                str(self.path.with_name(self.path.name.split('.')[0]+'-revised.ffprocess.npz')),'Process (*.ffprocess.npz)')
            if output:self.revise_to(output,[editor.value])
        editor.accepted.connect(accepted)

    def revise_to(self,output,updates):
        if self.path is None:return False
        from film_foundry.app.film_process_revision import revise_work
        source=self.path;updates=deepcopy(updates)
        return self._submit(lambda control:revise_work(source,output,updates,control=control))

    def start_dialog(self):
        source,_=QtWidgets.QFileDialog.getOpenFileName(self,text('选择未冲洗潜影','Choose undeveloped latent'),'','Latent (*.darkroom_latent.npz)')
        if not source:return
        output,_=QtWidgets.QFileDialog.getSaveFileName(self,text('保存冲洗存档','Save process checkpoint'),str(Path(source).with_suffix('.ffprocess.npz')),'Process (*.ffprocess.npz)')
        if output:self.start_from(source,output)

    def open_dialog(self):
        path,_=QtWidgets.QFileDialog.getOpenFileName(self,text('打开冲洗存档','Open process checkpoint'),'','Process (*.ffprocess.npz)')
        if path:self.open_from(path)

    def advance_dialog(self,finish):
        if self.path is None:return
        suffix='.darkroom_negative.npz' if finish else '.ffprocess.npz'
        output,_=QtWidgets.QFileDialog.getSaveFileName(self,text('保存新结果','Save new result'),str(self.path.with_name(self.path.name.split('.')[0]+'-next'+suffix)),
            'Medium (*.npz)' if finish else 'Process (*.ffprocess.npz)')
        if output:self.advance_to(output,None if finish else self.target.currentData())

    def start_from(self,source,output):
        try:config=deepcopy(self.config_provider())
        except Exception as exc:self.last_error=str(exc);self.status.setText(str(exc));return False
        return self._submit(lambda control:start_work(source,output,config,stop_after=0,control=control))

    def open_from(self,path):
        return self._submit(lambda _: (Path(path),describe_work(path)))

    def advance_to(self,output,stop_after=None):
        if self.path is None:return False
        source=self.path
        return self._submit(lambda control:continue_work(source,output,stop_after=stop_after,control=control))

    def _submit(self,action):
        if self.busy:return False
        self.busy=True;self.last_error=None;self._batch_cancel=CancelFlag();self._sync()
        self.status.setText(text('处理中…','Processing…'))
        control=WorkflowControl(cancel_requested=self._batch_cancel.requested)
        def work():
            result=action(control)
            info=None if isinstance(result,tuple) or result.status=='completed' else describe_work(result.output_path)
            return result,info
        def done(task):
            try:result,info=task.result();error=None
            except Exception as exc:result=info=None;error=str(exc)
            self.completed.emit(result,info,error)
        self.pool.submit(work).add_done_callback(done);return True

    def _completed(self,result,info,error):
        self.busy=False;self._batch_cancel=None;self.last_error=error
        if error:self.status.setText(error)
        elif isinstance(result,tuple):
            self.path,self.info=result;self.final_path=None;self._show_steps()
        elif result.status=='completed':
            self.final_path=result.output_path;self.status.setText(text('冲洗完成：','Processing complete: ')+str(self.final_path))
            self.result_ready.emit({'key':'film-process:'+self.info['run_id'],'source':str(self.path),
                'label':self.info['source_name'],'family':'film','medium':str(self.final_path),
                'artifacts':result.artifacts,'cover':result.artifacts.get('positive_visual_preview',result.artifacts.get('negative_visual_preview',''))})
        else:
            self.path=result.output_path;self.info=info;self.final_path=None;self._show_steps()
            if result.status=='cancelled':self.status.setText(text('已停止并保存：','Stopped and saved: ')+str(self.path))
        self._sync()
        if self._closing:self.close()

    def _show_steps(self):
        self.steps.clear();self.target.clear()
        self.bath_target.clear()
        if self.bath_editor is not None:self.bath_editor.hide()
        reasons={'started':text('已开始','Started'),'finished':text('已完成','Finished'),
            'adjacency':text('邻接准备已使用','Used by adjacency preparation'),'dir':text('DIR 准备已使用','Used by DIR preparation'),
            'source_mapping':text('来源程序与存档不匹配','Source program differs from checkpoint')}
        first_editable=-1
        for option in self.info.get('baths',[]):
            action=text(*REACTIONS.get(option['row']['action'],(option['row']['action'],option['row']['action'])))
            reason=reasons.get(option['reason'],text('固定阶段','Fixed stage'))
            label=f'{option["bath"]} · {action}'
            self.bath_target.addItem(label if option['editable'] else f'{label} · {reason}',option if option['editable'] else None)
            if option['editable'] and first_editable<0:first_editable=self.bath_target.count()-1
            if not option['editable']:self.bath_target.model().item(self.bath_target.count()-1).setEnabled(False)
        self.bath_target.setCurrentIndex(first_editable)
        index=self.info['next_step']
        for i,step in enumerate(self.info['steps']):
            bath=step.get('bath_index');label=text(*REACTIONS.get(step['action'],(step['action'],step['action'])))
            bath_label='—' if step['action'] in ('age_latent','light_residual_halide') else str((i if bath is None else bath)+1)
            item=QtWidgets.QTreeWidgetItem([str(i+1),bath_label,label,
                text('已执行','Done') if i<index else text('待执行','Pending')])
            item.setToolTip(2,step['label'] or step['action']);self.steps.addTopLevelItem(item)
            if i>=index:self.target.addItem(f'{i+1} · {label}',i+1)
        for column in range(3):self.steps.resizeColumnToContents(column)
        self.status.setText(text(f'已保存 {index}/{len(self.info["steps"])} 步：',f'Saved {index}/{len(self.info["steps"])} steps: ')+str(self.path))

    def closeEvent(self,event):
        if self.busy:
            self._closing=True
            if self._batch_cancel:self._batch_cancel.cancel()
            event.ignore();return
        self.pool.shutdown(wait=False,cancel_futures=True);self.workspace_closed.emit();event.accept()
