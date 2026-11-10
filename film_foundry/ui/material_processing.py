"""Visible experimental quality and resolution controls for all seven families."""
from pathlib import Path
from film_foundry.ui.qt import QtCore, QtWidgets
from film_foundry.ui.curve_editor import text
from film_foundry.app.material_processing import processing_info


class MaterialProcessingBar(QtWidgets.QGroupBox):
    def __init__(self, owner):
        super().__init__(text('处理设置 · 实验中', 'Processing settings · Experimental'))
        self.owner = owner
        owner.setWindowTitle(owner.windowTitle()+' · '+text('实验中','Experimental'))
        self.reload_timer=QtCore.QTimer(self);self.reload_timer.setSingleShot(True);self.reload_timer.setInterval(180)
        self.reload_timer.timeout.connect(self._reload_fresh)
        form = QtWidgets.QHBoxLayout(self)
        self.full_precision=QtWidgets.QCheckBox(text('全高精度保存','Full-precision storage'))
        self.full_precision.setToolTip(text('默认仅外观字段以FP16保存，反应库存保持FP32。勾选保留全部FP32；不改变形成算法，也不恢复已损失的精度。','By default selected appearance fields are stored as FP16, chemistry stays FP32. Check to keep FP32 throughout storage; cannot recover prior quantization.'))
        form.addWidget(self.full_precision)
        self.full_precision.toggled.connect(lambda full:setattr(owner.session,'storage_precision','full' if full else 'auto'))
        self.limit = QtWidgets.QCheckBox(text('限制形成尺寸', 'Limit formation size'))
        self.limit.setChecked(True); form.addWidget(self.limit)
        self.edge = QtWidgets.QSpinBox(); self.edge.setRange(1, 2147483647)
        self.edge.setValue(1600); self.edge.setSuffix(' px'); self.edge.setMaximumWidth(140)
        self.edge.setToolTip(text('长边上限；关闭开关按原尺寸形成。改变尺寸需重新载入，已形成材料不会被放大。', 'Long-edge limit. Switch off for original resolution. Reload to change formation size; existing media are never enlarged.'))
        form.addWidget(self.edge)
        # Retain the existing data-only draft field and legacy zero=original.
        owner.work_size = QtWidgets.QSpinBox(self); owner.work_size.setRange(0, 2147483647)
        owner.work_size.setValue(1600); owner.work_size.hide()
        self.actual = QtWidgets.QLabel(); form.addWidget(self.actual, 1)
        self.apply = QtWidgets.QPushButton(text('应用尺寸', 'Apply size')); form.addWidget(self.apply)
        self.apply.setToolTip(text('重新读取原始来源；已有处理需确认后重新开始。', 'Reload the original source. Existing processing is replaced only after confirmation.'))
        self.limit.toggled.connect(self._changed); self.edge.valueChanged.connect(self._changed)
        owner.work_size.valueChanged.connect(self.sync)
        self.apply.clicked.connect(self.reload)

    def _changed(self, *_):
        self.owner.work_size.setValue(self.edge.value() if self.limit.isChecked() else 0)
        self.sync()
        self.reload_timer.start()

    def _reload_fresh(self):
        owner=self.owner
        if owner.busy:self.reload_timer.start();return
        if owner.session.state is None and owner.session.group is None and self.apply.isEnabled() and not self.apply.isHidden():self.reload()

    def sync(self, *_):
        owner = self.owner; value = owner.work_size.value()
        blocker=QtCore.QSignalBlocker(self.full_precision)
        self.full_precision.setChecked(owner.session.storage_precision=='full');del blocker
        blockers = [QtCore.QSignalBlocker(self.limit), QtCore.QSignalBlocker(self.edge)]
        self.limit.setChecked(value > 0)
        if value: self.edge.setValue(value)
        del blockers
        self.setEnabled(not owner.busy); self.edge.setEnabled(value > 0)
        info = processing_info(owner.session); size = info['working_size']; source = info['source_size']
        changed = False
        if size and source:
            # Compare dimensions without allocating or decoding pixels.
            w, h = source; scale = min(1., value / max(w, h)) if value else 1.
            expected = [max(1, round(w * scale)), max(1, round(h * scale))]
            changed = size != expected
        label = (' × '.join(map(str, size)) + ' px') if size else text('尚未载入', 'No source')
        if changed: label += text(' · 待应用', ' · pending')
        self.actual.setText(label)
        self.apply.setVisible(changed)
        source_path = owner.session.source_path
        self.apply.setEnabled(not owner.busy and bool(source_path) and Path(source_path).is_file())

    def reload(self):
        owner = self.owner; session = owner.session
        if owner.busy or not session.source_path or not owner.confirm_replace(): return
        size = owner.work_size.value(); path = session.source_path
        if session.optical_source is not None or session.optical_reference is not None:
            action = lambda: session.open_medium(path, body_id=session.optical_body_id, relay=session.optical_relay, long_edge=size)
        elif session.source_info.get('kind') == 'image':
            policy = session.source_info.get('color', {}).get('input_icc_policy', 'apply')
            action = lambda: session.open_image(path, size, input_icc_policy=policy)
        else:
            owner.status.setText(text('请从图库重新选择原始来源。', 'Select the original source in the library.')); return
        owner._submit(action, text('重新载入形成尺寸…', 'Reloading formation size…'), body_index=0)

    def source_ready(self):
        owner=self.owner
        if owner.session.state is None and owner.session.group is None and not self.apply.isHidden() and self.apply.isEnabled():
            self.reload_timer.stop();self.reload();return False
        return True


def install_processing_bar(owner, layout):
    owner.processing_bar = MaterialProcessingBar(owner)
    layout.addWidget(owner.processing_bar)


def batch_processing_details(owner):
    size=owner.work_size.value()
    return [(text('材料','Material'),owner.windowTitle()),
            (text('处理质量','Processing quality'),text('完整算法 · 实验中','Full processing · Experimental')),
            (text('形成尺寸','Formation size'),text(f'长边上限 {size} px',f'Long edge up to {size} px') if size else text('各图原尺寸','Original resolution per image')),
            (text('计算精度','Arithmetic'),'float32'),(text('随机种子','Seed'),owner.seed.value())]
