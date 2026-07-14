# Film Foundry 预设分层说明

## 材料显示名与主 GUI 分类（2026-07）

主 GUI 在 full / develop 模式中先按材料的原生最终极性分为负片和正片，再列出对应 film preset。分类读取 `film.image_polarity`，并以 `film.medium_process` 作为旧预设兼容信息；彩色/黑白标签读取 `film.color_process`。这只是材料目录过滤，不会禁止负片材料使用反转程序或反转片材料使用负片程序。

`film.name` 是面向用户的显示名，允许中文和其他 Unicode 字符。编辑器以 UTF-8、`ensure_ascii=False` 保存 JSON。官方 preset 使用 i18n 显示名称，中文界面只显示中文主名称，英文界面只显示英文主名称；用户 preset 保留 `film.name` 原文。preset key（通常为文件 stem）继续作为路径、会话和 CLI 的稳定标识，但不显示在普通下拉列表中。

独立的材料、冲洗和透射扫描编辑器会继承启动器或主界面的当前语言。三个编辑器的内置 preset 名称和界面标签会同步切换；数值参数同时支持滑条调整与直接输入。

develop preset 的官方显示名明确包含彩色/黑白与负片/反转正片语义。scanner preset 按解释器分类：`negative_scan` 只进入负片扫描目录，`positive_transparency_scan` 只进入正片扫描目录。两类 preset 由同一个透射扫描/解释编辑器的模式切换分别保存；列表仍按当前解释过滤，不会把另一类 preset 混入造成误解。

主 GUI 只列出 `standard_color_negative`、`standard_bw_negative`、`standard_color_reversal`、`standard_bw_reversal` 四个内置冲洗流程。其他黑白流程不是删除：它们仍由银盐工艺程序编辑器和 CLI 读取，也可以另存为用户 preset 后重新进入主列表。

当前公开正片材料：`color_reversal_transparency`（标准）、`color_reversal_soft`（柔和宽容）、`color_reversal_vivid`（高饱和高反差）、`monochrome_reversal_transparency`（黑白细颗粒）。当前公开正片灯台：`positive_transparency_scan`（中性，`+0.35 EV`）、`positive_transparency_bright`（明亮，`+0.80 EV`）、`positive_transparency_warm`（暖调，`+0.50 EV / 4800 K`）。

非常规彩色负片材料包括：`blue_highlight_negative`（中高曝光逐渐偏冷蓝）、`vivid_balanced_negative`（低串色、浓郁且相对均衡的近数码式颜色分离）和 `cyan_shadow_warm_highlight_negative`（青色暗部向暖色高光过渡）。这些效果来自材料 H-D 层响应、染料吸收和片基配置，不是扫描后的 RGB 滤镜；更换冲洗程序或扫描解释仍会产生可解释的不同结果。

输出配置中，`export_transparent_plate` 表示 standalone 透明/透射介质，默认开启；`export_plate_set` 表示 standalone 密度与效果辅助层；`export_layer_pack` 表示包含前两者以及 NPZ、预览/raw 和 manifest 的完整归档。Layer Pack 开启时，前两个布尔值不再决定包内是否包含对应内容，也不会另外生成重复目录。

公开负片 film preset 的 `granularity_sigma` 当前较早期值约降低一成。这只降低材料本底；`build_effective_development()` 仍根据 developer profile、push、overdevelopment、temperature、developer/fixer exhaustion、retained silver、chemical stain、uneven development 和 frame size 生成实际 `grain_factor` 与 `grain_radius_factor`。scanner preset 的 `scan_saturation` 已小幅提高，但该字段仍只属于扫描观察。

这份说明用于解释预设的用途。不是每个 preset 都应该被理解成“正常照片输出”，有些是调试基准，有些是扫描风格，有些更接近材料母版。

当前推荐使用拆分后的预设目录：

```text
half_frame_darkroom/presets/film/
  film material: H-D curve, base, dye, grain, halation

half_frame_darkroom/presets/develop/
  silver-halide film process program: operator topology + developer/monobath,
  time, temperature, concentration, agitation, exhaustion, frame size
  银盐负片 / 反转片共享算子程序；非银盐介质使用独立 preset 目录

half_frame_darkroom/presets/scanner/
  扫描 / 正像解释 / 输出影调
```

