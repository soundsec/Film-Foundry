"""Edit offsets against the shared camera exposure, using the explicit source order."""
from pathlib import Path
from film_foundry.ui.qt import QtCore, QtGui, QtWidgets
from film_foundry.ui.curve_editor import text


def source_dialog(owner):
    paths = tuple(owner._selected_photos)
    dialog = QtWidgets.QDialog(owner.film_workspace)
    dialog.setWindowTitle(text('本次来源 / 逐片曝光','Sources / per-frame exposure'))
    layout = QtWidgets.QVBoxLayout(dialog)
    mode = owner.pipeline_mode.get()
    editable = bool(paths) and mode in ('full','expose','develop') and not owner._processing and owner._active_roll is None
    layout.addWidget(QtWidgets.QLabel(text('逐片 EV 加到共用曝光上。选择照片可查看或调整该片。',
        'Each frame offset adds to the shared exposure. Select a photo to inspect or edit it.') if editable else owner.source_summary.qt.text()))
    rows = QtWidgets.QTreeWidget()
    rows.setHeaderLabels([text('来源','Source'),text('曝光偏移 EV','Offset EV')])
    rows.setRootIsDecorated(False)
    rows.header().setSectionResizeMode(0,QtWidgets.QHeaderView.ResizeMode.Stretch)
    layout.addWidget(rows,1)
    offsets = dict(owner._frame_exposure_offsets)
    field = QtWidgets.QDoubleSpinBox()
    field.setRange(-20,20);field.setDecimals(2);field.setSingleStep(.25);field.setSuffix(' EV')
    field.setKeyboardTracking(False)
    layout.addWidget(field);field.setVisible(editable)
    for path in paths:
        key = str(Path(path).resolve())
        row = QtWidgets.QTreeWidgetItem(rows,[Path(path).name,format(offsets.get(key,0.),'+.2f') if editable else '—'])
        row.setData(0,QtCore.Qt.ItemDataRole.UserRole,key);row.setToolTip(0,str(path))
        if path == owner.input_path.get():rows.setCurrentItem(row)
    from concurrent.futures import ThreadPoolExecutor
    from statistics import median
    import numpy as np
    from film_foundry.core.color import srgb_to_linear
    from film_foundry.ui.desktop_library import image_preview
    rows.setIconSize(QtCore.QSize(72,48))
    levels={}
    equalize=QtWidgets.QPushButton(text('按来源亮度建议 EV','Suggest offsets from source brightness'))
    equalize.setVisible(editable and len(paths)>1);equalize.setEnabled(False);layout.addWidget(equalize)
    def inspect(path):
        image=image_preview(path,96,64).convertToFormat(QtGui.QImage.Format.Format_RGB888)
        if image.isNull():return image,None
        rgb=np.frombuffer(image.constBits(),np.uint8).reshape(image.height(),image.bytesPerLine())[:,:image.width()*3].reshape(-1,3)
        level=float(np.median(srgb_to_linear(rgb.astype(np.float32)/255) @ np.array([.2126,.7152,.0722])))
        return image, float(np.log2(level)) if level>0 else None
    pool=ThreadPoolExecutor(max_workers=1,thread_name_prefix='frame-meter')
    cursor=0;future=None
    def tick():
        nonlocal cursor,future
        if future is not None:
            if not future.done():return
            try:image,level=future.result()
            except (OSError,ValueError,RuntimeError):image,level=QtGui.QImage(),None
            row=rows.topLevelItem(cursor-1)
            if not image.isNull():row.setIcon(0,QtGui.QIcon(QtGui.QPixmap.fromImage(image)))
            if level is not None:levels[row.data(0,QtCore.Qt.ItemDataRole.UserRole)]=level
            future=None
        if cursor<len(paths):
            from film_foundry.app.preview_budget import Priority,preview_budget
            future=pool.submit(preview_budget.run,Priority.THUMBNAIL,inspect,paths[cursor]);cursor+=1
        else:timer.stop();equalize.setEnabled(len(levels)>1)
    timer=QtCore.QTimer(dialog);timer.timeout.connect(tick);timer.start(25)
    def stop(*_):
        timer.stop();pool.shutdown(wait=False,cancel_futures=True)
    dialog.finished.connect(stop)
    dialog.destroyed.connect(lambda *_:pool.shutdown(wait=False,cancel_futures=True))
    def suggest():
        target=median(levels.values())
        for i in range(rows.topLevelItemCount()):
            row=rows.topLevelItem(i);key=row.data(0,QtCore.Qt.ItemDataRole.UserRole)
            if key in levels:
                ev=round(max(-20,min(20,target-levels[key])),2)
                if ev:offsets[key]=ev
                else:offsets.pop(key,None)
                row.setText(1,f'{ev:+.2f}')
        selected(rows.currentItem())
    equalize.clicked.connect(suggest)
    def selected(current,*_):
        if current is not None:
            block = QtCore.QSignalBlocker(field)
            field.setValue(offsets.get(current.data(0,QtCore.Qt.ItemDataRole.UserRole),0.))
            del block
    def changed(ev):
        row = rows.currentItem()
        if row is None:return
        key = row.data(0,QtCore.Qt.ItemDataRole.UserRole)
        if ev:offsets[key]=ev
        else:offsets.pop(key,None)
        row.setText(1,format(ev,'+.2f'))
    if rows.currentItem() is None and paths:rows.setCurrentItem(rows.topLevelItem(0))
    selected(rows.currentItem());rows.currentItemChanged.connect(selected);field.valueChanged.connect(changed)
    buttons=QtWidgets.QDialogButtonBox(QtWidgets.QDialogButtonBox.StandardButton.Ok|QtWidgets.QDialogButtonBox.StandardButton.Cancel)
    buttons.accepted.connect(dialog.accept);buttons.rejected.connect(dialog.reject);layout.addWidget(buttons)
    def apply():
        if tuple(owner._selected_photos)!=paths or owner._processing or owner.pipeline_mode.get()!=mode:return
        if editable and offsets!=owner._frame_exposure_offsets:
            owner.edit_tools.record();owner._frame_exposure_offsets=offsets
            owner.edit_tools.record();owner._preview_edited()
        row=rows.indexOfTopLevelItem(rows.currentItem()) if rows.currentItem() else -1
        if row>=0:owner._focus_selected_photo(paths[row])
    dialog.resize(540,420)
    owner._present_workspace_dialog(dialog,on_accept=apply)
    return dialog
