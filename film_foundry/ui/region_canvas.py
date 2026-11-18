"""Draw and select normalized regions on a fitted preview without letterbox offsets."""
from film_foundry.ui.qt import QtCore, QtGui, QtWidgets


class RegionCanvas(QtWidgets.QWidget):
    selected = QtCore.Signal(object)
    activated = QtCore.Signal(int)
    leveled = QtCore.Signal(float)
    picked = QtCore.Signal(float,float)

    def __init__(self,parent=None):
        super().__init__(parent)
        self.pixmap=None
        self.region=None
        self.ellipse=False
        self.origin=None
        self.pick_mode=False
        self.marker=None
        self.zoom=1.
        self.offset=QtCore.QPointF()
        self.pan=None
        self.drag=None
        self.resize_anchor=None
        self.overlays=[]
        self.shapes=[]
        self.active_index=-1
        self.aspect=None
        self.dim_outside=False
        self.grid=False
        self.level=False
        self.line_end=None
        self.setMinimumSize(240,150)
        self.setMouseTracking(True)
        from film_foundry.ui.curve_editor import text
        self.setToolTip(text('滚轮缩放，中键拖动平移，双击中键适合窗口。','Wheel to zoom, middle-drag to pan, double middle-click to fit.'))

    def frame(self):
        size=self.pixmap.size() if self.pixmap is not None else QtCore.QSize(4,3)
        area=self.rect().adjusted(3,3,-3,-3)
        size.scale(area.size(),QtCore.Qt.AspectRatioMode.KeepAspectRatio)
        size=QtCore.QSizeF(size)*self.zoom
        return QtCore.QRectF(area.x()+(area.width()-size.width())/2+self.offset.x(),area.y()+(area.height()-size.height())/2+self.offset.y(),size.width(),size.height())

    def wheelEvent(self,event):
        before=self.frame()
        anchor=event.position()
        old=self.zoom
        self.zoom=max(1.,min(16.,old*1.25**(event.angleDelta().y()/120)))
        self.offset+=(anchor-before.center())*(1-self.zoom/old)
        if self.zoom==1:self.offset=QtCore.QPointF()
        self.update()

    def mouseDoubleClickEvent(self,event):
        if event.button()==QtCore.Qt.MouseButton.MiddleButton:
            self.zoom=1.;self.offset=QtCore.QPointF();self.update()

    def box(self,rect):
        l,t,r,b=rect;f=self.frame()
        return QtCore.QRectF(f.x()+l*f.width(),f.y()+t*f.height(),(r-l)*f.width(),(b-t)*f.height())


    def normalized(self, point):
        f=self.frame()
        if min(f.width(),f.height())<=0:return 0.,0.
        return max(0.,min(1.,(point.x()-f.left())/f.width())),max(0.,min(1.,(point.y()-f.top())/f.height()))

    def outline(self,index,box):
        shape,angle=self.shapes[index] if 0<=index<len(self.shapes) else ('ellipse' if self.ellipse else 'rectangle',0)
        path=QtGui.QPainterPath()
        if shape=='ellipse':path.addEllipse(box)
        else:path.addRect(box)
        transform=QtGui.QTransform()
        center=box.center();transform.translate(center.x(),center.y());transform.rotate(angle);transform.translate(-center.x(),-center.y())
        return transform.map(path)

    def handles(self,box):
        corners=(box.topLeft(),box.topRight(),box.bottomLeft(),box.bottomRight())
        if not 0<=self.active_index<len(self.shapes):return corners
        angle=self.shapes[self.active_index][1]
        transform=QtGui.QTransform();center=box.center()
        transform.translate(center.x(),center.y());transform.rotate(angle);transform.translate(-center.x(),-center.y())
        return tuple(transform.map(point) for point in corners)

    def paintEvent(self,event):
        p=QtGui.QPainter(self)
        p.fillRect(self.rect(),QtGui.QColor('#222222'))
        f=self.frame()
        if self.pixmap is not None:
            p.setRenderHint(QtGui.QPainter.RenderHint.SmoothPixmapTransform)
            p.drawPixmap(f,self.pixmap,QtCore.QRectF(self.pixmap.rect()))
        else:p.fillRect(f,QtGui.QColor('#555555'))
        if self.marker is not None:
            x,y=self.marker
            point=QtCore.QPointF(f.left()+x*f.width(),f.top()+y*f.height())
            for color,width in (('#151515',3),('#ffffff',1)):
                p.setPen(QtGui.QPen(QtGui.QColor(color),width))
                p.drawEllipse(point,5,5)
                p.drawLine(point-QtCore.QPointF(9,0),point+QtCore.QPointF(9,0))
                p.drawLine(point-QtCore.QPointF(0,9),point+QtCore.QPointF(0,9))
        if self.grid:
            p.setPen(QtGui.QPen(QtGui.QColor(240,240,240,155),1,QtCore.Qt.PenStyle.DashLine))
            for v in (1/3,2/3):
                x=f.left()+v*f.width();y=f.top()+v*f.height()
                p.drawLine(QtCore.QPointF(x,f.top()),QtCore.QPointF(x,f.bottom()))
                p.drawLine(QtCore.QPointF(f.left(),y),QtCore.QPointF(f.right(),y))
        for i,rect in enumerate(self.overlays):
            if i!=self.active_index:
                p.setPen(QtGui.QPen(QtGui.QColor("white"),1,QtCore.Qt.PenStyle.DashLine))
                p.drawPath(self.outline(i,self.box(rect)))
        if self.line_end is not None:
            p.setPen(QtGui.QPen(QtGui.QColor("yellow"),2))
            p.drawLine(self.line_start,self.line_end)
        if self.region:
            l,t,r,b=self.region
            region=QtCore.QRectF(f.x()+l*f.width(),f.y()+t*f.height(),(r-l)*f.width(),(b-t)*f.height())
            if self.dim_outside:
                mask=QtGui.QPainterPath()
                mask.addRect(f)
                mask.addPath(self.outline(self.active_index,region))
                p.fillPath(mask,QtGui.QColor(0,0,0,105))
            for color,width in (('#202020',3),('#ffd184',1)):
                p.setPen(QtGui.QPen(QtGui.QColor(color),width))
                p.drawPath(self.outline(self.active_index,region))

            for corner in self.handles(region):
                p.fillRect(QtCore.QRectF(corner.x()-3,corner.y()-3,6,6),QtGui.QColor("#ffd184"))

    def mousePressEvent(self,event):
        pos=event.position()
        if event.button()==QtCore.Qt.MouseButton.MiddleButton:
            self.pan=pos
            return
        if event.button()!=QtCore.Qt.MouseButton.LeftButton or not self.frame().contains(pos):return
        if self.pick_mode:
            if self.pixmap is not None:self.picked.emit(*self.normalized(pos))
            return
        if self.level:
            self.line_start=self.line_end=pos
            return
        self.origin=self.normalized(pos)
        self.drag=None
        if self.region:
            box=self.box(self.region)
            handles=self.handles(box)
            for i,corner in enumerate(handles):
                if (corner-pos).manhattanLength()<12:
                    opposite=handles[3-i]
                    angle=self.shapes[self.active_index][1] if 0<=self.active_index<len(self.shapes) else 0
                    if angle:self.resize_anchor=(opposite,angle)
                    self.origin=self.normalized(opposite)
                    return
        if not self.region or not self.outline(self.active_index,self.box(self.region)).contains(pos):
            for i in reversed(range(len(self.overlays))):
                if self.outline(i,self.box(self.overlays[i])).contains(pos):
                    self.activated.emit(i);break
        if self.region and self.outline(self.active_index,self.box(self.region)).contains(pos):self.drag=self.region

    def mouseMoveEvent(self,event):
        pos=event.position()
        if self.pan is not None:
            self.offset+=pos-self.pan;self.pan=pos;self.update();return
        if self.line_end is not None:
            self.line_end=pos;self.update();return
        if self.origin is None:return
        x,y=self.normalized(pos);a,b=self.origin
        if self.resize_anchor:
            anchor,angle=self.resize_anchor
            delta=QtGui.QTransform().rotate(-angle).map(pos-anchor)
            center=(anchor+pos)*.5
            frame=self.frame()
            half_w=min(abs(delta.x())/2,frame.width()/2)
            half_h=min(abs(delta.y())/2,frame.height()/2)
            cx=max(frame.left()+half_w,min(frame.right()-half_w,center.x()))
            cy=max(frame.top()+half_h,min(frame.bottom()-half_h,center.y()))
            self.region=(*self.normalized(QtCore.QPointF(cx-half_w,cy-half_h)),
                         *self.normalized(QtCore.QPointF(cx+half_w,cy+half_h)))
        elif self.drag:
            l,t,r,d=self.drag
            dx=max(-l,min(1-r,x-a));dy=max(-t,min(1-d,y-b))
            self.region=(l+dx,t+dy,r+dx,d+dy)
        else:
            if self.aspect:
                ratio=self.aspect*self.frame().height()/self.frame().width()
                sx=1 if x>=a else -1;sy=1 if y>=b else -1
                w=min(max(abs(x-a),abs(y-b)*ratio),1-a if sx>0 else a,(1-b if sy>0 else b)*ratio)
                x=a+sx*w;y=b+sy*w/ratio
            self.region=(min(x,a),min(y,b),max(x,a),max(y,b))
        self.update()

    def mouseReleaseEvent(self,event):
        if event.button()==QtCore.Qt.MouseButton.MiddleButton:
            self.pan=None;return
        if self.line_end is not None:
            import math
            delta=event.position()-self.line_start;self.line_end=None;self.update()
            if delta.manhattanLength()>5:
                angle=math.degrees(math.atan2(delta.y(),delta.x()))
                self.leveled.emit((angle+45)%90-45)
            return
        if self.origin is None:return
        self.mouseMoveEvent(event);self.origin=None;self.drag=None;self.resize_anchor=None
        l,t,r,b=self.region
        if r>l and b>t:self.selected.emit(self.region)
        else:self.region=None;self.selected.emit(None)
        self.update()


class RegionFields(QtWidgets.QWidget):
    changed=QtCore.Signal(object)
    def __init__(self):
        super().__init__()
        from film_foundry.ui.curve_editor import text
        layout=QtWidgets.QHBoxLayout(self);layout.setContentsMargins(0,0,0,0)
        self.fields=[]
        for zh,en in [('左','Left'),('上','Top'),('右','Right'),('下','Bottom')]:
            layout.addWidget(QtWidgets.QLabel(text(zh,en)))
            spin=QtWidgets.QDoubleSpinBox()
            spin.setRange(0,100);spin.setDecimals(2);spin.setSuffix('%')
            spin.setKeyboardTracking(False)
            spin.setAccessibleName(text(zh,en))
            layout.addWidget(spin);self.fields.append(spin)
            spin.valueChanged.connect(self.edited)
        self.set_rect(None)

    def set_rect(self,rect):
        for spin,value in zip(self.fields,rect or (0,0,1,1)):
            blocked=QtCore.QSignalBlocker(spin);spin.setValue(value*100);del blocked

    def edited(self):
        self.changed.emit(tuple(spin.value()/100 for spin in self.fields))
