"""Execution status shared by workbench and reusable recipe editors."""
from film_foundry.app.film_process_capabilities import describe_formation_execution
from film_foundry.ui.qt import QtWidgets
from film_foundry.ui.curve_editor import text


class FormationExecutionBadge(QtWidgets.QToolButton):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAutoRaise(True)
        self.setAccessibleName(text('形成模型', 'Formation model'))

    def set_config(self, config):
        info = describe_formation_execution(config)
        if not info['lower_order_enabled']:
            title = text('完整形成', 'Full formation')
            tip = text('按配方执行已开启的材料响应、药浴与事故。', 'Runs the enabled material responses, baths and incidents.')
        elif info['basic_formation_only']:
            title = text('基础快速冲洗', 'Basic fast processing')
            tip = text('只保留主反应、简化颗粒、灰雾与简化密度药痕。滚动、局部接触、污染操作、乳剂损伤、复杂残留水洗、同浴次反应、邻接与 DIR 不参与。',
                       'Retains primary reactions, simplified grain, fog and density staining. Omits rotary/local contact, contamination operations, emulsion damage, detailed residue/wash, secondary bath reactions, adjacency and DIR.')
        else:
            title = text('兼容降阶', 'Legacy reduced') + ' ' + info['profile_id'].rsplit('_', 1)[-1]
            tip = text('使用已保存的历史降阶方案；不自动升级到基础快速冲洗。', 'Uses the saved historical reduction profile without upgrading it.')
        tip += '\n' + text('扫描规则与输出尺寸不因降阶改变。编辑器保留完整配方，未参与的设置可供完整模式使用。',
                            'Reduction does not change scan rules or output dimensions. Editors retain the full recipe for full-mode reuse.')
        self.setText(title)
        self.setToolTip(tip)
        self.execution_info = info


def set_editor_execution_context(editor, config):
    from copy import deepcopy
    editor._execution_config = deepcopy(config)
    editor.formation_badge.set_config(config)
    # Processing policy follows the current job without replacing material or
    # chemistry drafts when the same embedded editor is revisited.
    if editor._base_config is not None:
        editor._base_config.processing = deepcopy(config.processing)
        editor._base_config.fast_mode = config.fast_mode
    inactive = {path.rsplit('.', 1)[-1] for path in editor.formation_badge.execution_info['inactive_fields']}
    for label in editor.controls_scroller.content.qt.findChildren(QtWidgets.QWidget):
        original = label.property('formationBaseTooltip')
        if original is None:
            original = label.toolTip()
            label.setProperty('formationBaseTooltip', original)
        omitted = any(key in original.splitlines() for key in inactive)
        suffix = text('\n本次形成不使用此参数；保留用于完整模式。', '\nNot used by this formation profile; retained for full mode.') if omitted else ''
        label.setToolTip(original + suffix)
