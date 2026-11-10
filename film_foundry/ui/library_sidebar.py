"""Folder and work-history navigation for the indexed gallery."""
from film_foundry.app.desktop_settings import load_settings, save_settings, sidebar_key, sidebar_entry, update_sidebar_entry
from film_foundry.ui.qt import QtCore, QtGui, QtWidgets
from film_foundry.ui.curve_editor import text


class LibrarySidebar(QtWidgets.QWidget):
    indexed = QtCore.Signal(object, str)

    def __init__(self, library, open_work=None, settings=None, live_work=None):
        super().__init__(library)
        self.library=library;self.open_work=open_work;self.live_work=live_work;self.indexing=False
        layout=QtWidgets.QVBoxLayout(self);layout.setContentsMargins(0,0,8,0)
        self.tree=QtWidgets.QTreeWidget();self.tree.setHeaderHidden(True);layout.addWidget(self.tree,1)
        self.tree.itemClicked.connect(self.select)
        self.tree.setContextMenuPolicy(QtCore.Qt.ContextMenuPolicy.CustomContextMenu)
        self.tree.customContextMenuRequested.connect(self.context_menu)
        for zh,en,callback in (('添加来源文件夹…','Add source folder…',self.add_folder),
                               ('刷新来源','Refresh sources',self.index_folders),
                               ('打开已有工作…','Open saved work…',self.choose_work)):
            button=QtWidgets.QPushButton(text(zh,en));button.clicked.connect(callback);layout.addWidget(button)
        self.indexed.connect(self.finish_index);self.refresh()

    def refresh(self):
        self.tree.clear()
        all_item=QtWidgets.QTreeWidgetItem(self.tree,[text('全部图片','All images')]);all_item.setData(0,QtCore.Qt.ItemDataRole.UserRole,('folder',''))
        active=QtWidgets.QTreeWidgetItem(self.tree,[text('当前打开的工作','Open work')])
        for label,page in self.live_work() if self.live_work else ():
            item=QtWidgets.QTreeWidgetItem(active,[label]);item.setData(0,QtCore.Qt.ItemDataRole.UserRole,('live',page))
            self.decorate(item,{'locked':bool(page.property('navigationLocked'))})
        recent=QtWidgets.QTreeWidgetItem(self.tree,[text('最近会话与工作','Recent sessions & work')])
        values=load_settings()
        for record in values['recent_work']:
            item=QtWidgets.QTreeWidgetItem(recent,[record['label']]);item.setToolTip(0,record['path']);item.setData(0,QtCore.Qt.ItemDataRole.UserRole,('work',record))
            self.decorate(item,sidebar_entry('work',record['path'],values))
        folders=QtWidgets.QTreeWidgetItem(self.tree,[text('来源文件夹','Source folders')])
        from pathlib import Path
        known=set(values['source_folders'])
        known.update(str(Path(e['path']).parent) for e in self.library.catalog.entries)
        for path in sorted(known):
            if sidebar_key('folder',path) in values['hidden_folders']:continue
            item=QtWidgets.QTreeWidgetItem(folders,[Path(path).name or path]);item.setToolTip(0,path);item.setData(0,QtCore.Qt.ItemDataRole.UserRole,('folder',path))
            self.decorate(item,sidebar_entry('folder',path,values))
        for item in (active,recent,folders):item.setExpanded(True)

    def decorate(self,item,metadata):
        if metadata.get('label'):item.setText(0,metadata['label'])
        if metadata.get('locked'):
            icon=QtGui.QPixmap(18,18);icon.fill(QtCore.Qt.GlobalColor.transparent)
            painter=QtGui.QPainter(icon);painter.setRenderHint(QtGui.QPainter.RenderHint.Antialiasing)
            painter.setPen(QtGui.QPen(self.palette().text().color(),1.5));painter.setBrush(QtCore.Qt.BrushStyle.NoBrush)
            painter.drawArc(5,1,8,10,0,180*16);painter.drawRoundedRect(3,7,12,9,2,2);painter.drawLine(9,10,9,13);painter.end()
            item.setIcon(0,QtGui.QIcon(icon))
            item.setToolTip(0,item.toolTip(0)+'\n'+text('已锁定：先解锁才能改名或移除入口。','Locked: unlock before renaming or removing this entry.'))

    def context_menu(self,position):
        item=self.tree.itemAt(position)
        menu=self.management_menu(item)
        if menu:
            menu.exec(self.tree.viewport().mapToGlobal(position));menu.deleteLater()

    def management_menu(self,item):
        value=item.data(0,QtCore.Qt.ItemDataRole.UserRole) if item else None
        if not value or (value[0]=='folder' and not value[1]):return None
        kind,data=value
        path=data['path'] if kind=='work' else data if kind=='folder' else None
        locked=bool(data.property('navigationLocked')) if kind=='live' else bool(sidebar_entry(kind,path).get('locked'))
        menu=QtWidgets.QMenu(self)
        rename=menu.addAction(text('重命名…','Rename…'),lambda:self.rename_entry(value,item.text(0)))
        rename.setToolTip(text('只修改软件内的显示名称，不移动或重命名原文件。','Change the display name only; files stay unchanged.'));rename.setEnabled(not locked)
        menu.addAction(text('解锁','Unlock') if locked else text('锁定','Lock'),lambda:self.lock_entry(value,not locked))
        menu.addSeparator()
        label=text('关闭工作','Close work') if kind=='live' else text('从历史记录移除','Remove from history') if kind=='work' else text('从侧栏移除','Remove from sidebar')
        remove=menu.addAction(label,lambda:self.remove_entry(value));remove.setEnabled(not locked)
        remove.setToolTip(text('不会删除磁盘上的文件。来源文件夹移除后停止自动检索，已入库图片保留。','Files are retained. Removing a source stops automatic indexing; existing catalog images remain.'))
        if kind=='live':remove.setEnabled(not locked and bool(data._workspace_host._pages[data][2]))
        menu.setToolTipsVisible(True)
        return menu

    def rename_entry(self,value,current):
        label,ok=QtWidgets.QInputDialog.getText(self,text('重命名入口','Rename entry'),text('显示名称','Display name'),text=current)
        if not ok or not label.strip():return
        kind,data=value
        try:
            if kind=='live':
                if data.property('navigationLocked'):return
                host=data._workspace_host;_,key,closable=host._pages[data]
                host._pages[data]=(label.strip(),key,closable);host.refresh_activity()
            else:update_sidebar_entry(kind,data['path'] if kind=='work' else data,label=label)
            self.refresh()
        except (OSError,ValueError) as exc:self.library.status.setText(str(exc))

    def lock_entry(self,value,locked):
        kind,data=value
        try:
            if kind=='live':data.setProperty('navigationLocked',locked)
            else:update_sidebar_entry(kind,data['path'] if kind=='work' else data,locked=locked)
            self.refresh()
        except (OSError,ValueError) as exc:self.library.status.setText(str(exc))

    def remove_entry(self,value):
        kind,data=value
        try:
            if kind=='live':
                if data.property('navigationLocked'):return
                host=data._workspace_host;host.close_page(host.indexOf(data))
            else:
                update_sidebar_entry(kind,data['path'] if kind=='work' else data,remove=True)
                if kind=='folder' and self.library.folder_filter==data:
                    self.library.folder_filter='';self.library.refresh()
            self.refresh()
        except (OSError,ValueError) as exc:self.library.status.setText(str(exc))

    def select(self,item,*_):
        value=item.data(0,QtCore.Qt.ItemDataRole.UserRole)
        if not value:return
        kind,data=value
        if kind=='folder':self.library.folder_filter=data;self.library.refresh()
        elif kind=='work' and self.open_work:self.open_work(data['path'],data['kind'])
        elif kind=='live':data._workspace_host.setCurrentWidget(data)

    def choose_work(self):
        menu=QtWidgets.QMenu(self)
        def file():
            path,_=QtWidgets.QFileDialog.getOpenFileName(self,text('打开会话','Open session'),'','Film Foundry session (*.json)')
            if path and self.open_work:self.open_work(path,'print' if path.lower().endswith('project.json') else 'session')
        def package():
            path=QtWidgets.QFileDialog.getExistingDirectory(self,text('选择 .fffilm / .ffworkspace 工作目录','Choose .fffilm / .ffworkspace folder'))
            if path and self.open_work:self.open_work(path,'film' if path.lower().endswith('.fffilm') else 'material')
        menu.addAction(text('会话文件…','Session file…'),file)
        menu.addAction(text('工作目录包…','Work package…'),package)
        menu.exec(self.mapToGlobal(self.rect().bottomLeft()))

    def add_folder(self):
        path=QtWidgets.QFileDialog.getExistingDirectory(self,text('添加图库来源','Add library source'))
        if not path:return
        values=load_settings();values['source_folders'].append(path)
        values['hidden_folders']=[key for key in values['hidden_folders'] if key!=sidebar_key('folder',path)]
        save_settings(values)
        self.refresh();self.index_folders()

    def index_folders(self):
        if self.indexing:return
        folders=load_settings()['source_folders']
        if not folders:return
        self.indexing=True;self.library.status.setText(text('正在检索来源文件夹…','Indexing source folders…'))
        catalog=self.library.catalog;signals=self.indexed
        future=self.library.pool.submit(catalog.index_candidates,folders)
        def done(task):
            try:paths=task.result();error=''
            except Exception as exc:paths=[];error=str(exc)
            try:signals.emit(paths,error)
            except RuntimeError:pass
        future.add_done_callback(done)

    def finish_index(self,paths,error):
        self.indexing=False
        if error:self.library.status.setText(error);return
        try:
            # Recheck removals made while the background directory walk ran.
            from film_foundry.app.catalog_assets import path_key
            from pathlib import Path
            folders=[Path(p).resolve() for p in load_settings()['source_folders']]
            paths=[p for p in paths if path_key(p) not in self.library.catalog.hidden_paths
                   and any(Path(p).resolve().is_relative_to(folder) for folder in folders)]
            self.library.catalog.add(paths)
            self.library.refresh();self.refresh()
        except (OSError,ValueError) as exc:self.library.status.setText(str(exc))
