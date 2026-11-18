"""Deliberately modal peek: no parameters, refresh, adoption or export."""
from concurrent.futures import ThreadPoolExecutor
from film_foundry.ui.qt import QtCore,QtWidgets
from film_foundry.ui.curve_editor import text
from film_foundry.ui.comparison_view import ComparisonView
from film_foundry.app.control import CancelFlag,WorkflowCancelled


class _Signals(QtCore.QObject):
    done=QtCore.Signal(object,str)


class RollPeekDialog(QtWidgets.QDialog):
    def __init__(self,request,parent=None):
        super().__init__(parent);self.setWindowTitle(text('作弊预览 · 关掉再调参','Cheat preview · close before editing'))
        self.resize(900,650);self.setMinimumSize(520,380);self.busy=True;self.closing=False
        self.setProperty('disposablePreview',True)
        self.cancel=CancelFlag();self.signals=_Signals();self.signals.done.connect(self.completed)
        self.pool=ThreadPoolExecutor(max_workers=1,thread_name_prefix='roll-peek')
        layout=QtWidgets.QVBoxLayout(self)
        self.note=QtWidgets.QLabel(text(f'第 {request.frame_index} 格 · 当前未提交药浴草稿 · 640px 试算\n查看期间不能调参；关闭即丢弃预览。正式冲洗仍处理整卷，细节与随机结果可能不同。',
            f'Frame {request.frame_index} · current uncommitted bath draft · 640px forecast\nEditing is blocked while viewing. Closing discards this preview. Formal processing covers the whole roll; detail and random results may differ.'))
        self.note.setWordWrap(True);layout.addWidget(self.note)
        self.viewer=ComparisonView();layout.addWidget(self.viewer,1)
        self.viewer.set_rotation(request.config.output.rotation_quarters)
        self.status=QtWidgets.QLabel(text('正在试算…','Rendering…'));layout.addWidget(self.status)
        close=QtWidgets.QPushButton(text('关闭预览，返回调参','Close preview and return to editing'));close.clicked.connect(self.reject);layout.addWidget(close)
        signals=self.signals;cancel=self.cancel
        def work():
            try:result=request.render(cancel);error=''
            except WorkflowCancelled:result=None;error=text('已取消','Cancelled')
            except Exception as exc:result=None;error=str(exc)
            signals.done.emit(result,error)
        self.pool.submit(work)

    def completed(self,image,error):
        self.busy=False
        if self.closing:self.reject();return
        if image is not None:self.viewer.show_array(image)
        self.status.setText(error or text('只读试算 · 未推进胶卷、未保存结果','Read-only forecast · roll unchanged, no result saved'))

    def done(self,result):
        if self.busy:
            self.closing=True;self.cancel.cancel();self.status.setText(text('等待当前计算结束后关闭…','Closing when the current calculation finishes…'));return
        self.viewer.clear_image();self.pool.shutdown(wait=False,cancel_futures=True);super().done(result)

    def closeEvent(self,event):
        if self.busy:self.reject();event.ignore();return
        super().closeEvent(event)
