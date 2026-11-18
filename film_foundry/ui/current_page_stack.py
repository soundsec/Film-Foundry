"""Form stack whose unused stages do not reserve vertical space."""
from film_foundry.ui.qt import QtCore, QtWidgets


class CurrentPageStack(QtWidgets.QStackedWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setSizePolicy(QtWidgets.QSizePolicy.Policy.Preferred,QtWidgets.QSizePolicy.Policy.Maximum)
        self.layout().setSizeConstraint(QtWidgets.QLayout.SizeConstraint.SetNoConstraint)
        self.currentChanged.connect(self._page_changed)

    def sizeHint(self):
        page=self.currentWidget()
        return page.sizeHint() if page is not None else QtCore.QSize(0,0)

    def minimumSizeHint(self):
        page=self.currentWidget()
        return page.minimumSizeHint() if page is not None else QtCore.QSize(0,0)

    def hasHeightForWidth(self):
        page=self.currentWidget()
        return bool(page is not None and page.hasHeightForWidth())

    def heightForWidth(self,width):
        page=self.currentWidget()
        return page.heightForWidth(width) if page is not None else 0

    def _page_changed(self,*_):
        self.updateGeometry()
        parent=self.parentWidget()
        if parent is not None and parent.layout() is not None:parent.layout().invalidate()
