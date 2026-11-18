"""Extensible desktop shortcut catalogue and shared, persisted bindings.

Add a ShortcutSpec here and its action to WorkShortcuts.invoke. Settings rows,
conflict checks, defaults, help and matching all use this catalogue.
"""
from dataclasses import dataclass
from film_foundry.ui.qt import QtCore, QtGui, QtWidgets
from film_foundry.ui.curve_editor import text
from film_foundry.ui.preferences import load_preferences, save_preferences, preferences_path


@dataclass(frozen=True)
class ShortcutSpec:
    key: str
    zh: str
    en: str
    defaults: tuple
    scope: str = 'work'
    hold: bool = False


SHORTCUTS = (
    ShortcutSpec('undo','撤回','Undo',('Ctrl+Z',)),
    ShortcutSpec('redo','重做','Redo',('Ctrl+Y','Ctrl+Shift+Z')),
    ShortcutSpec('save','保存当前工作','Save current work',('Ctrl+S',)),
    ShortcutSpec('preview','更新预览','Update preview',('F5',)),
    ShortcutSpec('compare','切换对比方式','Cycle comparison',('Ctrl+\\',)),
    ShortcutSpec('hold_source','按住看原图','Hold source preview',('Space',),hold=True),
    ShortcutSpec('history','预览历史','Preview history',('Ctrl+H',)),
    ShortcutSpec('hold_previous','按住看上次预览','Hold previous preview',('Shift+Space',),hold=True),
    ShortcutSpec('previous_stage','上一阶段','Previous stage',('Alt+Left',),scope='film'),
    ShortcutSpec('next_stage','下一阶段','Next stage',('Alt+Right',),scope='film'),
    ShortcutSpec('execute','正式执行…','Run job…',('Ctrl+Return',),scope='film'),
    ShortcutSpec('strip','打开试条','Open test strip',('Ctrl+T',)),
    ShortcutSpec('fit','适合窗口','Fit to window',('Ctrl+0',)),
    ShortcutSpec('actual','实际像素','Actual pixels',('Ctrl+1',)),
    ShortcutSpec('guide','本页指南','Page guide',('F1',),scope='window'),
    ShortcutSpec('advanced','当前页进阶参数','Current page advanced controls',('F8',),scope='film'),
)
SPECS = {spec.key:spec for spec in SHORTCUTS}


def canonical(value):
    if not isinstance(value,str):raise ValueError(text('无效快捷键。','Invalid shortcut.'))
    if not value:return ''
    sequence=QtGui.QKeySequence.fromString(value,QtGui.QKeySequence.SequenceFormat.PortableText)
    if sequence.count()!=1 or sequence[0].key() in (QtCore.Qt.Key.Key_unknown,0):
        raise ValueError(text('每项使用一个按键组合，不使用连续多段快捷键。','Use one key combination per binding, not a multi-step sequence.'))
    key=sequence[0].key()
    if key in (QtCore.Qt.Key.Key_Control,QtCore.Qt.Key.Key_Shift,QtCore.Qt.Key.Key_Alt,QtCore.Qt.Key.Key_Meta):
        raise ValueError(text('请包含一个非修饰键。','Include a non-modifier key.'))
    if sequence[0].keyboardModifiers()==QtCore.Qt.KeyboardModifier.NoModifier and key in (
        QtCore.Qt.Key.Key_Tab,QtCore.Qt.Key.Key_Backtab,QtCore.Qt.Key.Key_Escape,QtCore.Qt.Key.Key_Return,QtCore.Qt.Key.Key_Enter):
        raise ValueError(text('Tab、Esc、回车保留给焦点、关闭与确认。','Tab, Escape and Enter are reserved for focus, closing and confirmation.'))
    return sequence.toString(QtGui.QKeySequence.SequenceFormat.PortableText)


def defaults():return {s.key:list(s.defaults) for s in SHORTCUTS}


def validated(values):
    result={};used={}
    for spec in SHORTCUTS:
        keys=values.get(spec.key,[])
        if not isinstance(keys,(list,tuple)) or len(keys)>2:raise ValueError(text('每项最多两个快捷键。','Up to two bindings per action.'))
        result[spec.key]=[]
        for value in keys:
            key=canonical(value)
            if not key:continue
            if key in used:
                other=SPECS[used[key]]
                raise ValueError(text(f'{key} 重复：{other.zh} / {spec.zh}',f'{key} conflicts: {other.en} / {spec.en}'))
            used[key]=spec.key;result[spec.key].append(key)
    return result


def loaded(values):
    # Preserve explicit unbinding; new defaults never steal an existing custom key.
    values=values if isinstance(values,dict) else {}
    result=defaults();used=set()
    ordered=sorted(SHORTCUTS,key=lambda s:s.key not in values)
    for spec in ordered:
        candidates=values.get(spec.key,spec.defaults)
        if not isinstance(candidates,(list,tuple)):candidates=spec.defaults
        result[spec.key]=[]
        for value in candidates[:2]:
            try:key=canonical(value)
            except ValueError:continue
            if key and key not in used:result[spec.key].append(key);used.add(key)
    return result


