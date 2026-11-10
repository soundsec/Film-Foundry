"""Continuous roll strips and a read-only, coalesced light-table viewer."""
from concurrent.futures import ThreadPoolExecutor
from film_foundry.ui.qt import QtCore,QtGui,QtWidgets
from film_foundry.ui.curve_editor import text
from film_foundry.ui.desktop_library import thumbnail
from film_foundry.ui.image_view import ImageView
from film_foundry.app.roll_presentation import roll_segments,RollLightTable


class _Signals(QtCore.QObject):
    ready=QtCore.Signal(int,object,object)
    exported=QtCore.Signal(object,object)


class RollStripView(QtWidgets.QScrollArea):
    def __init__(self,parent=None):
        super().__init__(parent);self.setWidgetResizable(True);self.selected=1;self.generation=0;self.buttons={}
        self.pool=ThreadPoolExecutor(max_workers=1,thread_name_prefix='roll-strips');self.signals=_Signals(self)
        self.signals.ready.connect(self.ready);self.pending=[]
        self.destroyed.connect(lambda *_,pool=self.pool:pool.shutdown(wait=False,cancel_futures=True))

    def display(self,data,length):
        self.generation+=1;generation=self.generation
        for future in self.pending:future.cancel()
        self.pending=[];self.buttons={}
        old=self.takeWidget()
        if old:old.deleteLater()
        body=QtWidgets.QWidget();layout=QtWidgets.QVBoxLayout(body);layout.setSpacing(18)
        self.group=QtWidgets.QButtonGroup(body);self.group.setExclusive(True)
        for segment in roll_segments(data,length):
            strip=QtWidgets.QFrame();strip.setStyleSheet('QFrame {background:#222426;border:1px solid #555;} QToolButton {border:1px solid #484a4c;color:#eee;} QToolButton:checked {border:2px solid #e8b665;}')
            row=QtWidgets.QHBoxLayout(strip);row.setSpacing(3);row.setContentsMargins(12,10,12,10)
            for frame in segment:
                index=frame['frame_index'];button=QtWidgets.QToolButton();button.setCheckable(True)
                button.setToolButtonStyle(QtCore.Qt.ToolButtonStyle.ToolButtonTextUnderIcon);button.setIconSize(QtCore.QSize(144,104));button.setMinimumSize(96,130)
                button.setSizePolicy(QtWidgets.QSizePolicy.Policy.Expanding,QtWidgets.QSizePolicy.Policy.Fixed)
                button.setText(f"{index:02d}"+(' · '+text('未曝光','Unexposed') if frame.get('unexposed') else ''))
                button.setToolTip(text('选择底片后打开灯台；分段不裁切介质。','Select a frame, then open the light table. Segments do not cut the medium.'))
                button.clicked.connect(lambda _,i=index:setattr(self,'selected',i))
                self.group.addButton(button,index);row.addWidget(button,1);self.buttons[index]=button
                paths=frame['artifacts'];path=paths.get('negative_visual_preview') or paths.get('positive_visual_preview')
                if path:
                    signals=self.signals
                    def load(p=path,i=index,g=generation,s=signals):
                        img=thumbnail(p)
                        try:s.ready.emit(g,i,img)
                        except RuntimeError:pass
                    self.pending.append(self.pool.submit(load))
            for _ in range(length-len(segment)):
                spacer=QtWidgets.QWidget();spacer.setMinimumWidth(96);row.addWidget(spacer,1)
            layout.addWidget(strip)
        layout.addStretch();self.setWidget(body)
        if self.selected not in self.buttons:self.selected=next(iter(self.buttons),1)
        if self.selected in self.buttons:self.buttons[self.selected].setChecked(True)

    def ready(self,generation,index,image):
        if generation==self.generation and index in self.buttons and not image.isNull():
            self.buttons[index].setIcon(QtGui.QIcon(QtGui.QPixmap.fromImage(image)))


