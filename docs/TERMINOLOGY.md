# Film Foundry 术语与参数说明

这份说明面向调参和后续开发。项目当前核心是“把普通图像解释成一次胶片曝光，再生成可复用电子负片”，所以最重要的分界是：

```text
输入图像
-> 胶片 / 冲洗：形成底片密度
-> 扫描 / 输出：解释底片，得到正像
```

一个实用判断是：如果某个参数会改变 `.npz` 电子负片里的密度，它属于胶片/冲洗阶段；如果只改变从同一张底片扫出来的最终图，它属于扫描/输出阶段。

## 阶段词汇

| 术语 | 含义 | 改动后果 |
| --- | --- | --- |
| 输入图像 / input image | 普通 JPG/PNG/TIFF，被当作显示编码图像 | 它不是真实场景线性辐照度，不能认为还原了真实光子能量 |
| 近似线性工作空间 | sRGB 解码后的浮点空间 | 后续扩散、曝光、颗粒叠加更合理，但仍只是近似 |
| 胶片本体 / film stock | 胶片材料属性，如 H-D 曲线、片基、染料吸收、颗粒、halation | 改它会改变电子负片母版，不适合在 scan-only 阶段反复试 |
| 冲洗 / chemistry / develop | 显影条件，如迫冲、温度、药水疲劳 | 改它会改变底片密度、灰雾、反差和颗粒 |
| 电子负片 / electronic negative | 已冲洗完成的密度母版，主要是 `density_cmy` 和 `density_grain` | 这是项目最重要的可复用中间产物 |
| scanner raw / 线性电子负片 TIFF | 扫描器看到的负片透射图像，通常带片基边框 | 适合外部软件手动去色罩，也适合 scan-only 快速重扫 |
| 扫描 / scan | 对已冲洗底片做片基平衡、反相、黑白点、色彩解释 | 改它不应改变电子负片，只改变最终正像 |
| 输出 / output | 保存格式、尺寸、质量、bit depth、sidecar | 只影响文件交付，不应承担胶片物理意义 |

## Sidecar 文件

sidecar 是跟在主文件旁边的 `.json` 伴随文件。它不保存图像数据本身，而是保存这张图像/底片如何生成：

- 输入文件名和输出文件名。
- 生成时间。
- 当次随机种子或 seed 策略。
- 胶片、冲洗、扫描、输出配置快照。
- 对 `scanner_raw.tiff` 来说，还会记录边框表示未曝光片基，可用于去色罩取样。

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
| `halation_threshold` | `film` | 触发 halation 的亮度阈值 | 越高越只在极亮处触发，白墙等普通亮部更干净 |
| `halation_softness` | `film` | 触发阈值的软化宽度 | 光晕触发更平滑，硬边更少，但范围可能更宽 |
| `halation_core_radius` | `film` | 近程乳剂散射半径 | 光晕核心更厚，靠近高光处更糊 |
| `halation_exponential_radius` | `film` | 长程片基反射衰减半径 | 外层红橙扩散更远，更有“片基反射”感 |
| `film_base_density_rgb` | `film` | 彩负片基/橙色 mask 的 RGB 光学密度 | 负片外观更橙，扫描去色罩压力更大 |
| `layer_sensitivity_matrix` | `film` | RGB 曝光如何落到三层乳剂 | 改变颜色如何形成 CMY 密度，属于胶片材料层 |
| `dye_absorption_matrix` | `film` | CMY 染料密度如何吸收 RGB 光 | 交叉项越强，颜色越“互染”、越不数码干净 |
| `hd_gamma` | `film` | H-D 曲线中段斜率 | 底片密度分离更强，扫描后反差也更容易增强 |
| `density_min` | `film` | 最小密度 / 片基灰雾 | 黑位更抬、底片更“雾”，扫描后可能更灰 |
| `density_max` | `film` | 最大可形成密度 | 高曝光区域能形成更厚密度，高光压缩空间更大 |
| `log_exposure_toe` | `film` | 暗部趾部位置 | 暗部开始分离的位置变化，影响阴影可见性 |
| `log_exposure_shoulder` | `film` | 高光肩部位置 | 高光更早或更晚进入压缩，影响灯牌和天空 |
| `granularity_sigma` | `film` | 密度域颗粒 RMS 强度基准 | 颗粒更明显，尤其密度较高区域 |
| `grain_density_correlation_radius` | `film` | 颗粒空间相关半径，按画幅比例换算像素 | 颗粒更粗，细密噪声更少，大块感更明显 |
| `emulsion_mtf_strength` | `film` | 乳剂有限解析力 / 输入高频抑制 | 数码锐边更不“浮”，但真实细节也可能被压掉 |
| `digital_artifact_suppression` | `film` | 对 ISP 锐化振铃的额外抑制 | JPEG/手机锐化边缘更柔，但过高会糊 |

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
| `frame_size` | `chemistry/develop` | 画幅 / 放大倍率代理 | 半格/35mm 颗粒更显眼，中画幅和大画幅更细；不改变胶片材料本身 |
| `time_min` | `chemistry/develop` | 显影时间 | 时间更长通常显影更充分，反差/雾化/颗粒可能增加 |
| `concentration` | `chemistry/develop` | 药水浓度倍率 | 浓度更高会提高显影活性，但也更容易增加灰雾和反差 |
| `agitation` | `chemistry/develop` | 搅拌强度 | 搅拌更强会提高局部药水交换和显影活性 |
| `process_mode` | `chemistry/develop` | 处理模式 | 当前主线为 `normal_negative`；后续可扩展黑白反转、交叉处理等 |
| `compensation` | `chemistry/develop` | 补偿显影倾向 | 更强时更压高光、肩部更早介入 |
| `push_stops` | `chemistry` | 迫冲档数 | 中段反差、灰雾、颗粒都会变强；阴影可能更硬 |
| `temperature_c` | `chemistry` | 显影温度 | 高于基准时反应更激烈，反差和颗粒略增 |
| `developer_exhaustion` | `chemistry` | 药水疲劳 | 最大密度下降，灰雾增加，反差可能变钝，颗粒更脏 |

