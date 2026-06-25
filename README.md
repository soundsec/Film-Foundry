# Film Foundry / Electronic Negative Factory

Film Foundry 是一个早期阶段的物理启发式胶片成像与电子负片生成项目。它不是传统 LUT 滤镜软件，也不是严格的胶片化学仿真器。当前目标是建立一条可解释、可调试、适合单张图像处理的流程：既能生成最终正像，也能输出可复用的电子负片。

Film Foundry is an early-stage, physics-inspired film imaging prototype. It is not a LUT filter app and not a strict chemical simulator. The current focus is a practical single-image pipeline that can generate both final rendered images and reusable electronic negative materials.

项目中的模型是有意降阶的近似实现：它们受物理、化学和真实暗房/扫描工作流启发，但不追求严格物理精确。项目更重视可解释的控制、可检查的中间状态，以及有创作辨识度的材料行为。

Its models are intentionally approximate: physics-inspired, chemistry-inspired, and real-workflow-inspired rather than physically exact. The project favors explainable controls, inspectable intermediate states, and creative material behavior over laboratory-accurate simulation.

长期的材料、工艺和输出解释设计记录在 `docs/MEDIA_PIPELINE_ROADMAP.md`。

Longer-term material/process/render design notes are tracked in `docs/MEDIA_PIPELINE_ROADMAP.md`.

## 核心流程 / Core Pipeline

