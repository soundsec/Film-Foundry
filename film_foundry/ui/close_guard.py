"""Native close interception. The frontend decides whether its task can close."""
from film_foundry.ui.qt import QtCore


class CloseGuard(QtCore.QObject):
    def __init__(self, window, allow_close):
        super().__init__(window)
        self.allow_close = allow_close
        window.installEventFilter(self)

    def eventFilter(self, watched, event):
        if event.type() == QtCore.QEvent.Type.Close and not self.allow_close():
            event.ignore()
            return True
        return super().eventFilter(watched, event)