## 扫描 / 输出解释参数

| 参数 | 所属配置 | 直观含义 | 增大时通常发生什么 |
| --- | --- | --- | --- |
| `scan_base_percentile` | `scanner` | 无边框时估计片基的高百分位 | 更倾向使用最亮/最低密度区域当片基，可能更强去色罩 |
| `print_reference_density` | `scanner` | 哪段正像 raw density 被映射到中灰附近 | 改变整体色平衡和中灰位置，三通道不一致会产生色偏 |
| `print_gamma` | `scanner` | 扫描/打印映射基础反差 | 正像反差更强，暗部和高光更容易分开 |
| `print_mapping_mode` | `scanner` | 正像映射曲线类型 | `printlike` 更像纸面展开，`sigmoid` 更像干净视频映射 |
| `print_color_shift` | `scanner` | log 域扫描/打印滤色 | 正值提高对应通道输出，比 RGB 乘法更像滤色片 |
| `print_color_bias` | `scanner` | RGB 乘法增益 | 直接改变输出通道，容易显得数码，建议少用 |
| `highlight_color_bias` | `scanner` | 只作用在高光的色偏 | 可做白色偏绿、压蓝高光，但过强会像数字调色 |
| `scan_saturation` | `scanner` | 扫描输出色彩浓度 | 只改变最终正像饱和度，不回写电子负片 |
| `scan_normalize` | `scanner` | 是否定黑白点 | 开启后更像扫描软件自动展开黑白点 |
| `scan_normalize_strength` | `scanner` | 黑白点归一化混合强度 | 越高越干净、越稳定，也越可能洗掉胶片色偏 |
| `scan_normalize_mode` | `scanner` | 黑白点归一化方式 | `luma` 保留色偏，`rgb` 更像自动白平衡 |
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

## 正冲 / 反转 / Reversal

中文里“正冲”常被用来指反转冲洗：最终直接在胶片上得到正像，而不是得到负片再扫描反相。

黑白反转的典型流程是：

```text
第一次显影
-> 漂白去掉第一次显影形成的负像银
-> 清洗 / 清除漂白残留
-> 二次曝光或化学雾化
-> 第二次显影形成正像
-> 定影 / 水洗 / 干燥
```

彩色反转并不是不存在。彩色反转片一般对应 E-6 一类流程，目标是得到彩色正片/幻灯片。它和“彩色负片 + C-41 + 扫描反相”不是一回事。

### 是否建议现在实现？

当前不建议马上把正冲做进主流程。

原因：

