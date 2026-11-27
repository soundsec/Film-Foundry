# 材料、工艺与参数参考

[English](REFERENCE.en.md) · [桌面使用指南](DESKTOP_GUIDE.md) · [效果展示](SHOWCASE.md) · [项目首页](../README.md)

本文说明 Film Foundry 的材料形成模型、扫描只读边界、光谱支持、参数字典与性能策略。日常界面操作与命令行示例请见[桌面使用指南](DESKTOP_GUIDE.md)。

> **模型定位与边界**：Film Foundry 是面向影像创作的可解释降阶模型，不做定量物理仿真，也不承诺复刻特定商业胶卷、药水或扫描仪。文中的工艺资料仅用于核对物理与化学变化方向，具体系数均为可调工程参数。

## 目录

- [统一材料与冲洗模型](#model)：潜影、卤化银池、算子顺序、极性与交叉冲洗
- [光谱曝光、材料与观察](#spectral)：曝光谱、最终组分谱与只读观察
- [术语与参数字典](#parameters)：阶段分工、材料、冲洗、扫描、微调与调参判断
- [工艺程序与扫描解释字段](#process-fields)：程序键、兼容关系、片基校正与反相
- [诊断与事故配方](#diagnostic)：诊断基准、药染、显影不均与留银
- [分色 HSL](#color-mixer)：输出端八色区色相、饱和度与明度调整
- [性能与计算策略](#performance)：预计算、阶段复用、分块、内存与硬件加速
- [致敬与借鉴说明](#acknowledgements)
- [源码范围与发布状态](#source)

---

<a id="model"></a>

## 统一材料与冲洗模型

Film Foundry 不把 C-41、ECN-2、E-6、黑白负片或黑白反转拆成互不相干的图像滤镜，而是让银盐胶片共享同一条因果链路：

```text
Material -> Exposure -> Latent State -> Ordered Process Operators
         -> Final Medium -> Read-only Optical Observation -> Digital Image
```

这里统一的是**材料状态语言与形成入口**，而非把所有材料混为一谈。负片材料、反转材料及各自预设仍独立描述片基、感色曲线、染料吸收、附加层和工艺兼容性。

运行时 `auto` 会根据材料原生身份和模式自动路由到对应的正式算子程序；旧版 H-D 密度实现仅在显式指定 `legacy_density` 时运行，仅供回归对照，不属于主流程。

### 1. 连续潜影与唯一卤化银池

在概念上，第 `i` 层卤化银可视为可显影部分 $H_i^E$ 与未激活部分 $H_i^U$ 之和：

```text
H_i^E = H_i * e_i
H_i^U = H_i * (1 - e_i),  e_i in [0, 1]
```

工程实现不分别维护两个可变数组，而只保存两个状态量：

```text
halide         = H_i   # 剩余卤化银总量
developability = e_i   # 当前可显影比例
```

两项份额在计算时随时派生，从而在多步冲洗中天然满足材料守恒：

```text
developable_halide + inactive_halide == halide
```

### 2. 材料层与介质级组分

每层感光乳剂维护一组简化的材料池，介质级定义则额外保存片基与附加层：

```text
L_i = {halide, developability, metallic_silver, bleached_halide?, coupler?, dye?}
M   = {L_1 ... L_n, base, auxiliary layers}
```

- 黑白材料不含成色剂（`coupler`）与染料（`dye`）；彩色成色材料为各层提供独立容量与染料吸收参数。
- 片基、橙色色罩、rem-jet、防光晕层和保护层属于介质级组分，不会伪装成每层感光乳剂的独立状态。
- **区分“黑白材料”与“黑白程序”**：彩色负片执行 `bw_negative` 或 `bw_reversal` 时，依然保留三层感色响应、染料吸收矩阵与有色片基；黑白程序只是不生成染料，将三层形成的金属银解释为宽谱中性吸收。
- **潜影材料绑定**：新生成的潜影档案会冻结曝光时的材料参数。后续冲洗可自由更换药浴、条件和事故设置，但不能用另一种材料替换已曝光的片基或染料。桌面与手机均保留原始潜影档案，在工作副本中冲洗并另存独立结果；扫描仅读取最终冲洗介质。

### 3. 选择性处理算子

冲洗过程由有序算子组成，每一步由动作类型、强度、层选择性及材料—工艺兼容关系共同决定：

- **潜影选择性银显影**：将可显影卤化银还原为金属银；
- **彩色偶合显影**：在银显影的同时消耗成色剂生成染料，受银显影量、成色剂余量与层兼容性共同限制；
- **剩余卤化银激活**：提升尚未激活的卤化银可显影性（用于反转）；
- **金属银漂白**：将金属银转化为可被定影溶解的漂白银盐；
- **定影移除**：清除尚未显影的卤化银与漂白银盐；
- **首次银像直接移除**：用于黑白反转流程；
- **染料破坏与附加层移除**：用于彩色程序尾段及非标准冲洗。

材料预设中的交叉冲洗兼容字段（分别控制银显影、染料偶合、激活、漂白、定影、去银、染料稳定性、附加层去除与层平衡）仅在“程序与材料不匹配”时生效；原生匹配程序保持中性兼容，避免逆冲调节干扰常规冲洗。

### 4. 程序顺序决定最终极性

- **黑白负片**：保留首次显影生成的银像，经定影得到负像。
- **彩色负片**：偶合显影后经漂白、定影留下染料像；降低银漂白完成度（漂白旁路）则会同时保留部分金属银。
- **黑白反转**：
  ```text
  首次显影 -> 移除首次银像 -> 激活剩余卤化银 -> 二次显影 -> 定影
  ```
- **彩色反转**：
  ```text
  首次银显影 -> 激活剩余卤化银 -> 彩色显影 -> 统一漂白两次生成的银 -> 定影
  ```

彩色反转无需在激活前先移除第一次银像：首次显影已消耗对应卤化银，两次显影产生的银在末段统一漂白、定影即可。黑白与彩色反转的正像都源于有限卤化银池的竞争，而非对已完成负片做数学反相。

### 5. 交叉冲洗与有色色罩

交叉冲洗即使用非原生程序处理某一材料，画面变化来自材料池状态与兼容系数的实际演化，而非末端套色滤镜：

- `彩负材料 + color_reversal` → 带有原生有色片基的正像透明介质；
- `反转材料 + color_negative` → 透明片基上的负像介质；
- `黑白材料 + color_negative / color_reversal` → 沿用同一拓扑但不成色（成色剂容量为零）；标准银漂白会清除银像，漂白旁路则保留银像。

**有色片基与色罩漂白**：
- 彩色负片的橙色色罩由“成像染料非理想吸收”与“片基整体掩蔽密度”共同描述。由于输入与输出均为三通道 RGB，片基和染料的光谱关系采用三宽波段近似。扫描器只会扣除介质中冻结的均匀片基密度，有色片基对染料等效响应的影响仍然保留。
- 彩负直接反转成正像时，标准流程会保留有色色罩（通常呈现强烈的橙褐综合色调），观察阶段不会暗中将其消除。若希望得到透明底正像，需要显式开启实验性的**色罩/染料漂白（`BLEACH_MASK_DYE`）**——它与银漂白完全分离，在降低色罩的同时按材料参数伴随一定成像染料损伤。

材料编辑入口可共享银盐胶片外壳，但必须显式声明 `color_negative`、`bw_negative`、`color_reversal` 或 `bw_reversal` 类别；冲洗程序预设仅决定本次处理，不得反向改写材料类别。

### 6. 最终介质与只读观察

最终介质保存金属银、染料、未显影卤化银、漂白银盐、片基和附加层等权威形成状态，并连同冲洗时冻结的观察参数派生出只读的 RGB 总光学密度（`optical_density_rgb`）。扫描阶段只读取这份光学结果：

```text
最终介质光学密度 + 光源 / 传感器 / 观察解释参数 = 数字成片
```

- **单向只读边界**：扫描预设不能覆盖材料光学属性，也不能回写 `density_cmy`、`density_grain`、材料池或工艺记录。
- **统一透射扫描器**：负片与正片共享同一套“透射光源 + 传感器”采集阶段。**去片基（`remove_base_mask`）**和**反相（`invert_transmission`）**是采集后的独立用户开关：介质契约只提供建议（负片通常双开，正片通常双关），用户也可以关闭两项以模拟灯台翻拍负片，或自由组合实验观察。
- **片基参考边框**：可选边框在 `optical_density_rgb` 派生完成后扩展观察画布，填入透明片基光学密度并经过同一光源与传感器，用于去色罩锚定或展示透光边缘，绝不写回光学母版。
- **漂白与定影状态独立**：漂白把金属银转为漂白银盐，定影负责清除未显影卤化银与漂白银盐。两者分开保存，互不替代：漂白不足留下中性吸收的金属银（反差升高、饱和度下降）；定影不足留下残留银盐，按材料自身光学倾向产生浑浊或偏色（通用彩负默认偏蓝密度升高、低密度发黄；黑白材料默认中性浑浊）。
- **正片密度与暗部适应**：正片先在有界范围内调整反差，再做趾部、中间调与肩部塑形，避免亮暗端一次性压死；灯台观察的暗部适应保持明暗次序与色彩比例，不会把纯黑抬成死灰。

### 7. 冲洗动力学、现象与事故阶段

#### 药水条件与动力学
- **显影条件**：时间、温度、浓度、搅动、迫冲/减冲、显影液疲劳与补偿显影先汇总为有效活性、进度、反差、灰雾、最高密度、趾肩与颗粒倍率，再进入材料转换。迫冲提升活性、反差、灰雾与颗粒；显影液疲劳则先降低有效活性，伴随灰雾与粗粒。
- **定影液疲劳**：仅降低定影清除完成度，使更多银盐残留，不影响显影活性或已形成的银/染料像。
- **药水品种与状态分离**：显影类型（标准、细颗粒、补偿、高锐度、高反差、单浴）各自决定颗粒幅度、尺度与粗粒比例；定影类型（快速、硬膜、单浴）决定清除倾向与疲劳容忍。迫冲与疲劳由独立强度控制，不伪装成新的药水品种。

#### 现象与事故的阶段归属

所有效果严格按物理/工艺发生阶段介入，不把前期现象挪到扫描端伪造：

```text
灰尘、碎屑、毛发、飞虫、边字闪光、漏光、光晕  -> 潜影形成之前（曝光侧）
显影不均、邻接效应、DIR 层间抑制                -> 材料池转换当时（显影侧）
乳剂与染料颗粒、独立金属银颗粒                  -> 已形成层密度之上
化学污斑、表面镀银、乳剂网裂                    -> 形成之后的附加光学层
染料损伤                                        -> 染料材料池处理步骤
扫描与调色                                      -> 只读观察
```

- **曝光侧遮挡与预曝光**：灰尘与碎屑按帧变化，毛发与飞虫则像粘附片门，同批可共享位置身份。漏光在潜影前增加非负曝光，支持局部边缘、四周、半幅、锥形、次级形态叠加、外部遮罩及手动定位。片基导光（默认关闭）仅从声明的画框边缘向内衰减，不读取场景内容。光晕支持兼容暖色回流与实验性的三尺度分层回流。
- **片边与厂印编码**：相机激光点阵、光学琥珀等打印在曝光阶段进入潜影。齿孔透出扫描灯的亮度和颜色，孔外片基和厂印按胶片极性解读。桌面与手机均将厂印片边随冲洗保存在光学底片中。先选厂印模板固定胶片名、条码、片号和过片标记的位置。上片偏移按一格的宽度计，整卷共用；归零即对准。Code 128 C 编入四位批号和四位片序；跨格的条码可沿走片方向拼接读取。自绘厂印图案编码格式为 `FFEM1:宽x高:十六进制位图`（上限 128×32 格，按行高位在前，末尾补零）；在「上下排版」可以为上下边分别启用文字、片号、方向与编码，按片宽百分比指定各自的起点和终点。自定义边字可输入英文字母和空格，制版时转为大写。文字与片号共用方格体、圆笔黑体或细线印刷体。桌面和手机均可调整厂印曝光（−4 至 +4 EV）及灯源颜色。机身漏光与厂印曝光共同作用于片边乳剂。自绘图案占文字位。光学掩模板和聚焦曝光会使片边随药浴显影，后者的光斑扩散可调。激光刻蚀会在标记处去除乳剂，保留片基和剩余背层。
- **油墨与片边微漏光**　在「片边与齿孔 → 边字与曝光」选油墨印刷，可调满版油墨的透射颜色和明暗。「规格与齿孔 → 片边微漏光」默认 0%。提高后，片带外缘与齿孔周围会受到微弱暖色曝光。激光曝光还会在笔画附近散光，油墨字缘保持清晰。
- **画面内打印**可选原版激光点阵、光学投字和实验室印字。「使用原版激光点阵」恢复原机背字形与光源。点阵显示和七段数码字形可搭配不同灯色。七段数码管可选微缝或宽缝。微缝字形沿用原管的笔画比例，在接头留一道窄隙。新打印从 +1.25 EV 起，旧记录保留已保存的剂量。光晕随材料响应与打印曝光变化。
- **颗粒与极端曝光**：颗粒随影像密度增加并在接近最高密度时平缓收敛。彩色颗粒在染料层形成后再经吸收矩阵显色；独立金属银颗粒（默认关闭）仅对实际留下的金属银产生中性起伏。普通材料严重过曝时趋向肩部；材料编辑器另提供默认关闭的研究性极端曝光回落尾部（仅改潜影，非二次曝光或扫描反相）。
- **显影邻接与 DIR 层间抑制**：两者均默认关闭。首次显影邻接通过只读预估局部卤化银消耗生成近零均值速率修正，表达边缘反差（非扫描锐化），不延续到反转二次显影。DIR 层间抑制在彩色显影中按材料声明的源—目标矩阵和有界扩散抑制目标层，标准彩反默认关闭。
- **形成后事故**：化学污斑（只生成一次后处理沉积）、表面镀银（中性金属银斑驳，与漂白旁路留银无关）、乳剂网裂（显式开启，不由单一浴温自动触发，只产生近零均值结构密度）和彩色染料损伤（显式开启，与交叉冲洗染料稳定性损失相乘作用）均不反向修改已经生成的乳剂颗粒。
- **材料退化**：属于材料自身状态，按退化强度同时插值感光度损失、底雾、层间失衡与颗粒增量（最高约 +1/3），强度为零时完全不改变原行为。

### 8. 核心架构约束

```text
Film Formation = Material State + Latent Activation + Ordered Selective Operators
Polarity       = A Property of the Final Medium
Cross Process  = Program Applied to a Non-native Material
Scan           = Read-only Optical Observation
```

一体式即显、撕拉片、达盖尔银版、相纸、湿版、蓝晒、铂钯等非银盐工艺各自拥有独立的材料状态与工序模型，不强塞进上述银盐胶片池，仅复用共享的观察、保存与工作区接口。

---

<a id="spectral"></a>

## 光谱曝光、材料与观察

默认内置胶卷采用经验 RGB 模型。光谱能力需要显式加载材料或观察档案，支持规则清晰的创作用解析谱，无需先完成商业实测标定即可使用。

桌面端在“画幅与光谱档案”中提供三类独立入口：

1. **曝光光谱**：描述输入 RGB 基底与材料感光层响应，决定曝光与潜影形成。
2. **最终材料吸收谱**：描述染料、银、银盐、支撑体、色罩与附加层的逐波长吸收，随潜影冻结。
3. **观察光源与响应谱**：描述扫描光源与三通道读出响应，位于观察设置，仅改变扫描成片。

### 关键特性与存储开销

- **低维曝光预积分**：支持三基谱与 Smits 七基谱（W/C/M/Y/R/G/B）创作模板，运行前预积分为小矩阵，保留 HDR 高光曝光范围，不在逐像素上分配完整波长轴。
- **冻结与只读边界**：从潜影继续冲洗时沿用曝光时冻结的材料吸收谱，后续更换档案不会替换它。已形成谱介质可反复更换观察光源与响应谱重新积分；纯 RGB raw/TIFF 不具备材料谱信息，不能用于任意光谱观察。
- **谱颗粒与后处理光学层**：
  - 乳剂颗粒与独立金属银颗粒可与材料谱组合，以**全帧总量守恒**的局部分布写入九组分最终材料池；支撑体、色罩、附加层和片基锚点保持不变。
  - 化学污染、表面镀银与乳剂网裂写入独立后处理光学层，在观察时以 450 / 550 / 650 nm 为蓝/绿/红参考点做分段密度插值（范围外延续端点值）。
- **跨端与空间开销**：带光谱的介质额外保存 9 通道最终组分（约 **36 字节/像素**），启用后处理光学层再增加约 **12 字节/像素**，不含逐像素波长张量。桌面与手机可互读同一份谱介质，各自独立完成形成与观察计算，不承诺逐像素相同。

---

<a id="parameters"></a>

## 术语与参数字典

[材料参数](#material-parameters) · [冲洗参数](#bath-parameters) · [扫描参数](#scan-parameters) · [微调参数](#look-parameters) · [常见调参判断](#troubleshooting)

一个最实用的划分原则：**会改变 `.npz` 最终介质或光学密度的参数属于材料/工艺阶段；只改变从同一介质看到的成片画面的参数属于扫描/观察阶段。**

### 阶段词汇

| 术语 | 含义 | 调整后果 |
| --- | --- | --- |
| 输入图像 / input image | 普通 JPG / PNG / TIFF 等显示编码图像（或经解码的线性 DNG / Ultra HDR） | 普通 SDR 图像并非真实场景辐照度 |
| 近似线性工作空间 | sRGB 逆转换后的浮点空间 | 让扩散、曝光、颗粒叠加更合理，仍为工作近似 |
| 胶片材料 / film stock | H-D 曲线、片基、染料吸收、颗粒基准、光晕、MTF 等本体属性 | 改变底片/正片母版，不适合在只扫描阶段调节 |
| 冲洗 / chemistry / develop | 显影程序、时间、温度、浓度、搅拌、迫冲与药水疲劳 | 改变最终介质密度、灰雾、反差、颗粒与残银 |
| 已冲洗电子介质 | 保存材料契约、层母版与派生 `optical_density_rgb` 的 `.npz` | `optical_density_rgb` 为扫描权威输入；CMY / 层数组用于兼容与制版 |
| transmission raw / scanner raw | 经透射光源与传感器采样的 16-bit 线性透射图（可带片基边框） | 适合外部软件解释或高速重扫 |
| 扫描 / scan | 读取最终介质，按用户选择决定去片基、反相、黑白点与色彩解释 | 只读操作，不改变已冲洗介质 |
| 输出 / output | 文件格式、尺寸、位深、质量与 sidecar | 仅影响文件交付，无胶片物理含义 |

### Sidecar 文件与随机策略

每个主输出文件旁可附带同名 `.json` sidecar，记录输入/输出路径、生成时间、随机种子策略、完整配置快照，以及 scanner raw 的精确片基边框像素宽度。使用 `.npz` 重扫时，sidecar 能保留形成配置上下文。

随机性策略控制颗粒、漏光及过程波动的可重复性：

- `random`：每次运行重新随机采样；
- `fixed`：相同种子与配置严格可复现，适合对照实验与批处理；
- `path`：同一输入路径稳定复现，不同文件获得不同纹理。

> “过程条件随机波动”仅调整时间、温度、浓度、搅拌与疲劳的随机起伏幅度，不等同于上述随机性策略。

典型 `develop` 输出文件示例：

```text
image.darkroom_negative.npz          密度与光学母版
image.darkroom_negative.npz.json     母版 sidecar
image.darkroom_negative.png          橙色负片预览
image.scanner_raw.tiff               16-bit 线性 scanner-raw 透射图
image.scanner_raw.tiff.json          scanner-raw sidecar
```

### 配置分离原则

推荐按职责将预设拆为三类，完整流程再按需组合；根目录完整预设 JSON 仅作一体化示例：

```text
film preset      -> 胶片材料本体
develop preset   -> 冲洗程序、药水条件与冲洗侧微调
scanner preset   -> 扫描解释、影调曲线与成片侧微调
```

<a id="material-parameters"></a>

### 胶片材料参数（`film`）

| 参数 | 所属配置 | 含义 | 增大时的典型变化 |
| --- | --- | --- | --- |
| `halation_strength` | `film` | 光晕能量耦合强度 | 高光边缘红橙光晕更明显 |
| `halation_return_model` | `film` | 光晕回流模型 | `compatibility_rgb` 用兼容暖色回注；`layer_selective` 直接耦合至材料感光层 |
| `halation_layer_return_weights` | `film` | 分层光晕回流相对权重 | 仅 `layer_selective` 生效，控制三层接收回流比例 |
| `halation_spread_scale_weights` | `film` | 紧邻 / 主回流 / 宽域三尺度权重 | 仅 `layer_selective` 生效；默认 `(0, 1, 0)` |
| `halation_threshold` / `halation_softness` | `film` | 光晕亮度触发阈值与过渡软化宽度 | 阈值越高越只在极亮处触发；软化越大边缘越柔和 |
| `halation_core_radius` / `halation_exponential_radius` | `film` | 近程乳剂散射半径与长程片基反射半径 | 增大后光晕核心更厚、外延扩散更远 |
| `light_piping_strength` / `light_piping_depth` | `film` | 片基边缘光导强度与向内深度（默认关闭） | 潜影前增加画框边缘曝光，非扫描漏光 |
| `light_piping_edge_mode` / `light_piping_layer_weights` | `film` | 入光边缘与各感光层权重 | 仅按画框几何衰减，不读取画面物体边缘 |
| `extreme_exposure_reversal_strength` | `film` | 极端曝光潜影回落强度（研究用，默认 0） | 过阈值后连续降低潜影，非扫描反相或二次曝光 |
| `extreme_exposure_reversal_start_loge` / `width` | `film` | 回落起点与过渡宽度 | 基于内部归一化 `logE` 坐标 |
| `film_base_density_rgb` | `film` | 片基 / 橙色色罩 RGB 光学密度 | 负片底色更橙，去色罩跨度更大 |
| `clear_support_density_rgb` | `film` | 透明支撑体密度下限 | 不参与色罩漂白，防止片基密度归零 |
| `base_dye_interaction_strength` / `matrix` | `film` | 有色片基与宽带染料响应的耦合强度与重叠矩阵 | 强度 > 0 时，去色罩后仍保留有色片基对染料的等效光谱影响 |
| `retained_halide_density_rgb` | `film` | 定影不足残留卤化银的 RGB 密度倾向 | 决定残留银盐的浑浊与色偏方向 |
| `bleached_halide_density_*` | material | 漂白后银盐的独立光学系数 | 未填写时沿用残留卤化银系数 |
| `material_degradation` | `film` | 材料退化统一强度（0–1） | 同时插值感光度损失、底雾、层间失衡与颗粒增量 |
| `degradation_speed_loss_stops` / `fog_density_rgb` / `layer_balance` | `film` | 满强度退化时的失速档数、底雾密度与感色平衡 | 定义该材料老化的方向与幅度 |
| `auxiliary_layer_amount` / `auxiliary_layer_density_rgb` | `film` | 可清除附加层（如 rem-jet）初始量与单位密度 | 仅在清除不完全时进入最终介质密度 |
| `layer_sensitivity_matrix` | `film` | RGB 曝光到各乳剂层的映射矩阵 | 决定色彩如何激发各层潜影 |
| `dye_absorption_matrix` | `film` | CMY 染料密度到 RGB 吸收的映射矩阵 | 交叉项越强，色彩互染越显著 |
| `dir_material_response_matrix` / `dir_diffusion_radius` | `film` | 彩色材料 DIR 源—目标抑制矩阵与扩散半径 | 全零关闭；非零时在彩显阶段抑制目标层（黑白材料须关闭） |
| `hd_gamma` | `film` | H-D 曲线中段斜率 | 底片密度分离增强，反差提高 |
| `density_min` / `density_max` | `film` | 最小灰雾密度与最大可形成密度 | 分别决定黑位底雾高度与高光压缩上限 |
| `log_exposure_toe` / `log_exposure_shoulder` | `film` | 暗部趾部与高光肩部位置 | 控制阴影起跳与高光滚降的曝光区间 |
| `granularity_sigma` / `grain_density_correlation_radius` | `film` | 密度域颗粒 RMS 基准与空间相关半径 | 增大后颗粒更显著、颗粒团块更粗 |
| `silver_grain_strength` / `radius` / `clump_mix` | `film` | 独立金属银颗粒强度、半径与粗团聚比例（默认 0） | 仅作用于实际残银，产生中性宽谱密度纹理 |
| `emulsion_mtf_strength` / `digital_artifact_suppression` | `film` | 乳剂解析力柔化与数码锐化振铃抑制 | 抑制数码硬边与 ISP 伪影，过高会发糊 |

> **实际颗粒计算说明**：`granularity_sigma` 是材料基准，最终颗粒还会乘以冲洗推导的 `grain_factor`（幅度）、`grain_radius_factor`（尺度）及 `grain_clump_factor`（调整已有粗尺度的权重占比，不额外叠加第二套纹理）。

#### 保存数组的固定语义

为兼容历史 `.npz` 与分层制版工具，三个数组在现代介质中并存但职责严格分离：

| 字段 | 固定语义 | 是否为扫描权威 |
| --- | --- | --- |
| `optical_density_rgb` | 由最终材料组分派生的只读 RGB 总光学密度 | **是**，新介质的扫描与灯台只读取它 |
| `density_cmy` | 历史三通道形成层代理（颗粒与后处理沉积之前） | 否，仅供旧格式兼容、制版与分析 |
| `density_grain` | 历史复合层代理（含乳剂颗粒与部分后处理层代理） | 否，且不是纯颗粒扰动图 |

> Layer Pack 中的历史文件名 `grain_layer.png` 实际展示 `abs(density_grain - density_cmy)` 的复合差异；新清单同时提供更准确的别名 `postprocess_density_delta_layer`。

#### 旧版兼容字段

材料配置中保留的 `color_matrix`、`base_fog`、`contrast`、`toe_strength`、`shoulder_strength`、`shoulder_point`、`saturation`、`grain_strength`、`grain_scales`、`grain_scale_weights`、`grain_midtone_mu`、`grain_midtone_sigma` 属于历史字段，不参与当前主流程。编写新材料预设时请使用 H-D 曲线、密度范围、感色/吸收矩阵与颗粒基准；调整成片观感请修改扫描预设。

<a id="bath-parameters"></a>

### 冲洗参数（`chemistry` / `develop`）

冲洗参数描述本次处理过程，先经显影动力学推导有效活性、进度、灰雾、反差与颗粒倍率，再驱动算子：

| 参数 | 所属配置 | 含义 | 增大时的典型变化 |
| --- | --- | --- | --- |
| `developer_type` | `develop` | 显影液类型（标准 / 细颗粒 / 补偿 / 高锐度 / 高反差 / 单浴） | 影响速率、反差、灰雾、颗粒尺度/团聚与补偿倾向；高锐度预设启用首次显影邻接 |
| `fixer_type` | `develop` | 定影液类型（快速 / 硬膜 / 单浴） | 影响疲劳容忍度与银盐清除效率，不改显影量 |
| `frame_size` | `develop` | 画幅 / 放大倍率代理 | 小画幅（半格 / 35mm）颗粒更明显，大画幅更细腻 |
| `time_min` / `temperature_c` / `concentration` / `agitation` | `develop` | 显影时间、温度、浓度与搅拌强度 | 提高反应活性，增强反差、灰雾与颗粒 |
| `process_mode` | `develop` | 药水执行模式 | 区分常规、单浴与反转执行语义 |
| `compensation` | `develop` | 补偿显影倾向 | 更强地抑制高光继续增长，肩部更早介入 |
| `push_stops` | `chemistry` | 迫冲 / 减冲档数 | 正值提高活性、反差、灰雾与颗粒；负值相反 |
| `developer_exhaustion` / `fixer_exhaustion` | `chemistry` | 显影液与定影液疲劳度 | 前者降低活性与最高密度、增灰雾；后者只降低定影清除度 |
| `development_adjacency_strength` / `radius` | `chemistry` | 首次显影邻接强度与相对半径（默认关闭） | 表达局部耗竭造成的边缘反差，不改潜影、非扫描锐化 |
| `dir_process_effectiveness` | `chemistry` | 当前彩显条件对材料 DIR 的有效性 | `None` 时彩负默认 1、标准彩反默认 0 |
| `adjacency_work_long_edge` | `processing` | 显影邻接工作网格长边上限 | 仅控制内部计算网格分辨率，全局统计先冻结 |
| `silver_retention` | `chemistry` | 留银 / 漂白旁路强度 | 降低银漂白完成度，保留影像金属银 |
| `silver_plating` | `chemistry` | 表面镀银事故强度 | 冲洗后增加斑驳中性表面银沉积，不改原影像银池 |
| `mask_bleach_completion` | `develop` | 实验性色罩 / 染料漂白完成度 | 独立于银漂白，降低负片色罩并伴随一定染料损伤 |
| `auxiliary_removal` | `chemistry` | 附加层清除完成度 | 越低则保留越多 rem-jet / 防光晕背层密度 |
| `reticulation_strength` | `chemistry` | 乳剂网裂强度（默认 0） | 按同种子缩放近零均值明胶结构密度，不改片基与化学池 |
| `dye_damage_strength` | `chemistry` | 显式彩色染料损伤强度（默认 0） | 仅在统一彩色程序内减少剩余染料，黑白程序拒绝非零值 |
| `light_leak_template` / `secondary_template` / `mix` | `chemistry` | 主 / 次漏光形态与混合方式 | 支持 `auto`、`local_edge`、`perimeter`、`half_frame`、`cone` 及 `max` / `add` / `replace` |
| `light_leak_strength` / `mask_path` / `placement` / `identity_seed` | `chemistry` | 漏光强度、外部遮罩、定位模式与结构身份 | 潜影前增加非负曝光；可固定批次共享形状 |
| `exposure_occlusion_dust` / `debris` / `hair` / `insect` / `identity_seed` | `chemistry` | 曝光前灰尘、碎屑、毛发、飞虫遮挡及位置身份 | 挡住到达乳剂的光，非扫描脏点 |
| `film_border.imprint_*` | `film_border` | 激光点阵 / 光学琥珀 / 厂印边字 | 片门强光在 MTF 后加法进入潜影，非 RGB 水印 |

<a id="scan-parameters"></a>

### 扫描与观察参数（`scanner`）

| 参数 | 所属配置 | 含义 | 增大时的典型变化 |
| --- | --- | --- | --- |
| `transmission_light_ev` / `transmission_light_temperature_k` | `scanner` | 共享透射光源亮度与色温 | 改变采集端照明；启用同光源去片基时均匀增益会被部分抵消（`negative_backlight_*` / `light_table_*` 为旧兼容字段） |
| `remove_base_mask` | `scanner` | 是否去除片基 / 色罩 | 使用已知片基、边框样本或兜底估计平衡底色，不改介质 |
| `invert_transmission` | `scanner` | 是否反相解释透射信号 | 开启后转正密度并进入负片通道重建与正像映射 |
| `include_clear_base_border` | `scanner` | 是否显示派生片基参考边框 | 扩大观察画布并随同光源采样，不写回光学母版 |
| `scanner_response_matrix` | `scanner` | 传感器 RGB 采集响应矩阵 | 作用于去色罩之前，描述采样器特性 |
| `scan_base_percentile` | `scanner` | 外部 raw 无边框时的片基估计算法百分位 | 仅作外部无记录 raw 的兜底，不覆盖已知片基 |
| `negative_channel_matrix` / `negative_channel_gamma` | `scanner` | 去罩反相后的通道交叉重建与非线性曲线 | 温和校正扫描组合的通道串扰，不回写介质 |
| `negative_channel_compensation_enabled` / `strength` | `scanner` | 扫描侧负片蓝绿通道补偿开关与强度（默认关闭，0.35） | 仅作用于去罩后 RGB 场景密度，不反演材料染料矩阵 |
| `print_reference_density` | `scanner` | 中灰映射参考密度 | 决定整体亮暗与中灰平衡，三通道不等会产生色偏 |
| `print_gamma` / `print_mapping_mode` | `scanner` | 扫描反差与映射曲线（`printlike` / `sigmoid`） | `printlike` 偏纸面展开，`sigmoid` 更接近干净视频映射 |
| `print_color_shift` / `print_color_bias` | `scanner` | log 域滤色偏移与 RGB 乘法增益 | 优先使用更自然的 `print_color_shift` |
| `highlight_color_bias` | `scanner` | 阈值以上高光的逐通道增益（1.00 为中性） | 低于 1 压低该通道、高于 1 增强，不再被正片滤色强度二次衰减 |
| `projection_black_adaptation` | `scanner` | 正片灯台暗部观看适应 | 单调抬升极暗层次并保持色比，不造固定灰底 |
| `scan_saturation` | `scanner` | 扫描成片饱和度 | 仅影响最终输出色彩浓度 |
| `scan_normalize` / `strength` / `mode` | `scanner` | 自动黑白点归一化开关、强度与模式（`luma` / `rgb`） | `luma` 按统一亮度倍率展开并保留色偏；`rgb` 逐通道展开接近自动白平衡 |
| `scan_black_percentile` / `scan_white_percentile` | `scanner` | 黑点与白点采样百分位 | 黑点越高暗部越厚，白点越低高光越易拉白 |

<a id="look-parameters"></a>

### GUI / CLI 快捷微调参数（`look`）

| 参数 | 所属配置 | 作用阶段 | 增大时的典型变化 |
| --- | --- | --- | --- |
| `exposure_ev` | `look` | 曝光侧 | 调整输入曝光量，改变底片整体密度 |
| `negative_contrast` | `look` | 材料侧 | 缩放胶片 H-D gamma，改变底片反差 |
| `saturation_multiplier` | `look` | 材料侧 | 调整染料选择性吸收（只扫描阶段不建议使用） |
| `halation_multiplier` | `look` | 曝光侧 | 缩放光晕强度 |
| `grain_multiplier` / `grain_size_multiplier` | `look` | 形成侧 | 缩放颗粒强度与空间相关半径 |
| `print_contrast` / `print_exposure_ev` | `look` | 扫描侧 | 仅改变扫描成片反差与亮暗，不修改底片 |
| `look_strength` | `look` | 快捷总控 | 同步联动光晕、颗粒与 H-D gamma，适合快速试味；制作精细预设建议保持 `1.0` |

### 反转与交叉工艺速览

- **正冲 / 反转（`bw_reversal`、`color_reversal`）**：通过统一银盐材料池与剩余卤化银激活形成正像介质，而非扫描反相。最终介质声明正像极性，默认推荐关闭去片基与反相观察。
- **彩负黑白冲洗**：保留彩色材料的三层感色矩阵、橙色片基与光学参数，不生成染料，仅留下宽谱吸收的金属银（介质标记为 `silver_on_color_material`），区别于黑白胶片与简单去饱和。

<a id="troubleshooting"></a>

### 常见调参判断

| 画面现象 | 优先检查 | 调整建议 |
| --- | --- | --- |
| 负片成片整体发灰、黑白不到位 | `scan_normalize`、`scan_normalize_strength`、`print_contrast` | 开启 `luma` 归一化并适当提高强度，再小幅增加 `print_contrast`（诊断流程除外） |
| 调曝光 EV 画面亮暗变化不明显 | `scan_normalize` | 自动归一化抵消了曝光偏移，降低强度或关闭即可看清曝光差异 |
| 颜色发干、像自动白平衡过头 | `scan_normalize_mode`、`scan_normalize_strength` | 保持 `luma` 模式并降低强度，避免使用 `rgb` |
| 颜色偏淡 | `scan_saturation`、`print_contrast`、`print_color_shift` | 先调扫描饱和度与反差，再小幅调整 log 域滤色 |
| 整体偏红 | `print_color_shift`、`print_reference_density` | 优先微调 log 域滤色，避免用 RGB bias 硬压 |
| 颗粒像“盖在图上” | `emulsion_mtf_strength`、`grain_size_multiplier`、`digital_artifact_suppression` | 适度提高 MTF / 伪影抑制，并微调颗粒尺度 |
| 画面细节发糊 | `emulsion_mtf_strength`、`digital_artifact_suppression` | 降低两项值（尤其是干净相机原图） |
| 光晕边缘过硬 | `halation_softness`、`halation_gradient_suppression`、`halation_strength` | 提高软化与梯度抑制，略降光晕强度 |
| 高光色偏调不动或过强 | `highlight_color_bias`、`highlight_bias_threshold` | 确认高光达到阈值；以 `1.00` 为中性逐通道微调 |

**推荐调参顺序**：先固定输入并降低/关闭自动归一化 → 调稳 `film` 与 `chemistry` 并保存 `.npz` → 固定 `.npz` 只调 `scanner` → 最后决定输出格式与尺寸。处理大图时可先开边长缩放试参，正式输出再恢复原尺寸。

---

<a id="process-fields"></a>

## 工艺程序与扫描解释字段

### 工艺程序配置（`develop`）

```json
{
  "develop": {
    "program_key": "color_negative",
    "reversal_activation": 1.0,
    "first_silver_removal": 1.0,
    "silver_bleach_completion": 1.0,
    "halide_fixing_completion": 1.0,
    "dye_coupling_efficiency": 1.0,
    "auxiliary_removal": 1.0,
    "process_layer_balance": [1.0, 1.0, 1.0]
  }
}
```

- `program_key` 支持 `auto`、`bw_negative`、`color_negative`、`color_negative_bleach_bypass`、`bw_reversal`、`color_reversal` 及回归对照用的 `legacy_density`。
- `first_silver_removal` 仅作用于黑白反转程序；彩色反转的两段银像在末段由 `silver_bleach_completion` 统一漂白。
- `experimental_color_negative_clear_reversal` 在彩色反转程序尾段按 `mask_bleach_completion` 追加色罩/染料漂白，默认流程始终为 `0`。
- 交叉冲洗时，材料预设的 `cross_process_*` 兼容字段仅在材料与程序不匹配时生效。其中银显影、激活、漂白、定影、去银与附加层清除取值范围 0–1；`cross_process_dye_coupling` 与 `cross_process_layer_balance` 可大于 1（受容量上限约束），后者与程序的 `process_layer_balance` 相乘。

### 扫描解释配置（`scanner`）

扫描预设通过解释器角色声明其历史定位与推荐用途：

```json
{
  "scanner": {
    "interpreter_key": "negative_scan",
    "target_medium_process": "negative",
    "input_polarity": "negative",
    "output_polarity": "positive"
  }
}
```

- 新版会话统一保存 `interpretation_mode = "manual"`，由用户直接控制 `remove_base_mask` 与 `invert_transmission`；旧版负片预设自动迁移为“双开”，旧版正片预设迁移为“双关”。需要明确记录非反相直透观察时使用 `transmission_scan`。
- 即使完整 JSON 同时包含 `film` 字段，只扫描现代 `.npz` 介质时仍以介质自身冻结的 `optical_observation` 为准，扫描预设无法覆盖片基或染料吸收参数。

### 正片材料与灯台观看参数区分

正片 / 反转片将“形成侧密度塑形”与“灯台观察适应”严格分开：

- **材料形成侧**（改变电子正片密度）：
  - `positive_highlight_shoulder` / `positive_highlight_shoulder_width`：亮部高光肩部，防止亮区硬贴最低密度；
  - `positive_midtone_density`：正片中间调密度压实；
  - `positive_shadow_toe` / `positive_shadow_toe_width`：暗部趾部，缓和高密度阴影堵塞。
- **灯台观看侧**（不改变电子正片密度与 `light_table_raw`）：
  - `projection_white_softness`：观察白点软滚降；
  - `projection_black_adaptation`：观察极暗部黑位适应。

---

<a id="diagnostic"></a>

## 诊断与事故配方

### 冲洗灵敏度诊断组合：`film/diagnostic_develop_sensitive` + `scanner/diagnostic_flat_scan`

用于验证冲洗参数（时间、温度、浓度、疲劳、补偿、迫冲）是否确实作用于底片状态与最终画面，而非日常创作预设：

- 采用陡峭、窄宽容度 H-D 曲线，使冲洗条件变化直观可见；
- 降低光晕、颗粒、MTF 与扫描调色干扰；
- 刻意关闭扫描自动归一化，避免自动定黑白点抹平冲洗差异。

命令行对照示例：

```bash
python -m film_foundry.app.cli full input_images outputs/diagnostic_develop \
  --film-preset diagnostic_develop_sensitive \
  --develop-preset standard_color_negative \
  --scanner-preset diagnostic_flat_scan \
  --fast
```

### 事故配方字段示例：`develop/accident_kelp_light_leak`

用于测试或创作药液失效、留银、药染、显影不均与漏光等有界冲洗事故：

- `light_leak_strength`：在潜影前施加定向局部边缘预曝光（可通过工艺编辑器叠加次级形态、外部遮罩或手动定位）；
- `chemical_stain`：在材料形成后生成空间 CMY 化学污斑，不降低显影活性或既有染料最高密度；
- `uneven_development`：在材料转换前生成低频局部活性场，真实改变银与染料生成；
- `silver_retention`：通过降低漂白完成度保留已显影的影像金属银；
- `silver_plating`：在冲洗后叠加斑驳中性表面镀银沉积（不改原影像银池）。

材料退化（`material_degradation` 及配套项）归属 `film` 预设，不混入药水疲劳；附加层（`auxiliary_layer_amount` / `auxiliary_layer_density_rgb`）由 `auxiliary_removal` 控制清除程度，残留量归属最终介质。

---

<a id="color-mixer"></a>

## 分色 HSL

桌面「观察与调色 → 分色 HSL」与手机扫描页「色彩 → HSL」支持对**红、橙、黄、绿、青、蓝、紫、洋红**八个色区分别调节色相（H，±30°）、饱和度（S，±100）与明度（L，±100），支持单色及全部重置。

- 参数保存在 `scanner.color_mixer`（固定顺序 8 行 `[H, S, L]`），可随 JSON 与 FFC2 保存，纯属扫描观察端调整，不写入底片介质；缺省或全零时完全跳过以保持旧结果不变。
- 算法标识为 `output_hsl_bands_v1`，在显示编码与影调/RGB 曲线之后执行。色区中心依次位于 0° / 30° / 60° / 120° / 180° / 240° / 270° / 300°，相邻色区用 smoothstep 循环插值；饱和度按 `1 + S/100` 缩放，明度按 `0.0025 * L * min(chroma / 0.05, 1)` 增减，纯灰保持不变。此为项目自有调色规则，不承诺与其他软件同名滑块数值等效。
- 桌面与手机共享参数格式并各自独立实现；手机端在现有 GPU 扫描步骤后由 CPU 执行分色计算。

---

<a id="performance"></a>

## 性能与计算策略

Film Foundry 将“改变画面/尺寸的用户控制”与“保持结果一致的底层工程优化”严格分开：

- **三个正交用户控制轴**：**边长缩放**（减少处理像素）、**质量档位**（`draft` / `standard` / `high`，控制内部细节采样）与**降阶快速模式**（显式省略复杂形成机制换取速度），三者可自由组合。
- **硬件加速与回退**：桌面开发源码已对部分形成、转移、观察与显示编码算子接入可选 **CUDA / OpenCL** 加速，默认保持 CPU 启动，设备不可用或算子失败时自动回退 CPU；尚未实现全流程 GPU 化或 Vulkan。手机端对符合精度与耗时条件的步骤提供可回退的 **OpenGL ES 3.1** 协同计算。详细现状见[开发中源码快照](DEVELOPMENT_SNAPSHOT.md)。

### 通用执行优化

| 策略 | 实现方式 |
| --- | --- |
| 预计算小表 | 将 RGB / Smits 基底、感光层响应与观察系数预准备为紧凑查找表 |
| 阶段复用 | 仅调整扫描参数时直接复用已形成介质，无需重新曝光与冲洗 |
| 编辑合并 | 连续拖动参数只提交最新预览请求，后台线程保持界面响应 |
| 预览与正式分离 | 交互使用有界屏幕预览，正式输出按所选尺寸与质量执行 |
| 全局定标、分块执行 | 在整图上冻结片基、曝光与黑白点参考，再按行块执行避免接缝 |
| 峰值内存控制 | 按行积分光谱、复用临时数组、及时释放中间历史 |
| 批次调度 | 按帧执行，支持协作取消与 `--resume` 续跑 |
| 快速无损归档 | 潜影、底片与材料母带默认采用快速无损压缩（或可选不压缩），保持数组与精度不变 |

> **使用建议**：日常调参开启自动预览；确定配方后再以原尺寸输出。反复尝试扫描风格时，先保存冲洗介质再单独重扫，可省去重复冲洗耗时。

### 借鉴来源

[Fotufilm 的谱表实现](https://github.com/DhaliwalX/fotufilm-engine/blob/c30bf7176bdff85cc3bb8a34453311ebba359d57/Sources/FotufilmCore/SpectralModel.swift)为按配置准备谱数据、有界缓存与构建协调提供了工程参考。

---

<a id="acknowledgements"></a>

## 致敬与借鉴说明

感谢以下开源项目、研究者与依赖作者的公开工作：

| 项目 / 资料 | 借鉴方向 |
| --- | --- |
| [spektrafilm](https://github.com/andreavolpato/spektrafilm) · Andrea Volpato | 胶片、相纸与观察分阶段的光谱摄影研究思路 |
| [Fotufilm](https://github.com/DhaliwalX/fotufilm-engine) | 按配置构建谱表、有界缓存与重复准备协调（[参考实现](https://github.com/DhaliwalX/fotufilm-engine/blob/c30bf7176bdff85cc3bb8a34453311ebba359d57/Sources/FotufilmCore/SpectralModel.swift)） |
| Brian Smits [RGB → 光谱方法](https://doi.org/10.1080/10867651.1999.10487511) / [PBRT 光谱说明](https://www.pbr-book.org/3ed-2018/Color_and_Radiometry/The_SampledSpectrum_Class) | 七基颜色分解的方法背景（本项目使用自有解析基谱） |

上述摄影项目提供设计启发，Film Foundry 的材料、工艺与数值管线均为自主实现。同时感谢 Python、NumPy、OpenCV、Pillow、Qt / PySide6、LittleCMS、python-lcms2、LibRaw / rawpy、tifffile、imagecodecs 与 PyAV 等依赖的作者与维护者。本项目开发过程使用 AI 工具辅助。

---

<a id="source"></a>

## 源码范围与发布状态

公开源码仓库包含桌面 Python 应用、共享材料与介质契约、预设目录、自动化测试与资源，以及 Android 端五个模块（`:interchange`、`:kernel`、`:workflow`、`:platform`、`:app`）、Gradle wrapper、合成测试夹具和设备检查工具。本机签名密钥、SDK 路径、私人照片与内部开发记录不包含在公开源码中。

- **当前显示版本**：桌面 **0.4.0 Alpha**、Android **0.1.0 测试版**（历史变更见[更新记录](CHANGELOG.md#v040)，开发源码新增内容见[源码快照](DEVELOPMENT_SNAPSHOT.md)）。
- **包入口与跨端关系**：Python 入口统一为 `film_foundry`，历史介质与会话继续可读。桌面默认 CPU 并支持可选 CUDA / OpenCL 加速，手机支持可回退的 GLES 协同；两端采用独立数值实现，不承诺逐像素一致。

### 运行与基础自检

```bash
python run_film_foundry_launcher.py
python run_film_foundry_launcher.py --self-check
python -m pytest
```

### 许可与分发合规

算法署名与依赖许可详见[第三方声明](../THIRD_PARTY_NOTICES.md)及[第三方许可目录](../third_party_licenses/README.md)。8-bit 输出防色带已采用明确署名的 `lowbias32` 实现；Android 已登记实际运行时依赖及 CameraX 原生库并将许可正文内置于 APK。二次分发二进制安装包时，请同步提供对应版本的项目源码及适用的第三方许可材料。

## 片边形成方式

在「片边与齿孔 → 规格与齿孔」选择。手机在装卷时的片边设置里选择，新卷随曝光保存该设置。

| 模式 | 画面范围 | 片边上的效果 |
| --- | --- | --- |
| 后加片边（默认） | 保留原图大小，沿走片方向的两侧增加片边 | 冲洗后按本卷材料和药浴生成厂印，保留边界漏光 |
| 原生整片冲洗 | 原图占中央片门，两侧增加未曝光乳剂 | 画面、厂印、颗粒和局部事故在同一整幅坐标上计算 |
| 135 装入 120 | 保留原图尺寸与构图，齿孔和厂印沿原图走片方向排列 | 影像曝光到片边，齿孔之间也有影像 |

135 全幅沿 36 mm 边走片；半格沿 18 mm 边走片。横构图的 4∶3 半格在左右两侧各有四个齿孔，竖构图在上下两侧。厂名、片号、方向标和条码每八孔排一次。连续两个半格各取这块厂印的四孔宽窗口，例如卷内第 23、24 次曝光合起来是厂印第 12 格。上片偏移可使字和码跨越半格边界。满幅透过仅开放给 135 全幅胶片，模拟装入更宽的片门；普通 120 卷本身无孔。

新建片边按画幅换算字高。135 全幅和半格使用同一胶片横向规格，120 上的边字相对画面更小。桌面可在同页切换旧版字高。已有已冲洗底片保留原始像素。