```text
input image
-> approximate linear working space
-> film material + develop process stage
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

因此，你可以先冲洗一次得到 `.npz` 或 scanner raw TIFF，再用不同 scanner preset 反复扫描同一张电子负片。

This repository is an alpha prototype. The useful parts are already separated into two stages:

- **Develop stage**: makes an electronic negative from an input image.
- **Scan stage**: interprets an existing electronic negative into a positive image.

This means you can develop once, save a `.npz` or scanner raw TIFF, and then rescan the same electronic negative with different scanner presets.

## 功能 / Features

- 基于 H-D 曲线思想的胶片密度响应。 / H-D-curve-inspired density response.
- CMY density 形式的彩色负片表达。 / Color negative representation in CMY density space.
- 密度域颗粒，并受画幅、冲洗状态和材料颗粒参数影响。 / Density-domain grain affected by frame size, development state, and film material settings.
- 由高光能量触发的 halation / optical spread。 / Highlight-triggered halation and optical spread.
- 用于抑制过锐数字输入的 emulsion MTF 近似。 / Approximate emulsion MTF to soften overly digital input sharpness.
- 暗房事故参数：形成密度前的漏光、药染浑浊、显影不均、残银/镀银倾向。 / Darkroom accident controls: pre-density light leak, chemical stain, uneven development, and retained-silver/silvering tendency.
- 显式电子负片输出。 / Explicit electronic negative outputs:
  - `.npz` density master / 密度母版
  - orange-base negative preview / 橙色片基负片预览
  - 16-bit scanner raw TIFF, saved by default in Develop mode / Develop 模式默认保存的 16-bit scanner raw TIFF
  - transparent negative, CMY plates, grain/halation layers, and layer pack / 透明负片、CMY 分色、颗粒/光晕层和图层材料包
- film / develop / scanner preset 三层分离。 / Separate film, develop, and scanner presets.
- 适合 Windows + Anaconda + IDE 直接运行的脚本。 / Scripts designed for Windows, Anaconda, and IDE-based workflows.
- CLI 入口，方便批处理与复现。 / CLI entry point for batch processing and reproducible runs.

## 安装 / Installation

推荐 Python 3.10+；`environment.yml` 当前使用 Python 3.11+。

Python 3.10+ is recommended; the provided `environment.yml` currently uses Python 3.11+.

```bash
pip install -r requirements.txt
```

也可以使用 conda：

Or use conda:

```bash
conda env create -f environment.yml
conda activate film-foundry
```

主要依赖 / Main dependencies:

- NumPy
- OpenCV
- Pillow
- pytest, for tests / 用于测试

## GUI 使用 / GUI Usage

运行 / Run:

```bash
python run_film_foundry_launcher.py
```

也可以直接打开主 GUI：

You can also open the main GUI directly:

```bash
python run_darkroom_gui.py
```

GUI 适合快速预览整体色调和影调。界面按阶段分离：

The GUI is intended for quick visual iteration. It is separated by workflow stage:

- **Full**：输入图像 -> 冲洗底片 -> 扫描正像。
- **Develop**：只冲洗，输出电子负片。
- **Scan**：读取已有 `.npz` 或 scanner raw TIFF，重新扫描。

GUI 默认使用简易模式：常用流程只需要选择胶片材料、冲洗流程和扫描预设，再调少量暗房语言参数。勾选 **专家模式** 后，才会展开显影液类型、定影/清除、药水疲劳、残银、事故冲洗、滤色和高光偏色等更细的控制。

By default the GUI uses a simplified mode: common workflows only need a film material preset, a develop process preset, a scanner preset, and a few darkroom-style adjustments. **Expert mode** exposes finer controls such as developer/fixer behavior, exhaustion, retained silver, accident development, filtering, and highlight color bias.

普通 GUI 默认列表会隐藏 `diagnostic_`、`accident_`、`experimental_` 开头的内置 preset，避免调试或事故配方混入日常选择；这些 preset 仍可通过 CLI、文件路径或专门入口使用。

The default GUI preset lists hide built-in presets starting with `diagnostic_`, `accident_`, or `experimental_`, so diagnostic and accident recipes do not appear in the normal daily lists. They remain usable through the CLI, file paths, or dedicated tools.

## Windows EXE 测试包 / Windows EXE Build

如果不熟悉 Python，或只是想快速试用，可以下载 Windows EXE 测试包。

If you do not want to install Python manually, or just want to try the project quickly, use the Windows portable test package.

请在 Release 的附件中下载 Windows 打包：

Download the Windows portable package from the release assets:  
**FilmFoundry-portable-win64.zip**

解压后，直接运行其中的 .exe 文件即可。

Unzip the package and run the included `.exe` file.

便携版 EXE 适合只想快速试用 GUI 的用户，不需要手动安装 Python、创建虚拟环境或安装依赖。

The portable EXE is intended for users who want to quickly try the GUI without manually installing Python, creating a virtual environment, or installing dependencies.

由于 EXE 便携包中打包了运行环境和 Python 依赖，因此体积会明显大于源码包。

Because the EXE package includes the required runtime and Python dependencies, it is much larger than the source code package.

当前便携版主要面向 Windows 平台构建和测试。本版本暂未正式提供其他平台的打包版本。

This package is currently built and tested mainly for Windows. Other platforms are not officially packaged in this release.

请把整个 `FilmFoundry` 文件夹一起分发或运行，不要只拷贝单个 `.exe`。程序会在 `.exe` 所在目录旁边使用 `input_images/`、`outputs/` 和 `user_presets/`。

Distribute or run the whole `FilmFoundry` folder, not only the single `.exe`. The app uses `input_images/`, `outputs/`, and `user_presets/` next to the executable.

### Windows SmartScreen 说明 / Windows SmartScreen Notice

当前便携 EXE 是早期未签名的开源 alpha 测试包。Windows SmartScreen 可能提示未知发布者或阻止启动。这通常会出现在新打包、低下载量、未签名的便携程序上。

如果你不放心运行 EXE，请不要强行绕过提示。你可以查看源码，并选择从 Python 环境运行项目。

The current portable EXE is an early unsigned open-source alpha test package. Windows SmartScreen may warn about an unknown publisher or block launch. This is common for newly packaged, low-download, unsigned portable apps.

If you are not comfortable running the EXE, do not force your way past the warning. You can review the source code and run the project from a Python environment instead.

## IDE 脚本使用 / IDE Script Usage

如果你更习惯 IDE 直接运行的工作方式，可以直接打开 `run_darkroom.py`，修改顶部的“用户常用设置”，然后点击 IDE 的 Run 按钮。

If you prefer IDE direct workflow, open `run_darkroom.py`, edit the user settings near the top, and run the script from your IDE.

常用设置 / Common settings:

```python
PIPELINE_MODE = "full"       # "full", "develop", or "scan"
INPUT_PATH = PROJECT_ROOT / "input_images"
OUTPUT_PATH = PROJECT_ROOT / "outputs"
NEGATIVE_PATH = PROJECT_ROOT / "outputs" / "negatives"

FILM_PRESET_NAME = "clear_modern_negative"
DEVELOP_PRESET_NAME = "standard_color_negative"
SCANNER_PRESET_NAME = "neutral_scan"
```

## 命令行使用 / CLI Usage

查看帮助 / Show help:

```bash
python run_foundry_cli.py --help
```

示例 / Examples:

```bash
python run_foundry_cli.py full input_images outputs --film-preset clear_modern_negative --develop-preset standard_color_negative --scanner-preset neutral_scan

