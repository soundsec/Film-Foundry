"""Source-library landing page; dispatches existing workflows, never computes film."""
from collections import OrderedDict
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from film_foundry.app.desktop_catalog import DesktopCatalog
from film_foundry.ui.qt import QtCore, QtGui, QtWidgets
from film_foundry.ui.curve_editor import text
from film_foundry.ui.image_cache import preview_images

FAMILIES=(('film','胶片','Film'),('peel','撕拉片','Peel-apart'),('integral','拍立得','Instant'),('plate','银版','Silver plate'),
          ('paper','相纸','Paper'),('wet','湿版','Wet collodion'),('cyanotype','蓝晒','Cyanotype'),('platinum','铂钯','Platinum / palladium'))
ASSET_TYPES=(('','全部类型','All types'),('source','原始图像','Source images'),('latent','未冲洗潜影','Undeveloped exposures'),
    ('medium','胶片介质','Film media'),('negative','可用负片','Usable negatives'),('material','最终片体','Final bodies'),
    ('auxiliary','附属片体','Companion bodies'),('scan','扫描 / 观察成片','Scans / observations'),
    ('scanner_raw','扫描采集','Scanner captures'),('state','处理状态','Process states'),('group','关联介质组','Product groups'),('preview','预览图','Previews'),('document','配置 / 记录','Settings / records'))


def asset_label(asset):
    from film_foundry.ui.optical_source_panel import BODY_LABELS
    if asset['kind']=='group':return text('关联介质组','Product group')
    if asset['role'] in BODY_LABELS:return text(*BODY_LABELS[asset['role']])
    return next((text(zh,en) for key,zh,en in ASSET_TYPES if key==asset['kind']),asset['role'])


def favorite_icon(active=False):
    pixmap=QtGui.QPixmap(24,24);pixmap.fill(QtCore.Qt.GlobalColor.transparent)
    painter=QtGui.QPainter(pixmap);painter.setRenderHint(QtGui.QPainter.RenderHint.Antialiasing)
    path=QtGui.QPainterPath();path.moveTo(12,21);path.cubicTo(9,18,2,13,2,7)
    path.cubicTo(2,1,10,0,12,6);path.cubicTo(14,0,22,1,22,7);path.cubicTo(22,13,15,18,12,21)
    painter.setPen(QtGui.QPen(QtGui.QColor('#ef4150' if active else '#b8bec6'),1.5))
    painter.setBrush(QtGui.QColor('#ef4150') if active else QtCore.Qt.BrushStyle.NoBrush);painter.drawPath(path);painter.end()
    return QtGui.QIcon(pixmap)


def rating_icon(active=False):
    from math import cos,sin,pi
    from film_foundry.ui.theme import colors
    pixmap=QtGui.QPixmap(24,24);pixmap.fill(QtCore.Qt.GlobalColor.transparent)
    painter=QtGui.QPainter(pixmap);painter.setRenderHint(QtGui.QPainter.RenderHint.Antialiasing)
    path=QtGui.QPainterPath()
    for index in range(10):
        radius=10 if index%2==0 else 4.5;angle=-pi/2+index*pi/5
        point=QtCore.QPointF(12+radius*cos(angle),12+radius*sin(angle))
        if index==0:path.moveTo(point)
        else:path.lineTo(point)
    path.closeSubpath();color=QtGui.QColor(colors().accent if active else colors().muted)
    painter.setPen(QtGui.QPen(color,1.4));painter.setBrush(color if active else QtCore.Qt.BrushStyle.NoBrush)
    painter.drawPath(path);painter.end();return QtGui.QIcon(pixmap)


