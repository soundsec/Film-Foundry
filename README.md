# Film Foundry / Electronic Negative Factory

Film Foundry 是一个物理启发式虚拟暗房与电子胶片介质生成工具。它先根据材料和冲洗流程形成负片或反转片，再通过独立的透射扫描流程得到最终数字图像。

本项目仍处于早期 alpha 阶段。它不是严格的胶片化学仿真器，也不以复刻某一款商业胶卷或扫描仪为目标；它更重视可解释的材料行为、暗房控制和创作自由。

[English README](README.en.md)

[最新版本发布说明](https://github.com/soundsec/Film-Foundry/blob/main/docs/RELEASE_NOTES_0.3.md)

## 主要功能

- 彩色负片、黑白负片、彩色反转片和黑白反转片流程。
- 漂白旁路、交叉冲洗、迫冲/减冲、残银、镀银、漏光、药水疲劳、显影不均和材料退化等控制。
- 密度颗粒、画幅颗粒差异、光晕、乳剂清晰度与扫描色彩控制。
- 透明介质、物理透射 TIFF、scanner/light-table raw、制版分层等材料导出。

## 安装

推荐 Python 3.10 或更高版本；项目的开发环境使用 Python 3.11。

使用 pip：

```bash
pip install -r requirements.txt
```

或者使用 conda：

```bash
conda env create -f environment.yml
conda activate film-foundry
```

## 启动 GUI

推荐从启动器进入：

```bash
python run_film_foundry_launcher.py
```

也可以直接打开主流程 GUI：

```bash
python -m film_foundry.tools.run_darkroom_gui
```

主 GUI 提供三种工作方式：

- **完整流程 / Full**：输入图片 → 冲洗负片或反转片 → 扫描输出。
- **只冲洗 / Develop**：生成可复用的负片或正片介质文件。
- **只扫描 / Scan**：读取已有介质，以负片或正片方式重新扫描。

材料选择会先区分负片与正片，再显示对应胶卷预设；彩色和黑白材料也会明确标识。普通列表隐藏诊断、事故和实验预设，专门预设仍可通过编辑器或 CLI 使用。

## 处理模式与大图

CLI 和运行配置提供三种处理模式：

- `quality`：使用正式输出尺寸，适合最终成片。
- `scaled_fast`：先缩小到预览尺寸，适合快速试色和调参。
- `reduced_fast`：保留正式输出尺寸，但降低部分内部细节计算，适合希望保留分辨率的快速处理。

主 GUI 中“快速模式”复选框单独对应 `reduced_fast`；“按预览长边输出 / 冲洗”对应 `scaled_fast`，并且优先级更高。两项都开启时实际执行 `scaled_fast`。当前两项默认开启，预览长边默认为 1600 px，因此默认点击“开始处理”会先缩放，而不是在原始尺寸上执行降阶快速。取消“按预览长边输出 / 冲洗”后，“快速模式”才表示保持正式尺寸的内部降阶；再取消“快速模式”则进入 `quality`。

30MP 为默认测试，更高分辨率没有做出针对优化与支持。更大的图片仍可处理，但实际能否顺利完成取决于分辨率、导出内容和电脑内存。程序不会在未选择快速模式时自动缩小图片。

## 命令行使用

CLI 更适合批处理、自动化和重放 GUI 保存的会话。

查看帮助：

```bash
python -m half_frame_darkroom.app.cli --help
```

完整流程：

```bash
python -m half_frame_darkroom.app.cli full input_images outputs --film-preset clear_modern_negative --develop-preset standard_color_negative --scanner-preset neutral_scan
```

只冲洗介质：

```bash
python -m half_frame_darkroom.app.cli develop input_images outputs/negatives --film-preset clear_modern_negative --develop-preset standard_color_negative
```

重新扫描已有介质：

```bash
python -m half_frame_darkroom.app.cli scan outputs/negatives outputs/rescans --scanner-preset rich_color_scan
```

使用缩放快速模式：

```bash
python -m half_frame_darkroom.app.cli full input_images outputs --processing-mode scaled_fast --preview-long-edge 1600
```

读取 GUI 会话：

```bash
python -m half_frame_darkroom.app.cli full input_images outputs --session user_presets/film_foundry_session.json
```

## 预设与编辑器

内置预设分为三类：

```text
half_frame_darkroom/presets/film/      胶片材料
half_frame_darkroom/presets/develop/   冲洗工艺与药水
half_frame_darkroom/presets/scanner/   扫描与观看解释
```

用户预设保存到 `user_presets/`。同名情况下，用户预设优先于内置预设。

独立编辑器：

- `python -m film_foundry.tools.run_film_material_editor`：银盐胶片材料编辑器。
- `python -m film_foundry.tools.run_develop_process_editor`：冲洗流程与药水编辑器。
- `python -m film_foundry.tools.run_scanner_render_editor`：负片/正片透射扫描编辑器。

更多说明见 [预设指南](https://github.com/soundsec/Film-Foundry/blob/main/docs/PRESET_GUIDE.md)。

## 输出文件

完整流程通常会生成最终 PNG、JPEG 或 TIFF，以及对应的 sidecar。

只冲洗模式会根据最终介质类型写入：

```text
outputs/negatives/*.darkroom_negative.npz
outputs/positives/*.darkroom_positive.npz
```

根据导出选项，还可以生成：

- 负片视觉预览或正片观看预览。
- 16-bit scanner raw 或 light-table raw TIFF。
- 透明 PNG、透明 16-bit TIFF 和物理透射 TIFF。
- CMY/密度、颗粒和光晕制版辅助层。
- 包含介质、预览、raw、透明介质、制版层和说明文件的 Layer Pack。

Layer Pack 是完整归档合集，已经包含透明介质和制版层；它不是与两者并列的第三套重复输出。

## Windows 便携版本

如果 Release 页面提供 `FilmFoundry-portable-win64.zip`，解压后请保留整个 FilmFoundry 文件夹，并从文件夹内运行程序，不要只复制单个 EXE。

当前便携版本是未签名的 alpha 测试包，Windows SmartScreen 可能显示“未知发布者”。如果你不希望绕过该提示，可以查看源码并从 Python 环境运行。

## 当前限制

- 支持 JPG、JPEG、PNG、TIFF、BMP 和 WebP；暂不支持直接读取 RAW 格式。
- 输入图通常已经经过相机 ISP 和 tone mapping，因此项目中的 曝光 和 线性化 是面向视觉处理的近似。
- 高分辨率、多种附属导出和 Layer Pack 会显著增加内存、处理时间与磁盘占用。
- 预设按材料行为和画面行为命名，不代表任何厂商产品的官方模拟。
- 当前 Tk GUI 在部分 Windows 系统上拖动复杂主窗口时，手感可能不如原生界面即时；这不影响处理流程和输出结果。

## 许可证

Film Foundry 采用 **GNU General Public License version 3（GPL-3.0-or-later）**。

你可以按照 GPLv3 使用、研究、修改和分发本项目。分发修改版或派生作品时，需要遵守 GPLv3 的相应义务。

如需用于无法遵守 GPLv3 的闭源商业产品、专有再发行或商业 SDK/插件，请联系项目作者。

## 代码来源说明

本项目在开发过程中使用了 AI agent 辅助进行代码整理、重构、文档编写和调试。如果你认为某段代码、注释、文档、命名或结构可能侵犯你的权利，请向作者提供具体文件、位置和理由，以便核查和处理。

## 声明

Film Foundry 与任何胶片厂商、扫描仪厂商、相机厂商或商业胶片模拟软件均无从属、赞助、认证或官方授权关系。

