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

## 冲洗参数

| 参数 | 所属配置 | 直观含义 | 增大时通常发生什么 |
| --- | --- | --- | --- |
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
| `exposure_ev` | `look` | 冲洗前曝光代理 | 底片密度整体变化；scan normalize 开启后最终亮暗会被部分抵消 |
| `negative_contrast` | `look` | 胶片 H-D gamma 倍率 | 改变底片形成方式，属于负片层反差 |
| `print_contrast` | `look` | 扫描/打印 gamma 倍率 | 不改变底片，只改变正像解释反差 |
| `print_exposure_ev` | `look` | 扫描输出曝光 | 不改变底片，只改变最终正像整体亮暗 |
| `saturation_multiplier` | `look` | 胶片染料选择性 | 改变材料层染料吸收，不建议 scan-only 时当普通饱和度用 |
| `halation_multiplier` | `look` | halation 强度倍数 | 快速增加/减少光晕 |
| `grain_multiplier` | `look` | 颗粒强度倍数 | 只控制颗粒显著程度 |
| `grain_size_multiplier` | `look` | 颗粒尺寸倍数 | 只控制颗粒空间相关半径，底层按画幅比例换算 |
| `look_strength` | `look` | 总体风格强度 | 同时影响 halation、颗粒和 H-D gamma，适合粗调 |


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
