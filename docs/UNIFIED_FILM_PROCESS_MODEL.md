# 多材料银盐胶片统一状态转换模型

Film Foundry 不把 C-41、ECN-2、E-6、黑白负片或黑白反转实现为互不相关的图像滤镜。银盐胶片共享以下形成链路：

```text
Material -> Exposure -> Latent State -> Ordered Process Operators
         -> Final Medium -> Read-only Optical Observation -> Digital Image
```

统一的是材料状态语言和形成入口，不是具体材料、药水或标准工艺参数。负片材料、反转材料及其专属 GUI/preset 仍可分别描述片基、感光曲线、染料、附加层和兼容关系。

运行时 `auto` 只负责根据材料原生身份和模式选择下述正式算子程序，不代表另一套兼容引擎。旧 H-D 密度实现仅由显式 `legacy_density` 调用，用于回归比较，不属于主流程。

模型的资料事实、工程降阶、经验参数和探索性假设不混写为同一种“真实性”。本文只描述公开模型语义，不单独构成定量标定或严格化学仿真声明。

## 1. 连续潜影与唯一卤化银池

理论上，可将第 `i` 层卤化银解释为：

```text
H_i^E = H_i * e_i
H_i^U = H_i * (1 - e_i),  e_i in [0, 1]
```

`H_i^E` 是当前可优先显影的派生份额，`H_i^U` 是尚未激活的派生份额。工程实现不分别保存两个可变数组，而只保存：

```text
halide          = H_i
developability  = e_i
```

`FilmProcessState.developable_halide()` 和 `inactive_halide()` 按需生成上述视图，因此始终满足：

```text
developable_halide + inactive_halide == halide
```

这可以避免重复材料池在多步骤处理后失去守恒。

## 2. 材料层与介质级组分

感光层使用降阶池：

```text
L_i = {halide, developability, metallic_silver, bleached_halide?, coupler?, dye?}
```

介质级定义另外拥有片基和附加层：

```text
M = {L_1 ... L_n, base, auxiliary layers}
```

片基、色罩、rem-jet、防光晕层和保护层不必伪装成每个感光层都拥有的一份材料。黑白材料可以没有 coupler/dye；彩色成色材料可以为每层提供独立容量与染料吸收参数。

这里必须区分“黑白材料”和“黑白程序”。彩色负片执行 `bw_negative` 或 `bw_reversal` 时仍保留三层感色响应、染料吸收矩阵和有色片基；程序不再生成染料，只把三层反应形成的金属银解释为宽谱中性吸收。这是材料—程序交叉组合，不是运行时把彩色材料改写成黑白胶片。

## 3. 选择性处理算子

处理步骤由 action、strength、层选择性和材料—工艺兼容关系共同决定。当前共享算子包括：

- 潜影选择性银显影；
- 彩色偶合显影；
- 剩余卤化银激活；
- 金属银漂白为可定影银盐；
- 卤化银/漂白银盐定影移除；
- 黑白反转所需的首次银像直接移除；
- 染料破坏与附加层移除。

彩色偶合受银显影量、成色剂余量和层兼容性共同限制。`CompatibilityProfile` 分别保存银显影、染料偶合、激活、漂白、定影、去银、染料稳定性、附加层去除和层平衡，不能退化成单个“兼容度”。film preset 中对应的 `cross_process_*` 字段只在非原生程序时生成兼容 profile；原生程序使用中性 profile，避免逆冲调节意外改变正常冲洗。

## 4. 程序顺序产生最终极性

黑白负片保留首次显影银像；彩色负片在偶合显影后漂白、定影，通常留下染料像。漂白旁路通过降低银漂白完成度保留部分金属银。

黑白反转执行：

```text
first development -> remove first silver image
-> activate remaining halide -> second silver development -> final fix
```

彩色反转执行：

```text
first silver development -> activate remaining halide
-> color development -> bleach silver from both developments -> final fix
```

彩色反转不需要在激活前物理删除第一次银像；第一次显影已经消耗对应卤化银，第一次和第二次形成的银可以在末段统一漂白、定影。两种反转都由有限卤化银材料的竞争形成正像，不对完成的负片执行数学反相。

彩色程序尾段还显式执行染料稳定性与附加层去除算子。原生材料的默认兼容值不会破坏染料；逆冲材料可以通过兼容参数产生染料损伤或附加层清除不足。由此避免出现“编辑器有参数、程序却从不调用”的空控制。

## 5. 交叉冲洗

交叉冲洗是非原生程序作用于材料：

```text
ProcessProgram(Material, CompatibilityProfile)
```

程序拓扑保持可检查，兼容关系分别改变银显影、染料偶合、层平衡、漂白、定影、染料稳定性和附加层处理。视觉结果来自材料状态转换，不通过末端 RGB 滤镜伪造。

彩色双向逆冲因此直接表示为：

```text
color-negative material + color_reversal program -> positive medium on native colored base
color-reversal material + color_negative program -> negative medium on native clear base
```

