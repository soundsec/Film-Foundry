"""Search a snapshot of visible preset labels; apply only after confirmation."""
from film_foundry.ui.qt import QtCore, QtWidgets
from film_foundry.ui.curve_editor import text


class PresetPicker(QtWidgets.QDialog):
    def __init__(self, parent, title, labels, current):
        super().__init__(parent)
        self.setWindowTitle(title);self.resize(560,460)
        self.selected_index=None
        layout=QtWidgets.QVBoxLayout(self)
        self.search=QtWidgets.QLineEdit(self)
        self.search.setPlaceholderText(text("搜索预设名称…","Search preset names…"))
        self.search.setAccessibleName(text("搜索预设","Search presets"));self.search.setClearButtonEnabled(True)
        layout.addWidget(self.search)
        self.results=QtWidgets.QListWidget(self);self.results.setWordWrap(True)
        self.results.setHorizontalScrollBarPolicy(QtCore.Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        for index,label in enumerate(labels):
            item=QtWidgets.QListWidgetItem(label,self.results)
            item.setData(QtCore.Qt.ItemDataRole.UserRole,index);item.setToolTip(label)
        self.results.setCurrentRow(current)
        layout.addWidget(self.results,1)
        self.count=QtWidgets.QLabel(self);layout.addWidget(self.count)
        buttons=QtWidgets.QDialogButtonBox(self)
        self.apply=buttons.addButton(text("使用所选预设","Use selected preset"),QtWidgets.QDialogButtonBox.ButtonRole.AcceptRole)
        buttons.addButton(text("取消","Cancel"),QtWidgets.QDialogButtonBox.ButtonRole.RejectRole)
        layout.addWidget(buttons)
        buttons.accepted.connect(self._apply);buttons.rejected.connect(self.reject)
        self.search.textChanged.connect(self._filter)
        self.results.currentItemChanged.connect(lambda *_:self._sync_selection())
        self.results.itemDoubleClicked.connect(lambda _:self._apply())
        self._filter("")

    def _sync_selection(self):
        item=self.results.currentItem()
        self.apply.setEnabled(item is not None and not item.isHidden())

    def _filter(self, query):
        words=query.casefold().split();visible=[]
        for row in range(self.results.count()):
            item=self.results.item(row)
            matches=all(word in item.text().casefold() for word in words)
            item.setHidden(not matches)
            if matches:visible.append(item)
        if self.results.currentItem() not in visible:
            self.results.setCurrentItem(visible[0] if visible else None)
        self._sync_selection()
        self.count.setText(text(f"{len(visible)} 个预设",f"{len(visible)} presets"))

    def _apply(self):
        item=self.results.currentItem()
        if item is not None and not item.isHidden():
            self.selected_index=item.data(QtCore.Qt.ItemDataRole.UserRole)
            self.accept()
