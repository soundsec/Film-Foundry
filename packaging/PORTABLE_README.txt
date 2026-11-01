Film Foundry 0.4.0 Alpha 说明
作者：soundsec

v0.4.0 在 v0.3.1 的基础上更新了桌面界面、材料与冲洗配置，并优化预览和处理性能。

v0.4.0 更新：
- 全新 Qt 桌面工作台，支持中文／English、深色／浅色（米白）外观，常用参数前置，高级参数分组展开。
- 保留独立的材料、药浴和扫描编辑器，优化预设分类、搜索与选择。
- 彩色／黑白负片、反转片及交叉冲洗共享材料处理架构，支持去色罩、留银和冲洗事故等组合。
- 完善颗粒、乳剂 MTF、光晕、漏光、片门脏物和材料退化的配置与预览。
- 加入 Smits RGB→光谱升维，以及光谱材料与观察配置编辑。
- 优化影调／RGB 曲线操作，支持自动刷新、缩放、拖动和 100% 查看。
- 扫描调参复用已形成的介质，通过预计算、分块计算和缓冲复用减少重复处理；支持批次取消与未完成任务恢复。
- 延续历史介质、预设、会话和 FFC2 分享码兼容，并修复便携版 Qt 运行库加载问题。

EXE 便携版使用说明：
1. 请保持完整的 FilmFoundry 文件夹内容，尤其是 _internal 运行库；不能只复制 EXE。
2. 运行 FilmFoundry.exe 打开启动器，无需另装 Python。
3. 默认输入目录为 input_images，处理结果保存在 outputs；可在软件中修改路径。
4. 自定义预设与个人配置位于 user_presets。升级或迁移前，请备份该目录及需要保留的处理结果。

特别备注：
1. 大尺寸图像及高质量模式仍需要较多内存和处理时间；可先用小尺寸预览调整参数，再输出原尺寸结果。
2. 桌面版暂不直接读取 RAW／DNG，请先转换为 TIFF、PNG 或 JPEG。
3. 本包为 Windows x64 未签名 Alpha 测试版，Windows 可能显示“未知发布者”提示。也可从官方仓库获取源码，在 Python 环境运行。

开源协议：GPL-3.0-or-later
你可以按照 GPLv3 或后续版本使用、研究、修改和分发本项目。分发时请遵守相应协议，并保留项目与随附依赖的许可证及第三方声明。

Film Foundry 与任何胶片厂商、扫描仪厂商、相机厂商或商业胶片模拟软件均无从属、赞助、认证或官方授权关系。
详细使用说明、完整更新记录与源码请见官方仓库：
https://github.com/soundsec/Film-Foundry

----------------------------------------------------------------------------------------------------

Film Foundry 0.4.0 Alpha Notes
Author: soundsec

v0.4.0 updates the desktop interface, material and development configuration, preview responsiveness, and processing performance from v0.3.1.

v0.4.0 Update:
- New Qt desktop workbench with Chinese / English, dark / light (warm off-white) themes, common controls up front, and grouped advanced settings.
- Separate material, development, and scanner editors, with improved preset categories, search, and selection.
- Color / black-and-white negatives, reversal film, and cross-processing share a material-processing framework, including mask bleaching, retained silver, and processing accidents.
- Expanded configuration and previews for grain, emulsion MTF, halation, light leaks, gate debris, and material aging.
- Smits RGB-to-spectrum reconstruction and editing of spectral material and viewing profiles.
- Improved tone / RGB curve editing, automatic preview refresh, zoom, pan, and 100% viewing.
- Scanner adjustments reuse the developed medium. Precomputation, chunked computation, and buffer reuse reduce repeated work; batch cancellation and unfinished-task recovery are supported.
- Compatibility with existing media, presets, sessions, and FFC2 sharing codes, plus a fix for Qt runtime loading in the portable build.

Portable EXE Version Instructions:
1. Keep the complete FilmFoundry folder, especially the _internal runtime directory. Do not copy the EXE alone.
2. Run FilmFoundry.exe to open the launcher. No separate Python installation is required.
3. The default input folder is input_images, and results are saved in outputs. These paths can be changed in the application.
4. Custom presets and personal settings are stored in user_presets. Back up this folder and any results you wish to keep before upgrading or moving the application.

Important Notes:
1. Large images and high-quality processing can still require substantial memory and time. Adjust settings with a smaller preview before rendering at the original size.
2. The desktop version does not directly read RAW / DNG files. Convert them to TIFF, PNG, or JPEG first.
3. This is an unsigned Windows x64 Alpha test build. Windows may display an “Unknown publisher” warning. The source is also available from the official repository for running in a Python environment.

License: GPL-3.0-or-later
You may use, study, modify, and distribute this project under the GNU GPL version 3 or any later version. Follow the applicable license terms and retain the project and bundled dependency licenses and third-party notices when distributing it.

Film Foundry is not affiliated with, sponsored by, certified by, or officially authorized by any film manufacturer, scanner manufacturer, camera manufacturer, or commercial film-simulation software provider.
For detailed instructions, the full changelog, and source code, visit the official repository:
https://github.com/soundsec/Film-Foundry
