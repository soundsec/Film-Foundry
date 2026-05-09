# Film Foundry / Electronic Negative Factory

Film Foundry 是一个早期阶段的物理启发式胶片成像与电子负片生成项目。它不是传统 LUT 滤镜软件，也不是严格的胶片化学仿真器。当前目标是建立一条可解释、可调试、适合单张图像处理的流程：既能生成最终正像，也能输出可复用的“电子负片”材料。

Film Foundry is an early-stage, physics-inspired film imaging prototype. It is not a LUT filter app and not a strict chemical simulator. The current focus is a practical single-image pipeline that can generate both final rendered images and reusable electronic negative materials.

## 核心流程 / Core Pipeline

```text
input image
-> approximate linear working space
-> film/develop stage
-> electronic negative
-> scan/render stage
-> final sRGB output
```

普通 JPG / PNG / TIFF 输入图像通常已经经过相机 ISP、tone mapping、锐化、降噪和压缩。这里的 sRGB-to-linear 只表示“近似线性工作空间”，不是还原真实场景辐照度。

Typical JPG / PNG / TIFF images are display-referred and often already processed by camera ISP pipelines. The sRGB-to-linear conversion here only creates an approximate linear working space, not real scene radiance.

## 当前状态 / Current Status

这是一个 alpha 原型。当前最重要的结构是把流程拆成两步：

- **冲洗 / Develop**：从输入图像生成电子负片。
- **扫描 / Scan**：把已有电子负片解释成正像输出。

因此，你可以先冲洗一次得到 `.npz` 或 scanner raw TIFF，再用不同扫描 preset 扫描同一张底片。

This repository is an alpha prototype. The useful parts are already separated into two stages:

- **Develop stage**: makes an electronic negative from an input image.
- **Scan stage**: interprets an existing electronic negative into a positive image.

## 功能 / Features

- 基于 H-D 曲线思想的胶片密度响应。
- CMY density 形式的彩色负片表达。
- 密度域颗粒。
- 由高光能量触发的 halation / optical spread。
- 用于抑制过锐数字输入的 emulsion MTF 近似。
- 显式电子负片输出：
  - `.npz` density master
  - 橙色片基负片预览
  - 可选 16-bit scanner raw TIFF
  - 透明负片 / 分色母版 / 图层材料包
- film/develop preset 与 scanner/render preset 分离。
- 提供适合 Windows + Anaconda + IDE 直接运行的脚本。
- 提供 CLI 入口，方便批处理与复现。

## 安装 / Installation

推荐 Python 3.10+。

```bash
pip install -r requirements.txt
```

也可以使用 conda：

```bash
conda env create -f environment.yml
conda activate film-foundry
```

主要依赖：

- NumPy
- OpenCV
- Pillow
- pytest, for tests

## GUI 使用 / GUI Usage

运行：

```bash
python run_darkroom_gui.py
```

GUI 适合快速预览整体色调和影调。界面按阶段分离：

- **Full**：输入图像 -> 冲洗底片 -> 扫描正像。
- **Develop**：只冲洗，输出电子负片。
- **Scan**：读取已有 `.npz` 或 scanner raw TIFF，重新扫描。


## IDE 脚本使用 / IDE Script Usage

如果你更习惯 Windows + Anaconda + IDE 的工作方式，可以直接打开 `run_darkroom.py`，修改顶部的“用户常用设置”，然后点击 IDE 的 Run 按钮。

常用设置：

```python
PIPELINE_MODE = "full"       # "full", "develop", or "scan"
INPUT_PATH = PROJECT_ROOT / "input_images"
OUTPUT_PATH = PROJECT_ROOT / "outputs"
NEGATIVE_PATH = PROJECT_ROOT / "outputs" / "negatives"

FILM_PRESET_NAME = "clear_modern_negative"
SCANNER_PRESET_NAME = "neutral_scan"
```

## 命令行使用 / CLI Usage

查看帮助：

```bash
python run_foundry_cli.py --help
```

示例：

