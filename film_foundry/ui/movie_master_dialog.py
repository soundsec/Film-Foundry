"""Material master frontend. No film formulas, codecs or video editor state."""
from __future__ import annotations
import copy
from pathlib import Path
from threading import Thread
from uuid import uuid4
import numpy as np

from film_foundry.app.control import CancelFlag, WorkflowControl, WorkflowCancelled
from film_foundry.ui.qt import QtCore, QtGui, QtWidgets
from film_foundry.ui.curve_editor import text


class MovieMasterDialog(QtWidgets.QDialog):
    finished_job = QtCore.Signal(object)
    frame_progress = QtCore.Signal(int)

    def __init__(self,config,parent=None):
        super().__init__(parent)
        self.config=copy.deepcopy(config)
        self.running=False
        self.setWindowTitle(text('材料母带','Material movie master'))
        self.resize(720,540)
        layout=QtWidgets.QVBoxLayout(self)
        note=QtWidgets.QLabel(text(
            '视频导入为逐帧潜影母带；冲洗另存光学母带，原潜影可反复使用。观察只读取所选帧。\n'
            '使用打开窗口时的配方与计算设置。每帧保存现有NPZ，时间戳保留，占用可能很大。\n'
            '首版视频输入限SDR；不输出成片视频，不处理音轨。',
            'Import video as a latent material master. Development creates a separate optical master; sources remain reusable.\n'
            'Uses the recipe/computation settings frozen when opened. Existing NPZ per frame, original timestamps; storage can be large.\n'
            'Initial video ingest is SDR. No encoded movie or audio processing.'))
        note.setWordWrap(True);layout.addWidget(note)
        self.mode=QtWidgets.QComboBox()
        self.mode.addItems([text('视频 → 潜影母带','Video → latent master'),text('潜影母带 → 冲洗光学母带','Latent → developed optical master'),text('观察母带单帧','Observe a master frame'),text('导出材料母带 ZIP','Export material master ZIP')])
        layout.addWidget(self.mode)
        self.source=QtWidgets.QLineEdit();self.output=QtWidgets.QLineEdit()
        self.choose_buttons=[]
        for edit,label,callback in [(self.source,text('选择来源','Choose source'),self.choose_source),
                                    (self.output,text('新母带位置','New master location'),self.choose_output)]:
            row=QtWidgets.QHBoxLayout();row.addWidget(edit)
            button=QtWidgets.QPushButton(label);button.clicked.connect(callback);row.addWidget(button)
            self.choose_buttons.append(button);layout.addLayout(row)
        frame_row=QtWidgets.QHBoxLayout()
        frame_row.addWidget(QtWidgets.QLabel(text('帧序号（从1开始）','Frame index (starts at 1)')))
        self.frame=QtWidgets.QSpinBox();self.frame.setRange(1,100000000);frame_row.addWidget(self.frame)
        layout.addLayout(frame_row)
        self.preview=QtWidgets.QLabel(text('光学母带可在此观察所选帧','Select an optical master to observe a frame'))
        self.preview.setAlignment(QtCore.Qt.AlignmentFlag.AlignCenter)
        self.preview.setMinimumHeight(180)
        self.preview.setStyleSheet('background:#242424;color:#ddd;')
        layout.addWidget(self.preview,1)
        self.status=QtWidgets.QLabel(text('等待材料来源','Ready for a material source'))
        self.status.setWordWrap(True);layout.addWidget(self.status)
        self.start=QtWidgets.QPushButton(text('执行','Run'));self.start.clicked.connect(self.run)
        layout.addWidget(self.start)
        self.mode.currentIndexChanged.connect(self.controls)
        self.finished_job.connect(self.receive)
        self.frame_progress.connect(lambda n:self.status.setText(text(f'正在处理第 {n} 帧',f'Processing frame {n}')))
        self.controls()

    def controls(self):
        observe=self.mode.currentIndex() == 2
        export=self.mode.currentIndex() == 3
        self.choose_buttons[1].setText(text('新ZIP文件','New ZIP file') if export else text('新母带位置','New master location'))
        self.output.setEnabled(not self.running and not observe)
        self.frame.setEnabled(not self.running and observe)
        self.source.setEnabled(not self.running);self.mode.setEnabled(not self.running)
        for i,button in enumerate(self.choose_buttons): button.setEnabled(not self.running and (i == 0 or not observe))

    def choose_source(self):
        if self.mode.currentIndex() == 0:
            path,_=QtWidgets.QFileDialog.getOpenFileName(self,text('导入视频','Import video'),'','Video (*.mp4 *.mov *.mkv *.webm *.avi);;All files (*)')
        else:
            path,_=QtWidgets.QFileDialog.getOpenFileName(self,text('选择母带清单','Choose master manifest'),'','Material master (master.json)')
        if path:
            self.source.setText(path)
            parent=Path(path).parent
            if Path(path).name == 'master.json': parent=parent.parent
            self.output.setText(str(parent/('material-master-'+uuid4().hex[:8]+('.zip' if self.mode.currentIndex() == 3 else ''))))

    def choose_output(self):
        if self.mode.currentIndex() == 3:
            path,_=QtWidgets.QFileDialog.getSaveFileName(self,text('导出为新ZIP文件','Export as a new ZIP file'),self.output.text(),'ZIP (*.zip)')
            if path: self.output.setText(path)
            return
        parent=QtWidgets.QFileDialog.getExistingDirectory(self,text('选择新母带的上级目录','Choose parent of the new master'))
        if parent: self.output.setText(str(Path(parent)/('material-master-'+uuid4().hex[:8])))

    def run(self):
        if self.running:
            self.flag.cancel();self.start.setEnabled(False)
            self.status.setText(text('等待当前帧结束并清理…','Cancelling after the current frame…'))
            return
        mode=self.mode.currentIndex()
        if not self.source.text().strip() or (mode != 2 and not self.output.text().strip()):
            self.status.setText(text('请选择来源和新的母带目录','Choose a source and a new master directory'));return
        source,output=Path(self.source.text()),Path(self.output.text())
        index=self.frame.value()
        self.running=True;self.flag=CancelFlag();self.controls()
        self.start.setText(text('取消','Cancel'));self.status.setText(text('正在读取材料…','Reading material…'))
        def worker():
            count=0
            def progress(event):
                nonlocal count
                count+=1;self.frame_progress.emit(count)
            control=WorkflowControl(cancel_requested=self.flag.requested,on_progress=progress)
            try:
                from film_foundry.app.movie_master import import_video_master,develop_movie_master,observe_master_frame
                if mode == 0: value=import_video_master(source,output,self.config,control=control)
                elif mode == 1: value=develop_movie_master(source,output,self.config,control=control)
                elif mode == 3:
                    from film_foundry.app.movie_zip import export_master_zip
                    value=export_master_zip(source,output,control=control)
                else:
                    control.check_cancel();value=observe_master_frame(source,index,self.config).output_srgb
                    control.check_cancel()
                result=(True,value,mode)
            except WorkflowCancelled:
                result=(False,text('已取消，未提交本次母带','Cancelled; no master committed'),mode)
            except Exception as exc:
                result=(False,str(exc),mode)
            self.finished_job.emit(result)
        Thread(target=worker,daemon=True).start()

    @QtCore.Slot(object)
    def receive(self,result):
        success,value,mode=result
        self.running=False;self.start.setEnabled(True);self.start.setText(text('执行','Run'));self.controls()
        if not success: self.status.setText(value);return
        if mode == 2:
            rgb=np.ascontiguousarray(np.clip(value,0,1)*255,dtype=np.uint8)
            h,w=rgb.shape[:2]
            image=QtGui.QImage(rgb.data,w,h,rgb.strides[0],QtGui.QImage.Format.Format_RGB888).copy()
            pixmap=QtGui.QPixmap.fromImage(image)
            self.preview.setPixmap(pixmap.scaled(self.preview.size(),QtCore.Qt.AspectRatioMode.KeepAspectRatio,QtCore.Qt.TransformationMode.SmoothTransformation))
            self.status.setText(text('只读观察完成，母带保持不变','Observation complete; master unchanged'))
        elif mode == 3:
            self.status.setText(text('材料母带ZIP已保存：','Material master ZIP saved: ')+str(value))
        else:
            self.status.setText(text('新母带已保存：','New master saved: ')+str(value))
            self.source.setText(str(value));self.mode.setCurrentIndex(mode+1)
            self.output.setText(str(Path(value).parent/('material-master-'+uuid4().hex[:8])))

    def reject(self):
        if self.running:
            self.flag.cancel();self.status.setText(text('正在取消，清理完成后可关闭','Cancelling; close after cleanup'))
        else: super().reject()

    def closeEvent(self,event):
        if self.running: self.reject();event.ignore()
        else: event.accept()
