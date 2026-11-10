"""Batch planning using the same material-window controls and headless sessions."""
from pathlib import Path
from film_foundry.ui.material_lab_window import MaterialLabWindow
from film_foundry.ui.qt import QtCore,QtWidgets
from film_foundry.ui.curve_editor import text


class MaterialBatchDialog(MaterialLabWindow):
    def __init__(self,kind,paths,parent=None,*,load_source=True):
        self._batch_cancel=None;self._planning_batch=True
        super().__init__(kind);self.paths=list(paths)
        self.setWindowTitle(text('批量材料处理 · 实验中','Batch material processing · Experimental'))
        self.batch_button=QtWidgets.QPushButton(text(f'处理选中的 {len(paths)} 张…',f'Process {len(paths)} selected images…'))
        self.centralWidget().layout().addWidget(self.batch_button);self.batch_button.clicked.connect(self.start_batch)
        from film_foundry.ui.material_batch_io import install_resume
        install_resume(self)
        if load_source:self._submit(lambda:self.session.open_image(paths[0],1600),text('读取首张图像','Loading first image'))

    def _sync(self):
        super()._sync()
        # Batch controls describe a future fresh sheet, independent of preview stage.
        for group in (self.material_group,self.process_group,self.defect_group,self.finish_group):group.setEnabled(not self.busy)
        for c in list(self.process_controls.values())+list(self.defect_controls.values())+list(self.finish_controls.values())+list(self.clearing_controls.values()):c.setEnabled(not self.busy)
        if self.kind!='plate':self.direction.setEnabled(not self.busy)
        self.seconds.setEnabled(not self.busy)
        if self.kind=='plate':
            for op,controls in self.operation_controls.items():
                for key,control in controls.items():
                    control.setEnabled(not self.busy and not (op=='load_gilding' and key=='gold_load' and self.gold_intent['enabled'].isChecked()))
        if not self.busy:self._batch_cancel=None
        if hasattr(self,'batch_button'):
            self.resume_batch_button.setEnabled(not self.busy)
            self.batch_button.setEnabled(not self.busy or self._batch_cancel is not None)
            self.batch_button.setText(text('停止批量处理','Stop batch') if self._batch_cancel else text(f'处理选中的 {len(self.paths)} 张…',f'Process {len(self.paths)} selected images…'))

    def start_batch(self,output=None):
        if isinstance(output,bool):output=None
        if self._batch_cancel:self._batch_cancel.cancel();return
        if self.busy:return
        if output is None:
            from film_foundry.ui.output_destination import OutputDestinationDialog
            from film_foundry.ui.inline_editor import present_inline_editor
            from film_foundry.ui.material_processing import batch_processing_details
            dialog=OutputDestinationDialog(text(f'已选 {len(self.paths)} 张',f'{len(self.paths)} images selected'),self.centralWidget(),details=batch_processing_details(self))
            present_inline_editor(self.centralWidget(),dialog,lambda:self.start_batch(dialog.directory))
            return
        material={k:c.currentData() if isinstance(c,QtWidgets.QComboBox) else c.value() for k,c in self.material_controls.items()}
        process={k:c.value() for k,c in self.process_controls.items()};defects={k:c.value() for k,c in self.defect_controls.items()}
        seconds=self.seconds.value();steps=[]
        if self.kind=='plate':
            baths={op:{key:c.value() for key,c in controls.items()} for op,controls in self.operation_controls.items()}
            if self.gold_intent['enabled'].isChecked():
                from film_foundry.app.plate_gold_intent import gold_supply_parameters
                baths['load_gilding'].update(gold_supply_parameters(strength=self.gold_intent['strength'].value()))
            steps=[('prepare',{}),('polish',{'streaks':process['streaks'],'angle_degrees':process['polish_angle']}),('sensitize',{'strength':process['strength']}),('expose',{}),
                   ('develop',{'seconds':seconds}),('fix',baths['fix']),('load_gilding',baths['load_gilding']),('gild',baths['gild']),
                   ('rinse_gilding',baths['rinse_gilding']),('finish',{}),('surface',{k:process[k] for k in ('damage','tarnish')}),('weather',defects)]
        else:
            steps=[('expose',{})]+([('eject',{})] if self.kind=='integral' else [])
            steps += [('spread',process|defects|{'direction':self.direction.currentData()}),('advance',{'seconds':seconds}),('peel' if self.kind=='peel' else 'stabilize',{})]
            if self.kind=='peel' and material['donor_mode']=='silver_negative':steps.append(('clear_negative',{k:c.value() for k,c in self.clearing_controls.items()}))
            steps.append(('handling',{k:c.value() for k,c in self.finish_controls.items()}))
        if self.leak_strength.value():
            exposure_index=next(i for i,(op,_) in enumerate(steps) if op=='expose')
            steps.insert(exposure_index+1,('light_leak',{'strength':self.leak_strength.value(),'edge':self.leak_edge.currentData()}))
        size=self.work_size.value();view=self.conditions();seed=int(self.seed.value())
        from film_foundry.app.workspace_jobs import process_material_photos
        from film_foundry.app.control import CancelFlag,WorkflowControl
        self._batch_cancel=CancelFlag();control=WorkflowControl(cancel_requested=self._batch_cancel.requested)
        from film_foundry.ui.material_batch_io import capture_report
        adjustments=dict(self.session.scan_adjustments);storage_precision=self.session.storage_precision
        self._submit(lambda:capture_report(self,lambda:process_material_photos(self.kind,self.paths,output,material,steps,view,size,seed,on_result=self.result_ready.emit,control=control,scan_adjustments=adjustments,storage_precision=storage_precision)),text('批量处理中…','Processing batch…'),render=False)

    def closeEvent(self,event):
        if self._batch_cancel:self._batch_cancel.cancel()
        super().closeEvent(event)