材料编辑可共享一个银盐胶片外壳，但必须显式选择 `color_negative`、`bw_negative`、`color_reversal` 或 `bw_reversal` 材料类别。类别决定原生调用身份、片基语义、曲线方向和专属参数范围；程序 preset 只决定本次冲洗，不得反向改写材料类别。

## 6. 最终介质与只读观察

`FilmFinalMedium` 保存最终金属银、染料、原始残留卤化银、漂白后银盐、片基和附加层，并生成不可变 `optical_observation`。扫描器只读取这份介质及其密度母版：

```text
final medium optics + scanner/light/view parameters = digital observation
```

扫描 preset 不能覆盖材料光学属性，也不能回写 `density_cmy`、`density_grain`、材料池或工艺记录。负片扫描与正片透射扫描是不同观察方式，不是不同的材料形成引擎。

严格观察 API 按最终介质契约选择或校验解释器。主 GUI 的 scan-only 是有意提供的直接观察实验：用户明确选择按负片或正片解释，程序忽略记录极性但仍读取不可变光学参数。完整流程可以 `auto` 跟随刚形成的介质，也可以手动强制解释。两种手动模式都不得反写最终介质。

完整光谱模型可写为 Beer-Lambert 吸收与扫描光源/传感器积分；当前实现使用三层染料吸收矩阵和中性银密度的 RGB 降阶近似。光谱公式是接口的物理解释和未来扩展方向，不是当前精度承诺。

漂白与定影必须保持不同状态语义：彩色漂白将金属银氧化为可由定影液溶解的银盐，定影再移除原始未显影卤化银与漂白后银盐；漂白不完全留下的是宽谱吸收的金属银，定影不完全留下的是银盐。实现因此分别保存 `metallic_silver`、`residual_halide` 和 `bleached_halide`，只有在计算“可定影银盐总量”时才临时求和。完全定影会清除后两者，但不会移除未被漂白的金属银。

残留银盐的颜色倾向不再由引擎全局写死，而由材料字段 `retained_halide_density_rgb` 提供。通用彩色材料默认使用 `(0.62, 0.82, 1.00)`，表示定影不足常见的蓝密度上升/黄色 D-min 方向；黑白内置材料显式使用 `(1.00, 1.00, 1.00)` 的中性乳浊近似。不同彩色材料可以记录不同权重，以容纳乳剂、增感染料和故障程度差异。该字段仍是有方向依据的降阶系数，不是实测光谱数据。金属银继续按中性宽谱密度处理，因此漂白旁路仍表现为反差增加和饱和度下降。

