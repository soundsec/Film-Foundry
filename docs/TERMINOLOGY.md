# Film Foundry 术语与参数说明

这份说明面向调参与流程理解。Film Foundry 的项目层级是“成像介质形成与观察”；当前 0.3 工具包具体实现银盐负片、银盐反转正片及其透射扫描。因此，主控制台使用成像介质术语，而银盐材料、工艺与扫描编辑器仍按自己的真实适用范围命名。

```text
输入图像
-> 材料曝光与工艺：形成最终介质
-> 扫描 / 观察：解释透明介质，得到数字图像
```

一个实用判断是：如果某个参数会改变 `.npz` 最终介质或其派生光学密度，它属于材料/工艺阶段；如果只改变从同一介质观察得到的最终图，它属于扫描/观察阶段。

## 阶段词汇

| 术语 | 含义 | 改动后果 |
| --- | --- | --- |
| 输入图像 / input image | 普通 JPG/PNG/TIFF，被当作显示编码图像 | 它不是真实场景线性辐照度，不能认为还原了真实光子能量 |
| 近似线性工作空间 | sRGB 解码后的浮点空间 | 后续扩散、曝光、颗粒叠加更合理，但仍只是近似 |
| 胶片本体 / film stock | 胶片材料属性，如 H-D 曲线、片基、染料吸收、颗粒、halation | 改它会改变负片或正片介质母版，不适合在 scan-only 阶段反复试 |
| 冲洗 / chemistry / develop | 显影条件，如迫冲、温度、药水疲劳 | 改它会改变底片密度、灰雾、反差和颗粒 |
| 已冲洗电子介质 / developed electronic medium | 已冲洗完成的材料契约、层母版与派生 `optical_density_rgb` | `optical_density_rgb` 是重扫输入；CMY/层数据用于制版、分层与兼容 |
| transmission raw / scanner raw | 共享灯台、光源与传感器看到的线性透射图，负片或正片都可带片基参考边框 | 适合外部软件解释，也适合 scan-only 快速重扫 |
| 扫描 / scan | 对已冲洗透明介质采样，并按用户选择决定是否去片基、是否反相，再做黑白点和色彩解释 | 改它不应改变已冲洗介质，只改变数字观察结果 |
| 输出 / output | 保存格式、尺寸、质量、bit depth、sidecar | 只影响文件交付，不应承担胶片物理意义 |

## Sidecar 文件

sidecar 是跟在主文件旁边的 `.json` 伴随文件。它不保存图像数据本身，而是保存这张图像/底片如何生成：

- 输入文件名和输出文件名。
- 生成时间。
- 当次随机种子或 seed 策略。
- 胶片、冲洗、扫描、输出配置快照。
- 对 transmission/scanner raw TIFF 来说，还会记录派生片基参考边框；它可用于去色罩取样，也可直接显示灯台透光效果。

主 GUI 的随机性策略控制颗粒、漏光、过程波动及其他随机场的重复方式：

- `random`：每次运行重新采样；
- `fixed`：同一基础种子与设置可重复，用于批次复现；
- `path`：同一输入路径保持稳定，不同输入获得不同随机结果。

“过程条件随机波动”只控制时间、温度、浓度、搅拌、疲劳和已启用事故倾向的变化幅度，不等同于随机性策略。当前漏光 GUI 只暴露强度；入光边、宽度和纹理由种子自动采样，模板、组合与位置编辑尚未接入。

典型 develop 输出可以是：

```text
image.darkroom_negative.npz              密度母版
image.darkroom_negative.npz.json         密度母版 sidecar
image.darkroom_negative.png              橙色负片预览
image.scanner_raw.tiff                   16-bit linear 电子负片透射图
image.scanner_raw.tiff.json              电子负片 TIFF sidecar
```

如果用 `.npz` 重扫，sidecar 能告诉程序“这张底片当初是用哪个胶片/冲洗配置形成的”。如果 sidecar 丢失，程序只能按当前或默认配置解释密度，语义会弱一些。

## 配置分离

现在推荐把配置拆成两类：

