"""Single-window plate composition and print delivery over the public app API."""
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import numpy as np
from film_foundry.ui.qt import QtCore,QtGui,QtWidgets
from film_foundry.ui.curve_editor import text
from film_foundry.ui.image_view import ImageView
from film_foundry.app.printmaking import PrintmakingSession
from film_foundry.app.print_export import export_print


class _Signals(QtCore.QObject):
    done=QtCore.Signal(object,object)


class PrintmakingWorkspace(QtWidgets.QWidget):
    closed=QtCore.Signal()
    result_ready=QtCore.Signal(object)
    def __init__(self,parent=None):
        super().__init__(parent);self.session=PrintmakingSession();self.busy=False;self._closing=False
        self.pool=ThreadPoolExecutor(max_workers=1,thread_name_prefix='printmaking');self.signals=_Signals(self);self.signals.done.connect(self._done)
        root=QtWidgets.QVBoxLayout(self);bar=QtWidgets.QHBoxLayout();root.addLayout(bar)
        for zh,en,fn in [('打开图像','Open image',self.open_image),('打开分版工程','Open plate project',self.load_project)]:
            b=QtWidgets.QPushButton(text(zh,en));b.clicked.connect(fn);bar.addWidget(b)
        self.title=QtWidgets.QLabel(text('印刷与创意制版','Print & plate studio'));bar.addWidget(self.title,1)
        self.mode=QtWidgets.QComboBox();self.mode.addItems([text('图像直出','Direct image output'),text('创意叠印','Creative overprint')]);bar.addWidget(self.mode)
        split=QtWidgets.QSplitter();root.addWidget(split,1)
        self.controls=QtWidgets.QWidget();form=QtWidgets.QFormLayout(self.controls);split.addWidget(self.controls)
        self.layers=QtWidgets.QListWidget();self.layers.setMinimumHeight(120);self.layers.setMaximumHeight(128);self.layers.currentRowChanged.connect(self.select_layer);form.addRow(text('分版','Plates'),self.layers)
        row=QtWidgets.QHBoxLayout()
        for label,kind in [('CMY','cmy'),(text('单色','Mono'),'mono')]:
            b=QtWidgets.QPushButton(label);b.clicked.connect(lambda _,m=kind:self.reseparate(m));row.addWidget(b)
        form.addRow(row)
        b=QtWidgets.QPushButton(text('移除所选版','Remove selected plate'));b.clicked.connect(self.remove_plate);form.addRow(b)
        self.components=QtWidgets.QComboBox();form.addRow(text('材料组分','Components'),self.components)
        b=QtWidgets.QPushButton(text('添加材料组分','Add component'));b.clicked.connect(self.add_component);form.addRow(b)
        b=QtWidgets.QPushButton(text('导入覆盖图','Import coverage'));b.clicked.connect(self.import_plate);form.addRow(b)
        self.enabled=QtWidgets.QCheckBox(text('参与叠印','Include plate'));form.addRow(self.enabled)
        self.pattern=QtWidgets.QComboBox()
        for key,zh,en in [('dots','网点','Dots'),('lines','排线','Hatching'),('grid','交叉网格','Crosshatch'),('continuous','连续调','Continuous')]:self.pattern.addItem(text(zh,en),key)
        form.addRow(text('纹理','Screen'),self.pattern)
        self.ink=QtWidgets.QPushButton();self.ink.clicked.connect(self.pick_ink);form.addRow(text('油墨','Ink'),self.ink)
        self.numbers={}
        for key,zh,en,lo,hi,value in [('spacing_mm','间距 mm','Spacing mm',.05,20,1.2),('angle','角度 °','Angle °',-180,180,15),('opacity','墨量','Opacity',0,1,1),('gain','覆盖增益','Coverage gain',0,4,1),('offset_x_mm','横向错版 mm','Horizontal register mm',-50,50,0),('offset_y_mm','纵向错版 mm','Vertical register mm',-50,50,0)]:
            spin=QtWidgets.QDoubleSpinBox();spin.setRange(lo,hi);spin.setValue(value);spin.setDecimals(3);spin.setKeyboardTracking(False);self.numbers[key]=spin
            if key in ('opacity','gain'):
                row=QtWidgets.QHBoxLayout();slider=QtWidgets.QSlider(QtCore.Qt.Orientation.Horizontal);slider.setRange(round(lo*1000),round(hi*1000));slider.setValue(round(value*1000))
                slider.valueChanged.connect(lambda v,c=spin:c.setValue(v/1000));spin.valueChanged.connect(lambda v,c=slider:c.setValue(round(v*1000)))
                row.addWidget(slider,1);row.addWidget(spin);form.addRow(text(zh,en),row)
            else:form.addRow(text(zh,en),spin)
        self.invert=QtWidgets.QCheckBox(text('反转覆盖','Invert coverage'));form.addRow(self.invert)
        self.paper_button=QtWidgets.QPushButton(text('纸色…','Paper colour…'));self.paper_button.clicked.connect(self.pick_paper);form.addRow(self.paper_button)
        self.registration=QtWidgets.QCheckBox(text('分版加套准标记','Registration marks'));self.registration.setChecked(True);form.addRow(self.registration)
        self.controls.setMinimumWidth(270);scroll=QtWidgets.QScrollArea();scroll.setWidgetResizable(True);scroll.setWidget(self.controls);split.insertWidget(0,scroll)
        self.viewer=ImageView();split.addWidget(self.viewer);split.setSizes([290,760])
        output=QtWidgets.QHBoxLayout();root.addLayout(output)
        self.dpi=QtWidgets.QSpinBox();self.dpi.setRange(36,2400);self.dpi.setValue(300);output.addWidget(QtWidgets.QLabel('DPI'));output.addWidget(self.dpi)
        self.size=QtWidgets.QLabel();output.addWidget(self.size,1);self.dpi.valueChanged.connect(self.size_changed)
        self.render_button=QtWidgets.QPushButton(text('更新预览','Update preview'));self.render_button.clicked.connect(self.render);output.addWidget(self.render_button)
        self.export_button=QtWidgets.QPushButton(text('导出 TIFF…','Export TIFF…'));self.export_button.clicked.connect(self.export_dialog);output.addWidget(self.export_button)
        self.project_button=QtWidgets.QPushButton(text('保存分版工程…','Save plates…'));self.project_button.clicked.connect(self.save_project);output.addWidget(self.project_button)
        self.status=QtWidgets.QLabel();self.status.setWordWrap(True);root.addWidget(self.status)
        self.mode.currentIndexChanged.connect(self.show_source)
        self._selected=-1;self._ink=(0.,0.,0.)
        self._loading_controls=False
        for control in self.numbers.values():control.valueChanged.connect(self.mark_dirty)
        self.enabled.toggled.connect(self.mark_dirty);self.invert.toggled.connect(self.mark_dirty)
        self.pattern.currentIndexChanged.connect(self.mark_dirty);self.dpi.valueChanged.connect(self.mark_dirty)
        self.setToolTip(text('创意 CMY 和透明墨叠印是版画效果，不是印厂 CMYK 分色。覆盖图：白表示满墨；导出分版：黑表示印刷区域。尺寸只由像素与 DPI 决定，不放大材料。','Creative CMY and transparent inks are artistic, not press CMYK separation. Imported white means full coverage; exported black means ink. DPI sets physical size without upsampling.'))
        self.size_changed()

    def _run(self,fn):
        if self.busy:return
        self.busy=True;self.setEnabled(False);self.status.setText(text('处理中…','Processing…'))
        future=self.pool.submit(fn)
        def done(f):
            try:result=f.result();error=None
            except Exception as exc:result=None;error=str(exc)
            self.signals.done.emit(result,error)
        future.add_done_callback(done)

    def _done(self,result,error):
        self.busy=False;self.setEnabled(True)
        if error:self.status.setText(error)
        else:
            if isinstance(result,dict) and 'image' in result:
                self.set_source(**result)
            elif isinstance(result,dict) and 'artifacts' in result:self.result_ready.emit(result)
            if getattr(self,'_loading_project',False):
                self._selected=-1
                blockers=[QtCore.QSignalBlocker(self.mode),QtCore.QSignalBlocker(self.dpi)]
                self.mode.setCurrentIndex(1);self.dpi.setValue(round(self.session.dpi))
                self.components.clear();self.title.setText(text('分版工程','Plate project'))
                self.paper_button.setText(QtGui.QColor.fromRgbF(*self.session.paper).name())
                self.registration.setChecked(self.session.registration)
                del blockers
            if isinstance(result,np.ndarray):self.display(result)
            self.status.setText(text('完成','Completed'));self.rebuild_layers()
        self._loading_project=False
        callback=getattr(self,'_work_saved_callback',None);self._work_saved_callback=None
        if callback:callback(error is None)
        if self._closing:self.close()

    def set_source(self,image,provenance=None,components=None):
        if self.busy:raise ValueError('Print studio is busy')
        self._selected=-1;self.session.set_image(image,provenance,components)
        blocker=QtCore.QSignalBlocker(self.mode);self.mode.setCurrentIndex(0);del blocker
        self.title.setText(str((provenance or {}).get('name') or text('印刷与创意制版','Print & plate studio')))
        self.components.clear();self.components.addItems(list(self.session.components));self.rebuild_layers();self.display(self.session.image)

    def open_image(self):
        path,_=QtWidgets.QFileDialog.getOpenFileName(self,text('选择图像','Choose image'),'','Images (*.png *.jpg *.jpeg *.tif *.tiff *.dng)')
        if path:
            self.load_asset({'path':path,'kind':'source'})

    def load_asset(self,asset,*,film_config=None,_approved=False):
        if not _approved and hasattr(self,'protect_replacement'):
            return self.protect_replacement(lambda:self.load_asset(asset,film_config=film_config,_approved=True))
        from film_foundry.app.print_sources import load_print_source
        self._run(lambda:load_print_source(asset,film_config=film_config))

    def commit_layer(self):
        if 0<=self._selected<len(self.session.styles):
            s=self.session.styles[self._selected];s.enabled=self.enabled.isChecked();s.pattern=self.pattern.currentData();s.ink=self._ink;s.invert=self.invert.isChecked()
            for key,spin in self.numbers.items():setattr(s,key,spin.value())
        self.session.dpi=self.dpi.value()

    def select_layer(self,row):
        self.commit_layer();self._selected=row
        if row<0:return
        self._loading_controls=True
        s=self.session.styles[row];self.enabled.setChecked(s.enabled);self.pattern.setCurrentIndex(self.pattern.findData(s.pattern));self._ink=s.ink;self.invert.setChecked(s.invert)
        for key,spin in self.numbers.items():spin.setValue(getattr(s,key))
        self.ink.setText(QtGui.QColor.fromRgbF(*s.ink).name())
        self._loading_controls=False

    def rebuild_layers(self,selected=None):
        row=max(0,self._selected) if selected is None else selected
        self._selected=-1;self.layers.clear();self.layers.addItems(self.session.names);self.layers.setCurrentRow(min(row,len(self.session.names)-1));self.size_changed()

    def mark_dirty(self,*_):
        if not self._loading_controls and self.session.image is not None:
            self.status.setText(text('设置已更改 · 更新预览后查看','Settings changed · Update preview to view'))

    def pick_ink(self):
        color=QtWidgets.QColorDialog.getColor(QtGui.QColor.fromRgbF(*self._ink),self)
        if color.isValid():self._ink=color.getRgbF()[:3];self.ink.setText(color.name());self.mark_dirty()

    def pick_paper(self):
        color=QtWidgets.QColorDialog.getColor(QtGui.QColor.fromRgbF(*self.session.paper),self)
        if color.isValid():self.session.paper=color.getRgbF()[:3];self.paper_button.setText(color.name());self.mark_dirty()

    def remove_plate(self):
        if len(self.session.plates)<=1:
            self.status.setText(text('至少保留一张分版','Keep at least one plate'));return
        row=self.layers.currentRow()
        if row>=0:
            self.commit_layer()
            for values in (self.session.plates,self.session.styles,self.session.names):values.pop(row)
            self.rebuild_layers()

    def reseparate(self,mode):
        if self.session.image is None:return
        if QtWidgets.QMessageBox.question(self,text('替换分版','Replace plates'),text('以原图重新分版，替换当前分版设置？','Replace the current plates and settings from the source?'))!=QtWidgets.QMessageBox.StandardButton.Yes:return
        self.session.separate(mode);self.rebuild_layers();self.mode.setCurrentIndex(1)

    def add_component(self):
        key=self.components.currentText()
        if key not in self.session.components:return
        self.commit_layer();a=self.session.components[key];self.session.add_plate(a/max(float(a.max()),1e-8),key+' / max');self.rebuild_layers(len(self.session.plates)-1);self.mark_dirty()
        self.components.setToolTip(text('材料库存按本图最大值归一为创意覆盖率；不回写材料或假定是印刷色版。','Normalizes material inventory by its image maximum for creative coverage only. Does not modify the medium or imply a printing separation.'))

    def import_plate(self):
        path,_=QtWidgets.QFileDialog.getOpenFileName(self,text('导入覆盖图（白=满墨）','Import coverage (white = ink)'),'','Images (*.png *.tif *.tiff)')
        if path:
            from film_foundry.core.io_utils import load_image
            try:self.commit_layer();self.session.add_plate(load_image(path).mean(axis=-1),Path(path).name,source_path=path);self.rebuild_layers(len(self.session.plates)-1);self.mark_dirty()
            except Exception as exc:self.status.setText(str(exc))

    def render(self):
        if self.session.image is None:return
        self.commit_layer();creative=self.mode.currentIndex()==1
        self._run(lambda:self.session.render()[0] if creative else self.session.image)

    def show_source(self):
        if self.session.image is not None:self.render()

    def display(self,image):
        self.viewer.show_array(image)

    def size_changed(self):
        a=self.session.image
        self.size.setText('' if a is None else f'{a.shape[1]} × {a.shape[0]} px · {a.shape[1]*25.4/self.dpi.value():.1f} × {a.shape[0]*25.4/self.dpi.value():.1f} mm')
        self.controls.setEnabled(a is not None and self.mode.currentIndex()==1)
        for b in (self.render_button,self.export_button,self.project_button):b.setEnabled(a is not None)
        self.project_button.setEnabled(a is not None and self.mode.currentIndex()==1)

    def export_dialog(self):
        from film_foundry.app.output_destination import default_output_directory
        from datetime import datetime
        default=default_output_directory()/('print-'+datetime.now().strftime('%H%M%S')+'.tif')
        path,kind=QtWidgets.QFileDialog.getSaveFileName(self,text('印刷输出','Print output'),str(default),'RGB 16-bit TIFF (*.tif);;CMYK ICC 8-bit TIFF (*.tif)')
        if not path:return
        profile=None
        if kind.startswith('CMYK'):
            profile,_=QtWidgets.QFileDialog.getOpenFileName(self,text('选择印厂 CMYK ICC','Choose press CMYK ICC'),'','ICC (*.icc *.icm)')
            if not profile:return
        path=str(Path(path).with_suffix('.tif'));self.commit_layer();creative=self.mode.currentIndex()==1
        def export():
            from film_foundry.app.print_sources import print_result_record
            export_print(self.session.render()[0] if creative else self.session.image,path,dpi=self.session.dpi,cmyk_profile=profile,metadata=self.session.provenance,protected_paths=self.session.protected_paths)
            return print_result_record(path,self.session.provenance)
        self._run(export)

    def save_project(self):
        from film_foundry.app.output_destination import default_output_directory
        from datetime import datetime
        default=default_output_directory()/('plates-'+datetime.now().strftime('%H%M%S'))
        path,_=QtWidgets.QFileDialog.getSaveFileName(self,text('新建分版文件夹','New plate folder'),str(default),'Folder (*)')
        if path:
            if Path(path).exists():self.status.setText(text('请选择尚不存在的文件夹名称','Choose a new folder name'));return
            self.commit_layer();registration=self.registration.isChecked()
            def save():
                from film_foundry.app.print_sources import print_result_record
                project=self.session.save(path,registration=registration)
                return print_result_record(project.parent/'composite.tif',self.session.provenance,artifacts={'project':str(project)})
            self._run(save)

    def load_project(self):
        path,_=QtWidgets.QFileDialog.getOpenFileName(self,text('打开分版工程','Open plate project'),'','Project (project.json)')
        if path:self.open_project(path)

    def open_project(self,path,_approved=False):
        if not _approved and hasattr(self,'protect_replacement'):
            return self.protect_replacement(lambda:self.open_project(path,True))
        if path:
            self._selected=-1;self._loading_project=True
            def load():self.session.load(path);return self.session.result
            self._run(load)

    def save_work(self,path,completed):
        if self.busy:return False
        self.commit_layer();registration=self.registration.isChecked()
        from film_foundry.app.desktop_settings import remember_work
        def saved(ok):
            if ok:
                try:remember_work(Path(path)/'project.json','print',self.session.source_name or text('制版工作','Plate work'))
                except OSError as exc:self.status.setText(str(exc));ok=False
            completed(ok)
        self._work_saved_callback=saved
        self._run(lambda:self.session.save(path,registration=registration))
        return True

    def closeEvent(self,event):
        if self.busy:self._closing=True;event.ignore();return
        self.pool.shutdown(wait=False,cancel_futures=True);self.closed.emit();event.accept()
