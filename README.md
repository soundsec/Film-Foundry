# Film Foundry

Film Foundry 是一个物理启发式虚拟暗房与成像介质生成工具。它先根据材料和冲洗流程形成负片或反转片，再通过独立的透射扫描流程得到最终数字图像。

旧材料、sidecar 和命令入口仍可能出现 “Electronic Negative Factory” 或 `half_frame_darkroom` 等历史名称；它们是兼容别名，不是另一套产品。

本项目仍处于早期 alpha 阶段。它不是严格的胶片化学仿真器，也不以复刻某一款商业胶卷或扫描仪为目标；它更重视可解释的材料行为、暗房控制和创作自由。

[English README](README.en.md)

[最新版本发布说明](https://github.com/soundsec/Film-Foundry/blob/main/docs/RELEASE_NOTES_0.3.md)

## 主要功能

- 彩色负片、黑白负片、彩色反转片和黑白反转片流程。
- 漂白旁路、交叉冲洗、迫冲/减冲、残银、镀银、潜影前漏光模板、药水疲劳、显影不均、显式染料损伤、乳剂网裂和材料退化等控制。
- 密度颗粒、画幅颗粒差异、光晕、乳剂清晰度与扫描色彩控制。
- 负片与正片共用透射灯台和扫描采集；用户独立控制去片基、反相与片基参考边框，也可直接输出灯台上的负片翻拍。
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

桌面界面使用 PySide6；通过 `requirements.txt` 或 `environment.yml` 安装时会一并安装所需 Qt 运行时。

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

CLI 和运行配置提供三个可独立组合的处理控制：

- 边长缩放：按目标长边缩放后处理，适合快速试色、调参或直接输出较小尺寸。
- 降阶快速：选择明确声明的低阶算子；当前版本仍在逐步扩充其覆盖。
- 处理质量：`draft` / `standard` / `high` 控制当前所选模型内部的细节与采样预算。

主 GUI 的边长缩放、降阶快速和处理质量是三个可以任意叠加的控制。两项快速开关都开启时，系统会先缩放到目标长边，再在该尺寸上运行所选质量档位的低阶算子；关闭降阶但保留缩放，则是在较小尺寸上运行完整算子；关闭缩放但保留降阶，则是在正式尺寸上运行低阶算子。当前两项默认开启，缩放目标长边默认为 1600 px。

这里的“降阶快速”特指由用户开关选择的近似模型，可能以更简单的数学或统计表达换取速度。程序内部还会持续进行另一类通用算法优化，例如消除重复计算、复用已经没有后续消费者的临时缓冲和缩短数组生命周期；这类优化不由快速开关控制，不改变材料、工艺或扫描语义，并同时服务完整与降阶流程。

30MP 为默认测试，更高分辨率没有做出针对优化与支持。更大的图片仍可处理，但实际能否顺利完成取决于分辨率、导出内容和电脑内存。程序不会在未选择快速模式时自动缩小图片。

## 命令行使用

CLI 更适合批处理、自动化和重放 GUI 保存的会话。

查看帮助：

```bash
python -m film_foundry.app.cli --help
```

完整流程：

```bash
python -m film_foundry.app.cli full input_images outputs --film-preset clear_modern_negative --develop-preset standard_color_negative --scanner-preset neutral_scan
```

只冲洗介质：

```bash
python -m film_foundry.app.cli develop input_images outputs/negatives --film-preset clear_modern_negative --develop-preset standard_color_negative
```

重新扫描已有介质：

```bash
python -m film_foundry.app.cli scan outputs/negatives outputs/rescans --scanner-preset rich_color_scan
```

使用缩放快速模式：

```bash
python -m film_foundry.app.cli full input_images outputs --processing-mode scaled_fast --preview-long-edge 1600
```

缩放、降阶与 draft 质量叠加：

```bash
python -m film_foundry.app.cli full input_images outputs --processing-mode scaled_fast --fast --quality-mode draft --preview-long-edge 1600
```

读取 GUI 会话：

```bash
python -m film_foundry.app.cli full input_images outputs --session user_presets/film_foundry_session.json
```

## 预设与编辑器

内置预设分为三类：

```text
film_foundry/presets/film/      胶片材料
film_foundry/presets/develop/   冲洗工艺与药水
film_foundry/presets/scanner/   扫描与观看解释
```

用户预设保存到 `user_presets/`。同名情况下，用户预设优先于内置预设。

独立编辑器：

- `python -m film_foundry.tools.run_film_material_editor`：银盐胶片材料编辑器。
- `python -m film_foundry.tools.run_develop_process_editor`：冲洗流程与药水编辑器。
- `python -m film_foundry.tools.run_scanner_render_editor`：负片/正片透射扫描编辑器。

更多说明见 [预设指南](https://github.com/soundsec/Film-Foundry/blob/main/docs/PRESET_GUIDE.md)。

## 输出文件

主 GUI 的完整流程和只冲洗流程不会把新结果直接写回固定文件名。每次点击“开始处理”都会在所选输出根目录下创建独立的时间运行目录，并保存可重新加载的 `film_foundry_run.json` 完整设置快照。

完整流程通常会在运行目录中生成最终 PNG、JPEG 或 TIFF，以及对应的 sidecar。只冲洗模式将介质母版放在运行目录的 `negative/` 子目录中；文件后缀仍根据最终介质极性区分：

```text
outputs/2026-07-15_15-42-08_develop/
  film_foundry_run.json
  negative/
    *.darkroom_negative.npz
    *.darkroom_positive.npz
```

只扫描模式默认把结果放到底片目录的上一级。例如读取 `.../A/negative/` 时，扫描成片和本次扫描设置会保存在 `.../A/`；输出文件名包含本次扫描时间，因此重复扫描不会覆盖旧结果。GUI 中可以关闭“只扫描：默认输出到底片目录的上一级”，改用手动指定的输出目录。命令行接口仍严格使用命令中给出的目标路径，便于脚本和自动化控制。

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
- 当前桌面界面仍以功能迁移和流程一致性为先，视觉设计与高密度参数布局会在后续版本继续整理。

## 许可证

Film Foundry 采用 **GNU General Public License version 3（GPL-3.0-or-later）**。

你可以按照 GPLv3 使用、研究、修改和分发本项目。分发修改版或派生作品时，需要遵守 GPLv3 的相应义务。

如需用于无法遵守 GPLv3 的闭源商业产品、专有再发行或商业 SDK/插件，请联系项目作者。

## 代码来源说明

本项目在开发过程中使用了 AI agent 辅助进行代码整理、重构、文档编写和调试。如果你认为某段代码、注释、文档、命名或结构可能侵犯你的权利，请向作者提供具体文件、位置和理由，以便核查和处理。

## 声明

Film Foundry 与任何胶片厂商、扫描仪厂商、相机厂商或商业胶片模拟软件均无从属、赞助、认证或官方授权关系。

作者：**soundsec**

仓库：[soundsec/Film-Foundry](https://github.com/soundsec/Film-Foundry)
