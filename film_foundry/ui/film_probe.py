"""Read a published preview in its original image coordinates."""
import math
from film_foundry.ui.qt import QtCore, QtGui, QtWidgets
from film_foundry.ui.curve_editor import text


class ExposurePlot(QtWidgets.QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.probe = None
        self.point = None
        self.setMinimumSize(360,210)

    def paintEvent(self, event):
        p = QtGui.QPainter(self)
        p.setRenderHint(QtGui.QPainter.RenderHint.Antialiasing)
        plot = self.rect().adjusted(36,14,-20,-36)
        p.setPen(self.palette().text().color())
        p.drawRect(plot)
        p.drawText(7,plot.top()+8,'1')
        p.drawText(7,plot.bottom(),'0')
        for i,label in enumerate(('≤−6','-4','-2','0','2','4')):
            x=plot.left()+i*plot.width()/5
            p.drawText(round(x)-6,plot.bottom()+16,label)
        p.drawText(plot.left()+plot.width()//2-16,plot.bottom()+32,'log₁₀ H')
        probe=self.probe
        if probe is None or probe.bins is None or probe.count!=probe.expected_pixels:
            p.drawText(plot,QtCore.Qt.AlignmentFlag.AlignCenter,text('等待有完整逐层曝光记录的照片预览','Waiting for measured layer exposures'))
            return
        for i,counts in enumerate(probe.bins):
            colour=(QtGui.QColor.fromHsv((i*137+185)%360,155,200) if len(probe.bins)>3 else
                    QtGui.QColor(('#349ba9','#c35c9c','#c29532')[i] if len(probe.bins)>1 else '#888888'))
            p.setPen(colour)
            p.drawText(plot.right()-32*(len(probe.bins)-i),plot.top()+18, f"L{i+1}")
            p.setPen(QtGui.QPen(colour,1))
            peak=max(1,int(counts.max()))
            for j,n in enumerate(counts):
                x=plot.left()+(j+.5)*plot.width()/len(counts)
                p.drawLine(QtCore.QPointF(x,plot.bottom()),QtCore.QPointF(x,plot.bottom()-n/peak*plot.height()*.64))
            path=QtGui.QPainterPath()
            for j,value in enumerate(probe.curves[:,i]):
                point=QtCore.QPointF(plot.left()+j*plot.width()/(len(probe.edges)-1),plot.bottom()-float(value)*plot.height())
                if j:path.lineTo(point)
                else:path.moveTo(point)
            pen=QtGui.QPen(colour,2)
            if i:pen.setDashPattern([4,4] if i==1 else [1,7]);pen.setDashOffset(i*2)
            p.setPen(pen)
            p.drawPath(path)
            if self.point is not None:
                import numpy as np
                dose = float(np.clip(self.point[i], probe.edges[0], probe.edges[-1]))
                ratio = (dose-probe.edges[0])/(probe.edges[-1]-probe.edges[0])
                response = np.interp(dose,probe.edges,probe.curves[:,i])
                p.setBrush(colour)
                p.drawEllipse(QtCore.QPointF(plot.left()+ratio*plot.width(),plot.bottom()-response*plot.height()),4,4)
                p.setBrush(QtCore.Qt.BrushStyle.NoBrush)


class FilmProbe(QtCore.QObject):
    def __init__(self, owner):
        super().__init__(owner.result_view)
        self.view = owner.result_view
        self.view.probe_overlay=self.draw_pins
        self.data = None
        self.refresh_pending = False
        self.view.viewport().setMouseTracking(True)
        self.view.viewport().installEventFilter(self)
        self.action = owner.utilities_menu.addAction(text('密度与曝光落点','Density & exposure placement'))
        self.action.setCheckable(True)
        self.dialog = QtWidgets.QDialog(self.view)
        self.dialog.setWindowTitle(self.action.text())
        self.dialog.setMinimumSize(470,345)
        self.dialog.resize(610,365)
        layout = QtWidgets.QVBoxLayout(self.dialog)
        self.owner=owner;self.pins=[];self.source=None
        row=QtWidgets.QHBoxLayout();layout.addLayout(row)
        row.addWidget(QtWidgets.QLabel(text('取样孔径','Aperture')))
        self.aperture=QtWidgets.QComboBox()
        for n in (1,5,15):self.aperture.addItem(f'{n} × {n}',n)
        row.addWidget(self.aperture)
        row.addWidget(QtWidgets.QLabel(text('Shift + 单击钉点（最多 4 个）','Shift + click to pin (up to 4)')))
        clear=QtWidgets.QPushButton(text('清除钉点','Clear pins'));row.addWidget(clear)
        self.pinned=QtWidgets.QLabel();layout.addWidget(self.pinned)
        curve=QtWidgets.QPushButton(text('特性曲线…','Characteristic curve…'));layout.addWidget(curve)
        from film_foundry.ui.characteristic_curve import show_curve
        curve.clicked.connect(lambda:show_curve(owner,self.dialog))
        clear.clicked.connect(self.clear_pins)
        self.aperture.currentIndexChanged.connect(self.update_pins)
        self.plot = ExposurePlot()
        layout.addWidget(self.plot,1)
        note=QtWidgets.QLabel(text('曲线为各层曝光到可显影比例（0–1）；柱形为本图各层曝光分布，按各层峰值归一。指向结果图可读显示值、RGB 光学密度及逐层曝光。log₁₀ H 的读数下限为 −6。','Curves show layer exposure versus developable fraction (0–1). Bars show each layer’s exposure distribution relative to its peak. Point at the result for display values, RGB optical densities and layer exposures. The log₁₀ H readout is floored at −6.'))
        note.setWordWrap(True);layout.addWidget(note)
        self.readout = QtWidgets.QLabel()
        self.readout.setWordWrap(True)
        self.readout.setMinimumHeight(2*self.readout.fontMetrics().height()+4)
        layout.addWidget(self.readout)
        self.dialog.finished.connect(lambda:self.action.setChecked(False))
        def toggle(active):
            if active:
                self.dialog.show()
                owner._preview_edited()
                self.refresh_pending = owner._preview_running
                if not self.refresh_pending:owner._start_preview()
            else:
                self.refresh_pending = False
                self.publish(None)
                self.dialog.hide()
                if not owner._preview_running:owner._preview_session.clear_probe()
        self.action.toggled.connect(toggle)

    def clear_pins(self):
        self.pins.clear();self.update_pins()

    def update_pins(self):
        lines=[]
        if self.data is not None:
            shape=self.data.scan_geometry[0] if self.data.scan_geometry else ((self.view.result.height(), self.view.result.width()) if self.view.result is not None else None)
            if shape:
                h,w=shape
                for i,(u,v) in enumerate(self.pins,1):
                    x,y=int(u*w),int(v*h)
                    _,msg=self.measure(x,y,w,h)
                    lines.append(f'{i}   {msg}')
        self.pinned.setText('\n'.join(lines))
        self.view.viewport().update()

    def draw_pins(self,painter):
        view=self.view
        if not self.pins or self.data is None or view.result is None or view.held_preview or view._keyboard_source or view._holding:return
        from film_foundry.contracts.scan_framing import pixel_bounds
        from film_foundry.core.scan_framing import rotation_matrix
        geometry=self.data.scan_geometry
        width,height=view.result.width(),view.result.height()
        h,w=geometry[0] if geometry else (height,width)
        points=[]
        for i,(u,v) in enumerate(self.pins,1):
            x,y=int(u*w),int(v*h)
            if geometry:
                _,angle,rect=geometry
                if angle:x,y=rotation_matrix((h,w),angle) @ (x,y,1.)
                l,t,_,_=pixel_bounds((h,w),rect);x-=l;y-=t
            points.append((i,x,y))
        view.draw_probe_points(painter,points)

    def measure(self,x,y,w,h):
        aperture=self.aperture.currentData()
        density=self.data.density_at(x,y,w,h,aperture)
        placement=self.data.exposure_at(x,y,w,h,aperture)
        message=f'({x}, {y})'
        if density is not None:message+='   D(RGB) '+', '.join(f'{n:.3f}' for n in density)
        if placement is not None:message+='   log₁₀ H  '+', '.join('≤ −6' if n<=-6 else f'{n:.2f}' for n in placement)
        return placement,message

    def publish(self, data):
        self.data = data if self.action.isChecked() else None
        self.plot.point = None
        self.readout.clear()
        source=(self.owner.input_path.get(),self.owner.negative_path.get())
        if source!=self.source:self.pins.clear();self.source=source
        self.update_pins()
        self.plot.probe = self.data
        self.plot.update()

    def eventFilter(self, target, event):
        pin=(event.type()==QtCore.QEvent.Type.MouseButtonPress and event.button()==QtCore.Qt.MouseButton.LeftButton
             and bool(event.modifiers() & QtCore.Qt.KeyboardModifier.ShiftModifier))
        if (not pin and event.type()!=QtCore.QEvent.Type.MouseMove) or self.data is None:return False
        view=self.view
        if view.result is None or view._holding or view._keyboard_source or view.held_preview is not None:return False
        point=view.mapToScene(event.position().toPoint())
        x,y=math.floor(point.x()),math.floor(point.y())
        rotated=view.displayed_result()
        if view.mode=='side_by_side':x-=rotated.width()
        if view.mode=='wipe' and x<rotated.width()*view.divider:return False
        if not 0<=x<rotated.width() or not 0<=y<rotated.height():return False
        rgb=rotated.copy(x,y,1,1).toImage().pixelColor(0,0).getRgb()[:3]
        w,h=view.result.width(),view.result.height()
        q=view.rotation_quarters
        if q==1:x,y=y,h-1-x
        elif q==2:x,y=w-1-x,h-1-y
        elif q==3:x,y=w-1-y,x
        if self.data.scan_geometry is not None:
            from film_foundry.core.scan_framing import capture_position
            x,y,w,h=capture_position(self.data.scan_geometry,x,y)
        placement,message=self.measure(x,y,w,h)
        message+=f'   RGB8 {rgb}'
        if pin and 0<=x<w and 0<=y<h:
            self.pins=(self.pins+[((x+.5)/w,(y+.5)/h)])[-4:]
            self.update_pins()
        self.plot.point=placement
        self.plot.update()
        self.readout.setText(message)
        QtWidgets.QToolTip.showText(event.globalPosition().toPoint(),message,target)
        return pin
