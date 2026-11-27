# 构建与打包 / Building Film Foundry

日常使用无需阅读本目录。此处集中保留从公开源码构建 **Windows 便携版 EXE** 与 **Android APK** 所需的脚本与配置，不含签名私钥、密码或开发机路径。

> **源码快照构建提示**：当前开发源码沿用桌面 0.4.0 与 Android 0.1.0 显示版本。旧版 `packaging/requirements-build.txt` 与原生依赖清单对应历史 0.4.0 安装包；若从当前最新源码打包桌面版，需额外安装根目录 `requirements.txt`（包含 DNG 解码 `rawpy` / LibRaw 与 Ultra HDR 解码 `imagecodecs` / libultrahdr），并在打包时由脚本重新收集实际依赖与许可。详见[开发中源码快照](../docs/DEVELOPMENT_SNAPSHOT.md)。

---

## Windows x64 · 桌面便携版

在 Windows PowerShell 中，从仓库根目录运行（需已安装 Conda；以下命令会创建独立构建环境，不影响现有环境）：

```powershell
conda env create -f packaging/environment-build.yml
conda activate film-foundry-build
python -m pip install -r packaging/requirements-build.txt
python -m pip install -r requirements.txt
.\packaging\build_windows_exe.ps1 -Python (Get-Command python).Source
```

- **产物位置**：`portable_build/FilmFoundry/FilmFoundry.exe`。分发时请**保留整个 `FilmFoundry` 文件夹**（包含 `_internal/` 运行库、`docs/` 文档与 `licenses/` 依赖许可清单）。
- **防覆盖保护**：若 `portable_build/FilmFoundry` 已存在，脚本会直接停止；重新构建请指定新目录：
  ```powershell
  .\packaging\build_windows_exe.ps1 -Python (Get-Command python).Source -OutputRoot .\portable_build\next-build
  ```
- **内置校验**：脚本自动检查 Qt 平台插件、收集第三方原始许可、限制 DLL 搜索范围，并运行冻结程序的 `--self-check`（`-SkipFrozenSelfCheck` 仅供排障）。脚本不自动打包 ZIP、不签名、不上传。
- **Conda 许可缓存**：请保持构建环境的 Conda 包缓存中含有 `info/licenses` 以便收集器读取原始许可；若因清理缓存导致失败，重新安装对应包即可恢复。

---

## Android · APK 构建

Android 使用仓库自带的 Gradle Wrapper，要求 **JDK 17** 与 **Android SDK 37**：

```powershell
cd android
.\gradlew.bat :app:assembleDebug -PsplitApks=true
```

产物为 4 个分架构 APK 与 1 个 `universal` 通用 APK。正式 Release 包从四个 `FILM_RELEASE_*` 环境变量读取本地签名凭据（缺失则明确报错，绝不回退调试签名），详见[Android 构建与签名说明](../android/README.md)：

```powershell
.\gradlew.bat --no-daemon --no-configuration-cache :app:assembleRelease -PsplitApks=true
```

运行时依赖及许可清单见[Android 许可目录](../third_party_licenses/android/README.md)，Gradle 会在构建时校验清单与解析依赖是否一致。

---

## 本目录文件清单

| 文件 | 用途 |
| --- | --- |
| `build_windows_exe.ps1` / `FilmFoundry.spec` | 构建 Windows 便携 EXE、打包资源与运行库 |
| `environment-build.yml` / `requirements-build.txt` | 固定 Conda 原生包与主要 Python 构建依赖版本 |
| `collect_windows_licenses.ps1` / `collect_native_notices.py` | 收集随包原生库与 Python 依赖的原始许可通知 |
| `prepare_source_release.py` | 复制公开文档或按白名单生成干净的公开源码目录 |
| `PORTABLE_README.txt` | 随便携包附带的简短入门说明 |

按白名单导出干净的公开源码副本（目标目录必须不存在）：

```powershell
python packaging/prepare_source_release.py portable_build/source-release --include-android
```

脚本仅包含公开源码、构建脚本与公开文档，自动排除开发记录、签名目录、日志与用户照片。构建脚本随项目按 [GPL-3.0-or-later](../LICENSE) 提供，上游依赖沿用各自许可。

---

## English quick reference

- **Windows x64**: Create and activate the Conda build environment above, install both `packaging/requirements-build.txt` and root `requirements.txt` (needed for current DNG / Ultra HDR codecs), then run `packaging/build_windows_exe.ps1` from the repository root. Ship the complete output folder (`_internal`, `docs`, and `licenses`). Existing target folders are refused—pass a fresh `-OutputRoot` to rebuild. The script runs `--self-check` by default and never signs, zips, installs, or uploads artifacts.
- **Android**: Build with the bundled Gradle wrapper using **JDK 17** and **Android SDK 37** (`-PsplitApks=true` for per-ABI and universal APKs). See [Android build instructions](../android/README.md) for the four `FILM_RELEASE_*` signing environment variables.