class _CatalogCardDelegate(QtWidgets.QStyledItemDelegate):
    def sizeHint(self,option,index):return QtCore.QSize(232,194)

    def paint(self,painter,option,index):
        entry=index.data(QtCore.Qt.ItemDataRole.UserRole)
        if not entry:return
        painter.save();painter.setRenderHint(QtGui.QPainter.RenderHint.Antialiasing)
        rect=option.rect.adjusted(4,4,-4,-4)
        selected=bool(option.state & QtWidgets.QStyle.StateFlag.State_Selected)
        from film_foundry.ui.theme import colors
        painter.setPen(QtGui.QPen(option.palette.highlight().color() if selected else QtGui.QColor(colors().border),1))
        painter.setBrush(option.palette.highlight() if selected else option.palette.base());painter.drawRoundedRect(rect,5,5)
        picture=QtCore.QRect(rect.x()+8,rect.y()+8,rect.width()-16,126)
        icon=index.data(QtCore.Qt.ItemDataRole.DecorationRole)
        if icon:
            pixmap=icon.pixmap(picture.size());painter.drawPixmap(picture.center().x()-pixmap.width()//2,picture.center().y()-pixmap.height()//2,pixmap)
        color=option.palette.highlightedText().color() if selected else option.palette.text().color()
        painter.setPen(color)
        title=entry.get('label',Path(entry['path']).name)
        painter.drawText(QtCore.QRect(rect.x()+9,rect.y()+139,rect.width()-18,19),QtCore.Qt.AlignmentFlag.AlignLeft,
                         option.fontMetrics.elidedText(title,QtCore.Qt.TextElideMode.ElideMiddle,rect.width()-18))
        detail=asset_label(entry) if 'kind' in entry else text(f"{len(entry['runs'])} 次处理",f"{len(entry['runs'])} runs") if 'runs' in entry else text('原始图像','Original image')
        if entry['rating']:detail+=' · '+str(entry['rating'])+' ★'
        painter.drawText(QtCore.QRect(rect.x()+9,rect.y()+162,rect.width()-18,19),QtCore.Qt.AlignmentFlag.AlignLeft,detail)
        if entry['favorite']:favorite_icon(True).paint(painter,QtCore.QRect(rect.right()-29,rect.y()+12,22,22))
        if self.parent().property('multiSelect'):
            box=QtCore.QRectF(rect.x()+10,rect.y()+10,21,21)
            painter.setPen(QtGui.QPen(QtGui.QColor('#e4e5e6'),1.3))
            painter.setBrush(QtGui.QColor(colors().accent if selected else '#222529'));painter.drawRoundedRect(box,3,3)
            if selected:
                check=QtGui.QPainterPath();check.moveTo(box.x()+4,box.y()+10);check.lineTo(box.x()+9,box.y()+15);check.lineTo(box.x()+17,box.y()+5)
                painter.setPen(QtGui.QPen(QtGui.QColor('#202326'),2.2));painter.setBrush(QtCore.Qt.BrushStyle.NoBrush);painter.drawPath(check)
        painter.restore()


class _ThumbnailSignals(QtCore.QObject):
    ready=QtCore.Signal(object,QtGui.QImage)


class _ResultPreviewSignals(QtCore.QObject):
    ready=QtCore.Signal(int,QtGui.QImage,str)


def image_preview(path, width=192, height=128):
    return preview_images.read(path, width, height, _decode_image_preview)


def _decode_image_preview(path, width, height):
    if Path(path).suffix.lower() == '.dng':
        import numpy as np
        from film_foundry.core.io_utils import load_image
        from film_foundry.core.preview import resize_to_long_edge
        pixels=load_image(path)
        if width>0 and height>0:pixels=resize_to_long_edge(pixels,max(width,height))
        pixels=np.ascontiguousarray(np.clip(pixels*255+.5,0,255).astype(np.uint8))
        h,w=pixels.shape[:2]
        return QtGui.QImage(pixels.data,w,h,pixels.strides[0],QtGui.QImage.Format.Format_RGB888).copy()
    reader=QtGui.QImageReader(str(path));reader.setAutoTransform(True)
    size=reader.size()
    if size.isValid() and width>0 and height>0:reader.setScaledSize(size.scaled(width,height,QtCore.Qt.AspectRatioMode.KeepAspectRatio))
    return reader.read()


def thumbnail(path):
    return image_preview(path)


class DesktopLibrary(QtWidgets.QWidget):
    def __init__(self,open_source,open_editor,return_workbench,parent=None,*,open_batch=None,open_roll=None,open_artifact=None,open_stack=None,print_asset=None,open_browser=None,open_settings=None,open_work=None,live_work=None):
        super().__init__(parent)
        self.catalog=DesktopCatalog();self.open_source=open_source
        self.open_batch=open_batch;self.open_roll=open_roll
        self.open_artifact=open_artifact;self.open_stack=open_stack
        self.print_asset=print_asset
        self.open_browser=open_browser;self.folder_filter=''
        self.items={};self.loading=False;self.failed_thumbnails=set();self.cache=OrderedDict()
        self.pool=ThreadPoolExecutor(max_workers=1,thread_name_prefix='library-thumbnail')
        self.signals=_ThumbnailSignals();self.signals.ready.connect(self._thumbnail_ready)
        self.destroyed.connect(lambda *_,pool=self.pool:pool.shutdown(wait=False,cancel_futures=True))
        root=QtWidgets.QHBoxLayout(self)
        from film_foundry.ui.library_sidebar import LibrarySidebar
        self.sidebar=LibrarySidebar(self,open_work,open_settings,live_work)
        self.sidebar.setMinimumWidth(180);self.sidebar.setMaximumWidth(250);root.addWidget(self.sidebar)
        content=QtWidgets.QWidget();root.addWidget(content,1);layout=QtWidgets.QVBoxLayout(content);layout.setContentsMargins(0,0,0,0)
        header=QtWidgets.QHBoxLayout();layout.addLayout(header)
        brand=QtWidgets.QLabel('FILM FOUNDRY');brand.setStyleSheet('font-size:22px;font-weight:600;');header.addWidget(brand)
        header.addStretch()
        edit=QtWidgets.QToolButton();edit.setText(text('预设编辑器','Preset editors'));edit.setPopupMode(QtWidgets.QToolButton.ToolButtonPopupMode.InstantPopup)
        menu=QtWidgets.QMenu(edit)
        for bank,zh,en in (('film','材料','Material'),('develop','药水','Chemistry'),('scanner','扫描','Scanner')):
            menu.addAction(text(zh,en),lambda bank=bank:open_editor(bank))
        edit.setMenu(menu);edit.setToolTip(text('编辑当前胶片任务的工作副本；显式应用或保存','Edit the current film task copy; apply or save explicitly'))
        if not open_settings:header.addWidget(edit)
        modes=QtWidgets.QHBoxLayout();layout.addLayout(modes)
        self.work_mode=QtWidgets.QComboBox()
        self.work_mode.addItem(text('图像处理模式','Image processing'),'photos')
        self.work_mode.addItem(text('折腾模式 · 整卷暗房','Darkroom · Whole roll'),'darkroom')
        modes.addWidget(self.work_mode)
        self.collection=QtWidgets.QTabBar();self.collection.addTab(text('原始图像','Originals'));self.collection.addTab(text('来源堆叠','Source stacks'));self.collection.addTab(text('全部片体 / 图像','All bodies / images'))
        modes.addWidget(self.collection);modes.addStretch()
        self.roll_button=QtWidgets.QPushButton(text('电子胶卷…','Electronic roll…'));modes.addWidget(self.roll_button)
        self.roll_button.clicked.connect(lambda:open_roll() if open_roll else None)
        self.roll_button.hide()
        row=QtWidgets.QHBoxLayout();layout.addLayout(row)
        add=QtWidgets.QPushButton(text('导入…','Import…'));add.clicked.connect(self.import_dialog);row.addWidget(add)
        self.search=QtWidgets.QLineEdit();self.search.setPlaceholderText(text('搜索文件名','Search filenames'));row.addWidget(self.search,1)
        self.favorites=QtWidgets.QPushButton();self.favorites.setIcon(favorite_icon());self.favorites.setCheckable(True);self.favorites.setToolTip(text('仅收藏','Favorites only'));row.addWidget(self.favorites)
        self.favorites.toggled.connect(lambda checked:self.favorites.setIcon(favorite_icon(checked)))
        self.relation=QtWidgets.QComboBox()
        for label,key in ((text('所有评级','Any rating'),'any'),('=','eq'),('>','gt'),('<','lt'),('≥','ge'),('≤','le')):self.relation.addItem(label,key)
        row.addWidget(self.relation)
        self.minimum=QtWidgets.QComboBox();self.minimum.addItems([str(i)+' ★' for i in range(6)]);row.addWidget(self.minimum)
        self.sort=QtWidgets.QComboBox()
        for label,key in ((text('时间','Time'),'time'),(text('文件名','Filename'),'name'),(text('大小','Size'),'bytes')):self.sort.addItem(label,key)
        row.addWidget(self.sort)
        filters=QtWidgets.QHBoxLayout();layout.addLayout(filters)
        self.type_filter=QtWidgets.QComboBox()
        for key,zh,en in ASSET_TYPES:self.type_filter.addItem(text(zh,en),key)
        filters.addWidget(self.type_filter)
        self.family_filter=QtWidgets.QComboBox();self.family_filter.addItem(text('全部材料族','All material families'),'')
        for key,zh,en in FAMILIES:self.family_filter.addItem(text(zh,en),key)
        filters.addWidget(self.family_filter)
        self.source_filter=QtWidgets.QLineEdit();self.source_filter.setPlaceholderText(text('检索来源、胶卷或路径','Find source, roll or path'));filters.addWidget(self.source_filter,1)
        self.source_filter.setToolTip(text('类型、材料族、来源、评级和收藏同时匹配；堆叠会定位到匹配的具体片体。','All filters apply together to the same object. Stacks focus the matching body.'))
        reset=QtWidgets.QPushButton(text('清除筛选','Clear filters'));reset.clicked.connect(self.clear_filters);filters.addWidget(reset)
        manage=QtWidgets.QHBoxLayout();layout.addLayout(manage)
        self.selection_button=QtWidgets.QPushButton(text('选择','Select'));self.selection_button.setCheckable(True)
        self.selection_button.toggled.connect(self.set_selection_mode);manage.addWidget(self.selection_button)
        self.select_all_button=QtWidgets.QPushButton(text('全选','Select all'));self.select_all_button.clicked.connect(self.select_all);manage.addWidget(self.select_all_button);self.select_all_button.hide()
        self.remove_button=QtWidgets.QPushButton(text('从图库移除','Remove from library'));self.remove_button.clicked.connect(self.remove_selected);manage.addWidget(self.remove_button)
        self.remove_button.setToolTip(text('只移除软件索引，原图和软件生成的文件均保留。','Remove catalog references only. Source and generated files are retained.'))
        browse=QtWidgets.QPushButton(text('浏览所选','Browse selection'));browse.clicked.connect(self.browse_selected);manage.addWidget(browse);manage.addStretch()
        self.grid=QtWidgets.QListWidget();self.grid.setViewMode(QtWidgets.QListView.ViewMode.IconMode)
        self.grid.setResizeMode(QtWidgets.QListView.ResizeMode.Adjust);self.grid.setMovement(QtWidgets.QListView.Movement.Static)
        self.grid.setIconSize(QtCore.QSize(208,126));self.grid.setGridSize(QtCore.QSize(240,202));self.grid.setSpacing(0)
        self.grid.setItemDelegate(_CatalogCardDelegate(self.grid));self.grid.setUniformItemSizes(True)
        self.grid.setWordWrap(True);self.grid.setSelectionMode(QtWidgets.QAbstractItemView.SelectionMode.SingleSelection)
        self.grid.setContextMenuPolicy(QtCore.Qt.ContextMenuPolicy.CustomContextMenu)
        self.grid.customContextMenuRequested.connect(self.context_menu)
        layout.addWidget(self.grid,1)
        bottom=QtWidgets.QHBoxLayout();layout.addLayout(bottom)
        self.stars=[]
        for value in range(1,6):
            button=QtWidgets.QPushButton();button.setIcon(rating_icon());button.setIconSize(QtCore.QSize(24,24));button.setFixedSize(36,34);button.setStyleSheet('padding:0;')
            button.setToolTip(text(f'{value} 星；再次点击当前评级清除',f'{value} stars; click the current rating to clear'))
            button.clicked.connect(lambda _=False,value=value:self.rate(value));bottom.addWidget(button);self.stars.append(button)
        bottom.addSpacing(18)
        self.heart=QtWidgets.QPushButton();self.heart.setIcon(favorite_icon());self.heart.setFixedSize(36,34);self.heart.setToolTip(text('收藏','Favorite'));self.heart.clicked.connect(lambda:self.favor());bottom.addWidget(self.heart)
        self.status=QtWidgets.QLabel();bottom.addWidget(self.status,1)
        self.family=QtWidgets.QComboBox()
        for key,zh,en in FAMILIES:self.family.addItem(text(zh,en)+(text(" · 实验中"," · Experimental") if key!="film" else ""),key)
        bottom.addWidget(self.family)
        self.print_button=QtWidgets.QPushButton(text('印刷与制版','Print & plate studio'));bottom.addWidget(self.print_button)
        self.print_button.setVisible(print_asset is not None);self.print_button.clicked.connect(self.print_selected)
        self.start=QtWidgets.QPushButton(text('进入工作区','Open workspace'));self.start.setObjectName('primaryAction');self.start.clicked.connect(self.open_selected);bottom.addWidget(self.start)
        self.grid.itemDoubleClicked.connect(lambda _:None if self.selection_button.isChecked() else self.browse_selected());self.grid.currentItemChanged.connect(self.selection_changed)
        self.grid.itemSelectionChanged.connect(self.selection_changed)
        self.family.currentIndexChanged.connect(self.selection_changed)
        self.search.textChanged.connect(self.refresh);self.favorites.toggled.connect(self.refresh)
        for combo in (self.relation,self.minimum,self.sort):combo.currentIndexChanged.connect(self.refresh)
        for combo in (self.type_filter,self.family_filter):combo.currentIndexChanged.connect(self.filter_changed)
        self.source_filter.textChanged.connect(self.refresh)
        self.collection.currentChanged.connect(self.refresh)
        self.work_mode.currentIndexChanged.connect(self.mode_changed)
        self.timer=QtCore.QTimer(self);self.timer.setSingleShot(True);self.timer.setInterval(30);self.timer.timeout.connect(self.load_visible)
        self.grid.verticalScrollBar().valueChanged.connect(lambda _:self.timer.start())
        self.refresh()
        QtCore.QTimer.singleShot(0,self.sidebar.index_folders)

    def set_selection_mode(self,enabled):
        current=self.grid.currentItem()
        self.selection_button.blockSignals(True);self.selection_button.setChecked(enabled);self.selection_button.blockSignals(False)
        self.selection_button.setText(text('完成选择','Done selecting') if enabled else text('选择','Select'))
        self.select_all_button.setVisible(enabled)
        self.grid.setProperty('multiSelect',enabled)
        self.grid.setSelectionMode(QtWidgets.QAbstractItemView.SelectionMode.MultiSelection if enabled else QtWidgets.QAbstractItemView.SelectionMode.SingleSelection)
        if not enabled:
            self.grid.clearSelection()
            if current:current.setSelected(True)
        self.grid.viewport().update();self.selection_changed()

    def select_all(self):
        self.set_selection_mode(True);self.grid.selectAll()

    def context_menu(self,position):
        menu=self.selection_menu(self.grid.itemAt(position))
        menu.exec(self.grid.viewport().mapToGlobal(position))
        menu.deleteLater()

    def selection_menu(self,item=None):
        if item:
            if not item.isSelected():
                self.grid.clearSelection();self.grid.setCurrentItem(item);item.setSelected(True)
            else:self.grid.setCurrentItem(item,QtCore.QItemSelectionModel.SelectionFlag.NoUpdate)
        menu=QtWidgets.QMenu(self)
        if item:
            menu.addAction(text('浏览','Browse'),self.browse_selected)
            menu.addAction(text('从图库删除','Remove from library'),self.remove_selected)
            ratings=menu.addMenu(text('评分','Rating'))
            menu.rating_menu=ratings  # Keep the Python wrapper alive with its popup.
            menu.rating_action=ratings.menuAction()
            for value in range(6):
                ratings.addAction(text('无评分','Unrated') if value==0 else f'{value} ★',lambda checked=False,value=value:self.rate(value,toggle=False))
            chosen=[i.data(QtCore.Qt.ItemDataRole.UserRole) for i in self.grid.selectedItems()]
            favorite=not all(e['favorite'] for e in chosen)
            menu.addAction(text('收藏','Favorite') if favorite else text('取消收藏','Unfavorite'),lambda:self.favor(favorite))
            menu.addSeparator()
        if not self.selection_button.isChecked():menu.addAction(text('选择','Select'),lambda:self.set_selection_mode(True))
        else:
            menu.addAction(text('完成选择','Done selecting'),lambda:self.set_selection_mode(False))
            menu.addAction(text('取消全部选择','Clear selection'),self.grid.clearSelection)
        menu.addAction(text('全选','Select all'),self.select_all)
        return menu

    def mode_changed(self):
        darkroom=self.work_mode.currentData()=='darkroom'
        self.roll_button.setVisible(darkroom);self.family.setVisible(not darkroom)
        self.start.setText(text('打开堆叠','Open stack') if self.collection.currentIndex() else text('进入工作区','Open workspace'))
        if darkroom and self.open_roll:self.open_roll()

    def return_to_photos(self):
        blocker=QtCore.QSignalBlocker(self.work_mode)
        self.work_mode.setCurrentIndex(self.work_mode.findData('photos'))
        del blocker
        self.mode_changed()

    def import_dialog(self):
        paths,_=QtWidgets.QFileDialog.getOpenFileNames(self,text('导入图像或材料','Import images or materials'),'','Images / materials (*.jpg *.jpeg *.png *.tif *.tiff *.webp *.bmp *.dng *.npz *result.json)')
        if paths:
            try:
                failures=self.catalog.import_paths(paths)
                from film_foundry.app.catalog_assets import path_key
                imported={path_key(p) for p in paths}
                if any(a['role']!='source' and path_key(a['path']) in imported for a in self.catalog.assets()):self.collection.setCurrentIndex(1)
                self.refresh();self.sidebar.refresh()
                if failures:self.status.setText('\n'.join(Path(f['path']).name+': '+f['error'] for f in failures))
            except Exception as exc:self.status.setText(str(exc))

    def clear_filters(self):
        self.folder_filter=''
        for widget in (self.search,self.source_filter):widget.clear()
        for widget in (self.type_filter,self.family_filter,self.relation,self.minimum):widget.setCurrentIndex(0)
        self.favorites.setChecked(False)

    def filter_changed(self,*_):
        if self.collection.currentIndex()==0 and (self.family_filter.currentData() or self.type_filter.currentData() not in ('','source')):
            self.collection.setCurrentIndex(2)
        else:self.refresh()

    def selected(self):
        item=self.grid.currentItem()
        if item is None or not item.isSelected():item=next(iter(self.grid.selectedItems()),None)
        return item.data(QtCore.Qt.ItemDataRole.UserRole) if item else None

    def refresh(self,*_):
        self.failed_thumbnails.clear()
        self.start.setText(text('打开堆叠','Open stack') if self.collection.currentIndex() else text('进入工作区','Open workspace'))
        self.family.setVisible(not self.collection.currentIndex() and self.work_mode.currentData()=='photos')
        previous=self.selected();identity=previous['id'] if previous else None
        selection={i.data(QtCore.Qt.ItemDataRole.UserRole)['id'] for i in self.grid.selectedItems()}
        self.grid.blockSignals(True)
        self.grid.clear();self.items={}
        entries=self.catalog.query(text=self.search.text(),favorites=self.favorites.isChecked(),relation=self.relation.currentData(),rating=self.minimum.currentIndex(),sort=self.sort.currentData(),
            collection=('sources','results','assets')[self.collection.currentIndex()],kind=self.type_filter.currentData(),family=self.family_filter.currentData(),source=self.source_filter.text())
        if self.collection.currentIndex()==2 and not self.type_filter.currentData():
            entries=[e for e in entries if e['kind'] not in ('document','preview','group','state')]
        if self.folder_filter:
            folder=Path(self.folder_filter).resolve()
            entries=[e for e in entries if any(Path(p).resolve().is_relative_to(folder) for p in
                ([e['path']]+[r.get('source','') for r in e.get('runs',[])]) if p)]
        for entry in entries:
            badge=('♥ ' if entry['favorite'] else '')+(f"★{entry['rating']} " if entry['rating'] else '')
            name=self.grid.fontMetrics().elidedText(badge+(f"▣ {len(entry['runs'])} · " if 'runs' in entry else '')+entry.get('label',Path(entry['path']).name),QtCore.Qt.TextElideMode.ElideMiddle,190)
            if 'kind' in entry:name+='\n'+asset_label(entry)
            item=QtWidgets.QListWidgetItem(name)
            item.setToolTip((asset_label(entry)+'\n' if 'kind' in entry else '')+entry['path']);item.setData(QtCore.Qt.ItemDataRole.UserRole,entry);self.grid.addItem(item);self.items[entry['id']]=item
            key=preview_images.key(entry.get('preview') or entry['path'])
            item.setData(QtCore.Qt.ItemDataRole.UserRole+1,key)
            if key in self.cache:item.setIcon(self.cache[key])
            if entry['id']==identity:self.grid.setCurrentItem(item,QtCore.QItemSelectionModel.SelectionFlag.NoUpdate)
            item.setSelected(entry['id'] in selection)
        self.grid.blockSignals(False)
        self.selection_changed();self.timer.start()

    def selection_changed(self,*_):
        selected=self.selected();rating=selected['rating'] if selected else 0
        image=selected is None or ('runs' not in selected and selected.get('kind','source') in ('source','scan','preview'))
        count=len(self.grid.selectedItems())
        self.family.setVisible(image and self.work_mode.currentData()=='photos')
        self.start.setText((text('创建工作','Create work') if image else text('管理材料','Manage material'))+(text(f' · {count} 张',f' · {count} images') if count>1 else ''))
        self.print_button.setEnabled(bool(self.print_asset and selected and 'runs' not in selected and selected.get('kind','source') in ('source','scan','preview','medium','material','auxiliary','scanner_raw')))
        for i,button in enumerate(self.stars):button.setIcon(rating_icon(i<rating));button.setEnabled(selected is not None)
        favorite=bool(selected and selected['favorite']);self.heart.setIcon(favorite_icon(favorite))
        self.heart.setStyleSheet('font-size:22px;'+('color:#ef4150;' if favorite else ''))
        self.heart.setEnabled(selected is not None);self.start.setEnabled(selected is not None)
        count=len(self.grid.selectedItems());self.remove_button.setEnabled(count>0)
        self.status.setText(text(f'已选 {count} / {self.grid.count()} 项',f'{count} / {self.grid.count()} selected'))

    def print_selected(self):
        entry=self.selected()
        if self.print_button.isEnabled() and entry:
            try:self.print_asset(dict(entry,kind=entry.get('kind','source')))
            except Exception as exc:self.status.setText(str(exc))

    def rate(self,value,*,toggle=True):
        entry=self.selected()
        if entry:
            try:
                rating=0 if toggle and entry['rating']==value else value
                for item in self.grid.selectedItems():self.catalog.annotate(item.data(QtCore.Qt.ItemDataRole.UserRole)['id'],rating=rating)
                self.refresh()
            except Exception as exc:self.status.setText(str(exc))

    def favor(self,favorite=None):
        entry=self.selected()
        if entry:
            try:
                favorite=not entry['favorite'] if favorite is None else favorite
                for item in self.grid.selectedItems():self.catalog.annotate(item.data(QtCore.Qt.ItemDataRole.UserRole)['id'],favorite=favorite)
                self.refresh()
            except Exception as exc:self.status.setText(str(exc))

    def open_selected(self):
        entry=self.selected()
        if entry:
            try:
                chosen=[i.data(QtCore.Qt.ItemDataRole.UserRole) for i in self.grid.selectedItems()]
                images=all('runs' not in e and e.get('kind','source') in ('source','scan','preview') for e in chosen)
                if len(chosen)>1:
                    if images and self.open_batch:self.open_batch([e['path'] for e in chosen],self.family.currentData())
                    else:self.status.setText(text('批量创建工作请选择原图或成片；底片与材料请逐项选择工序。','Select originals or finished images for batch work. Choose a process for each material individually.'))
                    return
                if images:
                    if not Path(entry['path']).is_file():raise ValueError(text('源文件已移动或不存在','Source file moved or missing'))
                    self.open_source(entry['path'],self.family.currentData());return
                if 'runs' in entry:
                    self.show_stack(entry);return
                if entry.get('stack_id'):
                    stack=next(s for s in self.catalog.results if s['id']==entry['stack_id'])
                    self.show_stack(dict(stack,focus_asset=entry['id']));return
                if not Path(entry['path']).is_file():raise ValueError(text('源文件已移动或不存在','Source file moved or missing'))
                self.open_source(entry['path'],self.family.currentData())
            except Exception as exc:self.status.setText(str(exc))

    def browse_selected(self):
        entry=self.selected()
        if entry and self.open_browser:
            self.open_browser([self.grid.item(i).data(QtCore.Qt.ItemDataRole.UserRole) for i in range(self.grid.count())],entry['id'])

    def remove_selected(self):
        entries=[i.data(QtCore.Qt.ItemDataRole.UserRole) for i in self.grid.selectedItems()]
        if not entries:return
        answer=QtWidgets.QMessageBox.question(self,text('从图库移除','Remove from library'),
            text(f'移除所选 {len(entries)} 项的软件索引？原图及生成文件均保留。',f'Remove {len(entries)} catalog entries? Source and generated files are retained.'),
            QtWidgets.QMessageBox.StandardButton.Yes|QtWidgets.QMessageBox.StandardButton.Cancel,QtWidgets.QMessageBox.StandardButton.Cancel)
        if answer==QtWidgets.QMessageBox.StandardButton.Yes:
            try:self.catalog.remove_items(entries);self.refresh();self.sidebar.refresh()
            except (OSError,ValueError) as exc:self.status.setText(str(exc))

    def show_stack(self,entry):
        if self.open_stack:self.open_stack(entry);return
        dialog=QtWidgets.QDialog(self);dialog.setWindowTitle(entry['label']);dialog.resize(840,520)
        layout=QtWidgets.QVBoxLayout(dialog);split=QtWidgets.QSplitter();layout.addWidget(split,1)
        tree=QtWidgets.QTreeWidget();tree.setHeaderLabels([text('材料 / 结果','Material / result'),text('文件','File')]);split.addWidget(tree)
        preview=QtWidgets.QLabel();preview.setAlignment(QtCore.Qt.AlignmentFlag.AlignCenter);preview.setMinimumWidth(280);split.addWidget(preview)
        for index,run in enumerate(entry['runs'],1):
            parent=QtWidgets.QTreeWidgetItem(tree,[text(f'处理 {index}',f'Run {index}'),run['medium']])
            artifacts={'source':run['source'],'medium':run['medium'],**run['artifacts']}
            for role,path in artifacts.items():
                item=QtWidgets.QTreeWidgetItem(parent,[role,path]);item.setData(0,QtCore.Qt.ItemDataRole.UserRole,(path,run.get('family','film')))
            parent.setExpanded(True)
        tree.setColumnWidth(0,240)
        button=QtWidgets.QPushButton(text('打开所选文件','Open selected file'));layout.addWidget(button)
        def open_item():
            item=tree.currentItem();data=item.data(0,QtCore.Qt.ItemDataRole.UserRole) if item else None
            if not data:return
            path,family=data
            if not Path(path).exists():preview.setText(text('文件已移动或不存在','File moved or missing'));return
            if path.lower().endswith('.npz') and self.open_artifact:
                try:self.open_artifact(path,family);dialog.accept()
                except Exception as exc:preview.setText(str(exc))
            else:QtGui.QDesktopServices.openUrl(QtCore.QUrl.fromLocalFile(path))
        def show_image(item,*_):
            data=item.data(0,QtCore.Qt.ItemDataRole.UserRole) if item else None
            preview.clear()
            if data and Path(data[0]).suffix.lower() in ('.png','.jpg','.jpeg','.tiff','.tif','.webp','.bmp','.dng'):
                try:
                    pixmap=QtGui.QPixmap.fromImage(image_preview(data[0],800,600))
                    preview.setPixmap(pixmap.scaled(preview.size(),QtCore.Qt.AspectRatioMode.KeepAspectRatio,QtCore.Qt.TransformationMode.SmoothTransformation))
                except Exception as exc:preview.setText(str(exc))
        tree.currentItemChanged.connect(show_image)
        button.clicked.connect(open_item);tree.itemDoubleClicked.connect(lambda *_:open_item())
        from film_foundry.ui.inline_editor import present_inline_editor
        present_inline_editor(self,dialog)

    def resizeEvent(self,event):
        super().resizeEvent(event)
        if hasattr(self,'timer'):self.timer.start()

    def showEvent(self,event):
        super().showEvent(event)
        self.sidebar.refresh()
        self.timer.start()

    def load_visible(self):
        if self.loading or not self.isVisible():return
        viewport=self.grid.viewport().rect()
        for item in self.items.values():
            if self.grid.visualItemRect(item).intersects(viewport):
                entry=item.data(QtCore.Qt.ItemDataRole.UserRole)
                path=entry.get('preview') or entry['path']
                key=preview_images.key(path)
                item.setData(QtCore.Qt.ItemDataRole.UserRole+1,key)
                if key is None:
                    item.setIcon(QtGui.QIcon());continue
                if key in self.failed_thumbnails:continue
                if key in self.cache:
                    self.cache.move_to_end(key);item.setIcon(self.cache[key]);continue
                cached=preview_images.peek(key)
                if cached is not None:
                    item.setIcon(self._cached_icon(key,cached));continue
                self.loading=True;signals=self.signals
                future=self.pool.submit(thumbnail,path)
                def done(result,key=key):
                    try:image=result.result()
                    except Exception:image=QtGui.QImage()
                    try:signals.ready.emit(key,image)
                    except RuntimeError:pass
                future.add_done_callback(done)
                return

    def _thumbnail_ready(self,key,image):
        self.loading=False
        if image.isNull():self.failed_thumbnails.add(key)
        icon=self._cached_icon(key,image)
        for item in self.items.values():
            entry=item.data(QtCore.Qt.ItemDataRole.UserRole)
            if item.data(QtCore.Qt.ItemDataRole.UserRole+1)==key and preview_images.key(entry.get('preview') or entry['path'])==key:
                item.setIcon(icon)
        self.timer.start()

    def _cached_icon(self,key,image):
        # QIcons held by list items also own image memory. Bound those together
        # with this LRU, rather than bounding only the decoded worker cache.
        icon=QtGui.QIcon(QtGui.QPixmap.fromImage(image));self.cache[key]=icon
        self.cache.move_to_end(key)
        while len(self.cache)>256:
            expired,_=self.cache.popitem(last=False)
            self.failed_thumbnails.discard(expired)
            for item in self.items.values():
                if item.data(QtCore.Qt.ItemDataRole.UserRole+1)==expired:item.setIcon(QtGui.QIcon())
        return icon


class ResultStackView(QtWidgets.QWidget):
    """Persistent result inspector; pictures stay inside the desktop workspace."""
    def __init__(self,entry,open_artifact,parent=None,*,send_medium=None,catalog=None,open_asset=None,annotations_changed=None,print_asset=None,browse_only=False):
        super().__init__(parent);self.open_artifact=open_artifact;self.send_medium=send_medium
        self.catalog=catalog;self.open_asset=open_asset;self.annotations_changed=annotations_changed
        self.print_asset=print_asset
        self.browse_only=browse_only
        self.pool=ThreadPoolExecutor(max_workers=1,thread_name_prefix='result-preview')
        self.signals=_ResultPreviewSignals(self);self.signals.ready.connect(self.preview_ready)
        self.generation=0;self.pending=None;self.displayed_generation=-1
        self.destroyed.connect(lambda *_,pool=self.pool:pool.shutdown(wait=False,cancel_futures=True))
        from film_foundry.ui.image_view import ImageView
        layout=QtWidgets.QVBoxLayout(self)
        self.heading=QtWidgets.QLabel();self.heading.setStyleSheet('font-size:20px;font-weight:600;')
        layout.addWidget(self.heading)
        split=QtWidgets.QSplitter();self.split=split;layout.addWidget(split,1)
        self.tree=QtWidgets.QTreeWidget();self.tree.setHeaderLabels([text('材料 / 结果','Material / result'),text('文件','File')]);split.addWidget(self.tree)
        self.preview=ImageView();split.addWidget(self.preview);split.setStretchFactor(1,1);split.setSizes([320,800])
        bar=QtWidgets.QHBoxLayout();layout.addLayout(bar)
        self.status=QtWidgets.QLabel();self.status.setWordWrap(True);bar.addWidget(self.status,1)
        self.open_button=QtWidgets.QPushButton(text('进入材料工作区','Open material workspace'));bar.addWidget(self.open_button)
        self.open_button.clicked.connect(self.open_selected)
        self.rework_button=QtWidgets.QPushButton(text('处理附属片体','Treat companion'));bar.addWidget(self.rework_button)
        self.rework_button.setToolTip(text('恢复整组真实处理状态并选中该片体；清理、刷洗或表面痕迹按材料已有能力执行。原结果保留，新处理另存。','Restore the real group state and select this body. Cleaning and handling use the material’s existing operations; save changes separately.'))
        self.rework_button.clicked.connect(self.rework)
        self.target_family=QtWidgets.QComboBox()
        for key,zh,en in FAMILIES:self.target_family.addItem(text(zh,en)+(text(" · 实验中"," · Experimental") if key!="film" else ""),key)
        self.target_family.setCurrentIndex(self.target_family.findData('paper'))
        bar.addWidget(self.target_family)
        self.reexpose_button=QtWidgets.QPushButton(text('光学再曝光','Optical re-exposure'));bar.addWidget(self.reexpose_button)
        self.reexpose_button.setToolTip(text('将所选最终介质作为下一材料的曝光来源；接收页可选片体、曝光与滤色。','Use the selected final medium to expose the next material. Choose its body, exposure and filtration in the receiving page.'))
        self.reexpose_button.clicked.connect(self.reexpose)
        self.target_family.setVisible(send_medium is not None);self.reexpose_button.setVisible(send_medium is not None)
        self.tree.currentItemChanged.connect(self.show_selected)
        if not browse_only:self.tree.itemDoubleClicked.connect(lambda *_:self.open_selected())
        annotations=QtWidgets.QHBoxLayout();layout.addLayout(annotations)
        self.print_button=QtWidgets.QPushButton(text('印刷与制版','Print & plate studio'))
        self.print_button.setToolTip(text('以完整工作尺寸观察所选片体；底片使用当前扫描设置，其他材料使用默认观看条件。需要指定反光角度或白边时，从材料工作区送往制版。','Observe the selected body at full working size. Film uses current scan settings; other media use default viewing conditions. Send from the material workspace to specify angles or borders.'))
        self.print_button.clicked.connect(lambda: self.print_asset(self.selected_asset()))
        self.print_button.setVisible(print_asset is not None);annotations.addWidget(self.print_button)
        self.rating_buttons=[]
        for value in range(1,6):
            button=QtWidgets.QPushButton();button.setIcon(rating_icon());button.setIconSize(QtCore.QSize(24,24));button.setFixedSize(36,34);button.setStyleSheet('padding:0;')
            button.setToolTip(text(f'{value} 星；再次点击清除',f'{value} stars; click again to clear'))
            button.clicked.connect(lambda _=False,value=value:self.annotate(rating=value));annotations.addWidget(button);self.rating_buttons.append(button)
        annotations.addSpacing(16);self.favorite_button=QtWidgets.QPushButton();self.favorite_button.setIcon(favorite_icon());self.favorite_button.setFixedSize(36,34)
        self.favorite_button.setToolTip(text('收藏当前片体或图像','Favorite this body or image'))
        self.favorite_button.clicked.connect(lambda:self.annotate(favorite=True));annotations.addWidget(self.favorite_button);annotations.addStretch()
        for button in self.rating_buttons+[self.favorite_button]:button.setVisible(catalog is not None)
        self.load(entry)
        if browse_only:
            for widget in (self.open_button,self.rework_button,self.target_family,self.reexpose_button,self.print_button):widget.hide()

    def load(self,entry):
        from film_foundry.app.catalog_assets import record_assets
        self.entry=entry;self.heading.setText(entry['label']);self.tree.clear()
        self.assets={a['id']:a for a in self.catalog.assets(entry)} if self.catalog else {}
        first_image=None;focus=None;matches=set(entry.get('matched_assets',[]))
        for index,run in enumerate(entry['runs'],1):
            family=next((text(zh,en) for key,zh,en in FAMILIES if key==run.get('family','film')),run.get('family','film'))
            title=text('原始图像','Original image') if not run.get('family') and run['medium']==run.get('source') else text(f'处理 {index} · ',f'Run {index} · ')+family
            parent=QtWidgets.QTreeWidgetItem(self.tree,[title,Path(run['medium']).name])
            parent.setToolTip(0,text('来源：','Source: ')+run.get('source',''))
            details=None
            for asset in run.get('assets') or record_assets(run):
                role=asset['role'];path=asset['path'];asset=self.assets.get(asset['id'],asset)
                container=parent
                if asset['kind'] in ('document','preview'):
                    if details is None:details=QtWidgets.QTreeWidgetItem(parent,[text('文件与记录','Files and records'),''])
                    container=details
                label=(text('来源 · ','Source · ') if role=='source' and asset['kind']!='source' else '')+asset_label(asset)
                item=QtWidgets.QTreeWidgetItem(container,[label,Path(path).name]);item.setToolTip(1,path)
                item.setData(0,QtCore.Qt.ItemDataRole.UserRole,(path,run.get('family','film')))
                item.setData(0,QtCore.Qt.ItemDataRole.UserRole+1,role)
                item.setData(0,QtCore.Qt.ItemDataRole.UserRole+2,asset)
                if focus is None and (asset['id']==entry.get('focus_asset') or asset['id'] in matches):
                    focus=item;container.setExpanded(True)
                if first_image is None and (asset.get('preview') or Path(path).suffix.lower() in ('.png','.jpg','.jpeg','.tif','.tiff','.webp','.bmp','.dng')) and role!='source':first_image=item
            parent.setExpanded(True)
        self.tree.setColumnWidth(0,180)
        self.tree.setCurrentItem(focus or first_image or self.tree.topLevelItem(0))

    def selected_asset(self):
        item=self.tree.currentItem()
        return item.data(0,QtCore.Qt.ItemDataRole.UserRole+2) if item else None

    def annotate(self,*,rating=None,favorite=None):
        asset=self.selected_asset()
        if asset is None or self.catalog is None:return
        try:
            self.catalog.annotate(asset['id'],rating=(0 if rating==asset.get('rating',0) else rating) if rating is not None else None,
                                  favorite=not asset.get('favorite',False) if favorite is not None else None)
            entry=next((s for s in self.catalog.results if s['id']==self.entry['id'] or any(r['medium']==asset.get('run_medium') for r in s['runs'])),self.entry)
            self.load(dict(entry,focus_asset=asset['id']))
            if self.annotations_changed:self.annotations_changed()
        except (OSError,ValueError) as exc:self.status.setText(str(exc))

    def selected(self):
        item=self.tree.currentItem()
        return item.data(0,QtCore.Qt.ItemDataRole.UserRole) if item else None

    def show_selected(self,*_):
        self.generation+=1
        if self.pending is not None:self.pending.cancel()
        from film_foundry.app.catalog_assets import asset_actions
        data=self.selected();asset=self.selected_asset();actions=asset_actions(asset) if asset else ()
        self.print_button.setEnabled(bool(self.print_asset and asset and asset['kind'] in ('source','scan','preview','scanner_raw','medium','material','auxiliary') and Path(asset['path']).is_file()))
        self.open_button.setEnabled(bool(actions));self.rework_button.setEnabled(bool(asset and asset.get('process_path') and asset['kind']=='auxiliary' and self.open_asset))
        self.rework_button.setVisible(not self.browse_only and self.rework_button.isEnabled())
        self.reexpose_button.setVisible(not self.browse_only and bool(asset and 'reexpose' in actions and self.send_medium))
        self.target_family.setVisible(not self.browse_only and bool(asset and ('reexpose' in actions or 'image' in actions)))
        for index,button in enumerate(self.rating_buttons):
            button.setIcon(rating_icon(bool(asset and index<asset.get('rating',0))));button.setEnabled(asset is not None)
        self.favorite_button.setEnabled(asset is not None);self.favorite_button.setIcon(favorite_icon(bool(asset and asset.get('favorite'))))
        self.reexpose_button.setEnabled(False)
        self.preview.clear_image();self.status.setText('')
        if not data:return
        path=Path(data[0]);self.status.setText(asset_label(asset)+' · '+path.name);self.status.setToolTip(str(path))
        role=self.tree.currentItem().data(0,QtCore.Qt.ItemDataRole.UserRole+1)
        medium=asset['kind'] in ('medium','material','auxiliary','group')
        labels={'scan':('进入扫描','Scan'),'develop':('冲洗潜影','Develop exposure'),'continue':('继续处理','Continue processing'),
                'observe':('观察材料','Observe material'),'image':('作为图像处理','Process as image')}
        self.open_button.setText(text(*labels.get(actions[0] if actions else '',('查看文件','View file'))))
        is_image=path.suffix.lower() in ('.png','.jpg','.jpeg','.tif','.tiff','.webp','.bmp','.dng')
        self.open_button.setEnabled(bool(actions))
        if not path.is_file():self.status.setText(text('文件已移动或不存在','File moved or missing'));self.open_button.setEnabled(False);return
        if medium and self.send_medium is not None:
            from film_foundry.app.optical_cascade import source_choices
            try:self.reexpose_button.setEnabled(bool(source_choices(path)))
            except (ValueError,OSError,KeyError):pass
        preview_path=str(path) if is_image else asset.get('preview','')
        if preview_path and Path(preview_path).is_file():
            generation=self.generation;signals=self.signals
            full_resolution=self.browse_only and is_image
            dimensions=(0,0) if full_resolution else (1800,1400)
            label=asset_label(asset)+' · '+path.name
            def finished_label(image):
                return label+(text(' · 完整分辨率 ',' · Full resolution ')+f'{image.width()} × {image.height()}' if full_resolution else '')
            cached=preview_images.peek(preview_images.key(preview_path,*dimensions))
            if cached is not None and not cached.isNull():
                self.preview_ready(generation,cached,finished_label(cached));return
            thumbnail_image=preview_images.peek(preview_images.key(preview_path,1800,1400))
            if thumbnail_image is None or thumbnail_image.isNull():thumbnail_image=preview_images.peek(preview_images.key(preview_path))
            loading=text('正在载入完整分辨率… ','Loading full resolution… ') if full_resolution else text('读取图像… ','Reading image… ')
            if thumbnail_image is not None and not thumbnail_image.isNull():
                self.preview_ready(generation,thumbnail_image,loading+path.name)
            self.status.setText(loading+path.name)
            def read():
                try:
                    if generation!=self.generation:return
                    if full_resolution and path.suffix.lower()!='.dng' and (thumbnail_image is None or thumbnail_image.isNull()):
                        initial=image_preview(preview_path)
                        if generation!=self.generation:return
                        signals.ready.emit(generation,initial,loading+path.name)
                    image=image_preview(preview_path,*dimensions)
                    status=finished_label(image) if not image.isNull() else text('无法读取图像','Cannot read image')
                except Exception as exc:
                    image=QtGui.QImage();status=str(exc)
                try:signals.ready.emit(generation,image,status)
                except RuntimeError:pass
            self.pending=self.pool.submit(read)

    def preview_ready(self,generation,image,status):
        if generation!=self.generation:return
        self.status.setText(status)
        if image.isNull():return
        self.preview.show_image(image,preserve_view=self.displayed_generation==generation)
        self.displayed_generation=generation

    def closeEvent(self,event):
        self.generation+=1;self.pool.shutdown(wait=False,cancel_futures=True);super().closeEvent(event)

    def open_selected(self):
        if self.browse_only:return
        data=self.selected()
        if not data:return
        if self.open_asset:
            from film_foundry.app.catalog_assets import asset_actions
            asset=self.selected_asset();actions=asset_actions(asset)
            if actions:
                try:self.open_asset(asset,actions[0],self.target_family.currentData())
                except (ValueError,OSError) as exc:self.status.setText(str(exc))
            return
        path,family=data
        if Path(path).suffix.lower()=='.npz' and self.tree.currentItem().data(0,QtCore.Qt.ItemDataRole.UserRole+1)!='working_exposure':
            try:self.open_artifact(path,family)
            except Exception as exc:self.status.setText(str(exc))
        else:self.show_selected()

    def reexpose(self):
        data=self.selected()
        if data and self.send_medium is not None and self.reexpose_button.isEnabled():
            try:
                asset=self.selected_asset()
                if self.open_asset:self.open_asset(asset,'reexpose',self.target_family.currentData())
                else:self.send_medium(data[0],self.target_family.currentData())
            except (ValueError,OSError) as exc:self.status.setText(str(exc))

    def rework(self):
        if self.rework_button.isEnabled():
            try:self.open_asset(self.selected_asset(),'continue',None)
            except (ValueError,OSError) as exc:self.status.setText(str(exc))

