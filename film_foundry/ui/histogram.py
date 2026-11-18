"""Display-only histogram of the exact preview pixels; never modifies media."""
import numpy as np
from film_foundry.ui.qt import QtCore, QtGui, QtWidgets
from film_foundry.ui.curve_editor import text
from film_foundry.ui.theme import colors, text_on


def display_bins(pixels):
    rgb = np.asarray(pixels, dtype=np.uint8)
    if rgb.ndim!=3 or rgb.shape[-1]!=3:rgb=rgb.reshape(-1,1,3)
    # Keep integer Rec.709 rounding exact and bound temporary channel buffers
    # when the viewer is metering a full-resolution image.
    bins = np.zeros((4, 256), dtype=np.int64)
    weights = np.array([2126, 7152, 722], dtype=np.int32)
    rows = max(1, 65536 // max(1,rgb.shape[1]))
    for start in range(0, len(rgb), rows):
        block = rgb[start:start+rows].reshape(-1, 3)
        brightness = block @ weights
        brightness += 5000
        brightness //= 10000
        bins[0] += np.bincount(brightness, minlength=256)
        for channel in range(3):
            bins[channel+1] += np.bincount(block[:, channel], minlength=256)
    return bins


def histogram_plot(bins, channels, width, logarithmic=False):
    """Area-bin display at screen resolution; common amplitude, endpoints included.

    Raw 256-bin statistics stay unchanged. Grouping 2/4 adjacent bins suppresses
    subpixel combs without inventing peaks or normalizing channels separately.
    """
    group=1 if width>=512 else 2 if width>=256 else 4
    values=np.asarray(bins)[channels].reshape(len(channels),256//group,group).sum(axis=2)
    peak=max(1,int(values.max(initial=0)))
    heights=np.log1p(values)/np.log1p(peak) if logarithmic else values/peak
    total=max(1,int(np.asarray(bins)[0].sum()))
    return heights,peak/total*100,group


def histogram_area(values,rect,kind):
    path=QtGui.QPainterPath()
    if kind=='bars':
        step=rect.width()/len(values)
        for i,value in enumerate(values):
            if value>0:path.addRect(QtCore.QRectF(rect.left()+i*step,rect.bottom()-float(value)*rect.height(),step,float(value)*rect.height()))
    else:
        path.moveTo(rect.left(),rect.bottom())
        path.lineTo(rect.left(),rect.bottom()-float(values[0])*rect.height())
        for i,value in enumerate(values):path.lineTo(rect.left()+(i+.5)/len(values)*rect.width(),rect.bottom()-float(value)*rect.height())
        path.lineTo(rect.right(),rect.bottom()-float(values[-1])*rect.height())
        path.lineTo(rect.right(),rect.bottom());path.closeSubpath()
    return path


def solid_histogram_layer(areas,size,dpr=1.):
    """Opaque RGB areas add on a transparent layer, independent of paint order.

    R+G is yellow, G+B cyan, R+B magenta, RGB white. L is a neutral
    underlay; it never washes out the selected RGB overlap colours.
    """
    layer=QtGui.QImage(round(size.width()*dpr),round(size.height()*dpr),QtGui.QImage.Format.Format_ARGB32_Premultiplied)
    layer.setDevicePixelRatio(dpr);layer.fill(QtCore.Qt.GlobalColor.transparent)
    painter=QtGui.QPainter(layer);painter.setRenderHint(QtGui.QPainter.RenderHint.Antialiasing)
    painter.setCompositionMode(QtGui.QPainter.CompositionMode.CompositionMode_Plus)
    for channel,color in ((1,'#e60000'),(2,'#00e600'),(3,'#0000e6')):
        if channel in areas:painter.fillPath(areas[channel],QtGui.QColor(color))
    painter.end()
    return layer


class _ClippingButton(QtWidgets.QAbstractButton):
    def __init__(self,histogram,high):
        super().__init__(histogram);self.histogram=histogram;self.high=high
        self.setCheckable(True);self.setFixedSize(24,24)
        self.setFocusPolicy(QtCore.Qt.FocusPolicy.StrongFocus)
        self.setCursor(QtCore.Qt.CursorShape.PointingHandCursor)
        self.setAccessibleName(text('高光裁切提示','Highlight clipping') if high else text('阴影裁切提示','Shadow clipping'))
        self.setToolTip(text('RGB 任一通道达到 255。点击开关红色预览标记。','Any RGB channel reaches 255. Click to toggle red preview marks.') if high else
            text('RGB 任一通道达到 0。点击开关蓝色预览标记。','Any RGB channel reaches 0. Click to toggle blue preview marks.'))

    def paintEvent(self,event):
        p=QtGui.QPainter(self);p.setRenderHint(QtGui.QPainter.RenderHint.Antialiasing)
        t=colors();p.setPen(QtGui.QPen(QtGui.QColor(t.accent if self.isChecked() or self.hasFocus() else t.border),1))
        if self.isChecked() or self.hasFocus() or self.underMouse():
            p.setBrush(QtGui.QColor(t.selected if self.isChecked() else t.hover))
            p.drawRect(QtCore.QRectF(1.5,1.5,21,21))
        flags=self.histogram.endpoint_flags(self.high)
        color=QtGui.QColor(*[230 if flag else 45 for flag in flags]) if any(flags) else QtGui.QColor(t.disabled)
        p.setPen(QtGui.QPen(QtGui.QColor(t.text),1.0));p.setBrush(color)
        p.drawPolygon(QtGui.QPolygonF([QtCore.QPointF(6,8),QtCore.QPointF(18,8),QtCore.QPointF(12,17)]))


class Histogram(QtWidgets.QWidget):
    settings_changed=QtCore.Signal(object)

    def __init__(self, parent=None, *, compact=False):
        super().__init__(parent)
        self.bins = None
        self._endpoints = None
        from film_foundry.ui.histogram_settings import HistogramSettings
        self.settings=HistogramSettings.current()
        self.compact = compact
        self.setMinimumHeight(112)
        self.setSizePolicy(QtWidgets.QSizePolicy.Policy.Expanding,QtWidgets.QSizePolicy.Policy.Preferred)
        self.setToolTip(text('当前显示预览，非 RAW 曝光余量。左右三角开关裁切标记；右键设置布局、通道与绘制方式。',
                            'Displayed preview, not RAW headroom. Triangles toggle clipping marks; right-click for layout, channels and drawing options.'))
        self.setAccessibleName(text("预览直方图", "Preview histogram"))
        self.shadow_button=_ClippingButton(self,False);self.highlight_button=_ClippingButton(self,True)
        self.shadow_button.toggled.connect(lambda checked:self.settings.update(shadows=checked))
        self.highlight_button.toggled.connect(lambda checked:self.settings.update(highlights=checked))
        self.settings.changed.connect(self._settings_changed);self._settings_changed(self.settings.values)

    @property
    def bins(self):return self._bins

    @bins.setter
    def bins(self,value):
        self._bins=value;self._endpoints=None
        self._solid_cache_key=None;self._solid_cache=None
        if hasattr(self,'shadow_button'):
            self.shadow_button.update();self.highlight_button.update()

    @property
    def brightness(self):return self.settings.values['channels']==['L']

    @brightness.setter
    def brightness(self,value):self.settings.update(channels=['L'] if value else ['R','G','B'])

    @property
    def logarithmic(self):return self.settings.values['scale']=='log'

    @logarithmic.setter
    def logarithmic(self,value):self.settings.update(scale='log' if value else 'linear')

    def _settings_changed(self,value):
        if value['fill_style']!='solid':self._solid_cache_key=None;self._solid_cache=None
        for button,key in ((self.shadow_button,'shadows'),(self.highlight_button,'highlights')):
            button.blockSignals(True);button.setChecked(value[key]);button.blockSignals(False)
        self.update();self.settings_changed.emit(value)

    def sizeHint(self):
        return QtCore.QSize(340,154 if self.compact else 170)

    def set_pixels(self, pixels, *, endpoints=None):
        self.bins = display_bins(pixels)
        self._endpoints=endpoints
        self.update()

    def endpoint_flags(self,high):
        if self.bins is None:return [False]*3
        if self._endpoints is not None:return self._endpoints[int(high)]
        return self.bins[1:,255 if high else 0]>0

    def resizeEvent(self,event):
        self.shadow_button.move(8,5);self.highlight_button.move(self.width()-32,5)
        super().resizeEvent(event)

    def contextMenuEvent(self,event):
        from film_foundry.ui.histogram_settings import settings_menu
        self._menu=settings_menu(self.settings,self)
        self._menu.setAttribute(QtCore.Qt.WidgetAttribute.WA_DeleteOnClose)
        self._menu.popup(event.globalPos());event.accept()

    def paintEvent(self, event):
        p = QtGui.QPainter(self)
        p.setRenderHint(QtGui.QPainter.RenderHint.Antialiasing,True)
        t=colors()
        p.fillRect(self.rect(),QtGui.QColor(t.surface))
        options=self.settings.values
        mode=' + '.join(options['channels']) or text('未选通道','No channels')
        scale = text("对数", "Log") if self.logarithmic else text("线性", "Linear")
        kind=text('曲线','Curve') if options['kind']=='curve' else text('柱状','Bars')
        p.setPen(QtGui.QColor(text_on(t.surface)))
        header=f'{mode}  ·  {scale}  ·  {kind}'
        p.drawText(QtCore.QRectF(38,5,self.width()-76,24),QtCore.Qt.AlignmentFlag.AlignCenter,
                   p.fontMetrics().elidedText(header,QtCore.Qt.TextElideMode.ElideRight,self.width()-76))
        rect = QtCore.QRectF(44,42,max(1,self.width()-60),max(1,self.height()-65))
        plot_bg = '#17191b' if options['fill_style']=='solid' else t.input
        p.fillRect(rect,QtGui.QColor(plot_bg))
        grid=QtGui.QColor(t.border);grid.setAlpha(85);p.setPen(grid)
        for i in range(1,4):
            x=rect.left()+rect.width()*i/4;y=rect.top()+rect.height()*i/4
            p.drawLine(QtCore.QPointF(x,rect.top()),QtCore.QPointF(x,rect.bottom()))
            p.drawLine(QtCore.QPointF(rect.left(),y),QtCore.QPointF(rect.right(),y))
        if self.bins is not None and options['channels']:
            channels = [{'L':0,'R':1,'G':2,'B':3}[c] for c in options['channels']]
            heights,percent,group=histogram_plot(self.bins,channels,rect.width(),self.logarithmic)
            font=p.font();font.setPointSizeF(max(8,font.pointSizeF()*.82));p.setFont(font)
            p.setPen(QtGui.QColor(text_on(t.surface)))
            p.drawText(QtCore.QRectF(2,rect.top()-6,38,20),QtCore.Qt.AlignmentFlag.AlignRight,f'{percent:.1f}%')
            p.drawText(24,int(rect.bottom()),'0')
            if options['fill_style']=='solid':
                # Cache just one size/options combination, released on new bins.
                # The 4x256 snapshot also detects in-place changes by old callers.
                key=(self.width(),self.height(),self.devicePixelRatioF(),tuple(channels),options['kind'],options['scale'])
                if key!=self._solid_cache_key or not np.array_equal(self.bins,self._solid_cache[0]):
                    areas={channel:histogram_area(values,rect,options['kind']) for channel,values in zip(channels,heights)}
                    self._solid_cache=(self.bins.copy(),areas,solid_histogram_layer(areas,self.size(),self.devicePixelRatioF()))
                    self._solid_cache_key=key
                _,areas,layer=self._solid_cache
                if 0 in areas:p.fillPath(areas[0],QtGui.QColor('#a0a0a0'))
                p.drawImage(QtCore.QPointF(0,0),layer)
            for channel, values in zip(channels, heights):
                if options['fill_style']=='solid':continue
                color=QtGui.QColor([t.tone,t.red,t.green,t.blue][channel])
                fill=QtGui.QColor(color);fill.setAlpha(65)
                p.setBrush(fill if options['filled'] else QtCore.Qt.BrushStyle.NoBrush);p.setPen(QtGui.QPen(color,1.1))
                if options['kind']=='bars':
                    step=rect.width()/len(values)
                    for i,value in enumerate(values):
                        if value>0:p.drawRect(QtCore.QRectF(rect.left()+i*step,rect.bottom()-float(value)*rect.height(),step,float(value)*rect.height()))
                    continue
                path = QtGui.QPainterPath()
                for i, value in enumerate(values):
                    y = rect.bottom()-float(value)*rect.height()
                    if i==0:path.moveTo(rect.left(),y)
                    path.lineTo(rect.left()+(i+.5)/len(values)*rect.width(),y)
                    if i==len(values)-1:path.lineTo(rect.right(),y)
                if options['filled']:
                    area=QtGui.QPainterPath(path);area.lineTo(rect.right(),rect.bottom());area.lineTo(rect.left(),rect.bottom());area.closeSubpath()
                    p.fillPath(area,fill)
                p.setBrush(QtCore.Qt.BrushStyle.NoBrush)
                p.drawPath(path)
        else:
            empty=text('等待预览','Awaiting preview') if self.bins is None else text('右键选择显示通道','Right-click to select channels')
            p.setPen(QtGui.QColor(text_on(plot_bg)));p.drawText(rect,QtCore.Qt.AlignmentFlag.AlignCenter,empty)
        p.setPen(QtGui.QColor(text_on(t.surface)))
        p.drawText(44,self.height()-7,'0')
        p.drawText(int(rect.center().x())-9,self.height()-5,'128')
        p.drawText(self.width()-36,self.height()-7,'255')
