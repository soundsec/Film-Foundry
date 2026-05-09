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

## Windows  EXE 测试包 /  Windows EXE Build

如果不熟悉python或是想快速试用，项目提供了简单的exe程序以供使用。

Download the Windows portable package from the release assets:

请在 Release 的附件中下载 Windows 便携包：

**FilmFoundry-portable-win64.zip**

Unzip the package and run the included .exe file.

解压后，直接运行其中的 .exe 文件即可。

The portable EXE is intended for users who want to quickly try the GUI without manually installing Python, creating a virtual environment, or installing dependencies.

Because the EXE package includes the required runtime and Python dependencies, it is much larger than the source code package.

This package is currently built and tested mainly for Windows. Other platforms are not officially packaged in this release.

便携版 EXE 适合只想快速试用的用户，不需要手动安装 Python、创建虚拟环境或安装依赖。

由于 EXE 便携包中打包了运行环境和 Python 依赖，因此它的体积会明显大于源码包。

当前便携版主要面向 Windows 平台构建和测试。本版本暂未正式提供其他平台的打包版本。


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

Film Foundry 采用 **GNU General Public License version 3 (GPL-3.0-or-later)**。

你可以在 GPLv3 条款下使用、学习、研究、修改和分发本项目。如果你分发修改版或基于本项目的派生作品，需要遵守 GPLv3 的相应义务，包括在适用情况下提供对应源代码并保留许可证声明。

如果你希望将本项目用于闭源商业产品、专有再发行、商业 SDK/插件集成，或其他无法遵守 GPLv3 开源义务的产品，请单独联系项目作者获取商业授权。

Film Foundry is licensed under **GNU General Public License version 3  (GPL-3.0-or-later)**.

You may use, study, modify, and distribute this project under the GPLv3. If you distribute modified versions or derivative works, you must comply with GPLv3 obligations, including providing corresponding source code where required and preserving license notices.

For closed-source commercial use, proprietary redistribution, commercial SDK/plugin integration, or use in a product that cannot comply with GPLv3, please contact the project author for a separate commercial license.

## Code Provenance Notice

本项目在开发过程中使用了 AI agent 辅助进行代码整理、重构、文档编写和调试。虽然项目作者已尽力审查代码来源、项目结构和实现方式，但无法保证所有代码在形式上完全不与其他公开或闭源项目存在相似之处。

如果你认为本项目中的某段代码、注释、文档、命名或结构可能侵犯了你的权利，或与某个受保护项目高度相似，请联系项目作者并提供具体文件、位置和理由。作者会优先核查，并在确认问题后进行修改、替换或删除。

This project was developed with assistance from AI agents for coding, refactoring, documentation, and debugging. If you believe any code, comments, documentation, naming, or structure infringes your rights or is too similar to protected work, please contact the project author with specific details so it can be reviewed and corrected.


## Notice

Film Foundry 是一个物理启发式虚拟暗房与电子负片材料生成项目。

本项目与任何胶片厂商、扫描仪厂商、相机厂商或商业胶片模拟软件均无从属、赞助、认证或官方授权关系。

官方预设以画面行为或材料行为描述命名，不是任何商业胶卷产品的官方模拟。

Film Foundry is a physics-inspired virtual darkroom and electronic negative material factory.

It is not affiliated with, endorsed by, or sponsored by any film manufacturer, scanner manufacturer, camera manufacturer, or commercial film-emulation software vendor.

Official presets are named descriptively by visual behavior or material behavior. They are not official emulations of commercial film products.
