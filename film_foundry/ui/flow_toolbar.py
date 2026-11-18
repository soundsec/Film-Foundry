"""Compact tool rows that wrap whole controls without clipping their labels."""
from film_foundry.ui.qt import QtCore, QtWidgets


class FlowToolbar(QtWidgets.QLayout):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.items = []
        self.setContentsMargins(0, 0, 0, 0)
        self.setSpacing(4)

    def addItem(self, item):
        self.items.append(item)

    def addWidget(self, widget):
        if isinstance(widget, QtWidgets.QAbstractButton):
            widget.setProperty('toolbarAction', True)
        super().addWidget(widget)

    def addStretch(self, *_):
        pass  # Leave the remainder of each row free rather than stretch buttons.

    def count(self):
        return len(self.items)

    def itemAt(self, index):
        return self.items[index] if 0 <= index < len(self.items) else None

    def takeAt(self, index):
        return self.items.pop(index) if 0 <= index < len(self.items) else None

    def expandingDirections(self):
        return QtCore.Qt.Orientation(0)

    def hasHeightForWidth(self):
        return True

    def heightForWidth(self, width):
        return self._arrange(QtCore.QRect(0, 0, width, 0), False)

    def minimumSize(self):
        size = QtCore.QSize()
        for item in self.items:
            if not item.isEmpty():
                size = size.expandedTo(item.minimumSize())
        return size

    def sizeHint(self):
        return self.minimumSize()

    def setGeometry(self, rect):
        super().setGeometry(rect)
        self._arrange(rect, True)

    def _arrange(self, rect, apply):
        x, y, row_height = rect.x(), rect.y(), 0
        for item in self.items:
            if item.isEmpty():
                continue
            size = item.sizeHint().expandedTo(item.minimumSize())
            if row_height and x + size.width() > rect.x() + rect.width():
                x = rect.x()
                y += row_height + self.spacing()
                row_height = 0
            if apply:
                item.setGeometry(QtCore.QRect(QtCore.QPoint(x, y), size))
            x += size.width() + self.spacing()
            row_height = max(row_height, size.height())
        return y + row_height - rect.y()