上述转换关系依据 Kodak 的一手工艺资料：[Processing KODAK Motion Picture Films, Module 5](https://www.kodak.com/content/products-brochures/Film/Processing-KODAK-Motion-Picture-Films-Module-5.pdf)、[Using KODAK Kit Chemicals in Motion Picture Film Laboratories](https://www.kodak.com/content/products-brochures/Film/Using-KODAK-Kit-Chemicals-in-Motion-Picture-Film-Laboratories.pdf)、[Kodak Processing Techniques](https://www.kodak.com/en/motion/page/processing-techniques/)；定影不足的蓝密度/黄色 D-min 方向参考 [Kodak CIS-204 / TG-2044](https://www.kodak.com/cluster/global/plugins/acrobat/en/service/kLab/tg2044_1_02mar99.pdf)。黑白反转的第一次银像去除与剩余卤化银再显影也与 [ILFORD Reversal Processing](https://www.ilfordphoto.com/reversal-processing/) 的流程说明一致。

## 7. 冲洗参数与事故阶段

时间、温度、浓度、搅动、迫冲/减冲、显影液疲劳和补偿显影先被转换为有效活性、药力容量、进度、反差、显影灰雾、D-max、趾肩变化与颗粒响应。`push_stops` 不再只是成像风格修饰：它以保守的 `clamp(1.12^push, 0.60, 1.75)` 活性倍率进入材料池转换，并把该派生量记录为 `push_activity_factor`，同时保留独立时间/温度供用户按实际配方指定；迫冲提高转换、反差、雾和颗粒，减冲降低转换、反差和颗粒。显影液疲劳不再只追加视觉脏化：`developer_capacity = clamp(1 - 0.60 * exhaustion, 0.40, 1.00)` 先缩放有效活性，因此会降低银显影和染料偶合的材料池实际转换量；雾和粗颗粒是其附加后果。反转程序再用 `first_development_completion` / `second_development_completion` 分别缩放第一次与第二次显影。该方向与 [Kodak Push/Pull Processing](https://www.kodak.com/en/motion/page/push-pull-processing/) 对延长/缩短显影、反差、颗粒及有限感光度补偿的说明一致；本项目的倍率是工程降阶，不宣称复现任一标准线的精确时间表。

定影液疲劳不属于显影生成动力学：它不会改变显影活性、反差、已形成染料/银像或 `D-max`，只降低 `fix_halide` 的完成度，使 `residual_halide` 与 `bleached_halide` 留在最终介质中并由材料光学系数产生浑浊/色偏。`silver_retention` 只降低漂白去银完成度，保留的是显影已经形成的影像银；`silver_plating` 则表示快速显定一体或疲劳药液误操作导致的表面金属银沉积，以中性宽谱表面密度进入最终介质。两者不能互相代替，也不再重复叠加残留雾。化学污斑只在 `post_process_pre_grain` 密度事故层应用一次，不再同时写入均匀 `D-min` 或削弱 `D-max`；显影不均则在材料池转换前转成局部活性场，真实改变银/染料生成，而不是覆盖成片。`film_process_model.effective_development` 保存 `developer_profile`、`fixer_profile`、`developer_capacity`、`developer_fog_shift` 和清除失败等派生量，`process_program` 保存最终步骤强度，表面镀银另记录在 `surface_deposits`，使 GUI 数值、程序强度和最终介质可以相互核对。

药水类型与药水状态保持分离。公开显影 profile 是标准、细颗粒、补偿、高反差和单浴；迫冲与疲劳分别由 `push_stops` 和 `developer_exhaustion` 表达，不再伪装成新的药水品种。硬膜属于定影/明胶处理侧。快速、硬膜和单浴定影使用独立清除 profile，其中单浴具有显影与定影并行竞争的基础清除惩罚。Kodak 的工艺资料明确把定影效率归因于 fixer activity、扩散、时间、温度和搅动；[ILFORD ILFOTEC RT / fixer hardener instructions](https://www.ilfordphoto.com/wp/wp-content/uploads/2024/03/ILFOTEC-RT-RAPID-070324.pdf) 也指出加入硬膜剂需要延长定影与水洗时间，因此硬膜不能在相同条件下被解释为“清除更强”。

事故按发生阶段处理：

```text
light leak          -> pre_latent_exposure
uneven development  -> development_formation
chemical stain      -> post_process_pre_grain
grain               -> after damaged material density has formed
scan controls       -> observation only
```

漏光增加曝光并改变潜影；其降阶生成器通常选择一条入光边、偶尔选择第二入口，并在红橙的片基侧倾向与较中性的乳剂侧倾向之间变化，不再默认四边同时形成“光晕框”。显影不均改变局部显影活性并进入材料池转换；药染与表面镀银污染已经形成的密度母版；颗粒最后作用于该受损介质。`process_variation` 只改变冲洗条件，不拥有扫描曝光、归一化或滤色。扫描器不拥有任何冲洗事故控制。

统一材料池的潜影映射现在直接读取本次工艺的 H-D 曲线形状，因此 `gamma_factor`、`toe_shift` 和 `shoulder_shift` 在主路径中都有效；曲线的 D-min/D-max 会在转成 0–1 潜影份额时归一化掉，最终介质形成阶段再分别应用显影雾与密度幅度，避免重复加雾或重复压缩。补偿显影因此真实减少高曝光区域的继续增长，而不再只是旧路径中的预览参数。

表面镀银先定义为等通道 RGB 光学密度，再通过当前彩色材料的染料吸收矩阵反解为三层密度代理；不能直接给 C/M/Y 三层添加同一个数，否则带串扰的材料会把本应中性的金属银错误解释成色偏。普通 `color_negative` 只要实际漂白完成度低于 1，程序 contract 也会解析为 `color_negative_bleach_bypass`，使程序身份与最终留银状态一致。

附加层使用独立的归一化材料池。材料字段 `auxiliary_layer_amount` 与 `auxiliary_layer_density_rgb` 给出初始量和光学密度，`remove_auxiliary` 及材料—工艺兼容参数共同决定剩余量。标准流程完全清除时结果不变；rem-jet、防光晕背层或污染层清除不足时，剩余 RGB 密度进入最终介质并记录在 `pool_totals.auxiliary_remaining`，不再出现编辑器参数有数值但成像无响应的空控制。

### 材料退化

`film.material_degradation` 是材料状态的统一强度，不是药水事故。强度从 0 到 1 插值该材料定义的 `degradation_speed_loss_stops`、`degradation_fog_density_rgb` 与 `degradation_layer_balance`，分别产生感光度损失、底雾和层间感度失衡；密度颗粒基准同时最多增加 35%。强度为 0 时不改变旧材料结果。该模型合并了过期、热湿保存不当、环境辐射与潜影保存损失等来源，只保留方向明确的共同后果，不推断具体保存历史。[Kodak Film Storage Information](https://www.kodak.com/en/motion/page/storage-information/) 将 D-min 增加、趾部感光度/反差损失和颗粒增加列为老化与环境辐射的重要后果，因此这些量属于材料形成阶段并被写入最终介质，扫描只能观察，不能修复原始冲洗结果。

## 8. 核心约束

```text
Film Formation = Material State + Latent Activation + Ordered Selective Operators
Polarity       = A Property of the Final Medium
Cross Process  = Program Applied to a Non-native Material
Scan           = Read-only Optical Observation
```

拍立得、撕拉片、银版等具有不同形成机制的介质不会被强塞进本银盐模型；它们可以复用观察、UI 组件或通用状态设施，但应拥有独立材料模型和流程工具包。