```text
film preset
-> FilmStockConfig
 
develop preset
-> DevelopRecipeConfig / ChemistryConfig
-> developer, monobath, time, temperature, concentration, agitation, exhaustion, frame size
-> ChemistryConfig
-> develop/look 微调

scanner preset
-> ScannerConfig
-> scan/render 微调
```

完整流程会组合一个 film preset 和一个 scanner preset。只冲洗模式只选择 film preset；只扫描模式只选择 scanner preset。根目录完整 preset 只建议保留少量示例，用于展示一份配置同时包含胶片、冲洗、扫描、输出参数的写法。

## 胶片 / 负片参数

| 参数 | 所属配置 | 直观含义 | 增大时通常发生什么 |
| --- | --- | --- | --- |
| `halation_strength` | `film` | 光晕能量耦合强度 | 高光周围红橙扩散更明显，灯牌、反光边缘更“溢” |
| `halation_return_model` | `film` | Halation 回流模型 | `compatibility_rgb` 保持旧暖色 RGB 曝光回注；`layer_selective` 将同一传播场直接耦合到材料感光层 |
| `light_piping_strength` / `light_piping_depth` | `film` | 默认关闭的片基边缘光导强度与向内传播深度 | 只在潜影前增加材料层曝光，不属于扫描 flare 或漏光事故 |
| `light_piping_edge_mode` / `light_piping_layer_weights` | `film` | 明确声明入光画框边缘及各感光层相对响应 | 不读取场景物体边缘；`long_edges` / `short_edges` 依据当前画框几何解释 |
| `extreme_exposure_reversal_strength` | `film` | 默认关闭的研究性极端曝光潜影回落强度 | 普通材料应保持 0；它改变材料潜影，不是扫描反相或 Sabattier 二次曝光 |
| `extreme_exposure_reversal_start_loge` / `extreme_exposure_reversal_width` | `film` | 三材料层的回落起点和连续过渡宽度 | 使用内部归一化 `logE` 坐标，尚未按真实曝光量标定；只应配合有依据的异常材料使用 |
| `development_adjacency_strength` | `chemistry` | 默认关闭的一次性首次显影邻接强度 | 从预计反应量生成局部速率修正；不改变潜影、不执行扫描锐化 |
| `development_adjacency_radius` | `chemistry` | 邻接作用尺度，相对画面短边 | 当前是工程降阶尺度，未按乳剂厚度、扩散系数或具体药水定量标定 |
| `adjacency_work_long_edge` | `processing` | 显影邻接全局工作网格的长边上限 | 只改变内部计算分辨率；全局统计先冻结，材料分块不得各自估计 |
| `halation_layer_return_weights` | `film` | 材料层回流相对权重 | 仅在 `layer_selective` 下生效；控制三层材料接收回流曝光的相对比例，最大项归一为 1 |
| `halation_spread_scale_weights` | `film` | 紧邻、主回流、宽域三个传播尺度的相对权重 | 仅在 `layer_selective` 下生效；默认 `(0,1,0)` 只使用兼容主 PSF，增加首/末项会加入紧邻扩散或宽域薄雾 |
| `halation_threshold` | `film` | 触发 halation 的亮度阈值 | 越高越只在极亮处触发，白墙等普通亮部更干净 |
| `halation_softness` | `film` | 触发阈值的软化宽度 | 光晕触发更平滑，硬边更少，但范围可能更宽 |
| `halation_core_radius` | `film` | 近程乳剂散射半径 | 光晕核心更厚，靠近高光处更糊 |
| `halation_exponential_radius` | `film` | 长程片基反射衰减半径 | 外层红橙扩散更远，更有“片基反射”感 |
| `film_base_density_rgb` | `film` | 彩负片基/橙色 mask 的 RGB 光学密度 | 负片外观更橙，扫描去色罩压力更大 |
| `clear_support_density_rgb` | `film` | 不参与实验性色罩漂白的透明支撑体密度下限 | 限制“透明底”不会变成不存在的零密度材料 |
| `base_dye_interaction_strength` | `film` | 有色片基改变宽带 RGB 染料响应的降阶强度 | 为零时去色罩后片基颜色完全抵消；提高后仍保留等效光谱影响 |
| `base_dye_interaction_matrix` | `film` | 三个观察通道对相邻光谱带的非负重叠权重 | 控制不同片基颜色主要影响哪些染料/观察通道 |
| `mask_bleach_completion` | `develop` | 实验性色罩/染料漂白完成度；与银漂白分离 | 降低负片 mask，同时可能损伤成像染料 |
| `retained_halide_density_rgb` | `film` | 定影不足残留银盐的降阶 RGB 密度倾向 | 改变残留盐造成的色偏方向，不改变定影完成度 |
| `bleached_halide_density_*` | material model | 漂白后可定影银盐的独立降阶光学系数 | 未定义时复用原始残留卤化银系数；不会把两类状态重新合并 |
| `optical_density_rgb` | developed medium | 由最终材料及冻结观察参数派生、经过组件化事故/颗粒合成的只读 RGB 总光学密度 | 扫描优先读取；`density_cmy` 只保留层效果、兼容、制版与分层职责 |
| `material_degradation` | `film` | 材料退化/过期/保存不当的统一强度 | 同时插值感光度损失、底雾、层间失衡与颗粒增加；不代表某一种具体老化机制 |
| `degradation_speed_loss_stops` | `film` | 满强度退化时的感光度损失 | 数值越大，退化材料形成的潜影越弱 |
| `degradation_fog_density_rgb` | `film` | 满强度退化底雾的 RGB 光学密度 | 定义退化底雾的强度与颜色方向 |
| `degradation_layer_balance` | `film` | 满强度退化的感色层相对感度 | 描述不同感色层老化速度不同造成的色平衡漂移 |
| `auxiliary_layer_amount` | `film` | 可清除附加层的初始量 | 表示 rem-jet、防光晕背层或保护/污染层的降阶材料池 |
| `auxiliary_layer_density_rgb` | `film` | 附加层单位剩余量的 RGB 光学密度 | 只有附加层清除不完全时进入最终介质密度 |
| `layer_sensitivity_matrix` | `film` | RGB 曝光如何落到三层乳剂 | 改变颜色如何形成 CMY 密度，属于胶片材料层 |
| `dye_absorption_matrix` | `film` | CMY 染料密度如何吸收 RGB 光 | 交叉项越强，颜色越“互染”、越不数码干净 |
| `hd_gamma` | `film` | H-D 曲线中段斜率 | 底片密度分离更强，扫描后反差也更容易增强 |
| `density_min` | `film` | 最小密度 / 片基灰雾 | 黑位更抬、底片更“雾”，扫描后可能更灰 |
| `density_max` | `film` | 最大可形成密度 | 高曝光区域能形成更厚密度，高光压缩空间更大 |
| `log_exposure_toe` | `film` | 暗部趾部位置 | 暗部开始分离的位置变化，影响阴影可见性 |
| `log_exposure_shoulder` | `film` | 高光肩部位置 | 高光更早或更晚进入压缩，影响灯牌和天空 |
| `granularity_sigma` | `film` | 密度域颗粒 RMS 强度基准 | 颗粒更明显，尤其密度较高区域 |
| `grain_density_correlation_radius` | `film` | 颗粒空间相关半径，按画幅比例换算像素 | 颗粒更粗，细密噪声更少，大块感更明显 |
| `silver_grain_strength` | `film` | 实验性独立金属银颗粒强度；默认 0 | 只在最终介质确有残银时增加中性宽谱密度变化，不改变染料层颗粒 |
| `silver_grain_radius` / `silver_grain_clump_mix` | `film` | 金属银颗粒的相关半径与粗团聚混合 | 只改变残银空间纹理；不经过染料吸收矩阵，也不属于扫描噪声 |
| `emulsion_mtf_strength` | `film` | 乳剂有限解析力 / 输入高频抑制 | 数码锐边更不“浮”，但真实细节也可能被压掉 |
| `digital_artifact_suppression` | `film` | 对 ISP 锐化振铃的额外抑制 | JPEG/手机锐化边缘更柔，但过高会糊 |

