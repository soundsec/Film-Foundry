# Film Foundry Project Structure

这份文件说明当前项目的主要路径、组件职责和阶段边界。它面向开发者、协作者和后续 AI agent，用来快速理解代码应该改哪里、哪些配置属于胶片材料、哪些属于冲洗流程、哪些属于扫描解释。

## 核心流程

```text
input image
-> approximate linear working space
-> film material + develop process
-> electronic negative
-> scan / render interpretation
-> final sRGB output
```

当前项目的关键设计是把三件事分开：

```text
film preset      胶片材料本体
develop preset   冲洗流程
scanner preset   扫描/输出解释
```

扫描阶段只解释已经形成的电子负片，不应回头修改胶片材料或冲洗过程。

## Medium Extension Points

当前稳定主线仍是彩色/黑白负片电子底片，但配置层已经预留更通用的介质语义：

```text
film.medium_family      film / instant / plate / paper
film.medium_process     negative / slide / reversal / instant / direct_positive / daguerreotype
film.image_polarity     negative / positive
film.color_process      color / monochrome
develop.medium_process  negative / reversal / instant / direct_positive / daguerreotype
scanner.input_polarity  negative / positive
scanner.output_polarity positive / negative
```

后续加入正片、撕拉片、拍立得、银板等介质时，应优先新增介质分支和状态解释，而不是把所有差异继续塞进
`mode = color_negative / bw_negative`。

## 顶层入口

| 路径 | 职责 |
| --- | --- |
| `run_darkroom.py` | Windows + Anaconda + IDE 友好的运行脚本。用户直接修改顶部变量，然后点 IDE 的 Run。 |
| `run_film_foundry_launcher.py` | 总启动器。打开主控制台或三个独立 preset 编辑器。 |
| `run_darkroom_gui.py` | Tkinter GUI。支持 full / develop / scan 三阶段模式，带预览窗口和底片检查网格。 |
| `run_film_material_editor.py` | 独立胶片材料编辑器。用于编辑 H-D 曲线、片基、染料矩阵、颗粒基准、halation/MTF，并保存到 `user_presets/film/`。 |
| `run_develop_process_editor.py` | 独立冲洗流程编辑器。用于编辑显影液/定影/单浴、时间、温度、浓度、搅拌、疲劳、残银，并保存到 `user_presets/develop/`。 |
| `run_scanner_render_editor.py` | 独立扫描解释编辑器。用于编辑去片基、反相/打印曲线、滤色、饱和、高光偏色、黑白点，并保存到 `user_presets/scanner/`。 |
| `run_foundry_cli.py` | 命令行 launcher，转发到 `half_frame_darkroom.app.cli`。 |
| `run_preset_curve_preview.py` | 生成 film / develop / scanner preset 曲线预览，用于检查 H-D 和 scan/render 曲线。 |
| `README.md` | 面向用户的项目介绍、安装、GUI/CLI 用法、许可说明。 |
| `STRUCTURE.md` | 当前文件。面向开发者的结构总览。 |

## 包结构

```text
half_frame_darkroom/
  app/
  core/
  model/
  presets/
  tests/
```

### `half_frame_darkroom/app/`

| 路径 | 职责 |
| --- | --- |
| `half_frame_darkroom/app/cli.py` | 真正的 CLI 实现。提供 `full`、`develop`、`scan` 子命令。 |

CLI 的典型职责：

```text
full      input image -> negative -> scanned positive
develop   input image -> electronic negative / material exports
scan      .npz or scanner_raw.tiff -> scanned positive
```

### `half_frame_darkroom/model/`

| 路径 | 职责 |
| --- | --- |
| `half_frame_darkroom/model/config.py` | 所有配置 dataclass、JSON 读取、preset 合并逻辑。 |

主要配置对象：

| 配置 | 含义 |
| --- | --- |
| `FilmStockConfig` | 胶片材料本体：H-D 曲线、片基密度、染料吸收、颗粒基准、halation 参数、MTF 参数。 |
| `DevelopRecipeConfig` | 冲洗流程：显影液/显定一体、定影、时间、温度、浓度、搅拌、疲劳、残银、画幅。 |
| `ScannerConfig` | 扫描解释：scanner raw、去片基、反相、黑白点、色彩平衡、scan/render 曲线。 |
| `LookAdjustConfig` | GUI/CLI 微调层：曝光、光晕倍率、打印反差、扫描曝光等。 |
| `OutputConfig` | 输出格式、尺寸、sidecar、scanner raw、材料包导出选项。 |
| `ProcessingConfig` | 内部处理质量：`draft / standard / high`，以及 halation/grain 的工作长边。 |
| `DarkroomConfig` | 运行时总配置。 |

