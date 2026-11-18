# Film Foundry Android

当前版本0.1.0。Android 8.0及以上，当前以ARM64手机进行实机测试。提供arm64-v8a、armeabi-v7a、x86和x86_64分架构APK，以及包含四种ABI的universal通用包；其他架构尚未实机验证。

**2026-11-16 源码说明**：当前源码包含轻量形成和中性自动扫描等后续开发，本次没有生成新 APK，显示版本仍为 0.1.0。胶片新轻量内核为 `phone_formation_fast_v3`；旧卷、潜影和任务继续使用冻结版本。七类实验材料已有基础降阶内核和设置页隐藏单张相机，未进入主导航、胶卷库或图库；新版桌面 FFC2 预设合集仍未移植。不要把本树与早期 0.1.0 安装包视为完全相同。

v3 补上普通 RGB 黑白材料的感光矩阵响应，新增正色／蓝敏近似预设；不是测量光谱或红外模拟。普通材料、六个组合与八套隐藏材料已按手机实际支持字段更新，包含材料光晕、乳剂柔化和时间响应。旧卷和自定义配方不自动改写。

轻量胶片连续药浴已接 GLES 3.1：主池在单次上传内按顺序反应，随后接回原有明暗颗粒、简化事故及光学形成。独立队列协调两个行生产线程，实际参数与 CPU 对照合格、且包括打包／上传／读回的测速有收益才选 GPU；失败完整回退 CPU。设置的硬件自动测试新增连续药浴，最近记录区区分显影与扫描。颗粒、空间场、统计、预览和七类实验材料形成仍为 CPU。已通过主机编译和小样本数值检查，手机驱动及整卷速度尚未实测。

精度分开处理：计算、胶片潜影／化学库存／权威光学母版保持 FP32；标准 NPZ 可用 IEEE FP16 保存部分实验材料的独立外观字段，`MaterialStoragePrecision.Policy.FULL` 保留现有值的 FP32 保存。相纸和蓝晒的当前外观字段因保守误差门限保留 FP32，FP8 未启用。全精度另存不能恢复旧文件已丢失的精度。手机胶片没有桌面的冗余外观层，因此这项不会自动把胶片存档减半。

胶片 v2 增加材料储存失速／生雾、单次曝光互易律、潜影等待和冲洗后残盐受光；只推进显式记录的时间，扫描不推进反应。单次拍摄可使用相机记录的快门时间，多重曝光不推断统一时长。明暗相关颗粒、简化药痕及原有药浴顺序保留，复杂空间运输、逐浴颗粒历史和表面残盐反应不搬入基础手机档。

开发接口 `PhoneMaterialKernel` 提供七类材料的 `expose → process → finish`，`PhoneMaterialObserve` 独立只读观察。当前形成模型是 `phone_material_reduced_v3`，只保留各族最容易辨认的形成：撕拉／即显的有限转移、受光残色和封闭褪白；银版的定向金属反射、镀金和锈色；相纸的影调、彩色染料、冷暖、纤维／RC 干湿和光泽；湿版的浇注流向和正片眩光；蓝晒的笔刷与结合；铂钯的双金属上限。边缘损失、针孔、划伤、漏光、污染、碱洗和局部水洗不再执行，导入字段仍保留在溯源里。工作图不再设 4MP 上限，只拒绝无法装进一个数组的尺寸。外观和几何场默认按 binary16 舍入，守恒的化学池仍是 Float32；与全精度观察的差由测试限制在 5e-4 以内。七族形成仍是 CPU，不出现在主导航。设置页可以打开一台隐藏的单张相机：正式包要在 FFC2 栏输入 `FFC2:OPEN-MATERIALS`，可调试安装包直接显示入口。拍一张、选择材料、处理得到一张图，不写入胶卷库或系统相册。撕拉结果要向上揭开负片。设置页可为快门、反光板、过片上弦、卷片马达和倒片马达五条音效分别试听、更换或恢复内置合成音。自动过片播马达，手动过片播上弦声；快门跟随已知快门速度，反光板可在相机设置关闭。封卷后可看退出取景器、开盖和取卷，或跳过这段只保留渐黑；自动过片此时播放倒片马达。应用进入后台会取消未播放的快门尾音；停止连续过片会释放循环音轨。隐藏相机离开前台时取消绑定重试，过期相机回调不会重新占用相机。

