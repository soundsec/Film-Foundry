"""A view of the existing film controls; the workbench owns mode and drafts."""
from film_foundry.ui.qt import QtCore, QtWidgets
from film_foundry.ui.curve_editor import text


class OperatingModePicker(QtWidgets.QWidget):
    mode_changed = QtCore.Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setToolTip(text('在常用与上次的按效果 / 按药浴之间切换，只作用于当前冲洗页。',
            'Switch between Common and the last By effect / By bath mode on this development page.'))
        from film_foundry.ui.shortcut_settings import bind_hint
        bind_hint(self,'advanced')
        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 8)
        row = QtWidgets.QHBoxLayout()
        row.setSpacing(2)
        layout.addLayout(row)
        self.group = QtWidgets.QButtonGroup(self)
        self.buttons = {}
        for key, zh, en in (('normal', '常用', 'Common'), ('expected', '按效果', 'By effect'), ('principle', '按药浴', 'By bath')):
            button = QtWidgets.QToolButton()
            button.setText(text(zh, en))
            button.setCheckable(True)
            button.setSizePolicy(QtWidgets.QSizePolicy.Policy.Expanding, QtWidgets.QSizePolicy.Policy.Fixed)
            button.setAccessibleName(text('冲洗控制：', 'Development controls: ') + text(zh, en))
            self.group.addButton(button)
            self.buttons[key] = button
            row.addWidget(button)
            button.clicked.connect(lambda checked=False, mode=key: self.mode_changed.emit(mode))
        self.note = QtWidgets.QLabel()
        self.note.setWordWrap(True)
        self.note.setObjectName('sectionNote')
        layout.addWidget(self.note)

    def sync(self, mode, *, editable=True):
        self.buttons[mode].setChecked(True)
        for button in self.buttons.values():
            button.setEnabled(editable)
        descriptions = {
            'normal': ('调节常用冲洗条件；更多控制见“按效果”或“按药浴”。', 'Adjust common development conditions; choose By effect or By bath for more control.'),
            'expected': ('按期望效果调节材料响应；成片仍受工艺与通道耦合影响。', 'Set the intended material response; processing and channel interactions still affect the image.'),
            'principle': ('直接编辑药浴顺序、时间、温度与操作，控制形成过程。', 'Control formation through bath order, time, temperature and operations.'),
        }
        self.note.setText(text(*descriptions[mode]) if editable else text('本阶段已冻结或不在本次流程中，操控模式不可更改。', 'This stage is frozen or outside this task; its control mode cannot be changed here.'))