class ShortcutSettings(QtCore.QObject):
    changed=QtCore.Signal()
    _models={}

    def __init__(self,path):
        super().__init__(QtWidgets.QApplication.instance())
        self.path=path;self.values=loaded(load_preferences(path).get('shortcuts'))
        self._index={key:action for action,keys in self.values.items() for key in keys}

    @classmethod
    def current(cls):
        path=preferences_path()
        if path not in cls._models:cls._models[path]=cls(path)
        return cls._models[path]

    def apply(self,values):
        values=validated(values)
        save_preferences(shortcuts=values,path=self.path)
        self.values=values;self._index={key:action for action,keys in values.items() for key in keys}
        self.changed.emit()

    def command(self,event):
        sequence=QtGui.QKeySequence(event.keyCombination()).toString(QtGui.QKeySequence.SequenceFormat.PortableText)
        return self._index.get(sequence)

    def label(self,action):
        return ' / '.join(QtGui.QKeySequence(k).toString(QtGui.QKeySequence.SequenceFormat.NativeText) for k in self.values[action]) or text('未设置','Unassigned')


class ShortcutHint(QtCore.QObject):
    def __init__(self,widget,action):
        super().__init__(widget)
        self.widget=widget;self.action=action;self.base=widget.toolTip();self.model=ShortcutSettings.current()
        self.model.changed.connect(self.update);self.update()

    @QtCore.Slot()
    def update(self):self.widget.setToolTip(self.base+'\n'+self.model.label(self.action))


def bind_hint(widget,action):
    widget._shortcut_hint=ShortcutHint(widget,action)


class ShortcutSettingsPanel(QtWidgets.QWidget):
    def __init__(self,parent=None):
        super().__init__(parent);self.model=ShortcutSettings.current();self.editors={}
        root=QtWidgets.QVBoxLayout(self)
        note=QtWidgets.QLabel(text('点击键位框录入组合键；每项可设主键和备用键，清空即停用。编辑后点击“应用快捷键”，立即生效并保存。',
            'Record a primary and optional alternate key combination. Clear to unbind. Apply shortcuts to activate and save your changes immediately.'))
        note.setWordWrap(True);root.addWidget(note)
        table=QtWidgets.QScrollArea();table.setWidgetResizable(True);root.addWidget(table,1)
        body=QtWidgets.QWidget();grid=QtWidgets.QGridLayout(body);table.setWidget(body)
        for column,label in enumerate((text('功能','Action'),text('主键','Primary'),text('备用键','Alternate'))):grid.addWidget(QtWidgets.QLabel(label),0,column)
        for row,spec in enumerate(SHORTCUTS,1):
            label=QtWidgets.QLabel(text(spec.zh,spec.en));label.setWordWrap(True);grid.addWidget(label,row,0)
            editors=[]
            for column in (1,2):
                editor=QtWidgets.QKeySequenceEdit();editor.setMaximumSequenceLength(1);editor.setClearButtonEnabled(True)
                editor.setMinimumWidth(100);grid.addWidget(editor,row,column);editors.append(editor)
            self.editors[spec.key]=editors
        grid.setRowStretch(len(SHORTCUTS)+1,1)
        note=QtWidgets.QLabel(text('作用于当前工作；指南可用于主窗口各页面，进阶参数用于胶片页。输入框保留文字编辑，配置/作弊弹窗不穿透。独立材料窗口的保存操作保存材料状态。按住对照在松键、失焦或切页时复原，不重算图像。',
            'Actions target the active work; guide works across main pages, advanced controls on film pages. Text editing stays native and dialogs block underlying work. Standalone material Save stores material state. Hold comparison releases on key-up, focus loss or page changes without recomputing.'))
        note.setWordWrap(True);root.addWidget(note)
        self.status=QtWidgets.QLabel();self.status.setWordWrap(True);root.addWidget(self.status)
        row=QtWidgets.QHBoxLayout();root.addLayout(row)
        reset=QtWidgets.QPushButton(text('恢复默认快捷键','Restore default shortcuts'));reset.clicked.connect(self.reset);row.addWidget(reset);row.addStretch()
        apply=QtWidgets.QPushButton(text('应用快捷键','Apply shortcuts'));apply.clicked.connect(self.apply);row.addWidget(apply)
        self.model.changed.connect(self.refresh);self.refresh()

    @QtCore.Slot()
    def refresh(self):
        for action,editors in self.editors.items():
            keys=self.model.values[action]
            for index,editor in enumerate(editors):
                editor.setKeySequence(QtGui.QKeySequence(keys[index] if index<len(keys) else ''))
                editor.findChild(QtWidgets.QLineEdit).setPlaceholderText(text('录入快捷键','Press shortcut'))

    def apply(self):
        values={action:[e.keySequence().toString(QtGui.QKeySequence.SequenceFormat.PortableText) for e in editors] for action,editors in self.editors.items()}
        self.commit(values)

    def reset(self):self.commit(defaults())

    def commit(self,values):
        try:self.model.apply(values)
        except (ValueError,OSError) as exc:self.status.setText(str(exc));return
        self.status.setText(text('快捷键已应用并保存。','Shortcuts applied and saved.'))
