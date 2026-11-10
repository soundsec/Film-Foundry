"""Cached source/result inspection; switching never invokes image processing."""
import numpy as np
from film_foundry.ui.qt import QtCore,QtGui,QtWidgets
from film_foundry.ui.image_view import ImageView
from film_foundry.ui.curve_editor import text


class ComparisonView(ImageView):
    MODES=('result','side_by_side','wipe')
    def __init__(self,parent=None):
        super().__init__(parent)
        self.original=None;self.result=None;self.mode='result';self.divider=.5
        self.rotation_quarters=0;self._holding=False;self._wiping=False
        self._press=None;self._aligned=None;self._statistics_key=None

    @staticmethod
    def pixmap(array):
        pixels=np.ascontiguousarray(np.clip(array,0,1)*255,dtype=np.uint8)
        h,w=pixels.shape[:2]
        return QtGui.QPixmap.fromImage(QtGui.QImage(pixels.data,w,h,pixels.strides[0],QtGui.QImage.Format.Format_RGB888).copy())

    def set_original(self,array):
        self.original=None if array is None else self.pixmap(array)
        self._aligned=None;self._present()

    def show_array(self,array):
        self.result=self.pixmap(array);self._aligned=None;self._present()

    def set_pair(self,original,result):
        self.original=self.pixmap(original);self.result=self.pixmap(result)
        self._aligned=None;self._present()

    def set_rotation(self,quarters):
        self.rotation_quarters=int(quarters)%4;self._aligned=None;self._present()

    def set_comparison_mode(self,mode):
        if mode not in self.MODES:raise ValueError('Unknown preview comparison')
        self.mode=mode;self._holding=False;self._present()

    def clear_image(self,message=''):
        self.original=self.result=self._aligned=None;self._holding=False;self._statistics_key=None
        super().clear_image(message)

    def _pair(self):
        if self._aligned is not None:return self._aligned
        result=self.result or self.original
        if result is None:return None
        original=self.original or result
        transform=QtGui.QTransform().rotate(90*self.rotation_quarters)
        if self.rotation_quarters:
            original=original.transformed(transform);result=result.transformed(transform)
        # Letterbox differing source/medium frames; never stretch the source.
        aligned=QtGui.QPixmap(result.size());aligned.fill(QtGui.QColor('#222222'))
        scaled=original.scaled(result.size(),QtCore.Qt.AspectRatioMode.KeepAspectRatio,QtCore.Qt.TransformationMode.SmoothTransformation)
        painter=QtGui.QPainter(aligned);painter.drawPixmap((result.width()-scaled.width())//2,(result.height()-scaled.height())//2,scaled);painter.end()
        self._aligned=(aligned,result);return self._aligned

    def _present(self):
        pair=self._pair()
        if pair is None:return
        original,result=pair;w,h=result.width(),result.height()
        if self.mode=='result':canvas=original if self._holding else result
        else:
            canvas=QtGui.QPixmap(w*2 if self.mode=='side_by_side' else w,h);canvas.fill(QtGui.QColor('#222222'))
            painter=QtGui.QPainter(canvas)
            if self.mode=='side_by_side':
                painter.drawPixmap(0,0,original);painter.drawPixmap(w,0,result)
            else:
                x=round(w*self.divider);painter.drawPixmap(0,0,result)
                painter.setClipRect(0,0,x,h);painter.drawPixmap(0,0,original);painter.setClipping(False)
                painter.setPen(QtGui.QPen(QtGui.QColor('white'),1));painter.drawLine(x,0,x,h)
            painter.end()
        same=self.source is not None and self.source.size()==canvas.size()
        self.source=canvas;self._sync_source()
        if self._fit_mode or not same:self.fit()
        # Comparisons are display layouts, not a new image to meter. Dragging
        # the divider reuses the result histogram without rescanning pixels.
        measured=original if self.mode=='result' and self._holding else result
        if self._statistics_key==measured.cacheKey():return
        self._statistics_key=measured.cacheKey()
        image=measured.toImage().convertToFormat(QtGui.QImage.Format.Format_RGB888)
        pixels=np.frombuffer(image.constBits(),np.uint8).reshape(image.height(),image.bytesPerLine())[:,:image.width()*3].reshape(image.height(),image.width(),3)
        self.pixels_changed.emit(pixels)

    def _move_divider(self,event):
        if self.source is not None:
            self.divider=min(1.,max(0.,self.mapToScene(event.position().toPoint()).x()/self.source.width()))
            self._present()

    def mousePressEvent(self,event):
        if event.button()==QtCore.Qt.MouseButton.LeftButton and self.original is not None:
            self._press=event.position()
            if self.mode=='wipe':
                x=self.mapFromScene(QtCore.QPointF(self.source.width()*self.divider,0)).x()
                if abs(event.position().x()-x)<=14:
                    self._wiping=True;self._move_divider(event);event.accept();return
            elif self.mode=='result':self._holding=True;self._present()
        super().mousePressEvent(event)

    def mouseMoveEvent(self,event):
        if self._wiping:self._move_divider(event);event.accept();return
        if self._holding and self._press is not None and (event.position()-self._press).manhattanLength()>QtWidgets.QApplication.startDragDistance():
            self._holding=False;self._present()
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self,event):
        if self._wiping:self._wiping=False;event.accept();return
        if self._holding:self._holding=False;self._present()
        self._press=None;super().mouseReleaseEvent(event)

    def focusOutEvent(self,event):
        if self._holding:self._holding=False;self._present()
        super().focusOutEvent(event)


def comparison_choice(view,parent=None):
    combo=QtWidgets.QComboBox(parent)
    for key,zh,en in (('result','仅预览','Preview only'),('side_by_side','原图并列','Side by side'),('wipe','切分对比','Split comparison')):
        combo.addItem(text(zh,en),key)
    combo.setToolTip(text('并列：左原图、右结果。切分：拖动分界线。仅预览：按住左键看原图，松开恢复；拖动仍可平移。对比模式的直方图统计结果。','Side by side: source left, result right. Split: drag divider. Preview only: hold left mouse for source, release for result; drag to pan. Comparison histograms meter the result.'))
    combo.currentIndexChanged.connect(lambda:view.set_comparison_mode(combo.currentData()))
    return combo
