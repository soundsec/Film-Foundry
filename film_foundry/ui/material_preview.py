"""Embeddable diagnostic preview; worker owns the session, never GUI state."""
from concurrent.futures import ThreadPoolExecutor
import copy
from time import perf_counter
import numpy as np

from film_foundry.app.preview_session import PreviewSession
from film_foundry.ui.qt import QtCore, QtGui, QtWidgets
from film_foundry.ui.curve_editor import text


class _Signals(QtCore.QObject):
    ready = QtCore.Signal(int, object, str)


class MaterialPreview(QtWidgets.QWidget):
    def __init__(self, config_provider, parent=None, *, renderer=None):
        super().__init__(parent)
        self.config_provider = config_provider
        self.session = renderer if renderer is not None else PreviewSession()
        self.pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix='material-preview')
        self.signals = _Signals()
        self.signals.ready.connect(self._ready)
        pool = self.pool
        self.destroyed.connect(lambda *_: pool.shutdown(wait=False, cancel_futures=True))
        self.generation = 0
        self.busy = False
        self.pending = None
        self.frames = None
        self.pixmap = None
        layout = QtWidgets.QVBoxLayout(self);layout.setContentsMargins(0,0,0,0)
        row = QtWidgets.QHBoxLayout()
        self.target = QtWidgets.QComboBox()
        for key,zh,en in [('color24','24 色测试卡','24 colour swatches'),('grayscale','灰阶 / 黑白位','Grayscale / endpoints'),('edges','边缘 / 分辨率','Edges / resolution')]:
            self.target.addItem(text(zh,en),key)
        self.target.setToolTip(text('合成 sRGB 标靶，用于比较材料与扫描效果；不是实测校色卡。', 'Synthetic sRGB targets for material and scan comparisons, not a measured calibration chart.'))
        row.addWidget(self.target,1)
        self.refresh = QtWidgets.QPushButton(text('预览','Preview'));row.addWidget(self.refresh)
        layout.addLayout(row)
        row = QtWidgets.QHBoxLayout()
        self.display = QtWidgets.QComboBox()
        for zh,en in [('处理结果','Result'),('原始标靶','Original target'),('并排对比','Side by side')]:self.display.addItem(text(zh,en))
        row.addWidget(self.display,1)
        self.automatic = QtWidgets.QCheckBox(text('自动更新','Auto update'));self.automatic.setChecked(True);row.addWidget(self.automatic)
        layout.addLayout(row)
        self.canvas = QtWidgets.QLabel();self.canvas.setAlignment(QtCore.Qt.AlignmentFlag.AlignCenter)
        self.canvas.setMinimumSize(160,150);self.canvas.setSizePolicy(QtWidgets.QSizePolicy.Policy.Ignored,QtWidgets.QSizePolicy.Policy.Expanding)
        self.canvas.installEventFilter(self);layout.addWidget(self.canvas,1)
        self.status = QtWidgets.QLabel();self.status.setWordWrap(True);layout.addWidget(self.status)
        self.setToolTip(text('长边 480，固定比较种子，沿当前配方完成曝光、冲洗与扫描。只缩小标靶，不启用额外减配；小图不代表原尺寸颗粒与耗时。改扫描设置会复用已形成介质。',
                            '480 px, fixed comparison seed, current exposure, development and scan recipe. Only the target is resized; no extra reduction. Grain and timing are not full-size predictions. Scan-only edits reuse the formed medium.'))
        self.timer = QtCore.QTimer(self);self.timer.setSingleShot(True);self.timer.setInterval(350)
        self.timer.timeout.connect(self.request)
        self.target.currentIndexChanged.connect(self.invalidate)
        self.automatic.toggled.connect(self.invalidate)
        self.display.currentIndexChanged.connect(self._display)
        self.refresh.clicked.connect(self.request)

    def invalidate(self, *_):
        self.generation += 1
        # An old queued request is obsolete even when auto update is disabled.
        self.pending = None
        if self.frames is not None:self.status.setText(text('参数已更改','Settings changed'))
        details = {
            'color24': ('24 个合成 sRGB 色块，包含肤色、自然色、饱和色及六级中性灰；不是实测校色卡。', '24 synthetic sRGB swatches: skin, natural and saturated colours plus six neutrals; not a measured calibration card.'),
            'grayscale': ('连续渐变、21 级灰阶、0–12% 暗部与 88–100% 高光。用于观察黑白位、色偏、断层与宽容度。', 'Continuous ramp, 21 steps, 0–12% shadows and 88–100% highlights. Compare endpoints, casts, banding and latitude.'),
            'edges': ('斜边、同心环与变频线对，用于比较扩散、光晕、边缘变形与解析力。不是相机分辨率认证标靶。', 'Slanted edge, concentric rings and variable-frequency lines for spread, halation, deformation and detail comparisons; not a camera certification target.'),
        }
        self.target.setToolTip(text(*details[self.target.currentData()]))
        if self.isVisible() and self.automatic.isChecked():self.timer.start()

    def showEvent(self,event):
        super().showEvent(event)
        if self.automatic.isChecked():self.timer.start()

    def request(self,*_):
        self.timer.stop()
        try:config=copy.deepcopy(self.config_provider())
        except (ValueError,TypeError) as exc:self.status.setText(str(exc));return
        self.generation += 1
        self.pending = (self.generation, self.target.currentData(), config)
        self._start()

    def _start(self):
        if self.busy or self.pending is None:return
        generation, kind, config = self.pending;self.pending=None;self.busy=True
        self.status.setText(text('预览计算中…','Rendering preview…'))
        session,signals=self.session,self.signals
        def run():
            start=perf_counter()
            try:
                source,result,hit=session.render_target(kind,config)
                status=text('复用','Reused') if hit else text('完成','Rendered')
                frames=(source,result);status+=f' · {perf_counter()-start:.2f} s'
            except Exception as exc:frames=None;status=str(exc)
            try:signals.ready.emit(generation,frames,status)
            except RuntimeError:pass
        self.pool.submit(run)

    def _ready(self,generation,frames,status):
        self.busy=False
        if generation==self.generation:
            self.status.setText(status)
            if frames is not None:self.frames=frames;self._display()
        self._start()

    def _display(self,*_):
        if self.frames is None:return
        source,result=self.frames
        if self.display.currentIndex()==2:
            height=max(source.shape[0],result.shape[0])
            frames=[np.pad(a,((0,height-a.shape[0]),(0,0),(0,0)),constant_values=.12) for a in (source,result)]
            pixels=np.concatenate(frames,axis=1)
        else:pixels=source if self.display.currentIndex()==1 else result
        pixels=np.ascontiguousarray(np.clip(pixels*255+.5,0,255).astype(np.uint8))
        h,w=pixels.shape[:2]
        self.pixmap=QtGui.QPixmap.fromImage(QtGui.QImage(pixels.data,w,h,pixels.strides[0],QtGui.QImage.Format.Format_RGB888).copy())
        self._fit()

    def _fit(self):
        if self.pixmap is not None:self.canvas.setPixmap(self.pixmap.scaled(self.canvas.size(),QtCore.Qt.AspectRatioMode.KeepAspectRatio,QtCore.Qt.TransformationMode.SmoothTransformation))

    def eventFilter(self,obj,event):
        if obj is self.canvas and event.type()==QtCore.QEvent.Type.Resize:self._fit()
        return super().eventFilter(obj,event)
