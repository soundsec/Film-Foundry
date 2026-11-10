"""Small transactional film-rebate configuration, separate from picture imprints."""
from copy import deepcopy

from film_foundry.ui.qt import QtCore, QtGui, QtWidgets
from film_foundry.ui.curve_editor import text


class EdgePlacementPreview(QtWidgets.QLabel):
    dragged=QtCore.Signal(float,float)

    def __init__(self):
        super().__init__();self.previous=None;self.rebate_fraction=1.
        self.setCursor(QtCore.Qt.CursorShape.OpenHandCursor)

    def mousePressEvent(self,event):
        pixmap=self.pixmap()
        if event.button()==QtCore.Qt.MouseButton.LeftButton and pixmap is not None:
            rect=QtCore.QRectF((self.width()-pixmap.width())/2,(self.height()-pixmap.height())/2,pixmap.width(),pixmap.height())
            if rect.contains(event.position()):self.previous=event.position()

    def mouseMoveEvent(self,event):
        if self.previous is not None and event.buttons() & QtCore.Qt.MouseButton.LeftButton:
            delta=event.position()-self.previous;self.previous=event.position();pixmap=self.pixmap()
            self.dragged.emit(delta.x()/pixmap.width(),delta.y()/max(1,pixmap.height()*self.rebate_fraction))

    def mouseReleaseEvent(self,event):self.previous=None


