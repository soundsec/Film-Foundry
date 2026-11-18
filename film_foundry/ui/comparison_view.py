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
        self.held_preview=None
        self._keyboard_source=False
        self._press=None;self._aligned=None;self._rotated=None;self._statistics_key=None

    def _frames_changed(self):
        self._aligned=None
        self._rotated=None
        self._present()

    def set_original(self,image,*,as_result=False):
        self.original=image if image is None or isinstance(image,QtGui.QPixmap) else self.pixmap(image)
        if as_result:self.result=self.original
        self._frames_changed()

    def show_array(self,array):
        self.result=self.pixmap(array);self._frames_changed()

    def set_pair(self,original,result,*,rotation=None):
        self.original=original if isinstance(original,QtGui.QPixmap) else self.pixmap(original)
        self.result=self.pixmap(result)
        if rotation is not None:self.rotation_quarters=int(rotation)%4
        self._frames_changed()

    def set_pixmap_pair(self,original,result):
        self.original=original;self.result=result
        self._holding=False;self._frames_changed()

    def set_rotation(self,quarters):
        quarters=int(quarters)%4
        if quarters==self.rotation_quarters:return
        self.rotation_quarters=quarters;self._frames_changed()

    def set_comparison_mode(self,mode):
        if mode not in self.MODES:raise ValueError('Unknown preview comparison')
        self.mode=mode;self._holding=False;self._present()

    def set_source_held(self,held):
        held=bool(held) and self.original is not None
        if held==self._keyboard_source:return
        self._keyboard_source=held;self._present()

    def clear_image(self,message=''):
        self.held_preview=None
        self.original=self.result=self._rotated=self._aligned=None;self._holding=False;self._keyboard_source=False;self._statistics_key=None
        super().clear_image(message)

    def displayed_result(self):
        result=self.result or self.original
        if result is None or not self.rotation_quarters:return result
        if self._rotated is None:
            self._rotated=result.transformed(QtGui.QTransform().rotate(90*self.rotation_quarters))
        return self._rotated

    def _aligned_original(self,result):
        if self._aligned is not None:return self._aligned
        if self.original is None:original=result
        elif self.rotation_quarters:
            original=self.original.transformed(QtGui.QTransform().rotate(90*self.rotation_quarters))
        else:original=self.original
        if original.size()==result.size() and original.devicePixelRatio()==1 and not original.hasAlphaChannel():
            self._aligned=original
            return original
        aligned=QtGui.QPixmap(result.size());aligned.fill(QtGui.QColor('#222222'))
        scaled=original.scaled(result.size(),QtCore.Qt.AspectRatioMode.KeepAspectRatio,QtCore.Qt.TransformationMode.SmoothTransformation)
        painter=QtGui.QPainter(aligned);painter.drawPixmap((aligned.width()-scaled.width())//2,(aligned.height()-scaled.height())//2,scaled);painter.end()
        self._aligned=aligned
        return aligned

    def _present(self):
        result=self.displayed_result()
        if result is None:return
        w,h=result.width(),result.height()
        original=None
        if self.held_preview is None and (self._keyboard_source or self._holding or self.mode!='result'):
            original=self._aligned_original(result)
        if self.held_preview is not None:canvas=self.held_preview
        elif self._keyboard_source:canvas=original
        elif self.mode=='result':canvas=original if self._holding else result
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
        measured=self.held_preview or (original if self._keyboard_source or self.mode=='result' and self._holding else result)
        if self._statistics_key==measured.cacheKey():return
        self._statistics_key=measured.cacheKey()
        image=measured.toImage().convertToFormat(QtGui.QImage.Format.Format_RGB888)
        pixels=np.frombuffer(image.constBits(),np.uint8).reshape(image.height(),image.bytesPerLine())[:,:image.width()*3].reshape(image.height(),image.width(),3)
        self.pixels_changed.emit(pixels)

    def drawForeground(self,painter,rect):
        super().drawForeground(painter,rect)
        overlay=getattr(self,'probe_overlay',None)
        if overlay:overlay(painter)

    def _move_divider(self,event):
        if self.source is not None:
            width=self.source.width()
            divider=min(1.,max(0.,self.mapToScene(event.position().toPoint()).x()/width))
            moved=round(self.divider*width)!=round(divider*width)
            self.divider=divider
            if moved:self._present()

    def clipping_region(self):
        if self.held_preview is not None or self._keyboard_source or self.mode=='result':return super().clipping_region()
        result=self.displayed_result();rect=QtCore.QRectF(result.rect());offset=QtCore.QPointF()
        if self.mode=='side_by_side':offset.setX(result.width())
        else:rect.setLeft(round(result.width()*self.divider))
        return result,offset,rect

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
        self.set_source_held(False)
        if self._holding:self._holding=False;self._present()
        super().focusOutEvent(event)


def comparison_choice(view,parent=None):
    combo=QtWidgets.QComboBox(parent)
    for key,zh,en in (('result','对比：单图','Compare: single'),('side_by_side','对比：并列','Compare: side by side'),('wipe','对比：切分（拖线）','Compare: split (drag)')):
        combo.addItem(text(zh,en),key)
    combo.setToolTip(text('并列：左原图、右结果。切分：拖动分界线。单图：按住左键看原图，松开恢复；拖动仍可平移。对比模式的直方图统计结果。','Side by side: source left, result right. Split: drag divider. Single: hold left mouse for source, release for result; drag to pan. Comparison histograms meter the result.'))
    combo.currentIndexChanged.connect(lambda:view.set_comparison_mode(combo.currentData()))
    return combo
