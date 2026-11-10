"""Search a snapshot of visible preset labels; apply only after confirmation."""
from film_foundry.ui.qt import QtCore, QtWidgets
from film_foundry.ui.curve_editor import text


class PresetPicker(QtWidgets.QDialog):
    def __init__(self, parent, title, labels, current, records=None):
        super().__init__(parent)
        self.setWindowTitle(title);self.resize(560,460)
        self.selected_index=None
        layout=QtWidgets.QVBoxLayout(self)
        self.search=QtWidgets.QLineEdit(self)
        self.search.setPlaceholderText(text("搜索预设名称…","Search preset names…"))
        self.search.setAccessibleName(text("搜索预设","Search presets"));self.search.setClearButtonEnabled(True)
        layout.addWidget(self.search)
        self.records=records or [{} for _ in labels]
        filters=QtWidgets.QHBoxLayout();layout.addLayout(filters);self.filters={}
        for key,caption in (('origin',text('来源不限','Any source')),('color',text('色彩不限','Any colour')),('polarity',text('类型不限','Any type'))):
            values=sorted({record[key] for record in self.records if record.get(key)})
            if not values:continue
            combo=QtWidgets.QComboBox();combo.addItem(caption,'')
            names={'builtin':('内置','Built-in'),'custom':('自己保存的','Saved by me'),'color':('彩色','Colour'),'monochrome':('黑白','B&W'),'negative':('负片','Negative'),'positive':('正片','Positive')}
            for value in values:combo.addItem(text(*names.get(value,(value,value))),value)
            self.filters[key]=combo;filters.addWidget(combo)
            combo.currentIndexChanged.connect(lambda:self._filter(self.search.text()))
        reset=QtWidgets.QToolButton();reset.setText(text('清除筛选','Clear filters'));filters.addWidget(reset)
        reset.clicked.connect(self.clear_filters)
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
            record=self.records[row]
            searchable=(item.text()+' '+record.get('key','')+' '+record.get('name','')).casefold()
            matches=all(word in searchable for word in words) and all(not combo.currentData() or record.get(key)==combo.currentData() for key,combo in self.filters.items())
            item.setHidden(not matches)
            if matches:visible.append(item)
        if self.results.currentItem() not in visible:
            self.results.setCurrentItem(visible[0] if visible else None)
        self._sync_selection()
        self.count.setText(text(f"{len(visible)} 个预设",f"{len(visible)} presets"))

    def clear_filters(self):
        for combo in self.filters.values():combo.setCurrentIndex(0)
        self.search.clear();self._filter('')

    def _apply(self):
        item=self.results.currentItem()
        if item is not None and not item.isHidden():
            self.selected_index=item.data(QtCore.Qt.ItemDataRole.UserRole)
            self.accept()
