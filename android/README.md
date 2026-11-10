# Film Foundry Android

当前版本0.1.0。Android 8.0及以上，当前以ARM64手机进行实机测试。提供arm64-v8a、armeabi-v7a、x86和x86_64分架构APK，以及包含四种ABI的universal通用包；其他架构尚未实机验证。

**2026-11-10 源码留档说明**：当前源码包含轻量形成和中性自动扫描等后续开发，本次没有生成新 APK，显示版本仍为 0.1.0。旧卷按冻结版本处理；七类实验材料和新版桌面 FFC2 预设合集未移植。具体范围见[源码快照](../docs/DEVELOPMENT_SNAPSHOT.md)，不要把本树与早期 0.1.0 安装包视为完全相同。

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