根目录下的 `half_frame_darkroom/presets/*.json` 现在只建议保留少量完整配置示例。它们不是主流程依赖；GUI 和 CLI 默认组合 `film/`、`develop/` 与 `scanner/` 三个子目录。

拆分合并执行严格所有权：film preset 只接管 `FilmStockConfig` 和材料身份，develop preset 只接管 `DevelopRecipeConfig` 与冲洗侧 look，scanner preset 只接管 `ScannerConfig` 与扫描侧 look。放错目录的跨阶段字段不会通过 `merge_config_presets()` 渗入运行配置。需要保存所有阶段与输出开关时，应使用根目录完整配置或 session，而不是让一个拆分 preset 暗中拥有其他阶段。

## Silver-Halide Process Program Fields

新的 develop preset 可以在保留现有药水条件的同时声明降阶工艺程序：

```json
{
  "develop": {
    "program_key": "color_negative",
    "reversal_activation": 1.0,
    "first_silver_removal": 1.0,
    "silver_bleach_completion": 1.0,
    "halide_fixing_completion": 1.0,
    "dye_coupling_efficiency": 1.0,
    "auxiliary_removal": 1.0,
    "process_layer_balance": [1.0, 1.0, 1.0]
  }
}
```

`program_key` 当前支持 `auto`、`bw_negative`、`color_negative`、`color_negative_bleach_bypass`、`bw_reversal`、`color_reversal` 和诊断用 `legacy_density`。除最后一项外，它们都属于银盐胶片的共享降阶算子层；它们不用于描述拍立得扩散显影、撕拉片接收层或银版工艺。

`first_silver_removal` 只作用于当前黑白反转程序。彩色反转的第一次银像保留至末段，与第二次显影形成的银一起由 `silver_bleach_completion` 和定影步骤去除；不要用该字段伪造彩色反转的实际去银顺序。

黑白 `program_key` 描述银显影拓扑，不重定义 film preset 的材料身份。将彩色 film preset 与 `bw_negative` 组合会得到保留彩色材料三层感色和橙色片基的银像负片；使用真正的黑白 film preset 才会得到单层、通常为透明片基的黑白材料。两者不应靠修改 scanner preset 相互伪装。

材料 preset 的原生 `medium_process` / `image_polarity` 与 develop preset 的 `program_key` 可以交叉组合。彩负 + `color_reversal` 和反转片 + `color_negative` 都是有效逆冲；最终 NPZ 的 `cross_process` 会同时记录材料与程序极性。`first_development_completion` / `second_development_completion` 只区分反转程序内两次显影，并继续乘以由时间、温度、浓度、搅动和疲劳推导的全局完成度。

film preset 的 `cross_process_silver_development`、`cross_process_dye_coupling`、`cross_process_activation`、`cross_process_silver_bleach`、`cross_process_halide_fixing`、`cross_process_silver_removal`、`cross_process_dye_stability`、`cross_process_auxiliary_removal` 和 `cross_process_layer_balance` 只在材料类别与程序不匹配时生效。默认均为中性值，保证旧 preset 兼容；不应把它们放进 scanner preset。

显式 `program_key` 是形成流水线路由的权威来源。负片程序形成负像介质并声明 `negative_scan`；反转程序形成正像透明介质并声明 `positive_transparency_scan`。`auto` 根据材料 preset 的原生 `medium_process`、模式和旧式 develop 标记解析为同一套正式算子程序，不再回退 legacy。交叉冲洗应通过“材料 preset + 非原生 program preset”的组合表达，而不是修改 scanner preset 来伪造极性。

scanner preset 只拥有扫描和观看字段。即使完整 JSON 中出现 `film` 字段，新格式电子底片/正片在 scan-only 时也会优先使用自身 `optical_observation` 快照；scanner preset 不能借此改变片基、染料吸收或密度上下限。想改变这些材料属性必须重新冲洗并生成新的介质母版。

