"""Collapsible form section; folding only changes presentation."""
from film_foundry.ui.qt import QtCore, QtWidgets, Widget


class Disclosure(Widget):
    def __init__(self, parent, title, *, expanded=True, on_toggle=None):
        frame=QtWidgets.QFrame(parent._content_widget())
        frame.setObjectName("parameterSection")
        outer=QtWidgets.QVBoxLayout(frame);outer.setContentsMargins(0,0,0,0);outer.setSpacing(0)
        self.header=QtWidgets.QToolButton(frame)
        self.header.setObjectName("sectionToggle")
        self.header.setText(title.replace("&","&&"))
        self.header.setToolButtonStyle(QtCore.Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        self.header.setSizePolicy(QtWidgets.QSizePolicy.Policy.Expanding,QtWidgets.QSizePolicy.Policy.Fixed)
        self.header.setCheckable(True)
        self.header.setAccessibleName(title)
        self.title=title
        self.body=QtWidgets.QWidget(frame)
        outer.addWidget(self.header);outer.addWidget(self.body)
        super().__init__(parent,frame,padding=10)
        self._on_toggle=on_toggle
        self.header.toggled.connect(self.set_expanded)
        self.set_expanded(expanded)

    def _content_widget(self):
        return self.body

    @property
    def expanded(self):
        return self.header.isChecked()

    def set_expanded(self, expanded):
        expanded=bool(expanded)
        blocker=QtCore.QSignalBlocker(self.header)
        self.header.setChecked(expanded)
        del blocker
        self.header.setArrowType(QtCore.Qt.ArrowType.DownArrow if expanded else QtCore.Qt.ArrowType.RightArrow)
        self.body.setVisible(expanded)
        if self._on_toggle is not None:self._on_toggle(expanded)

    def set_summary(self, summary):
        label=self.title+(" · "+summary if summary else "")
        self.header.setText(label.replace("&","&&"))
        self.header.setToolTip(label)