`granularity_sigma` 只是材料基准。实际颗粒还会乘以冲洗得到的 `grain_factor`，空间尺度还会乘以 `grain_radius_factor`；迫冲、过显、高温、显影/定影疲劳、残银、药染、显影不均、显影液类型和画幅都可能让最终颗粒更强或更粗。

### 保存数组的固定语义

为了兼容旧 NPZ 和分层工具，三个数组长期并存，但用途不同：

| 字段 | 固定语义 | 是否为扫描权威 |
| --- | --- | --- |
| `density_cmy` | 历史三通道形成层代理；位于乳剂颗粒和冲洗后沉积代理之前，不承诺是纯染料 CMY | 否；仅供旧格式、兼容制版和分析 |
| `density_grain` | 历史复合层代理；包含乳剂颗粒以及药染、镀银等部分层空间事故代理 | 否；也不是独立颗粒扰动图 |
| `optical_density_rgb` | 从最终材料组分派生的只读 RGB 总光学密度 | 是；新介质的扫描与灯台只读取它 |

旧名 `density_grain` 为文件兼容而保留。Layer Pack 中旧文件名 `grain_layer.png` 实际显示 `abs(density_grain-density_cmy)` 的复合差异，可能同时包含颗粒与冲洗后事故；新 manifest 同时给出更准确的 `postprocess_density_delta_layer` 路径别名。它不能作为纯颗粒场使用，也不会为此默认增加一张大图数组。

