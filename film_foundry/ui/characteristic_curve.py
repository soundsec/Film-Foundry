"""An on-demand plot measured independently of the displayed photograph."""
from concurrent.futures import ThreadPoolExecutor
from film_foundry.app.control import CancelFlag, WorkflowControl
from film_foundry.app.preview_budget import Priority, preview_budget
from film_foundry.app.characteristic_curve import measure_curve
from film_foundry.ui.curve_editor import text
from film_foundry.ui.qt import QtCore, QtGui, QtWidgets


class CurvePlot(QtWidgets.QWidget):
    def __init__(self):
        super().__init__()
        self.data = None
        self.setMinimumSize(390, 220)

    def paintEvent(self, event):
        if self.data is None:return
        curve = self.data
        painter = QtGui.QPainter(self)
        painter.setRenderHint(QtGui.QPainter.RenderHint.Antialiasing)
        frame = self.rect().adjusted(46,12,-20,-38)
        top = max(.25, float(curve.densities.max()),float(curve.unexposed.max())) * 1.05
        painter.setPen(self.palette().text().color())
        painter.drawRect(frame)
        for i in range(5):
            y = frame.bottom()-i*frame.height()/4
            painter.drawText(4,round(y),f'{top*i/4:.2f}')
        def point(x,y):
            return QtCore.QPointF(frame.left()+(x+5)*frame.width()/7,frame.bottom()-y/top*frame.height())
        for x in range(-5,3):
            painter.drawText(round(point(x,0).x())-5,frame.bottom()+16,str(x))
        painter.drawText(frame.left()+frame.width()//2-22,frame.bottom()+32,'log₁₀ H')
        if curve.thresholds:
            pen=QtGui.QPen(self.palette().mid().color());pen.setStyle(QtCore.Qt.PenStyle.DashLine);painter.setPen(pen)
            for x in curve.thresholds[:2]:painter.drawLine(point(x,0),point(x,top))
        for i,color in enumerate(('#eb685f','#46b879','#618de4')):
            pen=QtGui.QPen(QtGui.QColor(color),2)
            if i:pen.setDashPattern([4,4] if i==1 else [1,7])
            painter.setPen(pen)
            path=QtGui.QPainterPath()
            for j,x in enumerate(curve.log_h):
                xy=point(x,curve.densities[j,i])
                if j:path.lineTo(xy)
                else:path.moveTo(xy)
            painter.drawPath(path)


def show_curve(owner, parent):
    config=owner._config()
    dialog=QtWidgets.QDialog(parent)
    dialog.setWindowTitle(text('特性曲线 · D–logH','Characteristic curve · D–logH'))
    layout=QtWidgets.QVBoxLayout(dialog);plot=CurvePlot();layout.addWidget(plot)
    note=QtWidgets.QLabel(text('正在按当前配方冲洗灰阶…','Developing a step wedge using the current recipe…'))
    note.setWordWrap(True);layout.addWidget(note)
    pool=ThreadPoolExecutor(max_workers=1,thread_name_prefix='sensitometry')
    flag=CancelFlag();check=WorkflowControl(cancel_requested=flag.requested).check_cancel
    future=pool.submit(preview_budget.run,Priority.STRIP,measure_curve,config,check=check)
    timer=QtCore.QTimer(dialog)
    def poll():
        if not future.done():return
        timer.stop()
        try:data=future.result()
        except Exception as exc:note.setText(str(exc));return
        plot.data=data;plot.update()
        base=text('零曝光 D','Zero-exposure D') if data.positive else text('片基加灰雾 D','Base + fog D')
        label=base+' (RGB)  '+', '.join(f'{v:.3f}' for v in data.unexposed)
        if data.thresholds:
            toe,shoulder,slope=data.thresholds
            label+=text('　趾 / 肩（密度变化 10% / 90%）','   Toe / shoulder (10% / 90% response)')+f' {toe:.2f} / {shoulder:.2f}'
            label+=text('　平均反差','   Average gradient')+f' {slope:.2f}'
        note.setText(label+text('。横轴为相对输入曝光，纵轴为 RGB 光学密度。',' · Relative input exposure and RGB optical density.'))
    timer.timeout.connect(poll);timer.start(50)
    try:dialog.exec()
    finally:flag.cancel();future.cancel();timer.stop();pool.shutdown(wait=False,cancel_futures=True);dialog.deleteLater()
