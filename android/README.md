# Film Foundry Android

[中文使用指南](../docs/MOBILE_GUIDE.md) · [English guide](../docs/MOBILE_GUIDE.en.md) · [翻译与移植指南](TRANSLATING.md)

Film Foundry Android（当前显示版本 **0.1.0**）是面向 **Android 8.0+** 的独立移动端实现：与桌面端共享潜影/光学介质 NPZ 格式与 `FFC2:` 配方，同时采用专为移动设备设计的降阶数值核。界面围绕 **拍摄、胶卷库、图库、设置** 四页组织，现阶段以 **ARM64 手机**为实机测试范围（同时提供多架构与 `universal` 构建）。

---

## 编译与打包

构建环境要求：**JDK 17**（请勿使用 Android Studio 内置的 JBR 25）与 **Android SDK 37**。将 `JAVA_HOME` 指向 JDK 17，通过 `ANDROID_HOME` 或本机 `android/local.properties`（不提交源码）配置 SDK 路径。

### 构建调试 APK

在 `android/` 目录下执行：

```powershell
# Windows
.\gradlew.bat :app:assembleDebug
```

macOS / Linux 可直接用 JDK 17 调用仓库内置的 Gradle Wrapper，无需另装 Gradle：

```bash
java -classpath gradle/wrapper/gradle-wrapper.jar org.gradle.wrapper.GradleWrapperMain :app:assembleDebug
```

> 首次构建需联网拉取 Gradle 与依赖；后续使用设备检查脚本的 `--build` 离线模式前，请先完成一次上述常规构建。当前 Wrapper 使用国内镜像加速，若不可达可将本机 `distributionUrl` 换为官方同版本 `https://services.gradle.org/distributions/gradle-9.6.0-bin.zip`。

### 按架构拆分 APK

```powershell
.\gradlew.bat :app:assembleDebug -PsplitApks=true
```

产物位于 `app/build/outputs/apk/debug/`，每个包均可独立安装（并非需要组合安装的 App Bundle 分片）：

| 文件后缀 | 适用架构 |
| --- | --- |
| `arm64-v8a` | 64 位 ARM Android 设备（当前实机验证范围） |
| `armeabi-v7a` | 32 位 ARM Android 设备 |
| `x86_64` | 64 位 x86 模拟器或设备 |
| `x86` | 32 位 x86 模拟器或设备 |
| `universal` | 包含全部四种 ABI（不确定架构时选用） |

---

## 正式签名与依赖许可

Release 签名从当前进程环境变量读取以下四项（源码与仓库不保存私钥或密码，缺任一项将明确构建失败，不会回退调试签名）：

| 环境变量 | 说明 |
| --- | --- |
| `FILM_RELEASE_STORE_FILE` | 本地 JKS 或 PKCS12 密钥库路径 |
| `FILM_RELEASE_STORE_PASSWORD` | 密钥库密码 |
| `FILM_RELEASE_KEY_ALIAS` | 密钥别名 |
| `FILM_RELEASE_KEY_PASSWORD` | 密钥密码 |

配置完成后在 `android/` 目录运行：

```powershell
.\gradlew.bat --no-daemon --no-configuration-cache :app:assembleRelease -PsplitApks=true
```

> **签名提示**：0.1.0（`versionCode 34`）正式包使用独立发布证书，与旧调试签名测试包不能直接覆盖安装；切换前请先导出重要胶卷、底片与照片。

**第三方依赖与许可核对**：运行时依赖清单与原始许可正文见[Android 许可目录](../third_party_licenses/android/README.md)，并内置于 APK 的「设置 → 关于软件 → 开源许可与第三方声明」中。变更依赖后请依次执行：

```powershell
.\gradlew.bat :app:exportReleaseDependencyInventory
python tools/collect_runtime_notices.py
```

---

## 隔离设备检查

连接已开启 USB 调试的 Android 设备后，在仓库根目录配置 `JAVA_HOME`（JDK 17）与 `ANDROID_HOME`，可运行两项基于合成数据的隔离 UI 检查（不会拍摄、不写入用户图库，结果写入 `.test_artifacts/`）：