```bash
python run_foundry_cli.py full input_images outputs --film-preset clear_modern_negative --scanner-preset neutral_scan

python run_foundry_cli.py develop input_images outputs/negatives --film-preset clear_modern_negative --save-scanner-raw --layer-pack

python run_foundry_cli.py scan outputs/negatives outputs/rescans --scanner-preset rich_color_scan
```

如果以包形式安装，也可以使用：

```bash
film-foundry --help
electronic-negative-factory --help
```

## Presets

当前推荐使用拆分后的 preset：

```text
half_frame_darkroom/presets/film/
  胶片本体、冲洗条件、电子负片形成

half_frame_darkroom/presets/scanner/
  扫描解释、色彩平衡、最终影调渲染
```

推荐默认组合：

```text
film/clear_modern_negative
scanner/neutral_scan
```

调试基准组合：

```text
film/clean_digital_like
scanner/clean_digital_scan
```

根目录下的 `half_frame_darkroom/presets/*.json` 只作为完整配置示例保留，不是推荐主流程。

更多说明：

- `docs/PRESET_GUIDE.md`
- `docs/TERMINOLOGY.md`

## 输出文件 / Output Files

Develop 模式可能生成：

```text
image.darkroom_negative.npz
image.darkroom_negative.npz.json
image.darkroom_negative.png
image.scanner_raw.tiff
image.scanner_raw.tiff.json
```

含义：

- `.npz`：电子负片密度母版。
- `.npz.json`：sidecar，保存配置快照和生成信息。
- `.png`：橙色片基负片视觉预览。
- `.scanner_raw.tiff`：16-bit linear 电子负片透射图，适合外部软件处理。
- `.scanner_raw.tiff.json`：scanner raw TIFF 的 sidecar。

`.npz` 是最重要的内部底片母版。scanner raw TIFF 更像便携、可被图像软件读取的电子负片图像。

## 电子负片材料导出 / Electronic Negative Material Exports

项目可以导出面向创作和制版的材料：

- transparent negative plate
- density alpha
- CMY plate set
- grain layer
- halation layer
- layer pack folder

这些输出适合导入 Photoshop、Krita、Affinity、Procreate 等软件，也可以用于海报、分色、丝网、半色调和后续接触印相类实验。

## 测试 / Tests

运行测试：

```bash
python -m pytest half_frame_darkroom/tests
```

快速语法检查：

```bash
python -m compileall half_frame_darkroom run_darkroom.py run_darkroom_gui.py
```

## License

本项目当前尚未决定最终采用哪一种开源协议。在正式添加 `LICENSE` 文件之前，项目作者暂时保留所有权利。

目前允许个人出于学习、研究、测试和非商业创作目的查看、运行和修改本项目代码。其他形式的分发、再授权、商业使用或将本项目整合进公开产品前，请先与项目作者确认授权边界。

The final open-source license has not been selected yet. Until a `LICENSE` file is added, all rights are reserved by the project author. Personal study, research, testing, and non-commercial creative use are currently permitted.

## Code Provenance Notice

本项目在开发过程中使用了 AI agent 辅助进行代码整理、重构、文档编写和调试。虽然项目作者已尽力审查代码来源、项目结构和实现方式，但无法保证所有代码在形式上完全不与其他公开或闭源项目存在相似之处。

如果你认为本项目中的某段代码、注释、文档、命名或结构可能侵犯了你的权利，或与某个受保护项目高度相似，请联系项目作者并提供具体文件、位置和理由。作者会优先核查，并在确认问题后进行修改、替换或删除。

This project was developed with assistance from AI agents for coding, refactoring, documentation, and debugging. If you believe any code, comments, documentation, naming, or structure infringes your rights or is too similar to protected work, please contact the project author with specific details so it can be reviewed and corrected.


## Notes

本项目不声称精确复刻任何商业胶卷。当前 preset 更适合理解为“行为类型”或“材料倾向”，不是官方胶片模拟。

This project avoids claiming exact film stock reproduction. Presets should be treated as behavior profiles, not official emulations of commercial film products.