最终产物沿用 `.ffmaterial.npz` 产品封装，可单独保存、读取、复用与传给桌面。撕拉片保留关联双片，只有声明黑白可用负片并执行清理才输出透射负片；其他族各一片。新产物使用桌面已能观察的版本：相纸和湿版 v2，蓝晒和铂钯 v3，撕拉、即显、银版维持 v2。旧的 v1 产物仍按原观察读取。手机不能据此续算桌面完整工艺存档。银版保留角度反射；相纸／湿版／铁盐印相的负正关系由材料形成决定，不自动翻转输入。

The source-only mobile update adds versioned reduced film time responses and headless experimental kernels for seven material families. Existing rolls retain their frozen implementation. Experimental formation is `phone_material_reduced_v3`: each family keeps its recognizable process, while generic defects are preserved but not applied. There is no 4MP working-image cap. Appearance and geometry fields default to binary16; conserved chemical pools stay Float32. Observation against the full-precision path stays within 5e-4. The main navigation does not list these materials. A hidden one-shot camera appears on Settings after entering `FFC2:OPEN-MATERIALS` in the recipe field; debuggable builds show that entry directly. The picture is not added to a roll or the system album. A peel-apart result stays covered until the negative is dragged upward. Settings can preview, replace, or restore all five cues: shutter, mirror, advance, motor, and rewind. Auto advance uses the motor cue; manual advance uses the wind cue. The shutter follows a known shutter speed, and the mirror cue can be disabled in camera settings. Sealing can show the exit, back opening and roll removal, or skip that scene after a fade to black. Auto advance plays the rewind motor then. Backgrounding cancels pending shutter sounds; stopping continuous transport releases its looping audio track. Leaving the hidden camera cancels binding retries and invalidates stale provider callbacks. This is not a new APK release.

Film fast v3 adds monochrome RGB sensitivity and refreshed built-in/seasonal materials. Ordered lightweight film baths can use qualified GLES 3.1 with transfer-inclusive CPU fallback; the hardware self-test includes this operation. Critical film arrays and arithmetic remain FP32. Selected experimental appearance fields use standard FP16 NPZ storage, with a full-precision opt-out; paper/cyanotype remain FP32 under the stricter mobile error bound. Device compatibility and end-to-end speed remain unmeasured.

[中文使用说明](../docs/MOBILE_GUIDE.md) · [English guide](../docs/MOBILE_GUIDE.en.md)

手机端独立实现材料计算，与桌面共享潜影／介质格式和FFC2配方。界面围绕拍摄、胶卷库、图库和设置组织。

## 编译

使用JDK 17和Android SDK 37。将`JAVA_HOME`指向JDK 17，在本目录执行：

```text
.\gradlew.bat :app:assembleDebug
```

以上为Windows命令。本仓库目前只附带Windows启动脚本；其他系统在本目录中使用JDK 17直接启动同一个wrapper，无需另装Gradle：

```text
java -classpath gradle/wrapper/gradle-wrapper.jar org.gradle.wrapper.GradleWrapperMain :app:assembleDebug
```

SDK路径通过`ANDROID_HOME`或本机`local.properties`配置，后者不提交到源码。第一次构建需要联网下载Gradle和依赖；设备检查工具的`--build`使用离线模式，需要先完成一次上述普通构建。当前wrapper下载地址使用腾讯镜像，Maven配置保留Google／Maven Central及阿里云镜像；若Gradle镜像不可达，可将本机`distributionUrl`改为同版本的`https\://services.gradle.org/distributions/gradle-9.6.0-bin.zip`。

