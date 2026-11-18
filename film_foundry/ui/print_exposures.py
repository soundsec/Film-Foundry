"""Preview and stage local optical dose on the receiving material's image plane."""
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from film_foundry.app.control import CancelFlag, WorkflowControl
from film_foundry.app.preview_budget import Priority, preview_budget
from film_foundry.contracts.optical_cascade import PrintExposureRegion
from film_foundry.core.color import linear_to_srgb
from film_foundry.core.optical_relay import apply_local_exposures
from film_foundry.ui.qt import QtCore, QtWidgets
from film_foundry.ui.curve_editor import text
from film_foundry.ui.region_canvas import RegionCanvas, RegionFields
from film_foundry.ui.comparison_view import ComparisonView


def projection(path, body_id, relay):
    from film_foundry.app.optical_cascade import load_source, project_medium, select_body, limit_projection
    source=load_source(path,reflection=relay.mode=='reflection')
    preview=limit_projection(select_body(source,body_id),relay,480)
    return project_medium(source,replace(preview,local_exposures=()),body_id=body_id)


def paper_estimate(base, regions, material, seed, overrides, view, scan, control):
    from film_foundry.app.material_lab_session import MaterialLabSession
    from film_foundry.core.preview import resize_to_long_edge
    session = MaterialLabSession('paper')
    session.image = resize_to_long_edge(base, 240).copy()
    apply_local_exposures(session.image, regions)
    session.scan_adjustments = scan
    session.run_standard(material, seed, overrides, control=control)
    return session.preview(view)


