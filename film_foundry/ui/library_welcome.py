"""Library entry points and local file drops; uses existing catalogue contracts."""
from pathlib import Path
from film_foundry.ui.qt import QtCore, QtWidgets
from film_foundry.ui.curve_editor import text
from film_foundry.app.desktop_catalog import IMAGE_SUFFIXES


def supported_path(path):
    path=Path(path)
    if path.is_dir():return path.suffix.lower() not in ('.fffilm','.ffworkspace')
    return path.is_file() and (path.suffix.lower() in IMAGE_SUFFIXES|{'.npz'} or path.name.lower().endswith('result.json'))


class LibraryWelcome(QtWidgets.QWidget):
    def __init__(self,library):
        super().__init__();self.library=library
        root=QtWidgets.QVBoxLayout(self);root.setContentsMargins(24,24,24,24);root.addStretch()
        self.title=QtWidgets.QLabel();self.title.setObjectName('sectionTitle');self.title.setWordWrap(True);root.addWidget(self.title)
        font=self.title.font();font.setPointSize(17);font.setBold(True);self.title.setFont(font)
        self.note=QtWidgets.QLabel();self.note.setWordWrap(True);root.addWidget(self.note)
        from film_foundry.ui.flow_toolbar import FlowToolbar
        row=FlowToolbar();root.addLayout(row);self.buttons={}
        for key,zh,en,action in (
            ('photos','导入照片…','Import photos…',library.import_dialog),
            ('materials','打开底片或潜影…','Open medium or latent…',lambda:library.import_dialog(materials=True)),
            ('roll','整卷暗房…','Whole-roll darkroom…',lambda:library.open_roll() if library.open_roll else None)):
            button=QtWidgets.QPushButton(text(zh,en));button.clicked.connect(action);row.addWidget(button);self.buttons[key]=button
        self.buttons['roll'].setEnabled(library.open_roll is not None)
        self.clear=QtWidgets.QPushButton(text('清除筛选','Clear filters'));self.clear.clicked.connect(library.clear_filters);root.addWidget(self.clear,0,QtCore.Qt.AlignmentFlag.AlignLeft)
        root.addStretch()

    def update_state(self,has_entries):
        library=self.library
        library.grid_pages.setCurrentWidget(library.grid if has_entries else self)
        if has_entries:return
        filtered=bool(library.search.text() or library.source_filter.text() or library.folder_filter or library.favorites.isChecked() or library.type_filter.currentData() or library.family_filter.currentData() or library.relation.currentData()!='any')
        self.title.setText(text('没有匹配的内容','No matching items') if filtered else text('从一张照片或一份材料开始','Start with a photo or a material'))
        self.note.setText(text('清除筛选可查看当前分类。也可拖入照片、底片、潜影或来源文件夹；导入仅登记来源，不搬动原文件，也不自动冲洗。',
            'Clear filters to view this collection. Drop photos, media, latents or source folders here. Import references files in place; it does not move them or start processing.') if filtered else text(
            '把照片、底片、潜影或来源文件夹拖到这里。也可以从下面的入口开始；已有材料导入后选择相应工序继续。',
            'Drop photos, media, latents or source folders here, or use an entry below. Import existing material, then choose its available workflow.'))
        self.clear.setVisible(filtered)


class LibraryDropTarget(QtCore.QObject):
    def __init__(self,library):
        super().__init__(library);self.library=library
        for target in (library,library.grid.viewport(),library.welcome):
            target.setAcceptDrops(True);target.installEventFilter(self)

    @staticmethod
    def paths(mime):
        # No network URLs, text interpretation or implicit file moves.
        return list(dict.fromkeys(url.toLocalFile() for url in mime.urls() if url.isLocalFile())) if mime.hasUrls() else []

    def eventFilter(self,target,event):
        kind=event.type()
        if kind not in (QtCore.QEvent.Type.DragEnter,QtCore.QEvent.Type.DragMove,QtCore.QEvent.Type.Drop):return False
        paths=self.paths(event.mimeData())
        accepted=[p for p in paths if supported_path(p)]
        if not accepted:event.ignore();return True
        event.setDropAction(QtCore.Qt.DropAction.CopyAction);event.accept()
        if kind==QtCore.QEvent.Type.Drop:
            self.import_paths(accepted)
        return True

    def import_paths(self,paths):
        files=[p for p in paths if Path(p).is_file()]
        folders=[p for p in paths if Path(p).is_dir()]
        if files:self.library.import_paths(files)
        if folders:
            from film_foundry.app.desktop_settings import load_settings,save_settings,sidebar_key
            try:
                values=load_settings()
                for path in folders:
                    if path not in values['source_folders']:values['source_folders'].append(path)
                    key=sidebar_key('folder',path)
                    values['hidden_folders']=[p for p in values['hidden_folders'] if p!=key]
                save_settings(values)
                self.library.clear_filters();self.library.collection.setCurrentIndex(0)
                self.library.sidebar.refresh();self.library.sidebar.index_folders()
            except (OSError,ValueError) as exc:self.library.status.setText(str(exc))
