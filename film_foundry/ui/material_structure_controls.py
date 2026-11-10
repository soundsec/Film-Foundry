"""Shared presentation for the independent materials' frozen texture scale."""
from film_foundry.ui.curve_editor import text

TIPS={
 'structure_model':('纹理在固定材料坐标中生成，再采样到工作尺寸。旧档保留原模型；观看不会重建纹理。',
                    'Textures form in fixed material coordinates, then sample to working pixels. Old archives retain their generator; viewing never rebuilds it.'),
 'material_long_edge_mm':('声明这张材料的长边尺寸，短边按图像比例确定；同样的毫米细纹在较大材料上占比更小。不会改变输出像素数。',
                          'Declared long edge; the image aspect sets the short edge. The same millimetre texture occupies less of a larger sheet. Does not change output dimensions.'),
 'texture_scale_mm':('控制纤维、打磨微纹与蚀坑等细结构的特征尺度，不是强度。小于模型采样能力的细纹会被平均；指纹等尚沿用原尺寸规则。',
                      'Characteristic size of fibres, polish texture and pits, not their strength. Unresolved detail is averaged; handling marks still use their existing scale rules.'),
 'process_scale_mm':('控制铺药通道、刷涂、流膜和腐蚀边缘等较大结构的尺度。具体作用随材料而异；只影响随后形成的介质。',
                      'Scale of reagent channels, brush passes, poured film and corrosion edges. Family-specific, affecting subsequent formation only.')}


def sync(window):
    session=window.session
    editable=not window.busy and ((session.state is None and session.group is None) or getattr(window,'_planning_batch',False))
    physical=window.material_controls['structure_model'].currentData()=='millimetres_v1'
    for key in ('material_long_edge_mm','texture_scale_mm','process_scale_mm'):
        window.material_controls[key].setEnabled(editable and physical)


def setup(window):
    for key,tip in TIPS.items():window.material_controls[key].setToolTip(text(*tip))
    window.material_controls['structure_model'].currentIndexChanged.connect(lambda:sync(window))
