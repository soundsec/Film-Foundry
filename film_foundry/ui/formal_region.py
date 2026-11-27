"""Inspect native output pixels from a frozen film request and its selected area."""
from pathlib import Path
from film_foundry.app.film_test_strip import FilmStripSession
from film_foundry.app.region_inspection import RegionInspection
from film_foundry.ui.qt import QtCore, QtGui, QtWidgets
from film_foundry.ui.curve_editor import text
from film_foundry.ui.region_canvas import RegionCanvas
from film_foundry.ui.comparison_view import ComparisonView
from film_foundry.ui.image_view import ImageView
from film_foundry.ui.work_dialog import WorkDialog


class RegionDetailView(ImageView):
    """Keep display magnification independent of the cropped formation pixels."""

    def __init__(self):
        super().__init__()
        self.zoom_changed.connect(self.update_sampling)

    def _set_scale(self, scale):
        # A one-pixel crop may already exceed the normal zoom limit when fitted.
        # Leave wheel zoom room above that fit scale even on a large display.
        viewport = self.viewport()
        super()._set_scale(scale, maximum=max(16.0, 2.0 * max(viewport.width(), viewport.height())))

    def update_sampling(self, _percent):
        # Preserve individual grain pixels at native size and when magnified.
        smooth = self.transform().m11() * self.viewport().devicePixelRatioF() < 1.0 - 1e-6
        mode = QtCore.Qt.TransformationMode
        self._image.setTransformationMode(mode.SmoothTransformation if smooth else mode.FastTransformation)
        self.setRenderHint(QtGui.QPainter.RenderHint.SmoothPixmapTransform, smooth)

    def show_region(self, image):
        first = self.source is None
        fit = self._fit_mode
        scale = self.transform().m11()
        self.show_array(image)
        if first:
            ratio = self.viewport().devicePixelRatioF()
            fit = (image.shape[1] < self.viewport().width() * ratio and
                   image.shape[0] < self.viewport().height() * ratio)
            scale = 1.0 / ratio
        if fit:
            self.fit()
        else:
            self._set_scale(scale)
            self.centerOn(self.sceneRect().center())


class FormalRegion(WorkDialog):
    def __init__(self,parent,session,choices,scaled,locator=None,rect=None):
        super().__init__(parent)
        self.session=session;self.choices=choices
        self.inspection=RegionInspection(session,scaled=scaled)
        self.locators=locator if isinstance(locator,list) else None
        self.setWindowTitle(text('局部正式尺度预览','Formal-scale region preview'))
        self.resize(820,580)
        layout=QtWidgets.QVBoxLayout(self)
        self.note=QtWidgets.QLabel()
        self.note.setWordWrap(True)
        layout.addWidget(self.note)
        self.choice=QtWidgets.QComboBox()
        for label,_ in choices:self.choice.addItem(label)
        if len(choices)>1:layout.addWidget(self.choice)
        self.stack=QtWidgets.QStackedWidget();layout.addWidget(self.stack,1)
        self.canvas=RegionCanvas();self.stack.addWidget(self.canvas)
        self.canvas.region=rect or (.35,.35,.65,.65)
        self.canvas.dim_outside=True
        self.view=RegionDetailView()
        self.stack.addWidget(self.view)
        layout.addWidget(self.status)
        row=QtWidgets.QHBoxLayout();layout.addLayout(row)
        self.back=QtWidgets.QPushButton(text('重新选区','Select region'));row.addWidget(self.back)
        self.back.clicked.connect(self.locate)
        self.calculate=QtWidgets.QPushButton(text('计算所选区域','Render selected area'));row.addWidget(self.calculate)
        self.calculate.clicked.connect(self.render)
        self.stop=QtWidgets.QPushButton(text('停止','Stop'));row.addWidget(self.stop)
        self.stop.clicked.connect(lambda:self.flag.cancel())
        self.fit_button=QtWidgets.QPushButton(text('适合窗口','Fit'))
        self.fit_button.clicked.connect(self.view.fit)
        row.addWidget(self.fit_button)
        self.native_button=QtWidgets.QPushButton('100%')
        self.native_button.clicked.connect(self.view.actual_size)
        row.addWidget(self.native_button)
        self.zoom_label=QtWidgets.QLabel()
        self.zoom_label.setAccessibleName(text('当前显示倍率','Current magnification'))
        self.view.zoom_changed.connect(lambda percent:self.zoom_label.setText(
            text('倍率 {zoom}%','Zoom {zoom}%').format(zoom=percent)))
        row.addWidget(self.zoom_label)
        close=QtWidgets.QPushButton(text('关闭','Close'));row.addStretch();row.addWidget(close);close.clicked.connect(self.reject)
        self.choice.currentIndexChanged.connect(self.locate)
        self.running_changed.connect(self.sync)
        self.canvas.selected.connect(lambda *_:self.sync(self.busy))
        if locator is not None:
            self.canvas.pixmap=self.locators[0] if self.locators else locator
        self.locate()

    def sync(self,busy):
        detail=self.stack.currentWidget() is self.view
        for widget in (self.fit_button,self.native_button,self.zoom_label):
            widget.setVisible(detail)
        self.calculate.setVisible(not detail)
        self.stop.setVisible(busy)
        self.stop.setEnabled(busy)
        self.choice.setEnabled(not busy)
        self.canvas.setEnabled(not busy)
        needs_locator=self.canvas.pixmap is None
        self.back.setText(text('生成定位图','Prepare locator') if needs_locator else text('重新选区','Select region'))
        self.back.setVisible(detail or needs_locator)
        self.back.setEnabled(not busy and (needs_locator or detail))
        self.calculate.setEnabled(not busy and self.canvas.region is not None and not needs_locator)

    def locate(self,*_):
        if self.locators:
            self.canvas.pixmap=self.locators[self.choice.currentIndex()]
        self.note.setText(text('在定位图上拖出选区，拖动边角可调整范围。',
                               'Drag to select a region. Drag its corners to resize it.'))
        self.stack.setCurrentWidget(self.canvas)
        if self.canvas.pixmap is None:
            cfg=self.choices[self.choice.currentIndex()][1]
            def loaded(image):
                pm=ComparisonView.pixmap(image)
                if not isinstance(cfg,dict):
                    pm=pm.transformed(QtGui.QTransform().rotate(90*cfg.output.rotation_quarters))
                self.canvas.pixmap=pm
                self.locate()
            self.submit(lambda control:self.session.render(cfg),loaded,text('正在生成定位图…','Preparing locator…'))
            return
        self.canvas.update()
        self.status.setText(self.seed_label())
        self.sync(False)

    def seed_label(self):
        if isinstance(self.session,FilmStripSession):
            if self.session.mode=='scan':return ''
            seed=self.session.seed
        else:
            snapshot=self.session.snapshot
            if snapshot['state'] is not None:seed=snapshot['state'].seed
            elif snapshot['group'] is not None:return ''
            else:seed=self.choices[self.choice.currentIndex()][1]['seed']
        return text('本次种子 ','Session seed ')+str(seed)

    def render(self):
        rect=self.canvas.region
        if not rect or self.busy:return
        index=self.choice.currentIndex()
        cfg=self.choices[index][1]
        def display(result):
            image,bounds,shape=result
            self.note.setText(text('颗粒随画面一起缩放。100% 时，一个成片像素对应一个屏幕像素。',
                                   'Grain scales with the image. At 100%, one output pixel maps to one screen pixel.'))
            self.stack.setCurrentWidget(self.view)
            self.sync(False)
            size=text('选区 {w} × {h} px · 全幅 {fw} × {fh} px · 起点 ({x}, {y})',
                      'Region {w} × {h} px · Frame {fw} × {fh} px · Origin ({x}, {y})')
            size=size.format(w=image.shape[1],h=image.shape[0],fw=shape[1],fh=shape[0],x=bounds[0],y=bounds[1])
            self.status.setText('   ·   '.join(filter(None,(size,self.seed_label()))))
            self.layout().activate()
            self.view.show_region(image)
        self.submit(lambda control:self.inspection.read(index,cfg,rect),display,
                    text('正在按正式设置处理整幅…','Processing the full frame at formal settings…'))


