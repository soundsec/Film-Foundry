# v0.2 Release Checklist

这份清单用于把当前阶段整理成 GitHub 小版本发布。v0.2 建议定位为
alpha / experimental update：功能方向明确，但仍然是物理、化学、真实工作流启发
的创作工具，不是严谨物理化学仿真器。

## 发布前清理

- 确认不要提交 `input_images/` 中的用户图片，只保留 `.gitkeep`。
- 确认不要提交 `outputs/` 中的渲染结果、`.npz`、scanner raw、smoke test 输出，只保留 `.gitkeep`。
- 确认不要提交 `dist/`、`build/`、portable zip/7z 包等打包产物。
- 确认不要提交 `.pytest_cache/`、`__pycache__/`、本地 IDE 配置、`.agents/`、`.codex/`。
- 确认不要提交本地参考汇总文件，例如 `*_COLLECTED_REFERENCE.py`。
- `packaging/` 目录里的脚本、spec 和说明文件是源码的一部分，可以提交；真正忽略的是打包输出。

## 建议保留提交的内容

- `half_frame_darkroom/` 源码、内置 preset、测试。
- `film_foundry/` 兼容入口。
- `run_*.py` 顶层脚本。
- `docs/` 文档。
- `packaging/` 打包脚本和说明。
- `README.md`、`STRUCTURE.md`、`pyproject.toml`、`requirements.txt`、`environment.yml`、`LICENSE`。
- `input_images/.gitkeep` 和 `outputs/.gitkeep`。

## v0.2 重点变更摘要

- 新增 diagnostic film / flat scan，用于放大冲洗参数影响。
- 极端冲洗参数加入安全钳制，避免数值失控。
- 新增暗房事故控制：漏光、海带 / 药染浑浊、显影不均 / 药痕。
- CLI、主 GUI、冲洗流程编辑器、IDE 脚本均接入新的事故参数。
- 新增 `accident_kelp_light_leak` develop preset。
- 新增材料 / 工艺 / 输出解释的长期路线文档。
- 明确项目定位：严肃的材料启发玩具，而不是精确物理化学仿真器。

## 发布前测试

推荐使用项目目标环境：

```powershell
& 'D:\Anaconda3\envs\film\python.exe' -m pytest half_frame_darkroom/tests -q
& 'D:\Anaconda3\envs\film\python.exe' -m compileall half_frame_darkroom run_darkroom.py run_darkroom_gui.py run_film_foundry_launcher.py run_film_material_editor.py run_develop_process_editor.py run_scanner_render_editor.py
```

可选 CLI smoke test：

```powershell
& 'D:\Anaconda3\envs\film\python.exe' run_foundry_cli.py --help
& 'D:\Anaconda3\envs\film\python.exe' run_foundry_cli.py full input_images outputs --film-preset diagnostic_develop_sensitive --develop-preset accident_kelp_light_leak --scanner-preset diagnostic_flat_scan --fast --preview
```

## 打包路径行为

PyInstaller 打包后，主 GUI 和启动器会把 `FilmFoundry.exe` 所在目录作为用户可见项目根目录：

```text
FilmFoundry/
  FilmFoundry.exe
  input_images/
  outputs/
  user_presets/
```

因此发给测试者时应提供整个 `dist/FilmFoundry` 文件夹，而不是只发单个 exe。
内置 preset 会从 PyInstaller 的资源目录读取；用户输入、输出和自定义 preset 会写到 exe
旁边的 `input_images/`、`outputs/` 和 `user_presets/`。