### Legacy RGB response / grain 字段

`FilmStockConfig` 中仍保留了一批旧字段：

```text
color_matrix
base_fog
contrast
toe_strength
shoulder_strength
shoulder_point
saturation
grain_strength
grain_scales
grain_scale_weights
grain_midtone_mu
grain_midtone_sigma
```

这些字段主要服务旧的 `core/response.py` 和 `core/grain.py` 辅助模块，不属于当前电子负片主链路。当前主链路使用的是：

```text
sensitometry.py      -> H-D density / CMY density
density_grain.py    -> density-domain grain
scanner.py          -> negative inversion / scan render
```

因此，新的 film preset 不建议再调 `contrast`、`saturation` 或 `grain_strength` 这类旧字段。若要改变底片材料，应优先调 `hd_gamma`、`density_min/max`、`layer_sensitivity_matrix`、`dye_absorption_matrix`、`granularity_sigma` 和 `grain_density_correlation_radius`。若只想改变最终正像，应优先调 scanner preset 中的 `scan_saturation`、`print_contrast`、`print_color_shift` 等。

## 冲洗参数

冲洗参数描述“这一次如何处理胶片材料”。它们不会永久改变 film stock，而是先进入一个简化显影动力学模型，推导出显影活性、显影进度、灰雾、最大密度限制、颗粒增长和肩部压缩，再影响最终底片密度。

| 参数 | 所属配置 | 直观含义 | 增大时通常发生什么 |
| --- | --- | --- | --- |
| `developer_type` | `chemistry/develop` | 显影液响应类型 | 改变显影速率、反差倾向、雾化、颗粒和高光补偿方式 |
| `fixer_type` | `chemistry/develop` | 定影/清除 profile | 快速、硬膜和单浴具有不同疲劳容忍与清除倾向，不改变显影生成量 |
| `frame_size` | `chemistry/develop` | 画幅 / 放大倍率代理 | 半格/35mm 颗粒更显眼，中画幅和大画幅更细；不改变胶片材料本身 |
| `time_min` | `chemistry/develop` | 显影时间 | 时间更长通常显影更充分，反差/雾化/颗粒可能增加 |
| `concentration` | `chemistry/develop` | 药水浓度倍率 | 浓度更高会提高显影活性，但也更容易增加灰雾和反差 |
| `agitation` | `chemistry/develop` | 搅拌强度 | 搅拌更强会提高局部药水交换和显影活性 |
| `process_mode` | `chemistry/develop` | 药水执行模式 | 区分常规、单浴与反转程序所需的执行语义 |
| `compensation` | `chemistry/develop` | 补偿显影倾向 | 更强时更压高光、肩部更早介入 |
| `push_stops` | `chemistry` | 迫冲档数 | 中段反差、灰雾、颗粒都会变强；阴影可能更硬 |
| `silver_retention` | `chemistry` | 留银/漂白旁路强度 | 只降低漂白去银完成度，保留显影形成的影像银 |
| `silver_plating` | `chemistry` | 表面镀银事故强度 | 在冲洗后介质上增加斑驳中性金属银沉积，不改变原影像银材料池 |
| `light_leak_strength` | `chemistry` | 漏光预曝光强度 | 在潜影形成前从一条或少数入光边增加局部曝光，不是四边光晕滤镜 |
| `auxiliary_removal` | `chemistry` | 附加层去除完成度 | 越低则最终介质保留更多由材料定义的 rem-jet/背层密度 |
| `temperature_c` | `chemistry` | 显影温度 | 高于基准时反应更激烈，反差和颗粒略增 |
| `developer_exhaustion` | `chemistry` | 药水疲劳 | 最大密度下降，灰雾增加，反差可能变钝，颗粒更脏 |
| `fixer_exhaustion` | `chemistry` | 定影清除能力衰减 | 留下更多原始/漂白后银盐；不降低显影活性或已形成染料 |

