"""Read-only desktop image inspection; display transforms never change image data."""
from __future__ import annotations

import numpy as np
from film_foundry.ui.qt import QtCore, QtGui, QtWidgets
from film_foundry.ui.curve_editor import text


class ImageView(QtWidgets.QGraphicsView):
    zoom_changed = QtCore.Signal(int)
    pixels_changed = QtCore.Signal(object)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.source = None
        self._fit_mode = True
        self.setMinimumSize(180, 160)
        self.setSizePolicy(QtWidgets.QSizePolicy.Policy.Ignored, QtWidgets.QSizePolicy.Policy.Expanding)
        scene = QtWidgets.QGraphicsScene(self)
        self.setScene(scene)
        self._image = scene.addPixmap(QtGui.QPixmap())
        self._image.setTransformationMode(QtCore.Qt.TransformationMode.SmoothTransformation)
        self._empty = scene.addText(text("选择来源，更新预览", "Choose a source and update preview"))
        self._empty.setDefaultTextColor(QtGui.QColor("#c4c4c4"))
        self.setBackgroundBrush(QtGui.QColor("#222222"))
        self.setStyleSheet("QGraphicsView {background:#222222; border:0;}")
        self.setFrameShape(QtWidgets.QFrame.Shape.NoFrame)
        self.setDragMode(QtWidgets.QGraphicsView.DragMode.ScrollHandDrag)
        self.setTransformationAnchor(QtWidgets.QGraphicsView.ViewportAnchor.AnchorUnderMouse)
        self.setResizeAnchor(QtWidgets.QGraphicsView.ViewportAnchor.AnchorViewCenter)
        self.setRenderHint(QtGui.QPainter.RenderHint.SmoothPixmapTransform)
        self.setAccessibleName(text("图像预览", "Image preview"))
        self.setToolTip(text("滚轮缩放 · 拖动平移 · 双击适合窗口", "Scroll to zoom · drag to pan · double-click to fit"))

    def show_array(self, array):
        # Same display conversion as the previous label viewer.
        pixels = np.ascontiguousarray(np.clip(array, 0, 1)*255, dtype=np.uint8)
        h, w = pixels.shape[:2]
        image = QtGui.QImage(pixels.data, w, h, pixels.strides[0], QtGui.QImage.Format.Format_RGB888).copy()
        pixmap = QtGui.QPixmap.fromImage(image)
        preserve = self.source is not None and self.source.size() == pixmap.size() and not self._fit_mode
        self.source = pixmap
        self.pixels_changed.emit(pixels)
        self._sync_source()
        if not preserve:
            self.fit()

    def clear_image(self, message=''):
        self.source = None
        self._image.setPixmap(QtGui.QPixmap())
        self._empty.setPlainText(message)
        self._sync_source()
        self.setSceneRect(self._empty.boundingRect())

    def show_image(self,image,*,preserve_view=False):
        """Upgrade a preview without changing the inspected region or fitted view."""
        previous=self.source
        keep=preserve_view and previous is not None and not self._fit_mode
        if keep:
            center=self.mapToScene(self.viewport().rect().center())
            relative=QtCore.QPointF(center.x()/previous.width(),center.y()/previous.height())
            scale=self.transform().m11()*previous.width()/image.width()
        self.source=QtGui.QPixmap.fromImage(image)
        self._sync_source()
        if keep:
            self.setTransform(QtGui.QTransform.fromScale(scale,scale))
            self.centerOn(relative.x()*image.width(),relative.y()*image.height())
            self.zoom_changed.emit(self.zoom_percent)
        else:self.fit()

    def _sync_source(self):
        present = self.source is not None
        self._empty.setVisible(not present)
        self._image.setVisible(present)
        if present:
            self._image.setPixmap(self.source)
            self.setSceneRect(self._image.boundingRect())

    def fit(self):
        self._fit_mode = True
        self._sync_source()
        if self.source is not None:
            self.setHorizontalScrollBarPolicy(QtCore.Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
            self.setVerticalScrollBarPolicy(QtCore.Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
            self.fitInView(self._image, QtCore.Qt.AspectRatioMode.KeepAspectRatio)
            self.zoom_changed.emit(self.zoom_percent)

    def actual_size(self):
        self._set_scale(1.0/self.viewport().devicePixelRatioF())

    @property
    def zoom_percent(self):
        return round(self.transform().m11()*self.viewport().devicePixelRatioF()*100)

    def zoom(self, factor):
        self._set_scale(self.transform().m11()*factor)

    def _set_scale(self, scale):
        if self.source is None:
            return
        self._fit_mode = False
        self.setHorizontalScrollBarPolicy(QtCore.Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self.setVerticalScrollBarPolicy(QtCore.Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        scale = min(16.0, max(.02, scale))
        self.scale(scale/self.transform().m11(), scale/self.transform().m22())
        self.zoom_changed.emit(self.zoom_percent)

    def wheelEvent(self, event):
        if self.source is None:
            return super().wheelEvent(event)
        delta = event.angleDelta().y() or event.pixelDelta().y()
        if delta:
            self.zoom(1.2 if delta > 0 else 1/1.2)
        event.accept()

    def mouseDoubleClickEvent(self, event):
        if event.button() == QtCore.Qt.MouseButton.LeftButton:
            self.fit()
            event.accept()
        else:
            super().mouseDoubleClickEvent(event)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if self._fit_mode:
            self.fit()
