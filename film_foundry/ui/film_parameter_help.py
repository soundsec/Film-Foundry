"""Terminology and explanatory tooltips; numerical definitions stay in core."""
from film_foundry.ui.curve_editor import text

# Labels are concise; units, scope and reduced-model limits belong in tooltips.
FIELDS={
    'granularity_sigma':(('颗粒度 σD','Granularity σD'),
        ('各感光层的密度起伏标准差。最终值还受曝光量、显影条件及颗粒尺度影响；不是厂商规定测量孔径下的 RMS 颗粒度。','Density fluctuation standard deviation per layer, modified by exposure, development and grain scale. Not manufacturer RMS granularity at a specified aperture.')),
    'grain_density_correlation_radius':(('颗粒相关半径','Grain correlation radius'),
        ('颗粒密度起伏的空间相关长度，按成像画幅长边归一化。它控制可见纹理尺度，不是银晶粒直径。','Spatial correlation length of density fluctuations, normalized to frame long edge. Controls visible texture scale, not silver crystal diameter.')),
    'silver_grain_strength':(('银像颗粒度','Silver-image granularity'),
        ('基于实际保留金属银形成中性密度起伏，不通过染料吸收矩阵。0 关闭该独立分量。与感光层颗粒同时开启时会叠加。','Neutral density fluctuations from actual retained metallic silver, independent of dye absorption. 0 disables this component. Adds to layer grain when both are enabled.')),
    'silver_grain_radius':(('银像颗粒相关半径','Silver-grain correlation radius'),
        ('银像纹理的相关长度，按画幅长边归一化；并非微观粒径。','Silver-image correlation length normalized to frame long edge, not microscopic grain size.')),
    'silver_grain_clump_mix':(('银像团聚比例','Silver-grain clumping'),
        ('银像颗粒中粗尺度分量的混合比例。逐浴模型还会使用实际保留产物的团聚响应。','Coarse component mixing in silver grain; bath models also use the retained products’ clumping response.')),
    'chemical_stain':(('药液污染','Chemical staining'),
        ('期望模式下映射到实际污染操作；与材料吸附、洗清和表面残留共同决定药染，不是成片颜色滤镜。','In expected mode this maps to contamination operations; material adsorption, washing and surface residue determine staining. Not an output colour filter.')),
    'uneven_development':(('显影不均匀度','Development nonuniformity'),
        ('控制显影空间响应。逐浴接触和携液操作另在药浴与操作页设置。','Controls spatial development response. Per-bath contact and carryover are set in Baths and operations.')),
    'reticulation_strength':(('乳剂网状裂纹','Emulsion reticulation'),
        ('期望模式映射温差冲击；实际响应取决于湿乳剂状态、坚膜程度和温度变化。不是单一高温等同网裂。','Expected mode maps to temperature shock. Response depends on wet emulsion, hardening and temperature change, not high temperature alone.')),
    'silver_retention':(('留银比例','Silver retention'),
        ('通过降低漂白去银保留原有银像；与表面银沉积是不同操作。不同负片／正片观察方式会产生不同影调。','Retains existing image silver by reducing bleaching; distinct from surface silver deposition. Negative and positive observation yield different tones.')),
    'silver_plating':(('表面银沉积','Surface silver deposition'),
        ('表面反应形成的附着银层，独立于乳剂内部银像；由现有表面模型计算，不是亮度覆盖。','Attached surface silver, distinct from emulsion image silver, calculated by the surface model rather than a brightness overlay.')),
    'development_adjacency_strength':(('邻接效应','Adjacency effect'),
        ('显影局部差异导致的边缘响应。采用降阶空间算子，不模拟完整扩散场。','Edge response caused by local development differences, using reduced spatial operators rather than full diffusion.')),
    'push_stops':(('迫冲／减冲 EV','Push / pull EV'),
        ('改变显影反应与反差，并可能耦合颗粒和灰雾；不补回拍摄时未记录的细节。','Changes development and contrast, with possible grain and fog coupling; does not recover unrecorded detail.')),
}


def field_label(key,fallback):
    return text(*FIELDS[key][0]) if key in FIELDS else fallback


def describe_control(key,*widgets):
    tip=(text(*FIELDS[key][1])+'\n' if key in FIELDS else '')+key
    for widget in widgets:
        widget.set_tooltip(tip)