## 扫描 / 输出解释参数

| 参数 | 所属配置 | 直观含义 | 增大时通常发生什么 |
| --- | --- | --- | --- |
| `transmission_light_ev` | `scanner` | 负片和正片共享的透射光源亮度 | 改变 linear transmission raw；启用同光源片基去除时整体亮度可能被部分抵消，不等于后段输出曝光 |
| `transmission_light_temperature_k` | `scanner` | 共享透射光源色温 | 改变 raw 通道照明；关闭去片基时会保留在直接透射结果中 |
| `negative_backlight_*` / `light_table_*` | `scanner` | 旧 preset 的两组光源字段 | 新配置优先使用共享字段；旧配置按原解释迁移，不改变旧结果 |
| `remove_base_mask` | `scanner` | 是否使用已知片基、明确边框样本或兜底估计去除片基/色罩 | 开启后执行片基平衡；它不修改介质中的真实片基 |
| `invert_transmission` | `scanner` | 是否把透射采样转为正密度并反相解释 | 开启后进入通道重建与正像映射；关闭时保留输入正负关系 |
| `include_clear_base_border` | `scanner` | 是否在扫描结果周围显示派生片基参考边框 | 扩大观察画布；边框经过同一光源和传感器，但不写回 `optical_density_rgb` |
| `scanner_response_matrix` | `scanner` | 背光透过介质后的传感器 RGB 响应 | 位于去色罩之前，描述采样器；不是负片染料校正或最终滤色 |
| `scan_base_percentile` | `scanner` | 无边框、无已知片基样本时估计片基的高百分位 | full 生成型负片会使用已知 clear base；该项主要是外部 scan-only 兜底 |
| `negative_channel_matrix` | `scanner` | 去罩并转入密度域后的通道交叉重建 | 可补偿材料/扫描组合的染料串扰；不应写成所有负片共用的固定蓝绿增强 |
| `negative_channel_gamma` | `scanner` | 去罩后 RGB 密度通道的非线性重建曲线 | 分别改变三通道密度展开，适合温和校正蓝绿记录，不回写冲洗介质 |
| `negative_channel_compensation_enabled` | `scanner` | 启用扫描侧负片蓝绿通道补偿 | 只作用于去色罩后的 RGB 场景密度，不读取或反演材料染料矩阵；默认关闭以兼容旧结果 |
| `negative_channel_compensation_strength` | `scanner` | 扫描侧蓝绿通道修正的混合强度 | 越高通道分离越强；它不尝试把残银、底雾或银盐重新识别成染料；默认 0.35 |
| `print_reference_density` | `scanner` | 哪段正像 raw density 被映射到中灰附近 | 改变整体色平衡和中灰位置，三通道不一致会产生色偏 |
| `print_gamma` | `scanner` | 扫描/打印映射基础反差 | 正像反差更强，暗部和高光更容易分开 |
| `print_mapping_mode` | `scanner` | 正像映射曲线类型 | `printlike` 更像纸面展开，`sigmoid` 更像干净视频映射 |
| `print_color_shift` | `scanner` | log 域扫描/打印滤色 | 正值提高对应通道输出，比 RGB 乘法更像滤色片 |
| `print_color_bias` | `scanner` | RGB 乘法增益 | 直接改变输出通道，容易显得数码，建议少用 |
| `highlight_color_bias` | `scanner` | 只作用在达到阈值后的 RGB 通道增益；`1.00` 中性 | 某通道低于 `1.00` 会压低，高于 `1.00` 会增强；该显式高光控制不再被正片总体滤色强度二次衰减 |
| `projection_black_adaptation` | `scanner` | 正片灯台观察阶段的暗部适应 | 单调抬升极暗层次并保持 RGB 比例；不制造固定灰底，也不改写正片密度母版 |
| `scan_saturation` | `scanner` | 扫描输出色彩浓度 | 只改变最终正像饱和度，不回写电子负片 |
| `scan_normalize` | `scanner` | 是否定黑白点 | 开启后更像扫描软件自动展开黑白点 |
| `scan_normalize_strength` | `scanner` | 全局黑白点标定的混合强度 | 越高越接近扫描软件自动展开后的正常显示范围；过高会削弱原场景曝光差异并增加极端值裁切风险 |
| `scan_normalize_mode` | `scanner` | 黑白点归一化方式 | `luma` 只重映射亮度并对每个像素的 RGB 使用同一倍率，保留整体色偏与灯台色温；`rgb` 分别展开通道，更像自动白平衡 |
| `scan_black_percentile` | `scanner` | 黑点百分位 | 越高黑位越容易压实，暗部更厚但可能堵 |
| `scan_white_percentile` | `scanner` | 白点百分位 | 越低高光越容易拉白，天空/灯牌更容易顶 |