`merge_config_presets()` 用于组合：

```text
film config + develop config + scanner config -> runtime DarkroomConfig
```

### `half_frame_darkroom/core/`

| 路径 | 职责 |
| --- | --- |
| `core/engine.py` | 主引擎。连接 develop 和 scan 阶段，提供 `process_file()`、`develop_negative()`、`scan_negative()`。 |
| `core/color.py` | sRGB/linear 转换、亮度计算、基础颜色工具。 |
| `core/mtf.py` | 输入端乳剂 MTF / 数字锐化伪影抑制近似。 |
| `core/halation.py` | 高光能量提取、halation PSF、光晕叠加。 |
| `core/development.py` | 冲洗动力学简化模型。把时间、温度、浓度、显定一体、疲劳等映射为有效冲洗状态。 |
| `core/sensitometry.py` | H-D 曲线核心。把 RGB 曝光代理转换为 CMY 层曝光，再生成染料密度。 |
| `core/density_grain.py` | 密度域颗粒。根据局部密度、冲洗状态和画幅生成相关颗粒。 |
| `core/scanner.py` | 负片透射、scanner raw、去片基、反相、scan/render 输出。 |
| `core/electronic_negative.py` | 电子负片材料导出：scanner raw TIFF、透明片基、CMY plate、grain/halation layer、layer pack。 |
| `core/negative_io.py` | `.npz` electronic negative 读取。 |
| `core/preview.py` | 预览尺寸和负片视觉预览。 |
| `core/io_utils.py` | 图像读写、文件夹迭代、输出保存。 |
| `core/states.py` | 阶段状态对象：`DevelopedNegative`、`ScannedPositive`。 |
| `core/subtractive.py` | 旧的/兼容用密度到正像映射。当前扫描主线优先走 `scanner.py`。 |
| `core/response.py` | legacy RGB response helper。新主线不应优先修改它。 |
| `core/grain.py` | legacy image-space grain helper。新主线不应优先修改它。 |

## Presets

```text
half_frame_darkroom/presets/
  film/
  develop/
  scanner/

user_presets/
  film/
  develop/
  scanner/
```

`half_frame_darkroom/presets/` 保存内置预设。主 GUI 只负责调用 preset；独立编辑器负责制造和保存用户 preset：
胶片材料写入 `user_presets/film/`，冲洗流程写入 `user_presets/develop/`，扫描解释写入
`user_presets/scanner/`。加载时优先读取用户同名预设，再回退到内置 preset。

### `presets/film/`

胶片材料本体。用于定义电子负片如何形成，但不表示一次具体冲洗。

示例：

```text
clear_modern_negative.json
clean_digital_like.json
red_rich_negative.json
warm_consumer_negative.json
monochrome_push.json
```

适合修改的字段：

```text
film.hd_gamma
film.density_min / density_max
film.log_exposure_toe / log_exposure_shoulder
film.layer_sensitivity_matrix
film.dye_absorption_matrix
film.film_base_density_rgb
film.granularity_sigma
film.grain_density_correlation_radius
film.halation_*
film.emulsion_mtf_*
```

### `presets/develop/`

一次冲洗流程。它不永久改变胶片材料，而是生成本次冲洗的 effective response。

示例：

```text
standard_color_negative.json
standard_bw_negative.json
fine_grain_bw.json
push_bw.json
compensating_bw.json
monobath_clean.json
monobath_exhausted_silvering.json
```

适合修改的字段：

```text
develop.developer_type
develop.fixer_type
develop.process_mode
develop.frame_size
develop.time_min
develop.temperature_c
develop.concentration
develop.agitation
develop.push_stops
develop.developer_exhaustion
develop.fixer_exhaustion
develop.compensation
develop.silver_retention
```

### `presets/scanner/`

扫描/输出解释。它只改变同一张电子负片被怎样解释成正像。

示例：

```text
neutral_scan.json
clean_digital_scan.json
rich_color_scan.json
warm_consumer_scan.json
bw_neutral_scan.json
dense_shadow_scan.json
```

适合修改的字段：

