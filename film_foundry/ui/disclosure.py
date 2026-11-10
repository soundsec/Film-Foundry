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
        head=QtWidgets.QHBoxLayout();head.setContentsMargins(0,0,0,0);head.setSpacing(0)
        head.addWidget(self.header,1)
        from film_foundry.ui.control_reset import reset_button
        self.reset_button=reset_button(frame)
        self.reset_button.setToolTip('重置这一组 / Reset section');self.reset_button.hide();head.addWidget(self.reset_button)
        outer.addLayout(head);outer.addWidget(self.body)
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
        help_text=self.qt.toolTip()
        self.header.setToolTip(label+("\n"+help_text if help_text else ""))

    def set_tooltip(self, text):
        super().set_tooltip(text)
        self.header.setToolTip(text)
