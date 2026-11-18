"""Explain the current desktop preview paths, without predicting timings."""
from film_foundry.ui.curve_editor import text


def recalculation_hint(stage, mode, photo_source):
    if stage=='output':
        return text('输出','Output'),text('这些选项用于正式保存或编码；预览不一定显示附属文件和保存精度的变化。','These options control formal saving or encoding; previews do not necessarily show auxiliary files or storage precision changes.')
    if stage=='scan':
        if mode not in ('full','scan'):return text('跳过','Skipped'),text('本次不执行扫描。','This task does not scan.')
        return text('仅观察','Observe'),text('不改变材料。完整照片预览可复用匹配的已形成介质；首次预览、清缓存或形成参数同时变化时仍需先形成。正式执行依本次终点运行。','Material is unchanged. Full photo previews can reuse a matching formed medium; the first preview, cleared cache or changed formation settings still require formation. Formal execution follows the selected endpoint.')
    active=mode in (('full','develop','expose') if stage=='film' else ('full','develop','develop_latent'))
    if not active:
        frozen=not photo_source and (stage=='film' or mode=='scan')
        return (text('已冻结','Frozen'),text('沿用已保存来源的事实；此工作不修改该阶段。','This work retains the saved source facts for this stage.')) if frozen else (text('跳过','Skipped'),text('本次不执行此阶段。','This task skips this stage.'))
    if mode=='develop_latent':
        return text('重新冲洗','Redevelop'),text('从已保存潜影重新计算冲洗；材料和曝光保持来源中的记录。','Recalculate development from the saved latent; retain its material and exposure.')
    return (text('重新曝光','Re-expose'),text('重新计算曝光，并继续计算至所选终点；已有保存结果不被改写。','Recalculate exposure and continue to the selected endpoint. Existing saved results remain.')) if stage=='film' else (text('重新冲洗','Redevelop'),text('按新的冲洗条件从当前照片重新形成底片，再观察至所选终点。','Form the film from the current photo using the new development settings, then observe it when scanning is selected.'))
