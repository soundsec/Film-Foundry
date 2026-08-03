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
```

彩色负片的橙色 mask 在当前降阶模型中由两部分共同描述：`dye_absorption_matrix` 表示成像染料的非理想交叉吸收，`film_base_density_rgb - clear_support_density_rgb` 表示未反应彩色 masking coupler 形成的整体掩蔽密度。负片扫描使用冲洗结果中冻结的该光学锚点进行去色罩；扫描 preset 不能改写它。

当彩色负片材料按反转程序形成可直接观看的正像时，标准路径会保留这层有色 mask。它可能产生很强的鲑橙/橙褐综合色调，但这不是扫描器通道裁切，也不应由正片扫描器暗中去罩。Kodak 对有色 masking coupler 的说明表明，这种掩蔽为后续印片校正而设计，若用于直接观看的反转材料会形成明显且不利的综合色调；因此项目把去除 mask 保留为显式的实验性强力漂白工艺，而不是“自动美化”交叉冲洗结果。参见 [Kodak H-188: Exploring the Color Image](https://www.kodak.com/content/products-brochures/Film/Exploring-the-Color-Image.pdf) 与 [US6274299B1](https://patents.google.com/patent/US6274299B1/en)。

由于系统输入与输出只有 RGB 三通道，片基—染料的全光谱关系使用三带宽响应降阶：`base_dye_interaction_matrix` 表示每个宽 RGB 观察通道对相邻光谱带的重叠，`base_dye_interaction_strength` 控制该降阶项。用户可以直接修改 `film_base_density_rgb` 定义综合色罩/片基颜色。扫描器会精确扣除同一最终介质中冻结的均匀片基锚点，但有色片基对宽带染料响应造成的等效变化仍然保留；中性片基或强度为零时严格退化为原先的密度相加模型。

实验性“透明底正冲”在标准彩色反转拓扑之后追加独立的 mask/dye bleach 算子。它不等同于银漂白：银漂白只把金属银转为可定影银盐；实验算子才降低 masking-coupler 密度，并按材料参数伴随一定图像染料损伤。该算子默认关闭，也不作为 C-41 或 E-6 的标准步骤。

```text
color-reversal material + color_negative program -> negative medium on native clear base
```

材料编辑可共享一个银盐胶片外壳，但必须显式选择 `color_negative`、`bw_negative`、`color_reversal` 或 `bw_reversal` 材料类别。类别决定原生调用身份、片基语义、曲线方向和专属参数范围；程序 preset 只决定本次冲洗，不得反向改写材料类别。

## 6. 最终介质与只读观察

`FilmFinalMedium` 保存最终金属银、染料、原始残留卤化银、漂白后银盐、片基和附加层，是形成侧的权威材料状态。材料池与冲洗时冻结的材料观察参数共同派生只读 `optical_density_rgb`；`optical_observation` 只是这些观察参数的持久化快照，不是材料池本身。扫描器只读取派生光学母版：

```text
final medium optics + scanner/light/view parameters = digital observation
```

扫描 preset 不能覆盖材料光学属性，也不能回写 `density_cmy`、`density_grain`、材料池或工艺记录。负片与正片使用同一个“透射光源 + 传感器”采集阶段；去片基和反相是采集后的独立用户控制，不是两台扫描器，也不是不同材料形成引擎。

最终介质契约仍记录极性和推荐解释，但主 GUI 只把它显示为建议：负片通常“去片基 + 反相”，正片通常“保留片基 + 不反相”。用户可以关闭两项，得到灯台透射的负片翻拍，也可以进行其他实验组合。旧 `auto/negative/positive` 配置会迁移到对应组合；无论选择什么，观察都只读取不可变光学参数，不得反写最终介质。

可选片基参考边框在 `optical_density_rgb` 派生完成之后扩展观察画布，边框值来自最终介质已知的透明片基光学密度，并经过同一个光源和传感器。它可用于片基锚定或直接展示透光边缘，但不属于冲洗结果，也不会写回 RGB 光学母版。

完整光谱模型可写为 Beer-Lambert 吸收与扫描光源/传感器积分；当前实现使用三层染料吸收矩阵和中性银密度的 RGB 降阶近似。光谱公式是接口的物理解释和未来扩展方向，不是当前精度承诺。

正片材料的密度份额先在有界 logit 域中调整反差，再进入趾部、中间调和肩部塑形。这样仍允许反转片拥有较窄宽容度和较高反差，却不会因为一次线性拉伸加硬裁切而把一段不同密度全部压成同一 `D-max`。灯台的暗部适应同样使用保持次序、保持综合色比的单调曲线：纯黑不被抬成固定灰底，相邻暗密度仍可分辨。两者都是工程降阶函数，不是任一胶片特性曲线的数值标定；Kodak 的感光测量资料只支持趾部、直线段、肩部和 `D-max` 的结构语义。[Kodak Basic Photographic Sensitometry](https://www.kodak.com/content/products-brochures/Film/Basic-Photographic-Sensitometry-Workbook.pdf)

漂白与定影必须保持不同状态语义：彩色漂白将金属银氧化为可由定影液溶解的银盐，定影再移除原始未显影卤化银与漂白后银盐；漂白不完全留下的是宽谱吸收的金属银，定影不完全留下的是银盐。实现因此分别保存 `metallic_silver`、`residual_halide` 和 `bleached_halide`，只有在计算“可定影银盐总量”时才临时求和。完全定影会清除后两者，但不会移除未被漂白的金属银。

残留银盐的颜色倾向不再由引擎全局写死，而由材料字段 `retained_halide_density_rgb` 提供。通用彩色材料默认使用 `(0.62, 0.82, 1.00)`，表示定影不足常见的蓝密度上升/黄色 D-min 方向；黑白内置材料显式使用 `(1.00, 1.00, 1.00)` 的中性乳浊近似。原始残留卤化银与漂白后银盐拥有独立的光学系数入口；现有 preset 未专门提供漂白盐数据时，后者回退复用残留卤化银系数，以保持旧结果。不同彩色材料可以分别记录两类权重。它们仍是有方向依据的降阶系数，不是实测光谱数据。金属银继续按中性宽谱密度处理，因此漂白旁路仍表现为反差增加和饱和度下降。

上述转换关系依据 Kodak 的一手工艺资料：[Processing KODAK Motion Picture Films, Module 5](https://www.kodak.com/content/products-brochures/Film/Processing-KODAK-Motion-Picture-Films-Module-5.pdf)、[Using KODAK Kit Chemicals in Motion Picture Film Laboratories](https://www.kodak.com/content/products-brochures/Film/Using-KODAK-Kit-Chemicals-in-Motion-Picture-Film-Laboratories.pdf)、[Kodak Processing Techniques](https://www.kodak.com/en/motion/page/processing-techniques/)；定影不足的蓝密度/黄色 D-min 方向参考 [Kodak CIS-204 / TG-2044](https://www.kodak.com/cluster/global/plugins/acrobat/en/service/kLab/tg2044_1_02mar99.pdf)。黑白反转的第一次银像去除与剩余卤化银再显影也与 [ILFORD Reversal Processing](https://www.ilfordphoto.com/reversal-processing/) 的流程说明一致。

## 7. 冲洗参数与事故阶段

时间、温度、浓度、搅动、迫冲/减冲、显影液疲劳和补偿显影先被转换为有效活性、药力容量、进度、反差、显影灰雾、D-max、趾肩变化与颗粒响应。`push_stops` 不再只是成像风格修饰：它以保守的 `clamp(1.12^push, 0.60, 1.75)` 活性倍率进入材料池转换，并把该派生量记录为 `push_activity_factor`，同时保留独立时间/温度供用户按实际配方指定；迫冲提高转换、反差、雾和颗粒，减冲降低转换、反差和颗粒。显影液疲劳不再只追加视觉脏化：`developer_capacity = clamp(1 - 0.60 * exhaustion, 0.40, 1.00)` 先缩放有效活性，因此会降低银显影和染料偶合的材料池实际转换量；雾和粗颗粒是其附加后果。反转程序再用 `first_development_completion` / `second_development_completion` 分别缩放第一次与第二次显影。该方向与 [Kodak Push/Pull Processing](https://www.kodak.com/en/motion/page/push-pull-processing/) 对延长/缩短显影、反差、颗粒及有限感光度补偿的说明一致；本项目的倍率是工程降阶，不宣称复现任一标准线的精确时间表。

定影液疲劳不属于显影生成动力学：它不会改变显影活性、反差、已形成染料/银像或 `D-max`，只降低 `fix_halide` 的完成度，使 `residual_halide` 与 `bleached_halide` 留在最终介质中并由材料光学系数产生浑浊/色偏。`silver_retention` 只降低漂白去银完成度，保留的是显影已经形成的影像银；`silver_plating` 则表示快速显定一体或疲劳药液误操作导致的表面金属银沉积，以中性宽谱表面密度进入最终介质。两者不能互相代替，也不再重复叠加残留雾。化学污斑只生成一次独立的形成后沉积密度，不再同时写入均匀 `D-min` 或削弱 `D-max`；显影不均则在材料池转换前转成局部活性场，真实改变银/染料生成，而不是覆盖成片。`film_process_model.effective_development` 保存 `developer_profile`、`fixer_profile`、`developer_capacity`、`developer_fog_shift` 和清除失败等派生量，`process_program` 保存最终步骤强度，表面镀银另记录在 `surface_deposits`，使 GUI 数值、程序强度和最终介质可以相互核对。

药水类型与药水状态保持分离。公开显影 profile 是标准、细颗粒、补偿、高反差和单浴；迫冲与疲劳分别由 `push_stops` 和 `developer_exhaustion` 表达，不再伪装成新的药水品种。硬膜属于定影/明胶处理侧。快速、硬膜和单浴定影使用独立清除 profile，其中单浴具有显影与定影并行竞争的基础清除惩罚。Kodak 的工艺资料明确把定影效率归因于 fixer activity、扩散、时间、温度和搅动；[ILFORD ILFOTEC RT / fixer hardener instructions](https://www.ilfordphoto.com/wp/wp-content/uploads/2024/03/ILFOTEC-RT-RAPID-070324.pdf) 也指出加入硬膜剂需要延长定影与水洗时间，因此硬膜不能在相同条件下被解释为“清除更强”。

事故按发生阶段处理：

```text
light leak / halation -> pre_latent_exposure
uneven development    -> development_formation
emulsion / dye grain  -> formed layer density
chemical stain        -> post_process_component_density
surface silver        -> post_process_surface_silver_density
scan controls         -> observation only
```

漏光事故增加曝光并改变潜影；其降阶生成器通常选择一条随机入光边、偶尔选择第二入口，并在红橙的片基侧倾向与较中性的乳剂侧倾向之间变化，不再默认四边同时形成“光晕框”。片基光导与事故漏光分开：它是默认关闭的材料响应，只从用户明确声明的画框边缘按片基传播深度向内衰减，并直接增加材料层曝光；它不读取场景物体边缘，也不随机选择入口。光晕同样在潜影前作为材料内部反射造成的附加曝光。既有材料默认使用兼容暖色 RGB 回注；实验性 `layer_selective` 材料复用同一高光源，可按紧邻乳剂、主片基回流和宽域薄雾三个传播尺度合成一张回流场，再按材料层权重直接增加各层曝光，不先伪装成 RGB 色偏。三者都属于潜影形成侧，不能被扫描器重算或覆盖。显影不均改变局部显影活性并进入材料池转换；乳剂/染料颗粒依据已经形成、但尚未叠加表面污染的层密度生成；药染与表面镀银随后作为独立组分加入，因此不会反向改变颗粒分布。`process_variation` 只改变冲洗条件，不拥有扫描曝光、归一化或滤色。扫描器不拥有任何冲洗事故控制。

基础密度颗粒的随机幅度随高于 `D-min` 的影像密度增加；接近材料 `D-max` 时再平滑收敛，避免已接近堵塞的正片阴影继续无限增大噪声。正片端点收敛强于负片，负片中段至高密度仍保持更明显的密度相关颗粒增长。形成后的药染、定影残留与表面镀银不再提高染料层颗粒幅度或尺寸；它们由各自材料分量表达。实验性的独立金属银颗粒默认关闭；启用后只依据最终介质中的实际残银生成中性宽谱密度扰动，直接进入 RGB 光学母版，不经过染料矩阵，完全去银时保持恒等。染料/乳剂颗粒曲线与金属银颗粒参数都属于经验降阶，尚未按具体材料的 diffuse RMS granularity 数据标定。

统一材料池的潜影映射只读取曝光、材料感色矩阵、材料自身的趾肩/反差字段，以及材料退化造成的感光度和层平衡变化。正式路径由 `ReducedFilmMaterial.expose()` 创建唯一潜影状态；显影时间、温度、浓度、药力、疲劳、迫冲/减冲和补偿参数不再进入该函数。因此同一曝光与同一材料在不同配方下产生完全相同的 `developability`。

普通材料在严重过曝时仍按特性曲线进入肩部并趋向 D-max；反转材料会表现为高光褪色、趋向透明片基，而不是自动发生 solarization。材料编辑器另提供默认关闭的“极端曝光潜影尾部”，仅用于研究个别异常材料：它从逐层阈值连续降低潜影激活比例，负片与反转共享同一个材料状态，最终极性仍由工艺算子序列决定。它不是显影中的二次曝光（Sabattier effect），也不是扫描端反相；当前阈值使用项目内部归一化 `logE` 坐标，不代表已按 lux-second 标定。

配方影响被移到后续显影算子：`progress_ratio` 控制全局完成度，`gamma_factor` 在已经冻结的潜影场上形成激活度相关的局部转换速率，补偿显影则平滑降低高激活区域的继续转换。这样保留“配方改变银/染料形成”的效果，同时不再提前改写潜影。当前仍是降阶动力学：它没有逐像素药液浓度场、扩散深度、真实边缘邻接或厚乳剂深度反应；搅拌不均继续通过独立空间活性场近似。

可选的显影邻接能力同样属于降阶动力学。启用后，系统先只读估计首次显影可能消耗的卤化银量，由其邻域差异生成一个全局、近零均值的速率修正，再让正式材料池按原程序执行一次。预计步骤不消耗材料，修正不改写潜影；反转流程的二次显影也不会沿用首次显影浴的邻接场。该能力用于表达显影液局部耗竭与抑制产物扩散可能造成的边缘反差方向，不是扫描锐化、反遮罩或完整反应—扩散仿真，默认关闭且尚无公开预设标定。

统一银盐材料池现在并行保留层数据与派生光学母版。`optical_density_rgb` 是扫描与灯台的权威最终介质观察：彩色染料层颗粒先在层空间生成，再经过三带宽染料吸收矩阵；金属银、原始残留卤化银、漂白后银盐、片基和附加层分别以各自 RGB 光学密度加入；表面镀银作为近中性宽谱密度直接加入，不再伪装成染料层。漏光和光晕在潜影形成前改变到达材料的曝光，显影不均改变算子反应速率，扫描事故只允许发生在透射采样之后。`density_cmy` / `density_grain` 继续保存，用于旧 NPZ、兼容制版、分层材料和回归；前者是颗粒与冲洗后沉积代理之前的历史形成层代理，后者是包含颗粒和部分事故代理的历史复合层密度，并非纯颗粒场。两者独立保存，颗粒与事故不会反向改写颗粒前形成层；黑白乳剂颗粒以中性宽谱密度进入 RGB 观察。扫描在存在权威 RGB 母版时不再从 CMY 反建最终介质；旧文件没有该数组时仍按明确的 legacy CMY 路径读取。

`developability` 是当前剩余卤化银中可显影份额 `H^E/H`，不是初始曝光后永久不变的标签。每次显影消耗 `H^E` 后都会用剩余 `H^E` 与剩余总卤化银重新计算该比例；没有再曝光或反转激活时，完整首次显影后的第二次显影不能凭空继续生成银。反转激活只提高当时剩余池中的未激活份额，因此部分激活也保持材料守恒。

兼容参数的范围也按语义收紧：银显影、激活、漂白、定影、去银和附加层清除是 `[0,1]` 的兼容效率，只能表示相对损失；成色耦合比和层平衡可以大于 1，但仍受材料池容量和最终反应上限约束。程序自己的 `process_layer_balance` 与非原生材料额外提供的 `cross_process_layer_balance` 是两个明确相乘的因素，不是同一参数的重复入口。

表面镀银在权威 `optical_density_rgb` 中直接作为等通道宽谱密度加入；为了兼容旧 CMY/制版输出，平行层母版才通过染料吸收矩阵的伪逆保存一个代理。扫描不得从该代理重新解释表面银，否则会把本应中性的金属银变成色偏。普通 `color_negative` 只要实际漂白完成度低于 1，程序 contract 也会解析为 `color_negative_bleach_bypass`，使程序身份与最终留银状态一致。

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