def open_formal_region(owner):
    from film_foundry.app.source_seed import offset_for
    from film_foundry.app.workflow import iter_latent_files
    from film_foundry.ui.inline_editor import present_inline_editor
    page=owner.workspace_host.currentWidget()
    if page in owner._material_windows.values():return open_material_region(page)
    if owner._processing or owner._active_roll is not None:
        owner.status.set(text('请在单片工作台空闲时打开。','Open this from an idle single-frame workbench.'));return
    if not owner._required_paths(preview=True,quiet=False):return
    mode=owner.pipeline_mode.get()
    try:
        if mode=='develop_latent':path=next(iter(iter_latent_files(Path(owner.negative_path.get()))),None)
        elif mode=='scan':path=owner._first_negative_file()
        else:path=owner._first_input_image()
        if path is None:raise ValueError(text('先选择来源。','Select a source first.'))
        config=owner._freeze_incident_config(owner._config())
        if mode in ("full","expose","develop"):
            config=owner._focused_incident_config(config,path)
        session=FilmStripSession(path,mode,config,path_seed_offset=offset_for(path,owner._path_seed_offsets))
        session.frame_settings=owner._focused_frame_settings(path)
        dialog=FormalRegion(owner.film_workspace,session,[(path.name,config)],bool(owner.run_as_preview.get()))
    except (ValueError,OSError) as exc:owner.status.set(str(exc));return
    present_inline_editor(owner.film_workspace,dialog)
    return dialog


def open_material_region(owner):
    from film_foundry.app.material_test_strip import MaterialStripSession
    from film_foundry.ui.material_test_strip import material_trial_config
    from film_foundry.ui.inline_editor import present_inline_editor
    if owner.busy:return
    source=owner.session
    if all(item is None for item in (source.image,source.state,source.group)):
        owner.status.setText(text('先载入材料或曝光来源。','Load a material or exposure source first.'))
        return
    fresh=source.state is None and source.group is None
    cfg=material_trial_config(owner,fresh,None)
    session=MaterialStripSession(source)
    dialog=FormalRegion(owner,session,[(source.source_name,cfg)],False)
    return present_inline_editor(owner,dialog)
