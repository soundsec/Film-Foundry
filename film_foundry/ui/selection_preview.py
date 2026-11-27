"""Lazy catalog thumbnails with a shared serial worker and bounded display cache."""
from collections import OrderedDict
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from film_foundry.app.preview_budget import Priority, preview_budget
from film_foundry.app.selection_preview import SelectionPreviewSession
from film_foundry.app.control import CancelFlag, WorkflowCancelled, WorkflowControl
from film_foundry.ui.qt import QtCore, QtGui, QtWidgets
from film_foundry.ui.curve_editor import text
from film_foundry.ui.image_view import ImageView


_POOL=ThreadPoolExecutor(max_workers=1,thread_name_prefix='selection-preview')


def _release(state,session):
    if state.get('released'):return
    state['released']=True
    if state.get('cancel'):state['cancel'].cancel()
    future=state.get('future')
    if future is not None and not future.done():
        future.cancel();future.add_done_callback(lambda _:session.clear())
    else:session.clear()


class SelectionPreview(QtWidgets.QWidget):
    def __init__(self, dialog, choices, record_for_item, *, bank='material', base=None, source=None):
        super().__init__(dialog)
        self.choices=choices;self.record_for_item=record_for_item
        self.session=SelectionPreviewSession(bank=bank,base=base,source=source)
        self.future=None;self.control=None;self.epoch=0;self.closed=False;self.shown=False;self.reveal_pending=True
        self._lifetime={}
        self.cache=OrderedDict();self.failed=set();self.active=None;self.pixmap=None
        layout=QtWidgets.QVBoxLayout(self);layout.setContentsMargins(0,0,0,0)
        self.target=QtWidgets.QComboBox();self.target.addItem(text('示例照片','Example photograph'),False)
        if source:self.target.addItem(text('当前照片' if bank!='material' else '来源照片（第一张）','Current photograph' if bank!='material' else 'Source photograph (first)'),True)
        self.target.addItem(text('合成色卡','Synthetic colour chart'),'color24')
        layout.addWidget(self.target)
        note=QtWidgets.QLabel(text('参考流程示意 · 240 px','Reference process · 240 px'));layout.addWidget(note)
        note.setToolTip(text('固定比较种子。材料采用标准工序与推荐观察；冲洗和扫描预设与打开窗口时的其他参数组组合。只缩图，不额外简化计算；不是当前工作最终效果或原尺寸颗粒的保证。',
            'Fixed comparison seed. Materials use standard processing and recommended observation; process/scan presets combine with the other banks captured when opened. Resized input, no extra reduction. Not a promise of the current work or full-size grain.'))
        scope=QtWidgets.QLabel(text('示例照片用参考负片呈现负感印相材料。来源照片与色卡直接曝光。','The example photograph uses a reference negative for negative-working prints. Source photographs and charts expose the material directly.') if bank=='material' else text('与当前其他参数组组合；采用后以工作预览为准。','Combined with the other current parameter banks; check the work preview after applying.'))
        scope.setWordWrap(True);layout.addWidget(scope)
        self.canvas=QtWidgets.QLabel(text('选择材料或预设以预览','Select a material or preset'))
        self.canvas.setAlignment(QtCore.Qt.AlignmentFlag.AlignCenter);self.canvas.setMinimumSize(160,100)
        self.canvas.setSizePolicy(QtWidgets.QSizePolicy.Policy.Ignored,QtWidgets.QSizePolicy.Policy.Expanding)
        layout.addWidget(self.canvas, 1)
        self.canvas.installEventFilter(self)
        self.status=QtWidgets.QLabel();self.status.setFixedHeight(self.status.fontMetrics().height());layout.addWidget(self.status)
        for widget in (self.target,note,scope,self.status):
            widget.setSizePolicy(QtWidgets.QSizePolicy.Policy.Preferred,QtWidgets.QSizePolicy.Policy.Fixed)
        self.canvas.setMaximumHeight(self.session.long_edge)
        choices.setViewMode(QtWidgets.QListView.ViewMode.IconMode)
        choices.setResizeMode(QtWidgets.QListView.ResizeMode.Adjust)
        choices.setMovement(QtWidgets.QListView.Movement.Static)
        choices.setWordWrap(True)
        choices.setGridSize(QtCore.QSize(148, 136))
        choices.setIconSize(QtCore.QSize(108,72));choices.setSpacing(3)
        placeholder=QtGui.QPixmap(108,72);placeholder.fill(choices.palette().alternateBase().color())
        self.placeholder=QtGui.QIcon(placeholder)
        self.prepare_rows()
        self.timer=QtCore.QTimer(self);self.timer.setInterval(40);self.timer.timeout.connect(self.tick)
        choices.currentItemChanged.connect(self.selection_changed)
        choices.verticalScrollBar().valueChanged.connect(self.schedule)
        choices.viewport().installEventFilter(self);dialog.installEventFilter(self)
        self.target.currentIndexChanged.connect(self.reset)
        self._dialog=dialog
        # No worker callback captures a Qt widget or the dialog.
        session,state=self.session,self._lifetime
        self.destroyed.connect(lambda *_:_release(state,session))

    def reset(self,*_):
        self.epoch+=1;self.cache.clear();self.failed.clear();self.pixmap=None
        if self.control:self.control.cancel()
        for row in range(self.choices.count()):self.choices.item(row).setIcon(self.placeholder)
        self.prepare_rows()
        self.selection_changed();self.schedule()

    def prepare_rows(self):
        # Reserving the thumbnail space prevents the list / scroll position
        # from jumping as individual images arrive.
        height=max(88,self.choices.fontMetrics().height()*3+12)
        for row in range(self.choices.count()):
            item=self.choices.item(row)
            item.setSizeHint(QtCore.QSize(144,max(128,height)))
            if item.icon().isNull():item.setIcon(self.placeholder)

    def selected_row(self):
        item=self.choices.currentItem()
        return self.choices.row(item) if item is not None and not item.isHidden() else -1

    def selection_changed(self,*_):
        row=self.selected_row();self.pixmap=self.cache.get(row)
        if self.pixmap is None:
            self.canvas.clear();self.canvas.setText(text('等待缩略预览…','Waiting for thumbnail…') if row>=0 else text('没有匹配项','No matching item'))
        else:self.fit()
        self.schedule()

    def schedule(self,*_):
        if not self.closed and self.isVisible():self.timer.start()

    def visible_rows(self):
        viewport = self.choices.viewport().rect()
        selected = self.selected_row()
        rows = [selected] if selected >= 0 else []
        for row in range(self.choices.count()):
            item = self.choices.item(row)
            if row != selected and not item.isHidden() and viewport.intersects(self.choices.visualItemRect(item)):
                rows.append(row)
                if len(rows) == 24:
                    break
        return rows

    def tick(self):
        if self.closed:return
        if self.reveal_pending:
            self.reveal_pending=False
            item=self.choices.currentItem()
            if item is not None and not item.isHidden():self.choices.scrollToItem(item)
        if self.future is not None and not self.future.done():return
        rows = self.visible_rows()
        if self.future is not None:
            future=self.future;self.future=None
            epoch,row=self.active
            try:
                pixels=future.result()
                if epoch==self.epoch and row<self.choices.count():
                    pixmap=ImageView.rgb_pixmap(pixels)
                    self.cache[row]=pixmap;self.cache.move_to_end(row)
                    self.choices.item(row).setIcon(QtGui.QIcon(pixmap))
                    while len(self.cache)>24:
                        old = next(key for key in self.cache if key not in rows)
                        del self.cache[old]
                        self.choices.item(old).setIcon(self.placeholder)
                    if row==self.selected_row():self.pixmap=pixmap;self.fit()
                    self.status.setText(text('缩略预览已更新','Thumbnail updated'))
            except WorkflowCancelled:pass
            except Exception as exc:
                if epoch==self.epoch:
                    self.failed.add(row);self.status.setText(text('预览失败（悬停查看）','Preview failed (hover for details)'));self.status.setToolTip(str(exc))
                    if row==self.selected_row():self.canvas.setText(text('此项暂不能预览','Preview unavailable'))
        if not self.isVisible():self.timer.stop();return
        row=next((r for r in rows if r>=0 and r not in self.cache and r not in self.failed),None)
        if row is None:self.timer.stop();return
        record=deepcopy(self.record_for_item(self.choices.item(row)))
        self.control=CancelFlag();self.active=(self.epoch,row)
        self.status.setText(text('后台计算缩略预览…','Rendering thumbnail…'))
        control = WorkflowControl(cancel_requested=self.control.requested)
        self.future = _POOL.submit(preview_budget.run, Priority.THUMBNAIL, self.session.render, record, self.target.currentData(), control, check=control.check_cancel)
        self._lifetime.update(future=self.future,cancel=self.control)

    def fit(self):
        if self.pixmap is not None:self.canvas.setPixmap(self.pixmap.scaled(self.canvas.size(),QtCore.Qt.AspectRatioMode.KeepAspectRatio,QtCore.Qt.TransformationMode.SmoothTransformation))

    def resizeEvent(self,event):
        super().resizeEvent(event)
        # Wrapped descriptions need their actual narrow-column height. Without
        # this, QVBoxLayout can overlap the canvas and status in a small dialog.
        widgets=[self.layout().itemAt(i).widget() for i in range(self.layout().count()) if self.layout().itemAt(i).widget() is not None]
        preferred = min(240, max(100, self.window().height()-400))
        if self.canvas.minimumHeight()!=preferred:self.canvas.setMinimumHeight(preferred)
        height=sum(w.minimumHeight() if w is self.canvas else max(w.minimumSizeHint().height(),w.heightForWidth(self.width()) if w.hasHeightForWidth() else w.sizeHint().height()) for w in widgets)
        height+=self.layout().spacing()*(len(widgets)-1)
        if height!=self.minimumHeight():self.setMinimumHeight(height)
        self.fit()

    def stop(self):
        if self.closed:return
        self.closed=True;self.timer.stop()
        _release(self._lifetime,self.session)
        self.cache.clear();self.pixmap=None;self.canvas.clear()
        for row in range(self.choices.count()):self.choices.item(row).setIcon(self.placeholder)

    def eventFilter(self,obj,event):
        if obj is self.canvas and event.type()==QtCore.QEvent.Type.Resize:self.fit()
        if obj is not self.canvas and event.type() in (QtCore.QEvent.Type.Show,QtCore.QEvent.Type.Resize):self.reveal_pending=True
        if obj is self._dialog and event.type()==QtCore.QEvent.Type.Show:self.shown=True
        if obj is self._dialog and self.shown and event.type() in (QtCore.QEvent.Type.Hide,QtCore.QEvent.Type.Close):self.stop()
        elif event.type() in (QtCore.QEvent.Type.Show,QtCore.QEvent.Type.Resize):self.schedule()
        return super().eventFilter(obj,event)
