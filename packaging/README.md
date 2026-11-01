# 构建与打包 / Building Film Foundry

普通使用无需阅读本目录。这里集中保留从公开源码生成 Windows EXE 与 Android APK 所需的入口；不包含发布私钥、密码或开发机配置。

## Windows x64 · 桌面 0.4.0

在 Windows 的 PowerShell 中，从仓库根目录执行。需要 Conda；下面创建独立构建环境，不修改原有环境：

```powershell
conda env create -f packaging/environment-build.yml
conda activate film-foundry-build
python -m pip install -r packaging/requirements-build.txt
.\packaging\build_windows_exe.ps1 -Python (Get-Command python).Source
```

产物为 `portable_build/FilmFoundry/FilmFoundry.exe`。**分发时保留整个 FilmFoundry 文件夹**，其中 `_internal` 是运行库；`docs/` 是使用说明，`licenses/` 是此次实际依赖的通知与清单。

已有 `portable_build/FilmFoundry` 时脚本会停止，避免覆盖应用或用户图片。再次构建可指定新的输出根目录：

```powershell
.\packaging\build_windows_exe.ps1 -Python (Get-Command python).Source -OutputRoot .\portable_build\next-build
```

脚本自动检查 Qt 平台插件、收集原始许可、限制 DLL 搜索来源并运行冻结程序的 `--self-check`。临时构建文件放在输出根目录的 `.build/`；不会把它们打进应用，不自动压缩、不签名 EXE、不上传或安装。`-SkipFrozenSelfCheck` 仅供排障，正常发行请保留自检。

`environment-build.yml` 固定本次 Python 和匹配的 Conda 原生包构建；`requirements-build.txt` 固定主要 Python 依赖和 PyInstaller 版本。完整原生文件记录见[原生依赖清单](../third_party_licenses/windows-native/README.md)。这些文件用于重建功能相同的程序，不承诺跨系统补丁、缓存和依赖解析得到逐字节相同的 EXE。

构建环境的 Conda 包缓存需保留 `info/licenses`，收集器从这些原始文件取得许可。若清理过缓存导致收集失败，重新安装对应包恢复其缓存；不要直接跳过许可证收集。首次安装环境和依赖需要联网。

## Android · 手机 0.1.0

已有完整的 Gradle 构建入口，无需额外打包器。使用 JDK 17 和 Android SDK 37：

```powershell
cd android
.\gradlew.bat :app:assembleDebug -PsplitApks=true
```

输出为四个 ABI 独立 APK 和一个通用 APK。正式签名通过四个 `FILM_RELEASE_*` 环境变量传入，详见 [Android 构建与签名说明](../android/README.md)。每位构建者使用自己的密钥；缺少密钥时 release 构建明确失败，不会使用作者的密钥或自动改成调试签名。

```powershell
.\gradlew.bat --no-daemon --no-configuration-cache :app:assembleRelease -PsplitApks=true
```

运行时依赖及原始通知见 [Android 许可清单](../third_party_licenses/android/README.md)。Gradle 会检查清单是否与解析依赖匹配；更新流程见 Android 说明。

## 本目录包含什么

| 文件 | 用途 |
| --- | --- |
| `build_windows_exe.ps1` / `FilmFoundry.spec` | 构建桌面 EXE、打包资源和运行库 |
| `environment-build.yml` / `requirements-build.txt` | 独立环境与版本固定 |
| `collect_windows_licenses.ps1` / `collect_native_notices.py` | 收集实际随包依赖的原始通知与来源清单 |
| `prepare_source_release.py` | 复制公开文档，或准备白名单源码目录 |
| `PORTABLE_README.txt` | 随便携包附带的简短使用说明 |

准备一份新的公开源码副本（目标目录必须不存在）：

```powershell
python packaging/prepare_source_release.py portable_build/source-release --include-android
```

默认保留本目录的九个公开文件。图标生成工具、本机签名管理助手、签名目录、日志、用户照片与开发记录不在白名单中。构建脚本与项目一起按 [GPL-3.0-or-later](../LICENSE) 提供；上游依赖保留自己的许可。这些脚本提供项目构建方法，不代替第三方依赖的对应源码。

## English quick reference

On Windows x64, create and activate the Conda environment above, install the pinned pip requirements, then run `packaging/build_windows_exe.ps1` from the repository root. Ship the complete output folder, including `_internal`, `docs` and `licenses`. Existing app folders are refused; select a fresh `-OutputRoot` to rebuild. The script performs a frozen self-check by default and never publishes, installs or creates a ZIP.

Android uses its existing Gradle wrapper with JDK 17 and SDK 37. See [Android instructions](../android/README.md) for the four release-signing environment variables; no private signing identity is distributed. Dependency notices and build instructions are separate from the corresponding source of third-party components.