python run_foundry_cli.py develop input_images outputs/negatives --film-preset clear_modern_negative --develop-preset monobath_clean --layer-pack

python run_foundry_cli.py scan outputs/negatives outputs/rescans --scanner-preset rich_color_scan
```

Develop 模式默认保存 `scanner_raw.tiff`；如不需要，可加 `--no-scanner-raw`。

Develop mode saves `scanner_raw.tiff` by default. Add `--no-scanner-raw` if you do not need it.

如果以包形式安装，也可以使用：

If installed as a package, you can also use:

```bash
film-foundry --help
electronic-negative-factory --help
```

## 预设 / Presets

当前推荐使用拆分后的 preset：

The recommended workflow uses split presets:

```text
half_frame_darkroom/presets/film/
  胶片材料本体 / film material:
  H-D curve, film base, dye absorption, grain baseline, halation, MTF

half_frame_darkroom/presets/develop/
  冲洗流程 / develop process:
  developer/monobath, fixer, time, temperature, concentration, agitation,
  push/pull, exhaustion, retained silver, darkroom accidents, frame size

half_frame_darkroom/presets/scanner/
  扫描 / 输出解释 / scanner-render interpretation:
  base removal, inversion, print mapping, black/white points, color balance,
  saturation, final tone
```

推荐默认组合：

Recommended default combination:

```text
film/clear_modern_negative
develop/standard_color_negative
scanner/neutral_scan
```

调试基准组合：

Diagnostic baseline combination:

```text
film/clean_digital_like
develop/standard_color_negative
scanner/clean_digital_scan
```

根目录下的 `half_frame_darkroom/presets/*.json` 只作为旧式完整配置示例保留，不是推荐主流程。新版官方 preset 应优先放在 `film/`、`develop/` 和 `scanner/` 三个子目录中。

The root-level `half_frame_darkroom/presets/*.json` files are kept only as legacy full-config examples. New official presets should live under `film/`, `develop/`, or `scanner/`.

主 GUI 只负责调用 preset；材料、冲洗流程、扫描解释的制造和保存由独立编辑器完成。加载 preset 时，程序会优先读取 `user_presets/` 中的用户同名 preset，再回退到内置 preset。

The main GUI calls presets. Creating and saving film materials, develop processes, and scanner interpretations is handled by the dedicated editors. When loading a preset, user presets under `user_presets/` take priority over built-in presets with the same name.

- `run_film_material_editor.py`：编辑胶片材料本体，保存到 `user_presets/film/`。 / Edit film material presets and save them to `user_presets/film/`.
- `run_develop_process_editor.py`：编辑冲洗流程/药水流程，保存到 `user_presets/develop/`。 / Edit develop process presets and save them to `user_presets/develop/`.
- `run_scanner_render_editor.py`：编辑扫描/输出解释，保存到 `user_presets/scanner/`。 / Edit scanner/render presets and save them to `user_presets/scanner/`.

普通 GUI 默认列表会隐藏 `diagnostic_`、`accident_`、`experimental_` 开头的内置 preset。它们仍然保留在文件夹中，适合测试、专家模式、CLI 或后续专门入口。

Built-in presets starting with `diagnostic_`, `accident_`, or `experimental_` are hidden from the normal GUI lists. They remain available on disk for tests, expert workflows, CLI usage, or future dedicated entry points.

启动器也提供常用文件夹按钮，可直接打开 `input_images`、`outputs`、`outputs/negatives` 和 `user_presets`。

The launcher also includes folder buttons for `input_images`, `outputs`, `outputs/negatives`, and `user_presets`.

更多说明：

More documentation:

- `STRUCTURE.md`
- `docs/PRESET_GUIDE.md`
- `docs/TERMINOLOGY.md`

## 输出文件 / Output Files

Develop 模式可能生成：

Develop mode can generate:

```text
image.darkroom_negative.npz
image.darkroom_negative.npz.json
image.darkroom_negative.png
image.scanner_raw.tiff
image.scanner_raw.tiff.json
```

含义 / Meaning:

- `.npz`：电子负片密度母版。 / Electronic negative density master.
- `.npz.json`：sidecar，保存配置快照和生成信息。 / Sidecar with config snapshot and generation metadata.
- `.png`：橙色片基负片视觉预览。 / Orange-base negative visual preview.
- `.scanner_raw.tiff`：16-bit linear 电子负片透射图，适合外部软件处理。 / 16-bit linear electronic-negative transmission image for external tools.
- `.scanner_raw.tiff.json`：scanner raw TIFF 的 sidecar。 / Sidecar for the scanner raw TIFF.

`.npz` 是最重要的内部底片母版。scanner raw TIFF 更像便携、可被图像软件读取的电子负片图像。

The `.npz` file is the most important internal negative master. The scanner raw TIFF is a more portable electronic-negative image that regular image tools can read.

## 电子负片材料导出 / Electronic Negative Material Exports

项目可以导出面向创作和制版的材料：

The project can export creative and plate-making materials:

- transparent negative plate
- density alpha
- CMY plate set
- grain layer
- halation layer
- layer pack folder

这些输出适合导入 Photoshop、Krita、Affinity、Procreate 等软件，也可以用于海报、分色、丝网、半色调和后续接触印相类实验。

These outputs are suitable for Photoshop, Krita, Affinity, Procreate, poster work, separations, screen printing, halftone experiments, and later contact-print-style experiments.

## 测试 / Tests

运行测试：

Run tests:

```bash
python -m pytest half_frame_darkroom/tests
```

快速语法检查：

Quick syntax check:

```bash
python -m compileall half_frame_darkroom run_darkroom.py run_darkroom_gui.py run_film_foundry_launcher.py run_film_material_editor.py run_develop_process_editor.py run_scanner_render_editor.py run_foundry_cli.py
```

在本项目的 Windows/Anaconda 测试环境中，可以直接使用：

In the current Windows/Anaconda test environment, use:

```powershell
& 'D:\Anaconda3\envs\film\python.exe' -m pytest half_frame_darkroom/tests -q
& 'D:\Anaconda3\envs\film\python.exe' -m compileall half_frame_darkroom run_darkroom.py run_darkroom_gui.py run_film_foundry_launcher.py run_film_material_editor.py run_develop_process_editor.py run_scanner_render_editor.py run_foundry_cli.py
```

## 许可证 / License

Film Foundry 采用 **GNU General Public License version 3 (GPL-3.0-or-later)**。

你可以在 GPLv3 条款下使用、学习、研究、修改和分发本项目。如果你分发修改版或基于本项目的派生作品，需要遵守 GPLv3 的相应义务，包括在适用情况下提供对应源代码并保留许可证声明。

如果你希望将本项目用于闭源商业产品、专有再发行、商业 SDK/插件集成，或其他无法遵守 GPLv3 开源义务的产品，请单独联系项目作者。

Film Foundry is licensed under **GNU General Public License version 3  (GPL-3.0-or-later)**.

You may use, study, modify, and distribute this project under the GPLv3. If you distribute modified versions or derivative works, you must comply with GPLv3 obligations, including providing corresponding source code where required and preserving license notices.

For closed-source commercial use, proprietary redistribution, commercial SDK/plugin integration, or use in a product that cannot comply with GPLv3, please contact the project author.

## 代码来源说明 / Code Provenance Notice

本项目在开发过程中使用了 AI agent 辅助进行代码整理、重构、文档编写和调试。虽然项目作者已尽力审查代码来源、项目结构和实现方式，但无法保证所有代码在形式上完全不与其他公开或闭源项目存在相似之处。

如果你认为本项目中的某段代码、注释、文档、命名或结构可能侵犯了你的权利，或与某个受保护项目高度相似，请联系项目作者并提供具体文件、位置和理由。作者会优先核查，并在确认问题后进行修改、替换或删除。

This project was developed with assistance from AI agents for coding, refactoring, documentation, and debugging. If you believe any code, comments, documentation, naming, or structure infringes your rights or is too similar to protected work, please contact the project author with specific details so it can be reviewed and corrected.

## 声明 / Notice

Film Foundry 是一个物理启发式虚拟暗房与电子负片材料生成项目。

本项目与任何胶片厂商、扫描仪厂商、相机厂商或商业胶片模拟软件均无从属、赞助、认证或官方授权关系。

官方预设以画面行为或材料行为描述命名，不是任何商业胶卷产品的官方模拟。

Film Foundry is a physics-inspired virtual darkroom and electronic negative material factory.

It is not affiliated with, endorsed by, or sponsored by any film manufacturer, scanner manufacturer, camera manufacturer, or commercial film-emulation software vendor.

Official presets are named descriptively by visual behavior or material behavior. They are not official emulations of commercial film products.
