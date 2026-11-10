"""One export dialog with actual processing facts and independent encoding quality."""
from pathlib import Path
from film_foundry.ui.qt import QtCore,QtWidgets
from film_foundry.ui.curve_editor import text
from film_foundry.app.material_processing import processing_info
from film_foundry.app.output_destination import default_output_directory


class MaterialExportDialog(QtWidgets.QDialog):
    def __init__(self, session, kind, view, body_index, parent=None):
        super().__init__(parent)
        self.session=session; self.kind=kind; self.selection=None
        self.setWindowTitle(text('导出 · 实验中', 'Export · Experimental')); self.resize(680,560)
        layout=QtWidgets.QVBoxLayout(self)
        form=QtWidgets.QFormLayout(); layout.addLayout(form)
        self.info=processing_info(session,body_index)
        from film_foundry.ui.desktop_library import FAMILIES
        family=next(text(zh,en) for key,zh,en in FAMILIES if key==session.kind)
        def row(zh,en,value):
            label=QtWidgets.QLabel(str(value)); label.setWordWrap(True); form.addRow(text(zh,en),label)
        row('材料','Material',family+' · '+text('实验中','Experimental'))
        row('处理质量','Processing quality',text('完整算法 · float32 计算','Full processing · float32 computation'))
        if self.info['storage_precision']:row('来源精度','Source precision',text('含 FP16 外观字段','Includes FP16 appearance fields'))
        size=self.info['working_size']
        row('实际形成尺寸','Actual formation size',' × '.join(map(str,size))+' px' if size else '—')
        source=self.info['source_size']
        row('原始尺寸','Source size',' × '.join(map(str,source))+' px' if source else text('未记录','Not recorded'))
        from film_foundry.ui.sheet_lab_window import STAGES as sheet_stages
        from film_foundry.ui.material_lab_window import STAGES as other_stages
        stage=self.info['stage']
        stage_name=text(*sheet_stages[stage]) if stage in sheet_stages else text(other_stages.get(stage,stage),stage)
        row('处理阶段','Process stage',stage_name)
        row('来源','Source',session.source_name or '—')
        row('成片旋转','Image rotation',str(session.scan_adjustments.get('rotation_quarters',0)*90)+'°')
        row('观看','Viewing',text('曝光补偿','Exposure')+f' {view.exposure_ev:+g} EV · '+(view.border_style if view.include_border else text('无白边','No border')))
        details=QtWidgets.QToolButton(); details.setText(text('基础配置','Configuration')); details.setCheckable(True)
        details.setArrowType(QtCore.Qt.ArrowType.RightArrow);details.setToolButtonStyle(QtCore.Qt.ToolButtonStyle.ToolButtonTextBesideIcon);layout.addWidget(details)
        details.toggled.connect(lambda opened:details.setArrowType(QtCore.Qt.ArrowType.DownArrow if opened else QtCore.Qt.ArrowType.RightArrow))
        config=QtWidgets.QWidget(); config.hide(); layout.addWidget(config)
        configuration=QtWidgets.QFormLayout(config)
        state=session.state
        if state:
            for key,zh,en in [('kind','乳剂类型','Emulsion'),('support','承载方式','Support'),('transfer','转移方式','Transfer'),
                              ('half_exposure','半响应曝光','Half-response exposure'),('maximum_density','单位物质密度','Density per unit'),
                              ('response_contrast','响应反差','Response contrast'),('material_long_edge_mm','材料长边 mm','Material long edge mm')]:
                if hasattr(state.material,key):
                    value=getattr(state.material,key)
                    from film_foundry.ui.sheet_lab_window import CHOICES
                    choices=CHOICES.get(key,())
                    value=next((text(zh,en) for code,zh,en in choices if code==value),value)
                    if key=='transfer':value=text('彩色染料转移','Dye transfer') if value=='dye' else text('银化合物转移','Silver-compound transfer')
                    configuration.addRow(text(zh,en),QtWidgets.QLabel(str(value)))
        configuration.addRow(text('随机种子','Seed'),QtWidgets.QLabel(str(self.info['seed']) if self.info['seed'] is not None else '—'))
        configuration.addRow(text('扫描调整','Scan adjustments'),QtWidgets.QLabel(text('已启用','Enabled') if session.scan_adjustments else text('中性','Neutral')))
        details.toggled.connect(config.setVisible)
        output=QtWidgets.QFormLayout(); layout.addLayout(output)
        self.format=QtWidgets.QComboBox()
        formats=[('PNG · 8-bit',('.png',8)),('PNG · 16-bit',('.png',16)),('TIFF · 8-bit',('.tif',8)),('TIFF · 16-bit',('.tif',16)),('JPEG',('.jpg',8))] if kind=='png' else [(text('材料存档 · NPZ','Material archive · NPZ'),('.ffmaterial.npz',32))]
        for label,value in formats:self.format.addItem(label,value)
        output.addRow(text('输出格式','Output format'),self.format)
        self.precision=QtWidgets.QComboBox()
        self.precision.addItem(text('自动混合精度','Automatic mixed precision'),'auto')
        self.precision.addItem(text('全高精度','Full precision'),'full')
        self.precision.setCurrentIndex(self.precision.findData(session.storage_precision))
        if kind!='png':output.addRow(text('材料保存精度','Material storage precision'),self.precision)
        else:self.precision.hide()
        self.quality=QtWidgets.QComboBox()
        for zh,en,value in [('高 · 100','High · 100',100),('中 · 70','Medium · 70',70),('低 · 40','Low · 40',40)]:self.quality.addItem(text(zh,en),value)
        output.addRow(text('JPEG 编码质量','JPEG encoding quality'),self.quality)
        self.quality.setToolTip(text('编码质量只影响输出文件，不改变材料形成质量。100 仍是有损 JPEG。','Encoding quality only affects the output file. JPEG 100 remains lossy.'))
        self.path=QtWidgets.QLineEdit(str(default_output_directory()/(session.kind+'_'+('image' if kind=='png' else kind)+formats[0][1][0])))
        output.addRow(text('导出位置','Export location'),self.path)
        browse=QtWidgets.QPushButton(text('浏览…','Browse…')); output.addRow('',browse); browse.clicked.connect(self.browse)
        self.format.currentIndexChanged.connect(self._format_changed); self._format_changed()
        self.status=QtWidgets.QLabel(); self.status.setWordWrap(True); layout.addWidget(self.status)
        buttons=QtWidgets.QDialogButtonBox(QtWidgets.QDialogButtonBox.StandardButton.Save|QtWidgets.QDialogButtonBox.StandardButton.Cancel)
        buttons.button(QtWidgets.QDialogButtonBox.StandardButton.Save).setText(text('导出','Export'))
        buttons.button(QtWidgets.QDialogButtonBox.StandardButton.Cancel).setText(text('取消','Cancel'))
        layout.addWidget(buttons); buttons.accepted.connect(self.apply); buttons.rejected.connect(self.reject)

    def _format_changed(self,*_):
        suffix,_=self.format.currentData(); self.quality.setEnabled(suffix=='.jpg')
        path=Path(self.path.text())
        if self.kind=='png' and path.suffix.lower() in ('.png','.jpg','.jpeg','.tif','.tiff'):
            self.path.setText(str(path.with_suffix(suffix)))

    def browse(self):
        suffix,_=self.format.currentData()
        path,_=QtWidgets.QFileDialog.getSaveFileName(self,text('导出位置','Export location'),self.path.text(),f'Output (*{suffix})')
        if path:self.path.setText(path)

    def apply(self):
        value=self.path.text().strip()
        if not value:self.status.setText(text('请选择导出位置','Choose an export location'));return
        path=Path(value).expanduser(); suffix,depth=self.format.currentData()
        if not str(path).lower().endswith(suffix):
            path=path.with_suffix(suffix) if self.kind=='png' else Path(str(path)+suffix)
        try:
            self.session._protect_bundle_member(path)
            if self.session.source_path and path.resolve()==Path(self.session.source_path).resolve():raise ValueError(text('不能覆盖原始来源','Cannot overwrite the source'))
            if path.exists() and path.is_dir():raise ValueError(text('请选择文件路径','Choose a file path'))
        except (ValueError,OSError) as exc:self.status.setText(str(exc));return
        targets=(path,path.with_suffix(path.suffix+'.json')) if self.kind=='png' else (path,)
        if any(p.exists() for p in targets):
            if QtWidgets.QMessageBox.question(self,text('替换输出','Replace output'),text('文件或配套说明已存在，是否替换？','The output or its sidecar exists. Replace it?'))!=QtWidgets.QMessageBox.StandardButton.Yes:return
        if self.kind!='png':self.session.storage_precision=self.precision.currentData()
        self.selection=(str(path),depth,self.quality.currentData());self.accept()