```powershell
# 扫描手势、精调刻度、抽屉布局、多指取消与对照图回收检查
python android/tools/run_device_tests.py --serial DEVICE --build --scan-ui

# 取景与拍摄界面合成检查（请先完成正在运行的冲洗任务）
python android/tools/run_device_tests.py --serial DEVICE --build --capture-ui
```

---

## 跨端材料字段与采集原片

- **共享状态词表**：维护桌面与手机共有的材料状态字段时，请先更新 [material_state_fields.json](../film_foundry/contracts/material_state_fields.json)。其中 `terms` 登记各字段物理含义，胶片潜影分必需/可选数组与标量，七类实验材料按当前可交换版本登记（`scalar` 为 H×W，`rgb` 为 H×W×3，`layers` 为 H×W×层数）。桌面 pytest 与 Android `PhoneExperimentalMaterialsTest` 共同校验此词表。
- **DNG 原片采集**：入卷的 DNG 原片与拍摄回执共同保存，形成潜影时按窗口解码并记录模型与位深。支持未压缩无符号 8 / 16 位 Bayer DNG（按有效区起点确定相位解马赛克）与三通道交错 LinearRaw DNG（逐平面定标并按标定组 1 转入线性工作 RGB）；不支持压缩 DNG 或非常规校正时会明确报错。旧版 `stored_sensor_dng_v1` 回执重冲时保持原算法。

---

## 当前开发源码能力与边界

以下为当前源码相较已发布 0.1.0 测试包的主要技术进展（尚未重新打包发布）：

- **胶片形成与版本冻结**：新装轻量卷（`phone_formation_fast_v6`）与详细卷（`phone_formation_detailed_v3`）按材料 H-D 曲线、乳剂最低密度与密度跨度形成潜影与底片，支持显式时间响应（储存失速/生雾、单次快门互易律、潜影等待、残盐受光）以及配方声明的邻接效应与 DIR 层间抑制。所有已装旧卷、旧潜影与旧任务严格按冻结版本回放。
- **GLES 3.1 连续药浴加速**：轻量胶片顺序药浴已接入可选 OpenGL ES 3.1 计算路径，经边界样本精度核对且计入传输仍快于 CPU 时启用，失败自动回退 CPU（硬件自测已包含连续药浴项）。颗粒、空间场、预览与实验材料形成仍由 CPU 执行。
- **分级存储精度**：胶片潜影、化学库存与权威光学母版保持 FP32；实验材料的部分独立外观字段默认采用标准 IEEE binary16 存储（`MaterialStoragePrecision.Policy.FULL` 可选全 FP32 保留），守恒化学池保持 FP32，不启用 FP8。
- **七类实验材料与隐藏单张相机**：`:kernel` 已内置七类实验材料（撕拉片、一体式即时片、银版、相纸、湿版、蓝晒、铂钯）的 `phone_material_reduced_v4` 形成与独立观察，产物采用与桌面兼容的 `.ffmaterial.npz` 封装，工作图不设 4MP 上限。七类材料暂不进入主导航，可通过设置页的隐藏单张相机试拍（正式包在配方 FFC2 栏输入 `FILMFOUNDRY` 解锁，调试包直接显示；结果不进入胶卷库或系统相册；撕拉片支持向上拖拽揭开负片）。
- **相机音效与退卷动画**：内置快门、反光板、过片上弦、卷片马达、倒片马达五条合成提示音，设置页支持试听、逐条替换为自定义音频或恢复内置（自动过片播马达、手动过片播上弦声，慢门按实际快门速度分离前后帘）；封卷提供可跳过的退卷退出动画。

> **English summary**: Build with **JDK 17** and **Android SDK 37** using `.\gradlew.bat :app:assembleDebug` (add `-PsplitApks=true` for per-ABI and universal APKs). Release builds read signing credentials strictly from the four `FILM_RELEASE_*` environment variables. The current development source updates film formation to fast v6 / detailed v3 with timing, adjacency/DIR, and qualified GLES 3.1 continuous-bath acceleration (with CPU fallback), adds uncompressed Bayer & interleaved 3-channel LinearRaw DNG decoding, maintains shared field contracts in `film_foundry/contracts/material_state_fields.json`, and includes `phone_material_reduced_v4` experimental materials behind a hidden Settings one-shot camera (unlocked with `FILMFOUNDRY` in release builds). This remains a source update rather than a new APK release.