主 GUI 的 `scanner.interpretation_mode` 支持 `auto`、`negative`、`positive`。scan-only 隐藏 `auto`，要求用户明确按负片或正片处理，并允许有意忽略 NPZ 记录极性；full 默认 `auto`，也允许手动覆盖。该字段只选观察算法，不能覆盖 `optical_observation`。底层严格 API 仍保留兼容性校验。

当前五种显式程序与 `auto` 均驱动正式材料池像素计算：负片与反转共享潜影和材料池，程序顺序决定最终正负极性。内置旧创作 preset 即使没有 `program_key` 也会通过默认 `auto` 进入统一路径；只有明确选择 `legacy_density` 才运行旧 H-D/正片代理对照。该模型仍是降阶状态转换，不应等同于严格化学仿真。

## 推荐默认

### `film/clear_modern_negative` + `scanner/neutral_scan`

用途：默认中性彩色负片、电子负片标准样张、日常照片。

特点：

- H-D 曲线有温和 toe / shoulder。
- RGB 三层只轻微分离。
- scan/render 曲线保持中性，不用强通道分离制造风格。
- 适合作为 GUI 和 `film_foundry/tools/run_darkroom.py` 默认 preset。

## 高宽容干净负片类

### `film/clean_digital_like` + `scanner/clean_digital_scan`

用途：高端干净彩色负片、低颗粒、高宽容度、接近数码容错的中性胶片基底。

特点：

- H-D 曲线更低反差、更宽 toe / shoulder，亮部和暗部都比默认负片更柔和。
- 片基仍是彩色负片的橙色 mask，不再使用近透明诊断片基。
- 颗粒、halation 和 MTF 都很克制，但不是完全“无胶片感”的调试材料。
- 适合需要干净、宽容、中性的输入基底，再通过 scanner preset 轻微解释成片。
- 不再作为流程诊断基准；需要诊断冲洗灵敏度时使用 `film/diagnostic_develop_sensitive` + `scanner/diagnostic_flat_scan`。

## 写实负片类

### `film/clear_modern_negative`

用途：现代干净负片、中性默认。

建议：

- 作为默认。
- 适合先确认输入照片在本项目里是否能稳定工作。

### `film/warm_consumer_negative`

用途：消费级暖调彩负。

特点：

- 高光略暖，蓝通道轻微压低。
- 颗粒、halation 和染料交叉比默认更明显，整体更软、更日常。
- 适合日常、街景、室内暖光。
- 不建议作为默认，因为它本身就是有风格的扫描解释。

### `film/red_rich_negative`

用途：红色更厚、但整体尽量不明显偏红的彩负。

特点：

- 胶片/染料层比默认更有颜色性格。
- 红色和暖色更容易显得浓郁，但主要发生在材料/染料侧，不依赖 scanner 强行拉红。
- 如果肤色或白色物体偏色明显，优先降低扫描饱和度和 `print_color_shift`，不要先改 H-D 曲线。

### `film/deep_shadow_negative`

用途：暗部更厚的中性负片。

特点：

- 适合低饱和街景、阴天、夜景氛围。
- toe 更短、gamma 略高，暗部更快进入厚密度。
- 黑位更明确，但仍应保持扫描阶段不过度通道分离。

### `film/high_contrast_push`

用途：彩色负片的高反差、粗颗粒、迫冲感材料 preset。

特点：

- gamma 和 D-max 高于默认负片，toe / shoulder 更短，宽容度更紧。
- 颗粒更粗，片基和 halation 明确写入，避免回退到隐式默认。
- 它描述“像迫冲材料”的胶片基底；真实本次迫冲仍应通过 develop preset 的 `push_stops`、时间、温度等参数表达。

## 风格扫描类

所有负片 scanner preset 都遵循同一阶段顺序：透射背光与
`scanner_response_matrix` 先形成 `scanner_raw`，随后用已知片基/边框去色罩，
再进入 `negative_channel_matrix` / `negative_channel_gamma` 和正像映射。前一个矩阵描述采样器，后一个矩阵描述去罩后的通道重建，不能互相代替。内置旧 preset 未显式提供新字段时按恒等重建加载，以保持兼容；只有经过材料/扫描组合校准的 preset 才应写入非恒等蓝绿校正。