```text
scanner.scan_method
scanner.scan_base_percentile
scanner.print_reference_density
scanner.print_gamma
scanner.print_mapping_mode
scanner.print_color_shift
scanner.highlight_color_bias
scanner.scan_saturation
scanner.scan_normalize_*
look.print_contrast
look.print_exposure_ev
```

## 状态对象

| 对象 | 所在路径 | 含义 |
| --- | --- | --- |
| `DevelopedNegative` | `core/states.py` | 已冲洗底片。包含 `density_cmy`、`density_grain`、`after_mtf`、`after_halation` 等。 |
| `ScannedPositive` | `core/states.py` | 已扫描正像。包含 `scanner_raw`、`negative_base_balanced`、`positive_raw`、`positive_linear`、`output_srgb` 等。 |

正常诊断顺序：

```text
DevelopedNegative.density_grain
-> negative_visual_preview
-> scanner_raw
-> negative_base_balanced
-> positive_raw
-> output_srgb
```

## 输出文件

Develop 阶段常见输出：

```text
image.darkroom_negative.npz
image.darkroom_negative.npz.json
image.darkroom_negative.negative_visual.png
image.darkroom_negative.scanner_raw.tiff
image.darkroom_negative.scanner_raw.tiff.json
image.darkroom_negative_transparent_plate/
image.darkroom_negative_plate_set/
image.darkroom_negative_layer_pack/
```

含义：

| 文件 | 含义 |
| --- | --- |
| `.npz` | 内部电子负片密度母版，最重要。 |
| `.npz.json` | sidecar，记录生成参数、seed、路径等。 |
| `.negative_visual.png` | 橙色片基负片外观预览，不是密度主数据。 |
| `.scanner_raw.tiff` | 16-bit linear 电子负片透射图，适合外部软件手动去色罩。 |
| `*_transparent_plate/` | 透明片基负片和 density alpha。 |
| `*_plate_set/` | CMY plate、density plate、grain layer、halation layer。 |
| `*_layer_pack/` | 一次性材料包。 |

## GUI 结构

`run_darkroom_gui.py` 当前有三种阶段模式：

| 模式 | 显示内容 |
| --- | --- |
| `full` | film preset + develop preset + scanner preset，直接输出最终图。 |
| `develop` | film preset + develop preset，只输出电子负片。 |
| `scan` | scanner preset，只读取现有 `.npz` 或 scanner raw TIFF。 |

GUI 默认是简易模式。勾选“专家模式”后显示更多内部过程控制，例如 developer/fixer 类型、疲劳、残银、滤色和高光偏色。

预览窗口现在包含轻量 Inspector：

```text
develop preview:
  input image | negative visual / density / CMY / halation grid

scan preview:
  negative visual | scanner raw / base balanced / raw positive / final scan grid
```

## 速度与分辨率策略

当前阶段推荐先用 `preview_long_edge` 或 GUI 预览模式试参，最终输出再用原始尺寸。运行时还可以使用 `processing.quality_mode` 控制内部低频/随机场模块的工作尺寸：

```text
draft     更快，halation/grain 默认较低工作长边
standard  默认平衡
high      尽量不缩小低频/颗粒模块
```

后续可以按这个顺序做加速：

1. 缓存 `.npz` / scanner raw，避免重复 develop。
2. 为 halation、MTF、颗粒低频场增加质量档位。
3. 用 resize / multi-resolution 做低频层计算，再回贴原图。
4. 对 H-D、扫描反相、归一化等局部运算做 tile 分块。
5. 对大半径 halation PSF 切换 FFT 或分离近似卷积。
6. 可选 Numba / CuPy / OpenCL 后端，但不作为核心依赖。

短期目标不是立刻追求极限速度，而是先把功能边界和架构稳定下来，再做易用、可控的性能优化。

## 修改建议

常见任务应该优先改这些路径：

| 任务 | 首选路径 |
| --- | --- |
| 改胶片材料性格 | `presets/film/*.json`、`core/sensitometry.py`、`core/density_grain.py` |
| 改冲洗流程 | `presets/develop/*.json`、`core/development.py` |
| 改扫描色彩/影调 | `presets/scanner/*.json`、`core/scanner.py` |
| 改 GUI 参数暴露 | `run_darkroom_gui.py` |
| 改 CLI 参数 | `half_frame_darkroom/app/cli.py` |
| 改电子负片导出 | `core/electronic_negative.py` |
| 改输入/输出保存 | `core/io_utils.py`、`core/engine.py` |
| 增加测试 | `half_frame_darkroom/tests/` |
