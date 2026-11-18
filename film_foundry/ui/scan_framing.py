"""Stage a scan framing edit against the uncropped capture orientation."""
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from dataclasses import replace
from film_foundry.app.control import CancelFlag, WorkflowControl
from film_foundry.app.preview_budget import preview_budget, Priority
from film_foundry.contracts.scan_framing import rectangle
from film_foundry.core.scan_framing import frame_scan
from film_foundry.ui.qt import QtCore, QtWidgets
from film_foundry.ui.region_canvas import RegionCanvas, RegionFields
from film_foundry.ui.comparison_view import ComparisonView
from film_foundry.ui.curve_editor import text


class ScanFraming(QtWidgets.QDialog):
    def __init__(self,parent,scanner):
        super().__init__(parent)
        self.scanner=deepcopy(scanner)
        self.frame=None
        self.sample_point=None
        self.before_color=None
        self.error=None
        self.preview=None
        self.future=None
        self.rendered_key=None
        self.resize(680,520)
        self.setWindowTitle(text('扫描裁切、拉直与片基','Scan crop, straighten & base'))
        layout=QtWidgets.QVBoxLayout(self)
        bar=QtWidgets.QHBoxLayout();layout.addLayout(bar)
        self.mode=QtWidgets.QComboBox()
        self.mode.addItems([text('裁切扫描成片','Crop scan output'),text('片基校准','Film base calibration')]);bar.addWidget(self.mode)
        self.angle=QtWidgets.QDoubleSpinBox();self.angle.setRange(-45,45);self.angle.setDecimals(2);self.angle.setSingleStep(.1)
        self.angle.setValue(scanner.straighten_degrees);self.angle.setSuffix('°')
        self.angle_label=QtWidgets.QLabel(text('拉直','Straighten'))
        bar.addWidget(self.angle_label);bar.addWidget(self.angle);bar.addStretch()
        self.calibration=QtWidgets.QWidget()
        options=QtWidgets.QHBoxLayout(self.calibration);options.setContentsMargins(0,0,0,0)
        self.remove_base=QtWidgets.QCheckBox(text('启用去片基','Apply base removal'))
        self.remove_base.setChecked(scanner.remove_base_mask);options.addWidget(self.remove_base)
        self.dropper=QtWidgets.QPushButton(text('滴管取样','Pick white point'));self.dropper.setCheckable(True)
        options.addWidget(self.dropper)
        self.radius=QtWidgets.QComboBox()
        for n in (1,3,5,9):self.radius.addItem(f'{n} × {n}',n//2)
        self.radius.setCurrentIndex(1)
        self.radius.currentIndexChanged.connect(self.refresh)
        options.addWidget(self.radius)
        self.swatches=QtWidgets.QLabel();self.swatches.setTextFormat(QtCore.Qt.TextFormat.RichText)
        options.addWidget(self.swatches)
        self.auto=QtWidgets.QPushButton(text('恢复自动参考','Use automatic reference'));options.addWidget(self.auto);options.addStretch()
        layout.addWidget(self.calibration)
        self.canvas=RegionCanvas();layout.addWidget(self.canvas,1)
        self.crop_tools=QtWidgets.QWidget()
        tools=QtWidgets.QHBoxLayout(self.crop_tools);tools.setContentsMargins(0,0,0,0)
        self.ratio=QtWidgets.QComboBox()
        for label,value in [(text('自由比例','Free aspect'),None),('1:1',1.),('3:2',1.5),('2:3',2/3),('4:3',4/3),('16:9',16/9)]:
            self.ratio.addItem(label,value)
        tools.addWidget(self.ratio)
        self.ratio.currentIndexChanged.connect(lambda:setattr(self.canvas,'aspect',self.ratio.currentData()))
        grid=QtWidgets.QCheckBox(text('参考网格','Grid'));tools.addWidget(grid)
        grid.toggled.connect(lambda on:(setattr(self.canvas,'grid',on),self.canvas.update()))
        self.line=QtWidgets.QPushButton(text('沿边拉直','Level along edge'));self.line.setCheckable(True);tools.addWidget(self.line)
        self.line.toggled.connect(lambda on:setattr(self.canvas,'level',on))
        self.canvas.leveled.connect(self.level_edge)
        inscribe=QtWidgets.QPushButton(text('内接裁切','Crop black corners'));tools.addWidget(inscribe)
        inscribe.clicked.connect(self.inscribe)
        layout.addWidget(self.crop_tools)
        self.coords=RegionFields();layout.addWidget(self.coords)
        self.status=QtWidgets.QLabel();self.status.setWordWrap(True);layout.addWidget(self.status)
        actions=QtWidgets.QDialogButtonBox(QtWidgets.QDialogButtonBox.StandardButton.Ok|QtWidgets.QDialogButtonBox.StandardButton.Cancel)
        self.clear_crop=actions.addButton(text('清除裁切框','Clear crop'),QtWidgets.QDialogButtonBox.ButtonRole.ResetRole)
        self.clear_crop.clicked.connect(lambda:self.update_region(None))
        actions.accepted.connect(self.accept);actions.rejected.connect(self.reject)
        layout.addWidget(actions)
        self.mode.currentIndexChanged.connect(self.refresh)
        self.angle.valueChanged.connect(self.angle_changed)
        self.remove_base.toggled.connect(self.reference_changed)
        self.dropper.toggled.connect(self.refresh)
        self.auto.clicked.connect(self.reset_reference)
        self.canvas.picked.connect(self.pick)
        self.canvas.selected.connect(self.update_region);self.coords.changed.connect(self.edit_coordinates)
        self.refresh()

    def level_edge(self, delta):
        self.line.setChecked(False)
        self.angle.setValue(self.angle.value()+delta)

    def angle_changed(self):
        self.refresh()
        if self.frame is not None and self.mode.currentIndex()==0 and self.angle.value():self.inscribe()

    def inscribe(self):
        import math
        if self.frame is None:return
        h,w=self.frame.output.shape[:2]
        angle=math.radians(abs(self.angle.value()))
        target=self.canvas.aspect or w/h
        c,s=math.cos(angle),math.sin(angle)
        height=min((w-1)/(target*c+s),(h-1)/(target*s+c),w/target,h)
        if height<=0:return
        dx,dy=height*target/2,height/2
        # The crop contract rounds outwards. Snap inwards first to exclude interpolation at black edges.
        l,r=math.ceil(w/2-dx),math.floor(w/2+dx)
        t,b=math.ceil(h/2-dy),math.floor(h/2+dy)
        if r>l and b>t:
            self.update_region(((l+1e-7)/w,(t+1e-7)/h,(r-1e-7)/w,(b-1e-7)/h))

    def edit_coordinates(self,rect):
        aspect=self.ratio.currentData()
        if aspect and self.frame is not None:
            l,t,r,b=rect;h,w=self.frame.output.shape[:2]
            old=self.scanner.crop_rect or (0,0,1,1)
            if r<=l or b<=t:self.coords.set_rect(old);return
            ratio=aspect*h/w
            width=r-l if (l,r)!=(old[0],old[2]) else (b-t)*ratio
            width=min(width,1.,ratio);height=width/ratio
            cx=max(width/2,min(1-width/2,(l+r)/2))
            cy=max(height/2,min(1-height/2,(t+b)/2))
            rect=(cx-width/2,cy-height/2,cx+width/2,cy+height/2)
        self.update_region(rect)

    def update_region(self,rect):
        if self.mode.currentIndex():return
        self.scanner.crop_rect=rect
        self.coords.set_rect(rect);self.canvas.region=rect;self.canvas.update()

    def pick(self,x,y):
        if not self.dropper.isChecked() or self.frame is None:return
        from film_foundry.contracts.scan_framing import base_reference_rgb
        try:reference=base_reference_rgb(self.frame.pick(x,y,self.radius.currentData()))
        except ValueError:
            self.status.setText(text('白参考需要三个有效的正通道值，请重新取样。','Pick another area with a positive value in every channel.'));return
        self.before_color=self.sample_output(x,y)
        self.scanner.base_reference_rgb=reference
        self.scanner.base_sample_rect=None
        self.sample_point=(x,y)
        self.remove_base.setChecked(True)
        self.dropper.setChecked(False)
        self.reference_changed()

    def sample_output(self,x,y):
        import numpy as np
        image=self.frame.output
        h,w=image.shape[:2];i=min(h-1,int(y*h));j=min(w-1,int(x*w))
        radius=self.radius.currentData()
        patch=image[max(0,i-radius):i+radius+1,max(0,j-radius):j+radius+1]
        rgb=np.rint(np.clip(np.median(patch.reshape(-1,3),axis=0),0,1)*255).astype(int)
        return '#{:02x}{:02x}{:02x}'.format(*rgb)

    def reset_reference(self):
        self.scanner.base_reference_rgb=None
        self.scanner.base_sample_rect=None
        self.sample_point=None
        self.dropper.setChecked(False)
        self.reference_changed()

    def key(self):
        return self.remove_base.isChecked(),self.scanner.base_reference_rgb,self.scanner.base_sample_rect

    def reference_changed(self,*_):
        self.error=None
        if self.preview is not None and self.future is None and self.key()!=self.rendered_key:
            self.start_render()
        self.refresh()

    def refresh(self,*_):
        sample=bool(self.mode.currentIndex())
        self.crop_tools.setVisible(not sample)
        self.angle_label.setVisible(not sample);self.angle.setVisible(not sample)
        self.calibration.setVisible(sample);self.coords.setVisible(not sample);self.clear_crop.setVisible(not sample)
        self.scanner.straighten_degrees=self.angle.value()
        self.canvas.region=None if sample else self.scanner.crop_rect
        self.canvas.marker=self.sample_point if sample else None
        self.canvas.origin=None
        self.canvas.pick_mode=sample
        picking=sample and self.dropper.isChecked()
        self.canvas.setCursor(QtCore.Qt.CursorShape.CrossCursor if picking else QtCore.Qt.CursorShape.ArrowCursor)
        self.dropper.setEnabled(self.frame is not None)
        manual=self.scanner.base_reference_rgb
        legacy=self.scanner.base_sample_rect
        self.auto.setEnabled(manual is not None or legacy is not None)
        if not sample:self.coords.set_rect(self.scanner.crop_rect)
        if self.frame is not None:
            if sample:image=self.frame.display
            else:image=frame_scan(self.frame.output,replace(self.scanner,crop_rect=None))
            self.canvas.pixmap=ComparisonView.pixmap(image)
        self.canvas.update()
        if self.error:note=self.error
        elif sample:
            if picking:
                note=text('点选白参考，取邻域的线性 RGB 中位数。','Pick a white reference using the median linear RGB in the aperture.')+' '+self.radius.currentText()
            elif manual is not None:
                note=text('滴管参考 · 线性 RGB','Eyedropper reference · linear RGB')+'  '+', '.join(f'{v:.4f}' for v in manual)
            elif legacy is not None:
                note=text('当前沿用此工作的旧片基框。可恢复自动参考或用滴管重新取样。','Using the region saved in this work. Restore automatic calibration or pick a new white point.')
            else:
                note=text('自动参考 · 优先读取材料片基。','Automatic reference · use the recorded film base when available.')
                if self.frame is not None and self.rendered_key==self.key() and self.remove_base.isChecked():
                    source=self.frame.reference_source
                    if source in ('generated_clear_base','recorded_clear_base'):
                        note=text('自动参考 · 材料片基','Automatic reference · material base')
                    elif source=='explicit_clear_base_samples':
                        note=text('自动参考 · 扫描文件片边','Automatic reference · scan border')
                    elif source=='image_percentile_fallback':
                        note=text('自动参考 · 画面透射值估计','Automatic reference · estimated from image transmission')
                    if self.frame.reference_rgb is not None:
                        note+='   RGB  '+', '.join(f'{v:.4f}' for v in self.frame.reference_rgb)
            if not self.remove_base.isChecked():note+='  '+text('去片基已关闭。','Base removal is off.')
        elif self.rendered_key!=self.key() and self.preview is not None:
            note=text('正在更新扫描预览…','Updating scan preview…')
        else:
            note=text('先拉直，再拖框保留成片区域。坐标为画幅百分比。','Straighten first, then drag the crop. Coordinates are percentages of the frame.')
        self.swatches.clear()
        if sample and self.sample_point and self.before_color and self.rendered_key==self.key():
            after=self.sample_output(*self.sample_point)
            self.swatches.setText(text('成片','Output')+f' <span style="background-color:{self.before_color}">&nbsp;&nbsp;&nbsp;</span> → <span style="background-color:{after}">&nbsp;&nbsp;&nbsp;</span>')
            self.swatches.setToolTip(f'{self.before_color} → {after}')
        self.status.setText(note)

    def load_preview(self,path,mode,config,offset,edge):
        from film_foundry.app.scan_calibration import CalibrationPreview
        self.preview=CalibrationPreview(path,mode,config,offset,edge)
        self.pool=ThreadPoolExecutor(max_workers=1,thread_name_prefix='scan-framing')
        self.flag=CancelFlag()
        self.timer=QtCore.QTimer(self);self.timer.timeout.connect(self.poll)
        self.finished.connect(self.stop)
        self.start_render();self.refresh()

    def start_render(self):
        self.running_key=self.key()
        check=WorkflowControl(cancel_requested=self.flag.requested).check_cancel
        self.future=self.pool.submit(preview_budget.run,Priority.INTERACTIVE,self.preview.render,*self.running_key,check=check)
        self.timer.start(40)

    def poll(self):
        if not self.future.done():return
        self.timer.stop()
        try:
            frame=self.future.result()
            if self.running_key==self.key():
                self.frame=frame;self.rendered_key=self.running_key;self.error=None
        except Exception as exc:
            self.error=str(exc)
        self.future=None
        if self.running_key!=self.key() and self.rendered_key!=self.key():self.start_render()
        self.refresh()

    def stop(self,*_):
        self.flag.cancel();self.timer.stop()
        if self.future is not None:self.future.cancel()
        self.pool.shutdown(wait=False,cancel_futures=True)

    def accept(self):
        try:
            self.scanner.remove_base_mask=self.remove_base.isChecked()
            self.scanner.crop_rect=rectangle(self.scanner.crop_rect)
        except ValueError as exc:self.status.setText(str(exc));return
        super().accept()


def open_framing(owner,*,calibration=False):
    from film_foundry.app.source_seed import offset_for
    mode=owner.pipeline_mode.get()
    if owner._processing or owner._active_roll is not None or mode not in ('full','scan'):
        owner.status.set(text('请先选择扫描终点。','Select a scan endpoint first.'));return
    try:
        config=owner._fast_preview_config()
        path=owner._first_input_image() if mode=='full' else owner._first_negative_file()
    except (ValueError,OSError) as exc:
        owner.status.set(str(exc));return
    if path is None:
        owner.status.set(text('请先选择扫描来源。','Choose a scan source first.'));return
    dialog=ScanFraming(owner.film_workspace,config.scanner)
    if calibration:dialog.mode.setCurrentIndex(1)
    dialog.load_preview(path,mode,config,offset_for(path,owner._path_seed_offsets),min(owner.PREVIEW_EDGE,640))
    def apply():
        fields=('crop_rect','straighten_degrees','base_sample_rect','base_reference_rgb','remove_base_mask')
        if all(getattr(dialog.scanner,key)==getattr(config.scanner,key) for key in fields):
            return
        owner.edit_tools.record()
        snapshot=owner.edit_tools.capture()
        scanner=snapshot['config']['scanner']
        for key in fields:
            scanner[key]=getattr(dialog.scanner,key)
        snapshot['link_scanner']=False
        owner.edit_tools.apply(snapshot);owner.edit_tools.record()
    owner._present_workspace_dialog(dialog,on_accept=apply)
    return dialog