输出为`app/build/outputs/apk/debug/app-debug.apk`，使用本机调试密钥签名；正式分发使用下方release入口。显示版本与内部`versionCode`在`app/build.gradle.kts`固定声明，构建不会修改版本号；后续更新需递增内部版本码。

按架构生成四个可独立安装的APK及通用包：

```text
.\gradlew.bat :app:assembleDebug -PsplitApks=true
```

输出位于同一目录，分别为`app-<ABI>-debug.apk`和`app-universal-debug.apk`，不是需要组合安装的应用分片。对外文件命名为`FilmFoundry-Android-0.1.0-<ABI>.apk`，通用包尾缀为`universal`；应用内版本仍为`0.1.0`。

| 尾缀 | 适用架构 |
| --- | --- |
| `arm64-v8a` | 64位ARM Android设备 |
| `armeabi-v7a` | 32位ARM Android设备 |
| `x86_64` | 64位x86 Android设备或模拟器 |
| `x86` | 32位x86 Android设备或模拟器 |
| `universal` | 包含以上四种架构，不确定时可使用 |

实际选择以Android系统支持的ABI为准。以上仍是Android安装包，不能直接用于Windows或iOS。架构包使用相同应用ID、版本码和签名，设备支持时可相互覆盖安装。

## 隔离设备检查

Windows上设置`JAVA_HOME`为JDK 17、`ANDROID_HOME`为SDK路径，在仓库根目录运行`python android/tools/run_device_tests.py --serial DEVICE --build --capture-ui`。这项检查使用合成窗口，不拍摄、不写入用户图库；需要设备允许USB调试和安装。主应用以保数据方式更新，测试结果写入`.test_artifacts/`，请先完成正在运行的冲洗任务。

## 正式签名与许可

release签名从当前进程环境读取以下四项，源码不保存密码或私钥：

| 环境变量 | 内容 |
| --- | --- |
| `FILM_RELEASE_STORE_FILE` | 自己长期保管的JKS或PKCS12密钥文件路径 |
| `FILM_RELEASE_STORE_PASSWORD` | 密钥库密码，从本机安全存储注入 |
| `FILM_RELEASE_KEY_ALIAS` | 密钥别名 |
| `FILM_RELEASE_KEY_PASSWORD` | 该密钥的密码 |

全部配置后在android目录执行：

```text
.\gradlew.bat --no-daemon --no-configuration-cache :app:assembleRelease -PsplitApks=true
```

输出为`app/build/outputs/apk/release/app-<ABI>-release.apk`及通用包。缺少签名变量会失败，不会悄悄使用debug密钥；四项需同时提供，命令结束后应清除密码环境变量。不要把密钥、密码、包含秘密的命令或日志提交到仓库。

本次0.1.0/code34使用独立发布证书。旧测试包使用调试证书，不能直接覆盖安装；切换前先导出重要胶卷、底片和照片。不要为了安装新包直接卸载仍存有数据的旧包。密钥与密码需要安全离线备份，后续同一应用更新继续使用同一发布身份。[Android官方签名说明](https://developer.android.com/studio/publish/app-signing)

实际依赖清单与原始通知位于[Android许可目录](../third_party_licenses/android/README.md)。APK包含相同许可正文，可在“设置 → 关于软件 → 开源许可与第三方声明”离线阅读。release构建会核对解析依赖与记录坐标；变更依赖后，先运行以下命令更新（第一步若声明过期会失败，但仍写出坐标清单）：

```text
.\gradlew.bat :app:exportReleaseDependencyInventory
python tools/collect_runtime_notices.py
```

收集器读取本机Gradle缓存和已保留的上游通知，不复制依赖二进制、个人路径或密钥；新的许可类型或原生依赖仍需核对其原始通知。发布APK时应同时提供对应的项目源码版本和这些许可材料；只链接尚未同步的旧仓库不足以说明本次二进制来源。