1. 项目当前最有价值的主线是电子负片母版，正冲会绕开“负片 -> 扫描解释”这条主线。
2. 黑白反转可以较简单地做成 `bw_reversal_positive`，但它需要新的状态对象：正片密度或正像透过率，而不是 `density_cmy` 负片密度。
3. 彩色反转需要新的材料假设：正片胶片的 H-D、染料形成、颜色密度和扫描逻辑都不同，不能直接复用彩负参数。
4. 如果只是为了“像正片”，更适合先做 scan/render preset；如果要物理语义成立，就应该作为独立 film mode。

建议路线：

```text
第一步：继续打磨彩色负片 / 电子负片
第二步：加 bw_reversal_positive 原型，只做黑白正片
第三步：稳定后再考虑 color_reversal_slide / E-6-inspired 模式
```

## 常见调参判断

| 现象 | 优先检查 | 建议 |
| --- | --- | --- |
| 输出整体发白 | `print_contrast`, `scan_black_percentile`, `scan_normalize_strength` | 先提高 print contrast，再略提高黑点百分位 |
| 曝光 EV 不再明显控制亮暗 | `scan_normalize` | 归一化会重新定黑白点，降低 strength 或关闭 |
| 颜色像 YCrCb、太干净 | `scan_normalize_mode`, `scan_normalize_strength` | 保持 `luma`，降低 strength，避免 `rgb` |
| 颜色寡淡 | `scan_saturation`, `print_contrast`, `print_color_shift` | 先调扫描饱和度，再调 print contrast，最后小幅 log 域滤色 |
| 整体偏红 | `print_color_shift`, `print_reference_density` | 不要用 RGB bias 硬压；优先微调 log 域滤色 |
| 红色不够浓 | `scan_saturation`, `dye_absorption_matrix`, `layer_sensitivity_matrix` | 若只是最终图，调 scan saturation；若要材料特性，做新 preset |
| 颗粒像盖在图上 | `emulsion_mtf_strength`, `grain_size_multiplier`, `digital_artifact_suppression` | 提高 MTF/伪影抑制，适当增大颗粒尺寸 |
| 细节被糊掉 | `emulsion_mtf_strength`, `digital_artifact_suppression` | 降低这两个值，尤其对干净相机图 |
| 光晕太硬 | `halation_softness`, `halation_gradient_suppression`, `halation_strength` | 提高软化和边缘补偿，略降强度 |
| 高光颜色抢戏 | `highlight_color_bias` | 减小高光偏色，先让整体扫描色彩成立 |

## 工作流建议

调 preset 时建议按这个顺序：

1. 固定输入图，关闭或降低 scan normalize，看底片/扫描基础是否正常。
2. 先调 `film` 和 `chemistry`，生成稳定的 `.npz` 电子负片。
3. 固定 `.npz`，只调 `scanner` 和扫描侧 `look`。
4. 最后再调输出格式、尺寸、sidecar、layer pack。

不要一次同时改胶片、冲洗和扫描参数。否则很难判断问题来自底片本身，还是扫描解释。

## 大图性能路线

当前开发阶段可以用 `preview_long_edge` 或 GUI 预览模式快速试参；正式输出再回到原始尺寸。后续若要让大分辨率冲洗更快，优先考虑这些方向：

1. **分辨率分层计算**：halation、MTF 判定、部分低频残留层可以在较低分辨率计算，再上采样回原图；真正需要像素级保真的 H-D 密度映射保留全分辨率。
2. **分块 / tile 处理**：对 H-D、密度映射、扫描反相这类局部运算可按块处理，降低内存峰值；halation 这类卷积需要带 overlap padding。
3. **缓存电子负片阶段**：develop 阶段生成 `.npz` / scanner raw 后，反复调扫描不再重跑冲洗，这是当前最重要的实际加速方式。
4. **更快的随机场生成**：颗粒可以先在低分辨率生成相关随机场，再按画幅比例上采样；细节颗粒可叠加轻量局部噪声。
5. **可选 FFT / separable convolution**：大半径 halation PSF 可以切换到 FFT 卷积或分离近似核，避免大核 `filter2D` 在高分辨率下变慢。
6. **Numba / CuPy / OpenCL 可选后端**：核心仍保持 NumPy/OpenCV，但以后可以为重计算模块加可选加速后端，而不是把 GPU 作为必需依赖。