class PrintExposures(QtWidgets.QDialog):
    def __init__(self,parent,path,body_id,relay):
        super().__init__(parent)
        self.regions=list(relay.local_exposures);self.base=None
        self.message=""
        self.setWindowTitle(text('印相遮挡 / 加光','Print dodge / burn'));self.resize(650,490)
        layout=QtWidgets.QVBoxLayout(self)
        bar=QtWidgets.QHBoxLayout();layout.addLayout(bar)
        self.list=QtWidgets.QComboBox();bar.addWidget(self.list,1)
        self.add=QtWidgets.QPushButton(text('添加区域','Add region'));bar.addWidget(self.add)
        self.remove=QtWidgets.QPushButton(text('移除区域','Remove region'));bar.addWidget(self.remove)
        self.canvas=RegionCanvas();self.canvas.ellipse=True;layout.addWidget(self.canvas,1)
        self.coords=RegionFields();layout.addWidget(self.coords)
        row=QtWidgets.QHBoxLayout();layout.addLayout(row)
        self.shape=QtWidgets.QComboBox()
        for key,zh,en in [('ellipse','椭圆','Ellipse'),('rectangle','矩形','Rectangle'),('gradient','渐变','Gradient')]:self.shape.addItem(text(zh,en),key)
        row.addWidget(self.shape)
        self.angle=QtWidgets.QDoubleSpinBox();self.angle.setRange(-180,180);self.angle.setSuffix('°');row.addWidget(self.angle)
        self.ev=QtWidgets.QDoubleSpinBox();self.ev.setRange(-6,6);self.ev.setSingleStep(.25);self.ev.setSuffix(' EV')
        self.feather=QtWidgets.QDoubleSpinBox();self.feather.setRange(0,100);self.feather.setSuffix('%')
        for label,widget in [(text('遮挡 − / 加光 +','Dodge − / burn +'),self.ev),(text('羽化','Feather'),self.feather)]:
            row.addWidget(QtWidgets.QLabel(label));row.addWidget(widget)
        self.multiplier=QtWidgets.QLabel();row.addWidget(self.multiplier);row.addStretch()
        self.status=QtWidgets.QLabel(text('正在读取接收面光照预览…','Loading receiver illumination…'));self.status.setWordWrap(True);layout.addWidget(self.status)
        self.buttons=QtWidgets.QDialogButtonBox(QtWidgets.QDialogButtonBox.StandardButton.Ok|QtWidgets.QDialogButtonBox.StandardButton.Cancel)
        self.paper_receiver=getattr(parent,'receiver',None)
        self.paper_future=None
        if getattr(self.paper_receiver,'kind',None)=='paper':
            self.estimate=self.buttons.addButton(text('纸面预估','Estimate paper print'),QtWidgets.QDialogButtonBox.ButtonRole.ActionRole)
            self.estimate.setEnabled(False)
            self.estimate.clicked.connect(self.estimate_paper)
        layout.addWidget(self.buttons);self.buttons.accepted.connect(self.accept);self.buttons.rejected.connect(self.reject)
        self.canvas.activated.connect(self.list.setCurrentIndex);self.canvas.selected.connect(self.edited_rect);self.coords.changed.connect(self.edited_rect)
        self.angle.valueChanged.connect(self.edited_values);self.shape.currentIndexChanged.connect(self.edited_values)
        self.ev.valueChanged.connect(self.edited_values);self.feather.valueChanged.connect(self.edited_values)
        self.list.currentIndexChanged.connect(self.select)
        self.remove.clicked.connect(self.remove_region);self.add.clicked.connect(self.add_region)
        self.update_list(0)
        pool=ThreadPoolExecutor(max_workers=1,thread_name_prefix='print-region')
        flag=CancelFlag()
        self.pool,self.control=pool,WorkflowControl(cancel_requested=flag.requested)
        self.paper_timer=QtCore.QTimer(self);self.paper_timer.timeout.connect(self.paper_ready)
        future=pool.submit(preview_budget.run,Priority.INTERACTIVE,projection,path,body_id,relay,
                           check=WorkflowControl(cancel_requested=flag.requested).check_cancel)
        timer=QtCore.QTimer(self)
        def poll():
            if not future.done():return
            timer.stop()
            try:
                self.base=future.result();self.render()
                if hasattr(self,"estimate"):self.estimate.setEnabled(True)
                self.message=text('接收面光照 · 拖动区域或角点调整。负 EV 遮挡，正 EV 加光；重叠区域累加。',
                    'Receiver illumination. Drag a region or a corner handle. Negative EV dodges, positive EV burns. Overlapping offsets add.')
                self.status.setText(self.message)
            except Exception as exc:self.status.setText(str(exc))
        def stop(*_):
            flag.cancel();future.cancel();timer.stop();self.paper_timer.stop();pool.shutdown(wait=False,cancel_futures=True)
        self.finished.connect(stop);timer.timeout.connect(poll);timer.start(50)

    def estimate_paper(self):
        from copy import deepcopy
        owner=self.paper_receiver
        self.paper_regions=tuple(self.regions)
        self.estimate.setEnabled(False)
        self.status.setText(text('正在按当前相纸与工艺形成小样…','Processing the current paper and baths…'))
        self.paper_future=self.pool.submit(preview_budget.run,Priority.STRIP,paper_estimate,self.base,
            self.paper_regions,owner._values(owner.material_controls),int(owner.seed.value()),
            {op:owner._values(group) for op,group in owner.operation_controls.items()},
            owner.conditions(),deepcopy(owner.session.scan_adjustments),self.control,check=self.control.check_cancel)
        self.paper_timer.start(50)

    def paper_ready(self):
        if not self.paper_future.done():return
        self.paper_timer.stop();self.estimate.setEnabled(True)
        if self.paper_regions!=tuple(self.regions):
            self.status.setText(text('区域已修改，请重新预估。','Regions changed. Estimate the print again.'));return
        try:pixmap=ComparisonView.pixmap(self.paper_future.result())
        except Exception as exc:self.status.setText(str(exc));return
        self.status.setText(self.message)
        dialog=QtWidgets.QDialog(self)
        dialog.setWindowTitle(text('纸面预估 · 当前相纸与标准工序','Paper estimate · current stock and reference process'))
        layout=QtWidgets.QVBoxLayout(dialog);image=QtWidgets.QLabel()
        image.setPixmap(pixmap);layout.addWidget(image);dialog.exec()

    def update_list(self,index):
        self.add.setEnabled(len(self.regions)<64)
        block=QtCore.QSignalBlocker(self.list);self.list.clear()
        for i,region in enumerate(self.regions):
            self.list.addItem(f'{i+1}   {region.ev:+.2f} EV')
        self.list.setCurrentIndex(min(index,len(self.regions)-1));del block
        self.select(self.list.currentIndex())

    def add_region(self):
        if len(self.regions)>=64:return
        self.regions.append(PrintExposureRegion((.25,.25,.75,.75),-.5))
        self.update_list(len(self.regions)-1)

    def remove_region(self):
        i=self.list.currentIndex()
        if i>=0:self.regions.pop(i);self.update_list(i)

    def select(self,i):
        self.buttons.button(QtWidgets.QDialogButtonBox.StandardButton.Ok).setEnabled(True)
        if self.message:self.status.setText(self.message)
        region=self.regions[i] if i>=0 else None
        for widget in (self.coords,self.ev,self.feather,self.remove,self.angle,self.shape):widget.setEnabled(region is not None)
        if region:
            block=QtCore.QSignalBlocker(self.shape);self.shape.setCurrentIndex(self.shape.findData(region.shape));del block
            for widget,value in ((self.angle,region.angle),(self.ev,region.ev),(self.feather,region.feather*100)):
                block=QtCore.QSignalBlocker(widget);widget.setValue(value);del block
        self.canvas.region=region.rect if region else None
        self.coords.set_rect(self.canvas.region);self.render()

    def edited_rect(self,rect):
        i=self.list.currentIndex()
        try:
            if rect is None:
                self.select(i);return
            if i<0:
                self.regions.append(PrintExposureRegion(rect,-.5));self.update_list(len(self.regions)-1)
            else:
                self.regions[i]=replace(self.regions[i],rect=rect)
                self.coords.set_rect(rect);self.canvas.region=rect;self.render()
            self.buttons.button(QtWidgets.QDialogButtonBox.StandardButton.Ok).setEnabled(True)
            if self.message:self.status.setText(self.message)
        except ValueError as exc:
            self.status.setText(str(exc))
            self.buttons.button(QtWidgets.QDialogButtonBox.StandardButton.Ok).setEnabled(False)

    def edited_values(self,*_):
        i=self.list.currentIndex()
        if i<0:return
        self.regions[i]=replace(self.regions[i],shape=self.shape.currentData(),angle=self.angle.value(),ev=self.ev.value(),feather=self.feather.value()/100)
        self.list.setItemText(i,f'{i+1}   {self.ev.value():+.2f} EV');self.render()

    def render(self):
        self.canvas.shapes=[(r.shape,r.angle) for r in self.regions]
        self.canvas.overlays=[r.rect for r in self.regions]
        self.canvas.active_index=self.list.currentIndex()
        self.multiplier.setText(f"× {2**self.ev.value():.2f}" if self.regions else "")
        if self.base is not None:
            dose=self.base.copy()
            apply_local_exposures(dose,self.regions)
            self.canvas.pixmap=ComparisonView.pixmap(linear_to_srgb(dose))
        self.canvas.update()
