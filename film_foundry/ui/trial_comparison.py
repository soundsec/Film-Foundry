"""Compare completed trials at the same magnification and image location."""
import math
from film_foundry.ui.qt import QtCore, QtGui, QtWidgets
from film_foundry.ui.curve_editor import text


class TrialView(QtWidgets.QGraphicsView):
    def __init__(self, panel, pixmap):
        super().__init__()
        self.panel = panel
        self.picture = pixmap
        self.setScene(QtWidgets.QGraphicsScene(self))
        self.item = self.scene().addPixmap(pixmap)
        self.setSceneRect(QtCore.QRectF(pixmap.rect()))
        self.setDragMode(QtWidgets.QGraphicsView.DragMode.ScrollHandDrag)
        self.setMinimumSize(48, 45)
        self.setHorizontalScrollBarPolicy(QtCore.Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setVerticalScrollBarPolicy(QtCore.Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setRenderHint(QtGui.QPainter.RenderHint.SmoothPixmapTransform)
        self.rubber = QtWidgets.QRubberBand(QtWidgets.QRubberBand.Shape.Rectangle, self.viewport())
        self.origin = None

    def wheelEvent(self, event):
        factor = 1.25 if event.angleDelta().y() > 0 else .8
        point = self.mapToScene(event.position().toPoint())
        w,h = self.picture.width(), self.picture.height()
        self.panel.zoom = min(32., max(1., self.panel.zoom * factor))
        self.panel.center = (point.x()/w, point.y()/h)
        self.panel.sync()
        event.accept()

    def mousePressEvent(self, event):
        if self.panel.crop.isChecked() and event.button() == QtCore.Qt.MouseButton.LeftButton:
            self.origin = event.position().toPoint()
            self.rubber.setGeometry(QtCore.QRect(self.origin, self.origin))
            self.rubber.show()
            return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        if self.origin is not None:
            self.rubber.setGeometry(QtCore.QRect(self.origin, event.position().toPoint()).normalized())
            return
        super().mouseMoveEvent(event)
        if event.buttons() & QtCore.Qt.MouseButton.LeftButton:
            point = self.mapToScene(self.viewport().rect().center())
            self.panel.center = (point.x()/self.picture.width(), point.y()/self.picture.height())
            self.panel.sync(skip=self)

    def mouseReleaseEvent(self, event):
        if self.origin is None:
            return super().mouseReleaseEvent(event)
        rect = self.mapToScene(self.rubber.geometry()).boundingRect().intersected(self.sceneRect())
        self.rubber.hide()
        self.origin = None
        if min(rect.width(),rect.height()) >= 2:
            w, h = self.picture.width(), self.picture.height()
            self.panel.set_region(QtCore.QRectF(rect.x()/w, rect.y()/h, rect.width()/w, rect.height()/h))
            self.panel.crop.setChecked(False)

    def resizeEvent(self,event):
        super().resizeEvent(event)
        self.panel.sync_view(self)


class TrialComparison(QtWidgets.QDialog):
    chosen=QtCore.Signal(int,bool)
    def __init__(self, tiles, columns, parent, *, formal=None):
        super().__init__(parent)
        self.setWindowTitle(text('全部试条 · 同步对照','All trials · linked comparison'))
        self.resize(1060,680)
        self.center = (.5,.5)
        self.zoom = 1.
        self.views = []
        root = QtWidgets.QVBoxLayout(self)
        actions = QtWidgets.QHBoxLayout()
        root.addLayout(actions)
        self.crop = QtWidgets.QPushButton(text('框选局部','Select region'))
        self.crop.setCheckable(True)
        actions.addWidget(self.crop)
        if formal:
            native=QtWidgets.QPushButton(text('局部正式尺度…','Formal-scale region…'))
            native.clicked.connect(lambda:formal(self,self.region))
            actions.addWidget(native)
        fit = QtWidgets.QPushButton(text('显示全幅','Fit all'))
        fit.clicked.connect(self.fit)
        actions.addWidget(fit)
        actions.addWidget(QtWidgets.QLabel(text('滚轮缩放，拖动平移；所有试条同步。','Scroll to zoom and drag to pan across all trials.')))
        actions.addStretch()
        grid = QtWidgets.QGridLayout()
        root.addLayout(grid,1)
        columns = columns or math.ceil(math.sqrt(len(tiles)))
        for index,(label,pixmap) in enumerate(tiles):
            row,col = divmod(index,columns)
            view = TrialView(self,pixmap)
            bar=QtWidgets.QHBoxLayout()
            select=QtWidgets.QPushButton(label);select.setCheckable(True)
            if index==0:self.choices=QtWidgets.QButtonGroup(self)
            self.choices.addButton(select,index)
            select.clicked.connect(lambda _,i=index:self.chosen.emit(i,False))
            use=QtWidgets.QPushButton(text('采用','Use'));use.clicked.connect(lambda _,i=index:self.chosen.emit(i,True))
            bar.addWidget(select,1);bar.addWidget(use)
            grid.addLayout(bar,row*2,col)
            grid.addWidget(view, row*2+1, col)
            grid.setRowStretch(row*2+1,1)
            self.views.append(view)
        self.fit()

    def set_region(self, region):
        self.region=QtCore.QRectF(region)
        self.zoom = 1.
        self.center = (region.center().x(),region.center().y())
        for view in self.views:
            picture = view.picture
            w,h = picture.width(),picture.height()
            rect = QtCore.QRectF(region.x()*w,region.y()*h,region.width()*w,region.height()*h)
            bounds = rect.toAlignedRect().intersected(picture.rect())
            pixels = picture if bounds == picture.rect() else picture.copy(bounds)
            view.item.setPixmap(pixels)
            view.item.setOffset(bounds.x(),bounds.y())
            view.setSceneRect(QtCore.QRectF(bounds))
        self.sync()

    def fit(self):
        self.set_region(QtCore.QRectF(0.,0.,1.,1.))

    def sync(self, *, skip=None):
        for view in self.views:
            if view is not skip:self.sync_view(view)

    def sync_view(self,view):
        w,h=view.picture.width(),view.picture.height()
        region=view.sceneRect()
        if region.isEmpty():return
        scale=min((view.viewport().width()-2)/region.width(),(view.viewport().height()-2)/region.height())
        if scale<=0:return
        scale*=self.zoom
        view.setTransform(QtGui.QTransform.fromScale(scale,scale))
        view.centerOn(w*self.center[0],h*self.center[1])
