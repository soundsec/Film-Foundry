"""Compact read-only formation record, separate from editable scan settings."""
from pathlib import Path

from film_foundry.ui.qt import QtCore, QtWidgets
from film_foundry.ui.curve_editor import text


def scan_version_hint(context):
    messages = {
        'missing_image': ('成片文件已移动或不存在。', 'The scan image is missing.'),
        'missing_record': ('没有保存扫描参数记录；仍可将成片作为普通图像处理。', 'No scan recipe was saved; the image can still be processed as a new image.'),
        'unsupported_record': ('此文件没有胶片扫描记录。', 'This file has no film scan record.'),
        'missing_recipe': ('记录中缺少扫描参数，不能还原此版本。', 'The record has no scan recipe; this version cannot be restored.'),
        'missing_medium': ('原底片已移动、未保存或未记录；不能从成片反推底片。', 'The source medium is missing or was not recorded; it cannot be reconstructed from the scan.'),
        'ambiguous_medium': ('记录关联了多个底片，无法确定此版本的来源。', 'More than one source medium is recorded for this scan.'),
        'unsupported_source': ('此入口仅恢复已保存胶片介质的扫描。', 'This action restores scans of saved film media only.'),
        'invalid_record': ('扫描记录无法读取，当前工作保持不变。', 'The scan record cannot be read; current work is retained.'),
    }
    return text(*messages[context['problem']]) if context.get('problem') else text(
        '回到原底片，载入此成片的扫描、曲线、色彩及输出设置；修改后另存新成片。',
        'Open the source medium with this scan’s tone, color and output settings; save adjustments as a new scan.')


class ScanVersionButton(QtWidgets.QPushButton):
    failed = QtCore.Signal(str)

    def __init__(self, callback, parent=None):
        super().__init__(text('按此版本调整扫描', 'Adjust this scan version'), parent)
        self.callback = callback;self.path = None
        self.clicked.connect(self.open_version)
        self.hide()

    def select_asset(self, asset, catalog):
        self.path = None
        candidate = bool(self.callback and asset and asset.get('kind') in ('source', 'scan')
                         and asset.get('family', '') in ('', 'film'))
        context = None
        if candidate:
            from film_foundry.app.film_task import scan_version_context
            path = Path(asset['path'])
            # Normal photographs do not gain a disabled scan action.
            if path.with_suffix(path.suffix + '.json').is_file() or asset.get('kind') == 'scan':
                context = scan_version_context(path, catalog)
                if context['problem'] == 'unsupported_record':context = None
        self.setVisible(context is not None)
        self.setEnabled(bool(context and not context['problem']))
        if context:
            self.path = context['path'];self.setToolTip(scan_version_hint(context))

    def open_version(self):
        if self.path and self.isEnabled():
            try:self.callback(self.path)
            except (ValueError, OSError) as exc:self.failed.emit(str(exc))


class FilmSourceDialog(QtWidgets.QDialog):
    def __init__(self, context, parent=None):
        super().__init__(parent)
        self.setWindowTitle(text('形成记录 · 只读', 'Formation record · Read only'))
        self.resize(680, 520)
        layout = QtWidgets.QVBoxLayout(self)
        path = QtWidgets.QLineEdit(context['path']);path.setReadOnly(True);path.setCursorPosition(0);layout.addWidget(path)
        tree = QtWidgets.QTreeWidget();self.tree = tree
        tree.setHeaderLabels([text('记录', 'Record'), text('保存的值', 'Saved value')])
        tree.setColumnWidth(0, 260);layout.addWidget(tree, 1)

        def add(parent, name, value):
            item = QtWidgets.QTreeWidgetItem(parent, [str(name), '' if isinstance(value, (dict, list, tuple)) else str(value)])
            if isinstance(value, dict):
                for key, child in value.items():add(item, key, child)
            elif isinstance(value, (list, tuple)):
                if all(not isinstance(v, (dict, list, tuple)) for v in value):item.setText(1, ', '.join(map(str, value)))
                else:
                    for index, child in enumerate(value, 1):add(item, index, child)
            item.setToolTip(0, str(name));item.setToolTip(1, item.text(1))
            return item

        config = context.get('config') or {}
        add(tree, text('已记录来源', 'Recorded sources'), context['parents'] or text('未记录', 'Not recorded')).setExpanded(True)
        if config:
            for key, zh, en in (('film', '材料', 'Material'), ('chemistry', '冲洗', 'Development'),
                                ('exposure', '曝光条件', 'Exposure'), ('film_border', '片边与打印', 'Edges & imprint')):
                value = config.get(key, config.get('develop') if key == 'chemistry' else None)
                if value is not None:add(tree, text(zh, en), value)
        else:add(tree, text('完整形成配方', 'Full formation recipe'), text('来源未保存；不使用当前工作参数补填', 'Not saved with this source; current settings are not substituted'))
        add(tree, text('介质内记录', 'Embedded medium record'), context['metadata'])
        buttons = QtWidgets.QDialogButtonBox()
        buttons.addButton(text('关闭', 'Close'),QtWidgets.QDialogButtonBox.ButtonRole.RejectRole)
        buttons.rejected.connect(self.reject);layout.addWidget(buttons)