## GUI / CLI 微调参数

| 参数 | 所属配置 | 作用对象 | 增大时通常发生什么 |
| --- | --- | --- | --- |
| `exposure_ev` | `look` | 输入曝光代理 EV | 在冲洗/底片形成前调整输入曝光计量，不是药水参数；会改变底片密度整体变化 |
| `negative_contrast` | `look` | 胶片 H-D gamma 倍率 | 改变底片形成方式，属于负片层反差 |
| `print_contrast` | `look` | 扫描/打印 gamma 倍率 | 不改变底片，只改变正像解释反差 |
| `print_exposure_ev` | `look` | 扫描输出曝光 | 不改变底片，只改变最终正像整体亮暗 |
| `saturation_multiplier` | `look` | 胶片染料选择性 | 改变材料层染料吸收，不建议 scan-only 时当普通饱和度用 |
| `halation_multiplier` | `look` | halation 强度倍数 | 快速增加/减少光晕 |
| `grain_multiplier` | `look` | 颗粒强度倍数 | 只控制颗粒显著程度 |
| `grain_size_multiplier` | `look` | 颗粒尺寸倍数 | 只控制颗粒空间相关半径，底层按画幅比例换算 |
| `look_strength` | `look` | 体验型总体风格强度 | 同时改写 halation、颗粒和 H-D gamma，适合 GUI 粗调；严肃 preset 标定时不建议依赖 |

`look_strength` 不是物理参数，而是面向 GUI / CLI 的“一键味道浓淡”总控。它会直接修改运行时 film 参数，所以适合快速预览，不适合作为精细 film preset 的核心定义。制作严肃 preset 时，应把 `look_strength` 保持在 `1.0`，直接调胶片和扫描器参数。

## 工作流建议

调 preset 时建议按这个顺序：

1. 固定输入图，关闭或降低 scan normalize，看底片/扫描基础是否正常。
2. 先调 `film` 和 `chemistry`，生成稳定的 `.npz` 电子负片。
3. 固定 `.npz`，只调 `scanner` 和扫描侧 `look`。
4. 最后再调输出格式、尺寸、sidecar、layer pack。

出现问题时，建议不要一次同时改胶片、冲洗和扫描参数。否则很难判断问题来自底片本身，还是扫描解释。