class FilmBorderDialog(QtWidgets.QDialog):
    def __init__(self, config, parent=None):
        super().__init__(parent)
        self.config = deepcopy(config)
        self.setWindowTitle(text('片边与齿孔', 'Film edges & perforations'))
        self.resize(580, 740)
        box = QtWidgets.QVBoxLayout(self)
        self.enabled = QtWidgets.QCheckBox(text('在新底片上形成片边', 'Form rebates on new film'))
        self.enabled.setChecked(config.enabled);box.addWidget(self.enabled)
        self.enabled.setToolTip(text('片边随曝光设置冻结并随冲洗保存；扫描不会追加或改写。画面打印另行设置。', 'Rebates are frozen with exposure and saved with the developed medium. Scanning cannot add or change them. Picture imprints are configured separately.'))
        self.controls = QtWidgets.QTabWidget();box.addWidget(self.controls,1)
        geometry=QtWidgets.QWidget();geometry_form=form=QtWidgets.QFormLayout(geometry);self.controls.addTab(geometry,text('规格与齿孔','Format & perforations'))
        self.format = QtWidgets.QComboBox()
        for label,key in [('135 / 35 mm','135'),(text('135 半格','135 half frame'),'half_frame'),('120','120'),('110','110')]:
            self.format.addItem(label,key)
        self.format.setCurrentIndex(max(0,self.format.findData(config.format_key)));form.addRow(text('片边规格','Rebate format'),self.format)
        self.rows=QtWidgets.QComboBox()
        for key,zh,en in [('both','上下两侧','Both'),('top','仅上侧','Top'),('bottom','仅下侧','Bottom'),('none','不打孔','None')]:
            self.rows.addItem(text(zh,en),key)
        self.rows.setCurrentIndex(max(0,self.rows.findData(config.sprocket_rows)));form.addRow(text('齿孔位置','Perforations'),self.rows)
        if config.sprocket_count == 0:
            self.rows.setCurrentIndex(self.rows.findData('none'))
        self.count=QtWidgets.QSpinBox();self.count.setRange(0,64);self.count.setSpecialValueText(text('随规格','From format'));self.count.setValue(config.sprocket_count or 0)
        form.addRow(text('每侧孔数','Holes per side'),self.count)
        self.width=QtWidgets.QDoubleSpinBox();self.width.setRange(2,80);self.width.setSuffix(' %');self.width.setDecimals(1);self.width.setValue(config.rebate_height_ratio*100)
        self.width.setToolTip(text('每条片边相对于画面高度的比例，不裁切或缩放原画面。','Height of each rebate relative to the image gate; the image is not cropped or resized.'))
        form.addRow(text('单侧片边高度','Rebate height'),self.width)
        lettering=QtWidgets.QWidget();form=QtWidgets.QFormLayout(lettering)
        scroll=QtWidgets.QScrollArea();scroll.setWidgetResizable(True);scroll.setWidget(lettering);self.controls.addTab(scroll,text('边字与曝光','Lettering & exposure'))
        self.marks=QtWidgets.QCheckBox(text('厂标与识别条纹','Factory lettering & bars'));self.marks.setChecked(config.factory_marks);form.addRow(self.marks)
        self.label=QtWidgets.QLineEdit(config.stock_label);self.label.setMaxLength(14);self.label.setPlaceholderText(text('留空使用材料名','Use stock name when empty'))
        self.label.setValidator(QtGui.QRegularExpressionValidator(QtCore.QRegularExpression('[A-Za-z0-9 -]*'),self.label))
        form.addRow(text('片边文字','Stock label'),self.label)
        self.template=QtWidgets.QComboBox()
        for key,zh,en in [('legacy','原布局','Legacy'),('text_code','135 · 上字下码','135 · Text above / bars below'),('code_text','135 · 上码下字','135 · Bars above / text below'),('combined','上下字码同出','Text and bars on both edges'),('cinema','电影式 · 编号与条码','Cinema-style · Number & code'),('compact','半格 · 紧凑编号','Half-frame · Compact'),('roll_markings','120 · 文字与批号','120 · Lettering & batch'),('text_only','黑白片 · 纯文字','Monochrome · Lettering')]:self.template.addItem(text(zh,en),key)
        self.template.setCurrentIndex(max(0,self.template.findData(config.factory_layout)));form.addRow(text('布局模板','Layout template'),self.template)
        self.batch=QtWidgets.QSpinBox();self.batch.setRange(0,4095);self.batch.setValue(config.factory_batch);form.addRow(text('批次标识','Batch identifier'),self.batch)
        self.number=QtWidgets.QSpinBox();self.number.setRange(-1,9999);self.number.setSpecialValueText(text('不打印','Off'));self.number.setValue(config.frame_number if config.frame_number is not None else -1)
        self.number.setToolTip(text('普通图片的片序标签；有胶卷身份时采用实际格号。已曝光片的号码不能由扫描修改。','Label for ordinary images. Roll exposures use their actual frame index. Scanning cannot change a frozen number.'))
        form.addRow(text('片序号','Frame number'),self.number)
        self.direction=QtWidgets.QComboBox();self.direction.addItem('→','right');self.direction.addItem('←','left');self.direction.setCurrentIndex(max(0,self.direction.findData(config.advance_direction)))
        self.barcode=QtWidgets.QComboBox()
        for key,zh,en in [('blocks','135式 · 双轨边码','135-style · Two tracks'),('staggered_blocks','等高条码 · 两档粗细','Equal-height · Two widths'),('paired_blocks','等高条码 · 密集宽窄','Equal-height · Dense bars'),('code128c','Code 128 · 批号与片序','Code 128 · Batch and frame'),('legacy','旧装饰条纹','Legacy decorative bars'),('none','不打印条码','No barcode')]:self.barcode.addItem(text(zh,en),key)
        self.barcode.setCurrentIndex(self.barcode.findData(config.factory_barcode));form.addRow(text('条码编码','Barcode'),self.barcode)
        self.barcode.setToolTip(text('135式参考真实边码的同步轨与数据轨排列，只是外观模拟；等高条码只改变条宽与间隔。Code 128编码本软件的批号与片序。均不冒用注册DX或KEYKODE身份，自定义图案优先。','135-style uses the clock/data-track arrangement as a visual reference only. Equal-height styles vary widths and spacing. Code 128 encodes our batch and frame identifier. No registered DX or KEYKODE identity is claimed; custom stencils take precedence.'))
        self.seed=QtWidgets.QSpinBox();self.seed.setRange(0,2147483647);self.seed.setValue(config.factory_barcode_seed)
        form.addRow(text('图案组合号','Pattern sequence'),self.seed)
        self.shuffle=QtWidgets.QPushButton(text('换一组条码','New bar sequence'));self.shuffle.clicked.connect(self.shuffle_blocks);form.addRow(self.shuffle)
        self.seed.setToolTip(text('固定条组按组合号、批号和片序排列；同一配置保持不变。装饰条码不是标准机器码。自定义点阵优先。','Fixed bar groups are sequenced by this number, batch and frame. The result is stable. Decorative bars are not standard machine codes. Custom stencils take precedence.'))
        self.exposure=QtWidgets.QDoubleSpinBox();self.exposure.setRange(-4,4);self.exposure.setDecimals(2);self.exposure.setSingleStep(.25);self.exposure.setSuffix(' EV');self.exposure.setValue(config.factory_exposure_ev);form.addRow(text('出厂预曝光','Factory exposure'),self.exposure)
        self.variation=QtWidgets.QDoubleSpinBox();self.variation.setRange(0,100);self.variation.setSuffix(' %');self.variation.setValue(config.factory_exposure_variation*100);form.addRow(text('曝光不均','Exposure variation'),self.variation)
        self.spread=QtWidgets.QDoubleSpinBox();self.spread.setRange(0,3);self.spread.setSingleStep(.1);self.spread.setValue(config.factory_mark_spread if config.factory_mark_type=='laser' else 0);form.addRow(text('曝光柔边','Exposure diffusion'),self.spread)
        self.exposure.setToolTip(text('在同一材料、药浴的曝光响应前改变剂量，影响实际形成密度；不是输出透明度。曝光不均模拟厂标曝光灯的不匀，不代表局部冲洗事故。','Changes exposure before the stock/bath response, affecting formed density, not output opacity. Variation describes the factory lamp, not spatial processing accidents.'))
        self.stencil=QtWidgets.QPushButton(text('编辑厂标图案…','Edit lettering stencil…'));self.stencil.clicked.connect(self.edit_stencil);form.addRow(self.stencil)
        placement=QtWidgets.QWidget();placement_form=QtWidgets.QFormLayout(placement);self.controls.addTab(placement,text('位置与方向','Placement & direction'))
        self.marker=QtWidgets.QComboBox()
        for key,label in [('legacy',text('原点阵箭头','Legacy dotted arrow')),('arrow','→'),('double_arrow','⇉'),('chevron','❯❯'),('triangle','▶'),('outline_triangle','▷'),('none',text('仅片序，不显示方向','Frame number only'))]:self.marker.addItem(label,key)
        self.marker.setCurrentIndex(self.marker.findData(config.advance_marker_style));placement_form.addRow(text('方向标记','Advance symbol'),self.marker)
        placement_form.addRow(text('过片方向','Film advance'),self.direction)
        from film_foundry.ui.control_reset import reset_button
        def position_spin(value,limit=100):
            control=QtWidgets.QDoubleSpinBox();control.setRange(-limit,limit);control.setDecimals(1);control.setSingleStep(.5);control.setSuffix(' %');control.setValue(value*100)
            reset=reset_button();reset.setToolTip(text('恢复打开时的位置','Restore original position'));reset.clicked.connect(lambda:control.setValue(value*100))
            return control,reset
        self.loading,reset=position_spin(config.loading_offset,50)
        loading_row=QtWidgets.QWidget();line=QtWidgets.QHBoxLayout(loading_row);line.setContentsMargins(0,0,0,0)
        self.loading_slider=QtWidgets.QSlider(QtCore.Qt.Orientation.Horizontal);self.loading_slider.setRange(-500,500);self.loading_slider.setValue(round(config.loading_offset*1000))
        line.addWidget(self.loading_slider,1);line.addWidget(self.loading);line.addWidget(reset);placement_form.addRow(text('整体上卷偏移','Loading offset'),loading_row)
        self.loading_slider.valueChanged.connect(lambda value:self.loading.setValue(value/10));self.loading.valueChanged.connect(lambda value:self.loading_slider.setValue(round(value*10)))
        loading_row.setToolTip(text('沿片带移动字码、片序和齿孔；跨边界处显示相邻格标记，画面不移动。以一格宽度为100%。','Shift lettering, frame marks and perforations along the strip. Adjacent frame marks enter at the boundary; the image stays fixed. 100% is one frame width.'))
        self.position_controls={}
        for name,zh,en in [('factory_offset_top','上边字码','Top lettering'),('factory_offset_bottom','下边字码','Bottom lettering'),('advance_marker_offset','片序与方向','Frame & direction')]:
            row=QtWidgets.QWidget();line=QtWidgets.QHBoxLayout(row);line.setContentsMargins(0,0,0,0);controls=[]
            for axis,value in zip(('X','Y'),getattr(config,name)):
                control,reset=position_spin(value);line.addWidget(QtWidgets.QLabel(axis));line.addWidget(control,1);line.addWidget(reset);controls.append(control)
            self.position_controls[name]=controls;placement_form.addRow(text(zh,en),row)
            row.setToolTip(text('X相对画面宽度，Y相对单侧片边高度；超出片边的部分被裁切。','X is relative to frame width; Y to one rebate height. Content outside the rebate is clipped.'))
        self.drag_target=QtWidgets.QComboBox()
        for key,zh,en in [('loading','整体上卷','Loading offset'),('factory_offset_top','上边字码','Top lettering'),('factory_offset_bottom','下边字码','Bottom lettering'),('advance_marker_offset','片序与方向','Frame & direction')]:self.drag_target.addItem(text(zh,en),key)
        placement_form.addRow(text('预览拖动对象','Drag in preview'),self.drag_target)
        self.preview=EdgePlacementPreview();self.preview.setFixedHeight(184);self.preview.setAlignment(QtCore.Qt.AlignmentFlag.AlignCenter);box.addWidget(self.preview)
        self.preview.dragged.connect(self.drag_selected)
        self.preview.setToolTip(text('在“位置与方向”页选择对象后，可在此拖动。这里是布局示意；实际字码经过材料、显影与定影形成，片边暂无局部颗粒与空间事故。','Choose a drag target on Placement & direction, then drag here. Layout only; actual marks form through stock, development and fixing. No local rebate grain or spatial accidents yet.'))
        buttons=QtWidgets.QDialogButtonBox(QtWidgets.QDialogButtonBox.StandardButton.Ok|QtWidgets.QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self.accept);buttons.rejected.connect(self.reject);box.addWidget(buttons)
        self.enabled.toggled.connect(self.refresh);self.format.currentIndexChanged.connect(self.refresh);self.rows.currentIndexChanged.connect(self.refresh)
        self.count.valueChanged.connect(self.refresh);self.width.valueChanged.connect(self.refresh);self.marks.toggled.connect(self.refresh);self.label.textChanged.connect(self.refresh)
        self.template.currentIndexChanged.connect(self.change_template);self.batch.valueChanged.connect(self.refresh);self.number.valueChanged.connect(self.refresh);self.direction.currentIndexChanged.connect(self.refresh)
        self.barcode.currentIndexChanged.connect(self.refresh)
        self.seed.valueChanged.connect(self.refresh);self.marker.currentIndexChanged.connect(self.refresh);self.loading.valueChanged.connect(self.refresh)
        for controls in self.position_controls.values():
            for control in controls:control.valueChanged.connect(self.refresh)
        for control in (self.exposure,self.variation,self.spread):control.valueChanged.connect(self.refresh)
        from film_foundry.ui.control_reset import form_reset_buttons
        form_reset_buttons(geometry_form);form_reset_buttons(form);form_reset_buttons(placement_form)
        self.refresh()

    def value(self):
        result=deepcopy(self.config)
        result.enabled=self.enabled.isChecked();result.format_key=self.format.currentData();result.sprocket_rows=self.rows.currentData()
        result.sprocket_count=self.count.value() or None;result.rebate_height_ratio=self.width.value()/100
        result.factory_marks=self.marks.isChecked();result.stock_label=self.label.text().strip().upper()
        result.factory_layout=self.template.currentData();result.factory_batch=self.batch.value()
        result.frame_number=None if self.number.value()<0 else self.number.value();result.advance_direction=self.direction.currentData()
        result.factory_barcode=self.barcode.currentData();result.factory_exposure_ev=self.exposure.value();result.factory_exposure_variation=self.variation.value()/100
        result.factory_barcode_seed=self.seed.value();result.loading_offset=self.loading.value()/100;result.advance_marker_style=self.marker.currentData()
        for name,controls in self.position_controls.items():setattr(result,name,tuple(control.value()/100 for control in controls))
        result.factory_mark_type='laser' if self.spread.value()>0 else 'hard';result.factory_mark_spread=self.spread.value()
        return result

    def shuffle_blocks(self):
        import secrets
        self.seed.setValue((self.seed.value()+1+secrets.randbelow(2147483646))%2147483648)

    def drag_selected(self,dx,dy):
        if not self.enabled.isChecked():return
        key=self.drag_target.currentData()
        if key=='loading':self.loading.setValue(self.loading.value()+dx*100)
        else:
            x,y=self.position_controls[key];x.setValue(x.value()+dx*100);y.setValue(y.value()+dy*100)

    def change_template(self,*_):
        self.config.factory_pattern_top='';self.config.factory_pattern_bottom=''
        self.config.factory_pattern_linked=False
        key=self.template.currentData()
        if key!='legacy':
            self.barcode.setCurrentIndex(self.barcode.findData('none' if key in ('text_only','roll_markings') else 'code128c' if key=='cinema' else 'blocks'))
            self.format.setCurrentIndex(self.format.findData('120' if key=='roll_markings' else 'half_frame' if key=='compact' else '135'))
            self.width.setValue(8 if key=='roll_markings' else 23)
            self.spread.setValue(.65);self.variation.setValue(18)
        if self.number.value()<0:self.number.setValue(1)
        self.refresh()

    def refresh(self,*_):
        self.controls.setEnabled(self.enabled.isChecked())
        holes=self.format.currentData()!='120'
        self.rows.setEnabled(holes);self.count.setEnabled(holes and self.rows.currentData()!='none')
        self.label.setEnabled(self.marks.isChecked());self.stencil.setEnabled(self.marks.isChecked())
        blocks=self.barcode.currentData() in ('blocks','staggered_blocks','paired_blocks')
        self.seed.setEnabled(blocks);self.shuffle.setEnabled(blocks)
        from film_foundry.core.film_border import resolve_film_border_layout
        from film_foundry.core.formed_film_border import _strip_masks
        import numpy as np
        config=self.value();layout=resolve_film_border_layout(config,(180,420,3))
        pixels=np.full((180,420,3),.28,dtype=np.float32)
        if layout is not None:
            marks,holes=_strip_masks(config,layout,config.stock_label or 'FILM FOUNDRY')
            exposure=marks*2.**config.factory_exposure_ev
            strip=np.array([.50,.29,.13],dtype=np.float32)[None,None,:]*np.exp(-1.6*exposure[...,None])
            strip=strip*(1-holes[...,None])+holes[...,None]*.96
            rows=layout.image_rect[1];pixels=np.concatenate((strip[:rows],pixels,strip[rows:]))
            self.preview.rebate_fraction=rows/pixels.shape[0]
        pixels=np.ascontiguousarray(np.clip(pixels*255,0,255),dtype=np.uint8)
        image=QtGui.QImage(pixels.data,pixels.shape[1],pixels.shape[0],pixels.strides[0],QtGui.QImage.Format.Format_RGB888).copy()
        self.preview.setPixmap(QtGui.QPixmap.fromImage(image).scaled(450,180,QtCore.Qt.AspectRatioMode.KeepAspectRatio,QtCore.Qt.TransformationMode.SmoothTransformation))

    def edit_stencil(self):
        from film_foundry.ui.edge_mark_editor import EdgeMarkEditor
        self.child=EdgeMarkEditor(self.value(),self)
        self.child.setWindowModality(QtCore.Qt.WindowModality.WindowModal)
        def apply():
            self.child.apply_to(self.config)
            self.spread.setValue(self.config.factory_mark_spread if self.config.factory_mark_type=='laser' else 0)
            self.refresh()
        self.child.accepted.connect(apply);self.child.open()
