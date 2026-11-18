"""Disposable preview marks. Source pixmaps and export arrays stay untouched."""
import numpy as np
from film_foundry.ui.qt import QtCore,QtGui,QtWidgets


class _MaskItem(QtWidgets.QGraphicsPixmapItem):
    def __init__(self,parent):
        super().__init__(parent);self.clip_rect=QtCore.QRectF()
        self.setAcceptedMouseButtons(QtCore.Qt.MouseButton.NoButton)
        self.setZValue(1)

    def paint(self,painter,option,widget=None):
        painter.save();painter.setClipRect(self.clip_rect,QtCore.Qt.ClipOperation.IntersectClip)
        super().paint(painter,option,widget);painter.restore()


class ClippingOverlay(QtCore.QObject):
    def __init__(self,view,settings):
        super().__init__(view);self.view=view;self.settings=settings;self.key=None
        self.item=_MaskItem(view._image);self.item.hide()
        settings.changed.connect(self.sync)

    def sync(self,*_):
        options=self.settings.values
        if self.view.source is None or not (options['shadows'] or options['highlights']):
            self.item.hide();return
        source,offset,rect=self.view.clipping_region()
        key=(source.cacheKey(),options['shadows'],options['highlights'])
        if key!=self.key:
            image=source.toImage().convertToFormat(QtGui.QImage.Format.Format_RGB888)
            h,w=image.height(),image.width()
            pixels=np.frombuffer(image.constBits(),np.uint8).reshape(h,image.bytesPerLine())[:,:w*3].reshape(h,w,3)
            rgba=np.zeros((h,w,4),np.uint8)
            if options['shadows']:rgba[np.any(pixels==0,axis=2)]=(45,100,255,210)
            if options['highlights']:rgba[np.any(pixels==255,axis=2)]=(255,65,45,210)
            mask=QtGui.QImage(rgba.data,w,h,rgba.strides[0],QtGui.QImage.Format.Format_RGBA8888).copy()
            self.item.setPixmap(QtGui.QPixmap.fromImage(mask));self.key=key
        self.item.setPos(offset);self.item.clip_rect=rect;self.item.show();self.item.update()
