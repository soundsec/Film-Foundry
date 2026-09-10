"""Reusable PySide6 scrolling form containers."""

from __future__ import annotations

from PySide6 import QtWidgets

from film_foundry.ui.qt import ui


class VerticalScrolledFrame(ui.Frame):
    """Native Qt scroll area exposing the form container as ``content``."""

    def __init__(self, parent, *, canvas_width: int = 500, **kwargs) -> None:
        super().__init__(parent, **kwargs)
        scroll = QtWidgets.QScrollArea(parent._content_widget())
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QtWidgets.QFrame.Shape.NoFrame)
        scroll.setMinimumWidth(int(canvas_width))
        self.qt.deleteLater()
        self.qt = scroll
        self.content = ui.Frame(self, padding=(4, 2, 8, 8))
        scroll.setWidget(self.content.qt)
        self.content.qt.show()

    def _content_widget(self):
        return self.qt.viewport()

    def bind_mousewheel(self) -> None:
        """QScrollArea owns wheel routing and scroll-range coalescing."""

    def scroll_to_top(self) -> None:
        self.qt.verticalScrollBar().setValue(0)
