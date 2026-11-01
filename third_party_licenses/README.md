# 第三方许可

项目自身代码使用 [GPL-3.0-or-later](../LICENSE)。依赖和采用的外部算法保留各自的授权条款；“免费”或“宽松许可”并不代表可以删除版权、许可证和声明。

## 目录

- [算法来源与设计参考](../THIRD_PARTY_NOTICES.md)：区分直接采用的算法与研究参考，并说明防色带实现替换。
- [Android实际运行时清单](android/README.md)：51个解析组件、原始POM、AAR通知、CameraX原生库及libyuv许可；APK内也保留可阅读声明。
- [Tanner Helland BSD-2-Clause](TANNER_HELLAND_BSD_2_CLAUSE.txt)：色温近似算法的原始通知。
- [lowbias32 Unlicense](HASH_PROSPECTOR_UNLICENSE.txt)：整数混合算法的上游授权。
- [Python](runtime/LICENSE_PYTHON.txt)：便携版内置解释器许可。
- [PyInstaller](runtime/PYINSTALLER_COPYING.txt)：含 bootloader 例外；使用此打包工具本身不要求应用改用 GPL。
- [随包依赖记录](runtime/dependencies/)：从本次 Windows x64 便携包复制的原始 METADATA 和许可文件，版本以各目录为准。
- [Windows原生库清单](windows-native/README.md)：实际打包的144个原生文件及9组匹配Conda包的原始通知，打包时隔离外部PATH。

## 本次桌面依赖

| 组件 | 版本 | 主要授权 |
| --- | --- | --- |
| NumPy | 2.4.4 | BSD-3-Clause；原始文件另含 OpenBLAS 等捆绑组件条款 |
| opencv-python | 4.13.0.92 | Apache-2.0；另附第三方许可汇总 |
| Pillow | 12.2.0 | 原始 LICENSE 所载的 PIL／HPND 等条款 |
| python-lcms2 | 0.1.1 | GPLv3；与底层 LittleCMS 的许可分别对待 |
| PySide6／Essentials／Shiboken6 | 6.11.1 | 提供 LGPL／GPL 等选择；本 GPL 发行采用 GPLv3 选项，正文见 [COPYING](../COPYING) |

Qt 包中附带的商业许可说明不代表本项目购买了商业授权。包内原有的 `_internal/*.dist-info` 许可也应保留。

spektrafilm、Fotufilm 等设计参考见[致敬与借鉴说明](https://github.com/soundsec/Film-Foundry/blob/main/docs/REFERENCE.md#acknowledgements)，不会仅因参考设计而成为随包依赖。

此目录记录本次依赖版本的许可材料，不限制用户只能安装这些版本；更换依赖或重新打包时，应重新收集实际捆绑版本的通知。当前原生文件已单独列出，实际发布二进制时仍须提供适用的对应源码材料；本目录不冒充依赖源码归档。

## English

Film Foundry's own code is GPL-3.0-or-later. External algorithms and dependencies retain their respective licenses. Permissive licensing can still require preservation of copyright and license notices.

`runtime/` records the notices and package metadata collected for this Windows desktop build. It is not a dependency lock file. The GPLv3 option is used for Qt / PySide6 / Shiboken in this GPL distribution; a bundled commercial-license text is not a commercial license grant. PyInstaller's bootloader exception does not impose its GPL on the application merely by packaging it.

Design references are distinguished from bundled code in `THIRD_PARTY_NOTICES.md`. Updating dependencies requires collecting the matching notices again; native-library inventory and applicable corresponding-source requirements are separate work.
