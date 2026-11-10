"""Display-only histogram of the exact preview pixels; never modifies media."""
import numpy as np
from film_foundry.ui.qt import QtCore, QtGui, QtWidgets
from film_foundry.ui.curve_editor import text


def display_bins(pixels):
    rgb = np.asarray(pixels, dtype=np.uint8).reshape(-1, 3)
    wide = rgb.astype(np.int32)
    brightness = (wide @ np.array([2126, 7152, 722]) + 5000) // 10000
    return np.stack([np.bincount(brightness, minlength=256)] +
                    [np.bincount(rgb[:, c], minlength=256) for c in range(3)])


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


class Histogram(QtWidgets.QWidget):
    def __init__(self, parent=None, *, compact=False):
        super().__init__(parent)
        self.bins = None
        self.brightness = False
        self.logarithmic = False
        self.setFixedHeight(100 if compact else 150)
        self.setToolTip(text("当前显示预览，非RAW曝光余量。点击切换RGB/亮度；右键切换线性/对数。",
                             "Displayed preview, not RAW headroom. Click for RGB/brightness; right-click for linear/log."))
        self.setAccessibleName(text("预览直方图", "Preview histogram"))

    def set_pixels(self, pixels):
        self.bins = display_bins(pixels)
        self.update()

    def mousePressEvent(self, event):
        if event.button() == QtCore.Qt.MouseButton.RightButton:
            self.logarithmic = not self.logarithmic
        else:
            self.brightness = not self.brightness
        self.update()

    def paintEvent(self, event):
        p = QtGui.QPainter(self)
        p.setRenderHint(QtGui.QPainter.RenderHint.Antialiasing,True)
        p.fillRect(self.rect(), QtGui.QColor("#181818"))
        p.setPen(QtGui.QColor("#cccccc"))
        mode = text("亮度", "Brightness") if self.brightness else "RGB"
        scale = text("对数", "Log") if self.logarithmic else text("线性", "Linear")
        p.drawText(8, 15, f"{mode} · {scale}")
        rect = QtCore.QRectF(42, 26, max(1,self.width()-54), max(1,self.height()-48))
        p.fillRect(rect, QtGui.QColor("#000000"))
        if self.bins is not None:
            channels = [0] if self.brightness else [1, 2, 3]
            heights,percent,group=histogram_plot(self.bins,channels,rect.width(),self.logarithmic)
            p.setPen(QtGui.QColor('#aaaaaa'))
            p.drawText(1,37,f'{percent:.1f}%')
            p.drawText(8,int(rect.bottom()),'0')
            p.setCompositionMode(QtGui.QPainter.CompositionMode.CompositionMode_Plus)
            p.setPen(QtCore.Qt.PenStyle.NoPen)
            for channel, values in zip(channels, heights):
                p.setBrush(QtGui.QColor(["#bbbbbb", "#cc0000", "#00cc00", "#0000cc"][channel]))
                path = QtGui.QPainterPath(QtCore.QPointF(rect.left(), rect.bottom()))
                for i, value in enumerate(values):
                    y = rect.bottom()-float(value)*rect.height()
                    if i==0:path.lineTo(rect.left(),y)
                    path.lineTo(rect.left()+(i+.5)/len(values)*rect.width(),y)
                    if i==len(values)-1:path.lineTo(rect.right(),y)
                path.lineTo(rect.right(), rect.bottom());path.closeSubpath()
                p.drawPath(path)
            p.setCompositionMode(QtGui.QPainter.CompositionMode.CompositionMode_SourceOver)
            for high in (False, True):
                rgb = [204 if self.bins[c, 255 if high else 0] else 60 for c in (1, 2, 3)]
                p.setBrush(QtGui.QColor(*rgb))
                x = rect.right()-8 if high else rect.left()
                p.drawPolygon(QtGui.QPolygonF([QtCore.QPointF(x, 20), QtCore.QPointF(x+8, 20), QtCore.QPointF(x+4, 27)]))
        p.setPen(QtGui.QColor("#555555"))
        for i in range(1, 4):
            x = rect.left()+rect.width()*i/4
            p.drawLine(QtCore.QPointF(x, rect.top()), QtCore.QPointF(x, rect.bottom()))
            y=rect.top()+rect.height()*i/4
            p.drawLine(QtCore.QPointF(rect.left(),y),QtCore.QPointF(rect.right(),y))
        p.setPen(QtGui.QColor("#aaaaaa"))
        p.drawText(42, self.height()-5, "0")
        p.drawText(int(rect.center().x())-9,self.height()-5,'128')
        p.drawText(self.width()-32, self.height()-5, "255")
