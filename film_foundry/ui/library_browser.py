"""Read-only gallery browsing, with ratings but no workflow dispatch."""
from pathlib import Path
from film_foundry.app.catalog_assets import path_key, asset_descriptor
from film_foundry.ui.desktop_library import ResultStackView, asset_label
from film_foundry.ui.qt import QtCore, QtGui, QtWidgets
from film_foundry.ui.curve_editor import text


class LibraryBrowser(ResultStackView):
    def __init__(self,entries,identity,catalog,parent=None,*,annotations_changed=None):
        self.entries=entries;self.position=next(i for i,e in enumerate(entries) if e['id']==identity)
        entry=self.stack_entry(entries[self.position],catalog)
        super().__init__(entry,None,parent,catalog=catalog,annotations_changed=annotations_changed,browse_only=True)
        sidebar=QtWidgets.QSplitter(QtCore.Qt.Orientation.Vertical,self)
        self.browser_sidebar=sidebar
        sidebar.setMinimumWidth(250)
        info=QtWidgets.QWidget();details=QtWidgets.QVBoxLayout(info);details.setContentsMargins(4,4,8,8)
        heading=QtWidgets.QLabel(text('文件信息','File information'));heading.setObjectName('sectionTitle');details.addWidget(heading)
        form=QtWidgets.QFormLayout();form.setFieldGrowthPolicy(QtWidgets.QFormLayout.FieldGrowthPolicy.AllNonFixedFieldsGrow);details.addLayout(form)
        self.file_info={}
        for key,zh,en in (('name','名称','Name'),('type','类型','Type'),('dimensions','尺寸','Dimensions'),('size','文件大小','File size'),('modified','修改时间','Modified'),('source','来源','Source'),('path','位置','Location')):
            value=QtWidgets.QLineEdit();value.setReadOnly(True);value.setFrame(False);value.setMinimumWidth(90)
            value.setStyleSheet('QLineEdit { border: 0; background: transparent; padding: 2px 0; }')
            self.file_info[key]=value;form.addRow(text(zh,en),value)
        details.addStretch()
        stack=QtWidgets.QWidget();stack_layout=QtWidgets.QVBoxLayout(stack);stack_layout.setContentsMargins(4,4,8,0)
        stack_heading=QtWidgets.QLabel(text('文件堆叠','File stack'));stack_heading.setObjectName('sectionTitle');stack_layout.addWidget(stack_heading)
        for label in (heading,stack_heading):
            font=label.font();font.setBold(True);label.setFont(font)
        self.split.replaceWidget(0,sidebar)
        stack_layout.addWidget(self.tree,1);sidebar.addWidget(info);sidebar.addWidget(stack)
        sidebar.setStretchFactor(1,1);sidebar.setSizes([290,430]);self.split.setSizes([320,900])
        self.tree.setColumnWidth(0,145)
        row=QtWidgets.QHBoxLayout()
        self.previous=QtWidgets.QPushButton(text('上一张','Previous'));self.previous.clicked.connect(lambda:self.move(-1));row.addWidget(self.previous)
        self.counter=QtWidgets.QLabel();row.addWidget(self.counter,1)
        for label,callback in ((text('适合窗口','Fit'),self.preview.fit),('100%',self.preview.actual_size)):
            button=QtWidgets.QToolButton();button.setText(label);button.clicked.connect(callback);row.addWidget(button)
        self.zoom_label=QtWidgets.QLabel();self.zoom_label.setMinimumWidth(48);row.addWidget(self.zoom_label)
        self.preview.zoom_changed.connect(lambda value:self.zoom_label.setText(f'{value}%'))
        self.zoom_label.setText(f'{self.preview.zoom_percent}%' if self.preview.source is not None else '—')
        self.next=QtWidgets.QPushButton(text('下一张','Next'));self.next.clicked.connect(lambda:self.move(1));row.addWidget(self.next)
        self.layout().insertLayout(0,row)
        for key,delta in (('Left',-1),('Right',1)):
            shortcut=QtGui.QShortcut(QtGui.QKeySequence(key),self)
            shortcut.setContext(QtCore.Qt.ShortcutContext.WidgetWithChildrenShortcut)
            shortcut.activated.connect(lambda d=delta:self.move(d))
        self.update_navigation()
        self.update_file_information()

    def show_selected(self,*args):
        super().show_selected(*args)
        if hasattr(self,'file_info'):self.update_file_information()

    def update_file_information(self):
        from datetime import datetime
        asset=self.selected_asset();values={key:'—' for key in self.file_info}
        if asset:
            path=Path(asset['path'])
            source=next((run.get('source','') for run in self.entry['runs'] if any(a['path']==str(path) for a in run.get('assets',[]))),self.entry['runs'][0].get('source',''))
            values.update(name=path.name,type=asset_label(asset)+' · '+path.suffix.lstrip('.').upper(),path=str(path),source=source or '—')
            try:
                stat=path.stat();size=stat.st_size
                values['size']=f'{size/1048576:.2f} MB' if size>=1048576 else f'{size/1024:.1f} KB'
                values['modified']=datetime.fromtimestamp(stat.st_mtime).strftime('%Y-%m-%d %H:%M:%S')
                if path.suffix.lower() in ('.png','.jpg','.jpeg','.tif','.tiff','.webp','.bmp'):
                    reader=QtGui.QImageReader(str(path));dimensions=reader.size()
                    if dimensions.isValid():values['dimensions']=f'{dimensions.width()} × {dimensions.height()} px'
            except OSError:values['size']=text('文件不存在','Missing file')
        for key,widget in self.file_info.items():
            widget.setText(values[key]);widget.setToolTip(values[key]);widget.setCursorPosition(0)

    @staticmethod
    def stack_entry(entry,catalog):
        if 'runs' in entry:return entry
        stack=next((s for s in catalog.results if s['id']==entry.get('stack_id') or
                    any(path_key(a['path'])==path_key(entry['path']) for a in catalog.assets(s))),None)
        if stack:return dict(stack,focus_asset='asset:'+path_key(entry['path']))
        asset=asset_descriptor(entry['path'],'source');asset.update(rating=entry['rating'],favorite=entry['favorite'])
        return dict(id=entry['id'],label=Path(entry['path']).name,rating=entry['rating'],favorite=entry['favorite'],
            focus_asset=asset['id'],runs=[dict(source=entry['path'],medium=entry['path'],family='',artifacts={},assets=[asset])])

    def update_navigation(self):
        self.previous.setEnabled(self.position>0);self.next.setEnabled(self.position+1<len(self.entries))
        self.counter.setText(f'{self.position+1} / {len(self.entries)}')

    def move(self,delta):
        target=self.position+delta
        if 0<=target<len(self.entries):
            self.position=target;self.load(self.stack_entry(self.entries[target],self.catalog));self.update_navigation()
