# Film Foundry 预设分层说明

这份说明用于解释预设的用途。不是每个 preset 都应该被理解成“正常照片输出”，有些是调试基准，有些是扫描风格，有些更接近材料母版。

当前推荐使用拆分后的预设目录：

```text
half_frame_darkroom/presets/film/
  胶片 / 冲洗 / 电子负片形成

half_frame_darkroom/presets/scanner/
  扫描 / 正像解释 / 输出影调
```

根目录下的 `half_frame_darkroom/presets/*.json` 现在只建议保留少量完整配置示例。它们不是主流程依赖；GUI 和 CLI 默认使用 `film/` 与 `scanner/` 两个子目录。

## 推荐默认

### `film/clear_modern_negative` + `scanner/neutral_scan`

用途：默认中性彩色负片、电子负片标准样张、日常照片。

特点：

- H-D 曲线有温和 toe / shoulder。
- RGB 三层只轻微分离。
- scan/render 曲线保持中性，不用强通道分离制造风格。
- 适合作为 GUI 和 `run_darkroom.py` 默认 preset。

## 系统基准类

### `film/clean_digital_like` + `scanner/clean_digital_scan`

用途：流程健康检查、scanner raw / inversion / render debug。

特点：

- H-D 曲线接近三通道重合。
- 片基和染料交叉都很弱。
- 颗粒、halation、MTF 都很轻。
- 如果这个 preset 扫出来也严重偏色，问题通常在扫描/输出解释层，而不是胶片材料层。

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
- 适合日常、街景、室内暖光。
- 不建议作为默认，因为它本身就是有风格的扫描解释。

### `film/red_rich_negative`

用途：红色更厚、但整体尽量不明显偏红的彩负。

特点：

- 胶片/染料层比默认更有颜色性格。
- 红色和暖色更容易显得浓郁。
- 如果肤色或白色物体偏色明显，优先降低扫描饱和度和 `print_color_shift`，不要先改 H-D 曲线。

### `film/deep_shadow_negative`

用途：暗部更厚的中性负片。

特点：

- 适合低饱和街景、阴天、夜景氛围。
- 黑位更明确，但仍应保持扫描阶段不过度通道分离。

## 风格扫描类

### `scanner/rich_color_scan`

用途：浓郁彩负扫描、偏风格化输出。

特点：

- scan/render 反差和饱和度更高。
- 通道分离比默认更明显，但已经避免过度压低蓝通道。
- 适合“想直接得到厚重成片”的场景，不适合作为系统基准。

### `scanner/neutral_scan`

用途：中性扫描解释，对已有电子负片重新扫描时做基准。

特点：

- 更像扫描器/软件的中性解释，而不是胶片材料 preset。
- 适合 scan-only 阶段测试。

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