### `scanner/color_restored_scan`

用途：材料感知的彩色负片染料通道补偿。

特点：

- 影调、归一化和饱和度保持接近 `neutral_scan`，便于单独比较通道补偿作用。
- 显式开启 `negative_channel_compensation_enabled`，强度为 0.35。
- 补偿矩阵来自当前最终介质保存的 `dye_absorption_matrix`，不是 preset 写死的蓝绿增益。
- 黑白介质自动关闭该步骤；缺少材料快照的任意外部 raw 只能使用当前配置的通用材料矩阵。

### `scanner/rich_color_scan`

用途：浓郁彩负扫描、偏风格化输出。

特点：

- scan/render 饱和度和反差高于默认，负责“浓郁成片”。
- 通道分离比默认更明显，但避免过度压低蓝通道或把暖色扫描变成红色滤镜。
- 适合“想直接得到厚重成片”的场景，不适合作为系统基准。

### `scanner/neutral_scan`

用途：中性扫描解释，对已有电子负片重新扫描时做基准。

特点：

- 更像扫描器/软件的中性解释，而不是胶片材料 preset。
- 只保留轻微黑白点归一化、很弱的高光偏色和接近中性的饱和度。
- 适合 scan-only 阶段测试。

### `scanner/warm_consumer_scan`

用途：轻微暖调的日常扫描解释。

特点：

- 比 `rich_color_scan` 更低饱和、更低反差。
- 主要通过温和的红/绿偏移和高光暖偏塑造消费级暖调。
- 适合搭配 `warm_consumer_negative`，但也可用于默认负片的轻度暖化。

### `scanner/dense_shadow_scan`

用途：压实暗部、强化黑位的扫描解释。

特点：

- 比默认有更高反差和更强归一化，但饱和度保持克制。
- 不负责制造浓郁色彩；浓郁色彩应优先使用 `rich_color_scan`。
- 适合搭配 `deep_shadow_negative` 或需要更明确黑位的场景。

## 黑白类

### `film/monochrome_push` + `scanner/bw_neutral_scan`

用途：黑白迫冲测试。

特点：

- 绕开彩色染料和色罩问题。
- 适合验证 H-D 曲线、颗粒、MTF、halation 是否自然。

## 调参原则

胶片味不应该主要靠 scan/render 的 RGB 通道强分离来获得。更稳的顺序是：

```text
H-D toe / shoulder
-> D-min / D-max
-> 密度域颗粒
-> 片基色罩
-> 温和染料交叉
-> 扫描黑白点与中间调反差
-> 少量扫描色偏和饱和度
```

如果 scan/render 曲线里蓝通道很早进入低平台，常见后果是：

- 高光发黄或发绿。
- 天空蓝变灰。
- 暗部颜色分离过度。
- 整体像通道曲线硬掰过。

如果想要“浓郁”，优先提高 luma 黑白点展开、中间调反差和扫描饱和度；不要只靠大幅度 `print_color_shift` 或三通道独立曲线。

## Diagnostic Develop Sensitivity

### `film/diagnostic_develop_sensitive` + `scanner/diagnostic_flat_scan`

Purpose: diagnostic preset pair for checking whether develop controls are actually changing the negative state and final render.

Characteristics:
- Very steep and narrow H-D curve, so time, temperature, concentration, exhaustion, compensation, and push controls produce visible density shifts.
- Low halation, grain, MTF smoothing, and scanner color styling, so those effects do not hide develop-stage changes.
- Scanner normalization is disabled. This is intentional: automatic black/white correction can flatten the difference between two develop recipes.
- Not intended as a daily creative look. Use it as a calibration target, then move back to normal film/scanner presets after the develop behavior is tuned.

Suggested CLI check:

```bash
python -m half_frame_darkroom.app.cli full input_images outputs/diagnostic_develop \
  --film-preset diagnostic_develop_sensitive \
  --develop-preset standard_color_negative \
  --scanner-preset diagnostic_flat_scan \
  --fast
```

## Darkroom Accidents

### `develop/accident_kelp_light_leak`