class RollLightTableDialog(QtWidgets.QDialog):
    workspace_closed=QtCore.Signal()
    def __init__(self,data=None,index=1,parent=None,*,medium_path=None):
        super().__init__(parent);self.setWindowTitle(text('底片灯台','Film light table'));self.resize(1080,760)
        self.setAttribute(QtCore.Qt.WidgetAttribute.WA_DeleteOnClose)
        self.records={1:{'medium':str(medium_path)}} if medium_path is not None else {f['frame_index']:data['frames'][str(f['frame_index'])] for f in data['roll']['frames']}
        self.viewer=RollLightTable();self.pool=ThreadPoolExecutor(max_workers=1,thread_name_prefix='roll-light-table')
        self.signals=_Signals(self);self.signals.ready.connect(self.ready);self.busy=False;self.revision=0;self.closed=False;self._closing=False
        self.exporting=False;self.view_valid=False;self.signals.exported.connect(self.export_ready)
        self.destroyed.connect(lambda *_,pool=self.pool:pool.shutdown(wait=False,cancel_futures=True))
        layout=QtWidgets.QVBoxLayout(self);bar=QtWidgets.QHBoxLayout();layout.addLayout(bar)
        self.frames=QtWidgets.QComboBox()
        for number in self.records:self.frames.addItem(f'{number:02d}',number)
        self.frames.setCurrentIndex(max(0,self.frames.findData(index)))
        for label,delta in (('‹',-1),('›',1)):
            button=QtWidgets.QPushButton(label);button.clicked.connect(lambda _,d=delta:self.frames.setCurrentIndex(max(0,min(self.frames.count()-1,self.frames.currentIndex()+d))));bar.addWidget(button)
        bar.addWidget(self.frames);bar.addStretch()
        self.mode=QtWidgets.QComboBox();self.mode.addItem(text('透射观察','Transmission'),'transmission');self.mode.addItem(text('表面反射','Surface reflection'),'reflection');bar.addWidget(self.mode)
        self.mode.setToolTip(text('反射读取曝光时确定的表面银光学参数；材料编辑器需启用该能力。切换观看不重新冲洗。','Reflection reads frozen surface silver optics, enabled in the material editor before exposure. Switching views never develops again.'))
        self.ev=QtWidgets.QDoubleSpinBox();self.ev.setRange(-5,5);self.ev.setSingleStep(.25);self.ev.setSuffix(' EV')
        self.temperature=QtWidgets.QSpinBox();self.temperature.setRange(2000,12000);self.temperature.setValue(5500);self.temperature.setSingleStep(100);self.temperature.setSuffix(' K')
        bar.addWidget(QtWidgets.QLabel(text('灯台光源','Illumination')));bar.addWidget(self.ev);bar.addWidget(self.temperature)
        viewing=QtWidgets.QHBoxLayout();layout.addLayout(viewing)
        self.angles=QtWidgets.QWidget();angle_bar=QtWidgets.QHBoxLayout(self.angles);angle_bar.setContentsMargins(0,0,0,0);viewing.addWidget(self.angles)
        self.light_angle=QtWidgets.QDoubleSpinBox();self.light_angle.setRange(-80,80);self.light_angle.setValue(30);self.light_angle.setSuffix('°')
        self.view_angle=QtWidgets.QDoubleSpinBox();self.view_angle.setRange(-80,80);self.view_angle.setSuffix('°')
        self.background=QtWidgets.QDoubleSpinBox();self.background.setRange(0,1);self.background.setDecimals(3);self.background.setSingleStep(.02);self.background.setValue(.02)
        for label,control in ((text('入射角','Light angle'),self.light_angle),(text('观看角','Viewing angle'),self.view_angle),(text('背景亮度','Background light'),self.background)):
            angle_bar.addWidget(QtWidgets.QLabel(label));angle_bar.addWidget(control)
        self.angles.hide();self.mode.currentIndexChanged.connect(lambda _:self.angles.setVisible(self.mode.currentData()=='reflection'))
        viewing.addStretch();self.export_button=QtWidgets.QPushButton(text('导出当前观看…','Export this view…'));viewing.addWidget(self.export_button);self.export_button.clicked.connect(self.export_view)
        self.export_button.setToolTip(text('按原始介质尺寸导出PNG，并把当前光源、角度和观看方式记录进文件。','Export an original-size PNG with illumination, angles and viewing mode recorded in the file.'))
        self.image=ImageView();layout.addWidget(self.image,1);self.status=QtWidgets.QLabel();self.status.setWordWrap(True);layout.addWidget(self.status)
        self.timer=QtCore.QTimer(self);self.timer.setSingleShot(True);self.timer.setInterval(100);self.timer.timeout.connect(self.start)
        for signal in (self.frames.currentIndexChanged,self.ev.valueChanged,self.temperature.valueChanged,self.mode.currentIndexChanged,self.light_angle.valueChanged,self.view_angle.valueChanged,self.background.valueChanged):signal.connect(self.schedule)
        self.schedule()

    def schedule(self,*_):
        if self._closing:return
        self.revision+=1;self.view_valid=False;self.export_button.setEnabled(False);self.timer.start()

    def view_settings(self):
        from film_foundry.app.film_observation import FilmViewingSettings
        return FilmViewingSettings(self.mode.currentData(),self.ev.value(),self.temperature.value(),self.light_angle.value(),self.view_angle.value(),self.background.value())

    def start(self):
        if self.busy or self.closed or self._closing:return
        self.busy=True;revision=self.revision;path=self.records[self.frames.currentData()]['medium'];settings=self.view_settings()
        self.status.setText(text('观察中…','Observing…'));viewer=self.viewer;signals=self.signals
        def work():
            image=None;error=None
            try:image=viewer.observe(path,settings.ev,settings.temperature,mode=settings.mode,light_angle=settings.light_angle,view_angle=settings.view_angle,background=settings.background)
            except Exception as exc:error=str(exc)
            try:signals.ready.emit(revision,image,error)
            except RuntimeError:pass
        self.pool.submit(work)

    def ready(self,revision,image,error):
        self.busy=False
        if self.closed:return
        if self._closing:
            if not self.exporting:self.close()
            return
        if revision!=self.revision:self.start();return
        self.status.setText(error or '')
        self.view_valid=image is not None
        self.export_button.setEnabled(self.view_valid and not self.exporting)
        if image is not None:self.image.show_array(image)
        else:self.image.clear_image(text('当前介质无法按此方式观察','This viewing mode is unavailable for this medium'))

    def export_view(self):
        if self.exporting or self.closed or not self.view_valid:return
        from pathlib import Path
        source=self.records[self.frames.currentData()]['medium'];settings=self.view_settings()
        target,_=QtWidgets.QFileDialog.getSaveFileName(self,text('导出观看结果','Export observation'),str(Path(source).with_suffix('.view.png')),'PNG (*.png)')
        if not target:return
        self.exporting=True;self.export_button.setEnabled(False);self.status.setText(text('正在导出原尺寸观看…','Exporting full-size view…'))
        signals=self.signals
        def work():
            error=None
            try:
                from film_foundry.app.film_observation import export_observation
                export_observation(source,target,settings)
            except Exception as exc:error=str(exc)
            try:signals.exported.emit(target,error)
            except RuntimeError:pass
        self.pool.submit(work)

    def export_ready(self,target,error):
        self.exporting=False
        if self.closed:return
        if self._closing:
            if not self.busy:self.close()
            return
        self.export_button.setEnabled(self.view_valid);self.status.setText(error or text('已导出：','Exported: ')+str(target))

    def closeEvent(self,event):
        self._closing=True;self.timer.stop()
        if self.busy or self.exporting:
            self.setEnabled(False);event.ignore();return
        was_closed=self.closed
        self.closed=True;self.pool.shutdown(wait=False,cancel_futures=True);super().closeEvent(event)
        if not was_closed:self.workspace_closed.emit()

    def reject(self):
        # Escape follows the same lifetime as the document close button.
        if self.busy or self.exporting:
            self._closing=True;self.timer.stop();self.setEnabled(False);return
        was_closed=self.closed
        self.closed=True;self.timer.stop();self.pool.shutdown(wait=False,cancel_futures=True)
        super().reject()
        if not was_closed:self.workspace_closed.emit()