Purpose: playful expert-mode accident preset for ruined chemistry, retained silver, murky stain, uneven development, and edge light leaks.

New develop fields:
- `light_leak_strength`: adds directional local edge exposure before latent-state formation; it no longer lights all four edges by default.
- `chemical_stain`: creates a murky, spatial CMY density deposit after material formation; it does not reduce developer activity or already formed silver/dye Dmax.
- `uneven_development`: creates a low-frequency local developer-activity field before material-pool conversion, so silver/dye formation—not a final overlay—becomes uneven.
- `silver_retention`: retains developed image silver by reducing bleach completion; use for bleach bypass, not surface plating.
- `silver_plating`: deposits patchy neutral surface silver after processing; use for mishandled/exhausted rapid monobath accidents.

Film presets may also define `material_degradation` (0–1) plus stock-specific full-strength `degradation_speed_loss_stops`, `degradation_fog_density_rgb`, and `degradation_layer_balance`. Degradation belongs to the material and should not be encoded as developer exhaustion.

Film presets can define `auxiliary_layer_amount` and `auxiliary_layer_density_rgb` for a reduced removable backing/rem-jet pool. Develop presets control its removal with `auxiliary_removal`; incomplete removal remains part of the developed medium, not a scanner effect.

These controls are bounded accident controls. They are meant to produce repeatable ruined-film behavior, not unbounded physical simulation.

## Scanner Preset Interpreter Keys

Scanner presets should declare their interpreter role explicitly:

```json
{
  "scanner": {
    "interpreter_key": "negative_scan",
    "target_medium_process": "negative",
    "input_polarity": "negative",
    "output_polarity": "positive"
  }
}
```

Most bundled scanner presets are `negative_scan`: negative base removal,
inversion, and positive rendering. Experimental positive-transparency presets
use `positive_transparency_scan`: virtual light-table illumination, no negative
base removal, and no inversion. Reflective scan and plate-view presets should
also use distinct interpreter keys instead of being hidden inside ordinary
negative scanner presets.

负片 scanner preset 使用 `negative_scan`，也就是“透射采样 + 已知片基去罩 + 密度反相 + 通道重建 + 正像映射”。正片透明片 preset 使用 `positive_transparency_scan`：同样先做透射采样，但随后直接进入灯台/正片观看解释，不做负片去罩与反相。统一扫描编辑器按当前模式严格过滤两类 preset；兼容入口只是预选正片模式，不代表存在第二套扫描器实现。

Experimental slide presets currently use separate material-side density controls
and light-table viewing controls:

- `positive_highlight_shoulder` / `positive_highlight_shoulder_width`: material-side highlight shoulder. These act while positive density is formed, keeping bright transparent areas from becoming a hard D-min clip.
- `positive_midtone_density`: material-side midtone density lift. This makes slide midtones denser before light-table viewing.
- `positive_shadow_toe` / `positive_shadow_toe_width`: material-side dense-shadow toe. These soften blocked positive shadows without turning the slide prototype into a wide-latitude negative workflow.
- `projection_white_softness`: light-table / projection interpretation rolloff. This softens display white after transmission, without changing the saved electronic positive density.
- `projection_black_adaptation`: light-table / projection interpretation black adaptation. This lifts very dark displayed areas without changing the saved electronic positive density or `light_table_raw`.

实验正片 preset 目前把材料侧密度控制和灯台观看控制分开：

- `positive_highlight_shoulder` / `positive_highlight_shoulder_width`：材料侧高光肩部，在正片密度形成时生效，避免亮部透明区直接硬贴 D-min。
- `positive_midtone_density`：材料侧中间调密度压实，在灯台观看之前改变电子正片密度。
- `positive_shadow_toe` / `positive_shadow_toe_width`：材料侧暗部 toe，缓和正片高密度暗部压死，但不把正片原型改成负片式宽容度。
- `projection_white_softness`：灯台 / 投影观看阶段的白点软滚降，只影响观看解释，不改变保存的电子正片密度。
- `projection_black_adaptation`：灯台 / 投影观看阶段的黑位适应，只提亮显示中的极暗区域，不改变保存的电子正片密度或 `light_table_raw`。
