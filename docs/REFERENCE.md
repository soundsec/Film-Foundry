# 材料、工艺与参数参考

本页解释形成与观察的边界、材料池和程序、参数字段、光谱与性能实现。操作入口与命令行示例见[桌面指南](DESKTOP_GUIDE.md)。

## 目录

- [统一材料与冲洗模型](#model)：潜影、卤化银池、算子顺序、极性与交叉冲洗。
- [光谱曝光、材料与观察](#spectral)：材料档案的所有权、冻结与只读扫描。
- [术语与参数字典](#parameters)：配置分工、材料、冲洗、扫描和调参判断。
- [工艺程序与扫描解释字段](#process-fields)：程序键、兼容关系、片基校正与反相。
- [诊断与事故配方](#diagnostic)：诊断基准、药染、显影不均与留银。
- [性能与计算策略](#performance)：预计算、阶段复用、分块与内存。
- [致敬与借鉴说明](#acknowledgements)
- [源码范围与发布状态](#source)

<a id="model"></a>

## 统一材料与冲洗模型

Film Foundry 不把 C-41、ECN-2、E-6、黑白负片或黑白反转实现为互不相关的图像滤镜。银盐胶片共享以下形成链路：

```text
Material -> Exposure -> Latent State -> Ordered Process Operators
         -> Final Medium -> Read-only Optical Observation -> Digital Image
```

统一的是材料状态语言和形成入口，不是具体材料、药水或标准工艺参数。负片材料、反转材料及其专属 GUI/preset 仍可分别描述片基、感光曲线、染料、附加层和兼容关系。

运行时 `auto` 只负责根据材料原生身份和模式选择下述正式算子程序，不代表另一套兼容引擎。旧 H-D 密度实现仅由显式 `legacy_density` 调用，用于回归比较，不属于主流程。

模型里的资料事实、工程简化、经验参数和探索性假设不写成同一种“真实性”。本文只说明公开的模型含义，不构成定量标定或严格的化学仿真。

### 1. 连续潜影与唯一卤化银池

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

可显影份额和未激活份额按上面两式随时算出，因此始终满足：

```text
developable_halide + inactive_halide == halide
```

这可以避免重复材料池在多步骤处理后失去守恒。

### 2. 材料层与介质级组分

感光层使用简化后的材料池：

```text
L_i = {halide, developability, metallic_silver, bleached_halide?, coupler?, dye?}
```

介质级定义另外拥有片基和附加层：

```text
M = {L_1 ... L_n, base, auxiliary layers}
```

片基、色罩、rem-jet、防光晕层和保护层不必伪装成每个感光层都拥有的一份材料。黑白材料可以没有 coupler/dye；彩色成色材料可以为每层提供独立容量与染料吸收参数。

这里必须区分“黑白材料”和“黑白程序”。彩色负片执行 `bw_negative` 或 `bw_reversal` 时仍保留三层感色响应、染料吸收矩阵和有色片基；程序不再生成染料，只把三层反应形成的金属银解释为宽谱中性吸收。这是材料—程序交叉组合，不是运行时把彩色材料改写成黑白胶片。

新潜影保存曝光时的材料参数。后续冲洗可以换药浴、条件与事故设置，不能靠另选一种材料替换已经曝光的片基或染料。桌面和手机都保留原潜影档案，在工作状态中执行冲洗，再保存独立结果；扫描只读取这些冲洗结果。旧档缺少材料快照时仍需提供对应材料，旧版已标记消耗的档案不会自动恢复。

### 3. 选择性处理算子

处理步骤由 action、strength、层选择性和材料—工艺兼容关系共同决定。当前共享算子包括：

- 潜影选择性银显影；
- 彩色偶合显影；
- 剩余卤化银激活；
- 金属银漂白为可定影银盐；
- 卤化银/漂白银盐定影移除；
- 黑白反转所需的首次银像直接移除；
- 染料破坏与附加层移除。

彩色偶合受银显影量、成色剂余量和层兼容性共同限制。兼容关系分别保存银显影、染料偶合、激活、漂白、定影、去银、染料稳定性、附加层去除和层平衡，不能收成一个总的“兼容度”。材料预设里的交叉冲洗字段只在程序和材料不匹配时生效；原本匹配的程序使用中性兼容，避免逆冲调节意外改变正常冲洗。

### 4. 程序顺序产生最终极性

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

### 5. 交叉冲洗

交叉冲洗是用非原生程序处理一份材料。程序步骤保持可检查，兼容关系分别改变银显影、染料偶合、层平衡、漂白、定影、染料稳定性和附加层处理。画面来自材料状态的变化，不是最后套一层颜色滤镜。

彩色双向逆冲因此直接表示为：

```text
color-negative material + color_reversal program -> positive medium on native colored base
```

黑白材料进入彩色程序时保持同一拓扑，但不成色：

```text
monochrome material + color_negative program -> silver-only negative; standard bleach clears the silver, bleach-bypass keeps it
monochrome material + color_reversal program -> silver-only positive topology with zero coupler capacity
```

这不是把黑白片假装成彩卷，也不是退回旧 H-D 密度映射。

彩色负片的橙色色罩由两部分一起描述：成像染料并不只吸收自己那一种颜色，片基密度里还有一层整体的掩蔽。扫描用冲洗时冻结的片基来去色罩，扫描预设不能改写它。

当彩色负片材料按反转程序形成可直接观看的正像时，标准路径会保留这层有色 mask。它可能产生很强的鲑橙或橙褐综合色调。这不是扫描通道被裁切，也不应由正片观看暗中去掉色罩。这种掩蔽本来是为后续印片校正准备的，直接观看时会形成明显且不利的综合色调。因此去掉色罩是一项单独打开的实验性漂白，不是交叉冲洗的自动美化。公开工艺资料只用来核对这个方向，不表示本项目复现任何厂商流程。

输入和输出都只有红绿蓝三通道，所以片基和染料的光谱关系用三个宽波段来近似。你可以直接改片基颜色。扫描会扣掉介质里冻结的均匀片基；有色片基对染料的影响仍然留下。片基是中性的，或这项强度为零时，就回到原来的密度相加。

实验性“透明底正冲”在标准彩色反转拓扑之后追加独立的 mask/dye bleach 算子。它不等同于银漂白：银漂白只把金属银转为可定影银盐；实验算子才降低 masking-coupler 密度，并按材料参数伴随一定图像染料损伤。该算子默认关闭，也不作为 C-41 或 E-6 的标准步骤。

```text
color-reversal material + color_negative program -> negative medium on native clear base
```

材料编辑可共享一个银盐胶片外壳，但必须显式选择 `color_negative`、`bw_negative`、`color_reversal` 或 `bw_reversal` 材料类别。类别决定原生调用身份、片基语义、曲线方向和专属参数范围；程序 preset 只决定本次冲洗，不得反向改写材料类别。

### 6. 最终介质与只读观察

最终介质保存金属银、染料、尚未显影的卤化银、漂白后的银盐、片基和附加层，是形成侧的权威材料状态。材料池与冲洗时冻结的观察参数一起派生只读的总光学密度；保存下来的观察参数只是快照，不是材料池本身。扫描只读取这块光学结果：

```text
final medium optics + scanner/light/view parameters = digital observation
```

扫描 preset 不能覆盖材料光学属性，也不能回写 `density_cmy`、`density_grain`、材料池或工艺记录。负片与正片使用同一个“透射光源 + 传感器”采集阶段；去片基和反相是采集后的独立用户控制，不是两台扫描器，也不是不同材料形成引擎。

最终介质契约仍记录极性和推荐解释，但主 GUI 只把它显示为建议：负片通常“去片基 + 反相”，正片通常“保留片基 + 不反相”。用户可以关闭两项，得到灯台透射的负片翻拍，也可以进行其他实验组合。旧 `auto/negative/positive` 配置会迁移到对应组合；无论选择什么，观察都只读取不可变光学参数，不得反写最终介质。

可选片基参考边框在 `optical_density_rgb` 派生完成之后扩展观察画布，边框值来自最终介质已知的透明片基光学密度，并经过同一个光源和传感器。它可用于片基锚定或直接展示透光边缘，但不属于冲洗结果，也不会写回 RGB 光学母版。

完整的光谱关系可以写成吸收和光源、传感器的积分。当前默认路径使用三层染料吸收和中性银密度的三通道近似。光谱是可选扩展，不是当前精度承诺。

正片材料先在有界范围内调整反差，再做趾部、中间调和肩部。这样反转片可以较窄、较高反差，又不会把一段不同密度一次性压成同一个最高密度。灯台的暗部适应保持明暗次序和颜色比例：纯黑不会被抬成一块固定灰，相邻的暗部仍能分开。这些都是工程上的简化，不是某一条实测特性曲线。感光测量里的趾部、直线段、肩部和最高密度只提供结构上的说法。

漂白与定影是两种状态。彩色漂白把金属银变成可以被定影溶解的银盐，定影再去掉尚未显影的卤化银和漂白后的银盐。漂白不完全留下的是金属银，定影不完全留下的是银盐。因此金属银、残留卤化银和漂白后银盐分开保存，只在计算可被定影去掉的总量时才临时相加。完全定影会清掉后两者，不会去掉还没漂白的金属银。

残留银盐的颜色由材料自己的密度倾向决定，不是全局写死。通用彩色材料默认偏向蓝密度升高、低密度发黄；黑白材料默认用中性的浑浊。没有单独填写漂白盐颜色时，沿用残留卤化银的系数，以保持旧结果。这些是有方向的简化系数，不是实测光谱。金属银按中性吸收处理，所以漂白旁路表现为反差增加、饱和度下降。

上述方向对照过公开的冲洗和反转工艺说明。定影不足时常见蓝密度上升。本项目的系数不是那些资料里的配方。

### 7. 冲洗参数与事故阶段

时间、温度、浓度、搅动、迫冲或减冲、显影液疲劳和补偿显影，会先变成有效活性、药力、进度、反差、显影灰雾、最高密度、趾肩和颗粒。迫冲不再只是最后的风格：它以提高后的有界活性进入材料转换，同时仍保留你填写的时间和温度。迫冲提高转换、反差、灰雾和颗粒，减冲降低转换、反差和颗粒。显影液疲劳会先降低有效活性，因此银显影和染料偶合的实际转换变少；灰雾和粗颗粒是附带结果。反转程序再用第一次和第二次显影的完成度分别缩放两步。延长或缩短显影会影响反差和颗粒，但本项目的倍率是自己的简化，不复现任何标准时间表。

定影液疲劳不属于显影：它不改变显影活性、反差、已经形成的染料或银像、最高密度，只降低定影完成度，让残留卤化银和漂白后银盐留在最终介质里，并按材料的光学系数产生浑浊或色偏。留银只降低漂白完成度，留下的是显影已经形成的影像银。表面镀银是快速显定一体或疲劳药液误操作造成的表面金属银，以中性密度进入最终介质。两者不能互相代替。化学污斑只生成一次冲洗后的沉积，不再同时抬高整体最低密度或削弱最高密度。显影不均在材料转换前变成局部活性，真实改变银和染料的生成，不是盖在成片上。冲洗结果会记下有效显影、定影情况和表面沉积，便于核对界面上的数值和最终介质。

药水品种和药水状态分开。显影类型是标准、细颗粒、补偿、高锐度、高反差和单浴。迫冲和疲劳用各自的强度表示，不伪装成新的药水品种。显影类型分别改变颗粒的幅度、尺度和粗粒比例；粗粒比例只重新分配已有颗粒，不另做一张纹理。黑白高锐度预设可以打开默认关闭的首次显影邻接，用局部速率表示边缘效应，不是扫描锐化。硬膜属于定影和明胶处理。快速、硬膜和单浴定影的清除倾向不同，单浴因为显影和定影同时进行，清除更受限制。定影效率与药力、扩散、时间、温度和搅动有关；加入硬膜剂通常需要更长的定影和水洗，所以硬膜不能在相同条件下理解成清除更强。

事故按发生阶段处理：

```text
灰尘、碎屑、毛发、飞虫、边字闪光、漏光、光晕  -> 潜影形成之前
显影不均                                      -> 材料转换当时
乳剂和染料颗粒                                -> 已经形成的层密度
化学污斑、表面银、乳剂网裂                    -> 形成之后的附加光学
染料损伤                                      -> 染料材料池上的处理步骤
扫描                                          -> 只观察
```

灰尘和碎屑挡在曝光之前，每格可以不同。毛发和飞虫也是挡住到达乳剂的光，但更像粘在片子上，同一批可以共用一套位置。它们都不是扫描时后加的脏点。边字是片门上的强光源，进入潜影，不是贴在成片上的字。漏光在潜影之前增加曝光，形态有局部边缘、四周、半幅和较窄的锥形；自动保持原来的局部边缘。编辑器还可以加第二种形态、外部遮罩，或手动指定位置。片基导光默认关闭，只从你指定的画框边缘向内衰减，不看画面里的物体边缘。光晕也在潜影之前，是材料内部的反射。普通材料用暖色回流；实验性的分层回流按近处、主要回流和宽薄雾三档，直接加到各感光层。扫描不能重算这些。显影不均改变局部显影。颗粒根据已经形成、还没叠上表面脏污的密度生成。药染、表面银和网裂随后另加，不会倒回去改颗粒。

桌面端的齿孔和片边目前只解析布局，不扩展最终介质；相机的激光点阵、光学琥珀等打印作为片门上的曝光进入形成。手机端的厂印片边则随材料和药浴形成并保存在光学底片中，扫描只读取它，与扫描器添加的片基观察边框分开。

厂印图案可以用方格绘制，编码为 `FFEM1:宽x高:十六进制位图`，支持最多128×32格，按行排列、高位在前，末尾不足一字节的位补零。它保存自绘图案，不代表真实条码标准。`factory_pattern_top/bottom` 分别控制上下片边，`factory_pattern_linked` 共用上边图案，`factory_flip_top/bottom` 各自旋转180°。空编码沿用材料文字；自绘图案替换材料名称区域，自动格号保留。

`factory_mark_type` 支持 `hard`（默认清晰曝光）和 `laser`（激光式曝光），`factory_mark_spread` 为0–3倍的扩散控制，0回到清晰版。手机端保留原分辨率字形，用局部小网格计算较轻的材料MTF与光晕，连续插值同一材料与冲洗流程形成的曝光样本，避免块状描边，形成后不再重绘。它是风格化的曝光模型，不模拟油墨沉积。桌面点阵编辑器可保存这些配置，正式片边输出尚未接入。

乳剂网裂要单独打开，表示浴间温差或操作失误造成的皱缩。单独一浴的温度不能代表前后浴的温差，所以不会自动出现网裂。它只改变可看见的结构密度，不改银、卤化银、染料或片基本身，也不改扫描解释。强度为零时，批次里的一般波动不会凭空打开它。扫描没有冲洗事故的控制。

彩色流程里的染料损伤默认关闭。它在彩色显影已经形成染料之后，按染料破坏步骤减少剩余染料，不修改银、银盐、成色剂消耗或片基。交叉冲洗带来的染料稳定性损失是另一项，两者按先后作用在剩余染料上，总损失是相乘而不是两个百分比直接相加。单独的温度或药液疲劳不会自动打开染料损伤。黑白流程会拒绝非零设置。扫描只观察损伤之后的介质，没有一个同名的降饱和代替项。

颗粒随影像密度增加，接近材料最高密度时再缓下来，避免已经堵死的正片暗部继续无限变噪。彩色颗粒在染料层里生成，再经过染料吸收变成颜色。它不是盖在最终图像上的普通噪声，也不逐粒保存染料粒子。药染、定影残留和表面镀银不再去加大染料颗粒。独立的金属银颗粒默认关闭；打开后只根据实际留下的银产生中性起伏，银被去干净时没有这项。这些颗粒参数是经验设置，没有按某款材料的粒度数据标定。

统一材料池在曝光时只读取曝光、材料感色、材料自己的趾肩和反差，以及材料退化造成的感光度和层平衡变化。显影时间、温度、浓度、药力、疲劳、迫冲、减冲和补偿不进入这一步。因此同一曝光、同一材料，在不同配方下得到相同的可显影比例。

普通材料在严重过曝时进入肩部，趋向最高密度。反转材料则表现为高光变淡、趋向透明片基，不会自动出现反转曝光。材料编辑器另有默认关闭的极端曝光尾部，只用于个别异常材料：过了阈值后连续降低潜影，负片和反转共用这个材料状态，最后的正负仍由冲洗步骤决定。它不是显影中途的二次曝光，也不是扫描时反相。阈值使用程序内部的曝光坐标，没有按实际曝光量标定。

配方的影响放在后面的显影步骤。整体完成度控制这一浴做多少，局部速率随已经冻结的潜影变化，补偿显影则让高曝光区域少继续转换。配方仍然改变银和染料，但不再提前改写潜影。这里没有逐点的药液浓度、真实的乳剂厚度或完整的扩散；搅拌不均仍用一块空间活性来近似。

显影邻接默认关闭。打开后，先估计首次显影可能消耗的卤化银，按邻域差异做一个不消耗材料的速率修正，再正式冲洗一次。修正不改潜影。反转的第二次显影不沿用第一次的邻接。它表示局部药液耗竭可能带来的边缘反差，不是扫描锐化，也不是完整的扩散仿真。

层间抑制和上面的邻接是两件事。它按材料声明的释放、扩散和层之间的关系，抑制其他感光层，估计过程不改真正的材料池，正式冲洗仍只做一次。普通彩色负片可以有这项，标准的彩色反转默认关闭，不会把负片的强度原样套到反转片上，也不会把第一次黑白显影里的层间影响说成染料抑制。它同时影响边缘和整体反差。材料侧在材料编辑器里调，工艺侧属于冲洗配方。当前没有内置的非零材料，也不用最终图像的锐化或饱和度来代替。

材料层和由此算出的光学结果一起保留。扫描和灯台只看总的光学密度：彩色颗粒先在染料层里生成，再按染料吸收变成颜色；银、残留银盐、漂白后的银盐、片基和附加层各自加入。表面镀银按中性密度加入，不伪装成染料。漏光和光晕发生在潜影之前，显影不均改变反应速率，扫描侧的变化只发生在观察时。旧的分层数据仍保留给兼容和制版，它们不是纯颗粒图，扫描也不会从它们反推新的介质。旧文件如果没有总光学密度，仍按原来的分层方式读取。

可显影比例是当前剩余卤化银里还能显影的那一份，不是曝光之后就永远不变的标签。每次显影之后都按剩下的量重算。没有再曝光或反转激活时，第一次显影做完后，第二次显影不能凭空继续生成银。反转激活只提高当时还没激活的那一份，所以部分激活也保持材料守恒。

兼容参数按含义限制范围。银显影、激活、漂白、定影、去银和附加层清除只能表示相对损失，范围是 0 到 1。成色比例和层平衡可以大于 1，但仍受材料容量限制。程序自己的层平衡，和交叉冲洗时材料额外给的层平衡，是相乘的两项，不是同一个旋钮写了两次。

表面镀银直接按中性密度加入光学结果。为了兼容旧的分层导出，旁边会另存一个代理；扫描不能拿这个代理再解释表面银，否则中性的银会变成偏色。彩色负片只要漂白没有做完，就会记成漂白旁路，让名称和实际留下的银一致。

附加层有自己的材料量。材料给出初始量和光学密度，清除完成度和兼容关系一起决定剩下多少。标准流程完全清除时结果不变。防光晕背层或污染层清除不足时，剩余密度进入最终介质，并记下剩余量。不会出现编辑器里有数值、画面却毫无反应的空控制。

#### 材料退化

材料退化是材料自己的强度，不是药水事故。强度从没有到满强度，按该材料定义的感光度损失、底雾和层间失衡插值；颗粒基准最多再增加约三分之一。强度为零时不改变旧材料。它把过期、保存不当和潜影保存损失收成同一组方向明确的后果，不推断具体保存历史。这些变化写在形成结果里，扫描只能看见，不能把原来的冲洗修回去。

### 8. 核心约束

```text
Film Formation = Material State + Latent Activation + Ordered Selective Operators
Polarity       = A Property of the Final Medium
Cross Process  = Program Applied to a Non-native Material
Scan           = Read-only Optical Observation
```

拍立得、撕拉片、银版等具有不同形成机制的介质不会被强塞进本银盐模型；它们可以复用观察、UI 组件或通用状态设施，但应拥有独立材料模型和流程工具包。

<a id="spectral"></a>

## 光谱曝光、材料与观察

默认内置胶卷仍使用经验 RGB 模型。光谱能力需要加载明确的材料档案，可采用有明确规则的创作型谱定义；本项目不做物理复刻或仿真，不要求用真实材料标定后才能使用光谱功能。

桌面现有“画幅与光谱档案”提供三种独立入口：

- **曝光光谱**：描述输入 RGB 基底与材料感光层响应，影响曝光与潜影。
- **最终材料吸收谱**：描述染料、银、银盐、支撑体、色罩与附加层的吸收，随潜影冻结。
- **观察光源与响应谱**：描述扫描光源与三通道读出，位于观察设置；只改变扫描结果。

独立材料编辑器顶部可以编辑曝光光谱和材料吸收谱：查看曲线，并修改逐波长的值、感光层响应和增益。原来的材料文件仍可加载、保存和应用。新建的七色曝光模板是本项目自己的创作用基谱，不是商品胶片的实测数据。
它保留高动态范围的曝光强度，不会自动换掉现有胶卷。桌面和手机可以读取同一份档案，但两边的画面不会逐张相同。

从冻结潜影继续冲洗时，采用曝光时的材料谱，后续加载另一份材料谱不会替换它。
已形成介质可以换光源和扫描设置重新观察。只有 RGB raw/TIFF 的文件不能宣称支持任意光谱观察。

材料谱现在可以与乳剂颗粒、独立金属银颗粒一起使用。颗粒保存在最终组分的局部分布中，
全帧各染料/银的总量保持，支撑体、色罩、附加层与片基参考保持原值；重新扫描不会重新生成颗粒。
这是用于创作的统计模型。

化学污染、银镜和网裂也可与材料谱、颗粒组合：它们在形成时写入独立光学层，污染保留颜色、
银镜保留中性沉积、网裂保留有正有负的结构密度。观察时以 450/550/650 nm 为蓝/绿/红参考点做
分段密度插值，范围外保持端点值。这是明确的工程代理，不依赖实测沉积物吸收谱。
重新扫描不会重新生成这些瑕疵；材料池与片基参考保持原值。曝光 MTF、光晕和工艺中的染料损伤继续可用。

带光谱的介质文件会多保存染料、银和其余材料组分，大约每像素增加 36 字节。
启用上面的污染、银镜或网裂光学层时，再大约增加 12 字节。没有这些层的旧文件仍可打开。
观察时按波长计算透过率再合成颜色，不保存整张逐像素光谱。手机和桌面可以打开同一份介质，形成过程仍然各自计算。

<a id="parameters"></a>

## 术语与参数字典

[材料参数](#material-parameters) · [冲洗参数](#bath-parameters) · [扫描参数](#scan-parameters) · [微调参数](#look-parameters) · [常见调参判断](#troubleshooting)

这份说明面向调参与流程理解。Film Foundry 的项目层级是“成像介质形成与观察”；当前 0.3 工具包具体实现银盐负片、银盐反转正片及其透射扫描。因此，主控制台使用成像介质术语，而银盐材料、工艺与扫描编辑器仍按自己的真实适用范围命名。

```text
输入图像
-> 材料曝光与工艺：形成最终介质
-> 扫描 / 观察：解释透明介质，得到数字图像
```

一个实用判断是：如果某个参数会改变 `.npz` 最终介质或其派生光学密度，它属于材料/工艺阶段；如果只改变从同一介质观察得到的最终图，它属于扫描/观察阶段。

### 阶段词汇

| 术语 | 含义 | 改动后果 |
| --- | --- | --- |
| 输入图像 / input image | 普通 JPG/PNG/TIFF，被当作显示编码图像 | 它不是真实场景线性辐照度，不能认为还原了真实光子能量 |
| 近似线性工作空间 | sRGB 解码后的浮点空间 | 后续扩散、曝光、颗粒叠加更合理，但仍只是近似 |
| 胶片本体 / film stock | 胶片材料属性，如 H-D 曲线、片基、染料吸收、颗粒、halation | 改它会改变负片或正片介质母版，不适合在 scan-only 阶段反复试 |
| 冲洗 / chemistry / develop | 显影条件，如迫冲、温度、药水疲劳 | 改它会改变底片密度、灰雾、反差和颗粒 |
| 已冲洗电子介质 / developed electronic medium | 已冲洗完成的材料契约、层母版与派生 `optical_density_rgb` | `optical_density_rgb` 是重扫输入；CMY/层数据用于制版、分层与兼容 |
| transmission raw / scanner raw | 共享灯台、光源与传感器看到的线性透射图，负片或正片都可带片基参考边框 | 适合外部软件解释，也适合 scan-only 快速重扫 |
| 扫描 / scan | 对已冲洗透明介质采样，并按用户选择决定是否去片基、是否反相，再做黑白点和色彩解释 | 改它不应改变已冲洗介质，只改变数字观察结果 |
| 输出 / output | 保存格式、尺寸、质量、bit depth、sidecar | 只影响文件交付，不应承担胶片物理意义 |

### Sidecar 文件

sidecar 是跟在主文件旁边的 `.json` 伴随文件。它不保存图像数据本身，而是保存这张图像/底片如何生成：

- 输入文件名和输出文件名。
- 生成时间。
- 当次随机种子或 seed 策略。
- 胶片、冲洗、扫描、输出配置快照。
- 对 transmission/scanner raw TIFF 来说，还会记录派生片基参考边框；它可用于去色罩取样，也可直接显示灯台透光效果。

主 GUI 的随机性策略控制颗粒、漏光、过程波动及其他随机场的重复方式：

- `random`：每次运行重新采样；
- `fixed`：同一基础种子与设置可重复，用于批次复现；
- `path`：同一输入路径保持稳定，不同输入获得不同随机结果。

“过程条件随机波动”只控制时间、温度、浓度、搅拌、疲劳和已启用事故倾向的变化幅度，不等同于随机性策略。当前漏光 GUI 可选择兼容自动局部边缘、局部边缘、四周、半幅和锥形；具体入口位置、宽度及纹理仍由种子采样。银盐工艺程序编辑器另外提供次级形态组合、外部 mask、手动位置和可选身份种子。

典型 develop 输出可以是：

```text
image.darkroom_negative.npz              密度母版
image.darkroom_negative.npz.json         密度母版 sidecar
image.darkroom_negative.png              橙色负片预览
image.scanner_raw.tiff                   16-bit linear scanner-raw 透射图
image.scanner_raw.tiff.json              scanner-raw TIFF sidecar
```

如果用 `.npz` 重扫，sidecar 能告诉程序“这张底片当初是用哪个胶片/冲洗配置形成的”。如果 sidecar 丢失，程序只能按当前或默认配置解释密度，语义会弱一些。

### 配置分离

现在推荐把配置拆成两类：

```text
film preset
-> 材料配置

develop preset
-> 冲洗配置
-> 显影液、单浴、时间、温度、浓度、搅动、疲劳、画幅
-> 冲洗侧的微调

scanner preset
-> 扫描与观看配置
-> 成片侧的微调
```

完整流程会组合一个 film preset 和一个 scanner preset。只冲洗模式只选择 film preset；只扫描模式只选择 scanner preset。根目录完整 preset 只建议保留少量示例，用于展示一份配置同时包含胶片、冲洗、扫描、输出参数的写法。

<a id="material-parameters"></a>

### 胶片 / 负片参数

| 参数 | 所属配置 | 直观含义 | 增大时通常发生什么 |
| --- | --- | --- | --- |
| `halation_strength` | `film` | 光晕能量耦合强度 | 高光周围红橙扩散更明显，灯牌、反光边缘更“溢” |
| `halation_return_model` | `film` | Halation 回流模型 | `compatibility_rgb` 保持旧暖色 RGB 曝光回注；`layer_selective` 将同一传播场直接耦合到材料感光层 |
| `light_piping_strength` / `light_piping_depth` | `film` | 默认关闭的片基边缘光导强度与向内传播深度 | 只在潜影前增加材料层曝光，不属于扫描 flare 或漏光事故 |
| `light_piping_edge_mode` / `light_piping_layer_weights` | `film` | 明确声明入光画框边缘及各感光层相对响应 | 不读取场景物体边缘；`long_edges` / `short_edges` 依据当前画框几何解释 |
| `extreme_exposure_reversal_strength` | `film` | 默认关闭的研究性极端曝光潜影回落强度 | 普通材料应保持 0；它改变材料潜影，不是扫描反相或 Sabattier 二次曝光 |
| `extreme_exposure_reversal_start_loge` / `extreme_exposure_reversal_width` | `film` | 三材料层的回落起点和连续过渡宽度 | 使用内部归一化 `logE` 坐标，尚未按真实曝光量标定；只应配合有依据的异常材料使用 |
| `development_adjacency_strength` | `chemistry` | 默认关闭的一次性首次显影邻接强度 | 从预计反应量生成局部速率修正；不改变潜影、不执行扫描锐化 |
| `development_adjacency_radius` | `chemistry` | 邻接作用尺度，相对画面短边 | 当前是工程降阶尺度，未按乳剂厚度、扩散系数或具体药水定量标定 |
| `dir_process_effectiveness` | `chemistry` | 当前彩显条件对材料 DIR 响应的有效性 | `None` 时彩负默认 1、标准穷尽彩反默认 0；属于冲洗配方，不在材料编辑器里改 |
| `adjacency_work_long_edge` | `processing` | 显影邻接全局工作网格的长边上限 | 只改变内部计算分辨率；全局统计先冻结，材料分块不得各自估计 |
| `halation_layer_return_weights` | `film` | 材料层回流相对权重 | 仅在 `layer_selective` 下生效；控制三层材料接收回流曝光的相对比例，最大项归一为 1 |
| `halation_spread_scale_weights` | `film` | 紧邻、主回流、宽域三个传播尺度的相对权重 | 仅在 `layer_selective` 下生效；默认 `(0,1,0)` 只使用兼容主 PSF，增加首/末项会加入紧邻扩散或宽域薄雾 |
| `halation_threshold` | `film` | 触发 halation 的亮度阈值 | 越高越只在极亮处触发，白墙等普通亮部更干净 |
| `halation_softness` | `film` | 触发阈值的软化宽度 | 光晕触发更平滑，硬边更少，但范围可能更宽 |
| `halation_core_radius` | `film` | 近程乳剂散射半径 | 光晕核心更厚，靠近高光处更糊 |
| `halation_exponential_radius` | `film` | 长程片基反射衰减半径 | 外层红橙扩散更远，更有“片基反射”感 |
| `film_base_density_rgb` | `film` | 彩负片基/橙色 mask 的 RGB 光学密度 | 负片外观更橙，扫描去色罩压力更大 |
| `clear_support_density_rgb` | `film` | 不参与实验性色罩漂白的透明支撑体密度下限 | 限制“透明底”不会变成不存在的零密度材料 |
| `base_dye_interaction_strength` | `film` | 有色片基改变宽带 RGB 染料响应的降阶强度 | 为零时去色罩后片基颜色完全抵消；提高后仍保留等效光谱影响 |
| `base_dye_interaction_matrix` | `film` | 三个观察通道对相邻光谱带的非负重叠权重 | 控制不同片基颜色主要影响哪些染料/观察通道 |
| `mask_bleach_completion` | `develop` | 实验性色罩/染料漂白完成度；与银漂白分离 | 降低负片 mask，同时可能损伤成像染料 |
| `retained_halide_density_rgb` | `film` | 定影不足残留银盐的降阶 RGB 密度倾向 | 改变残留盐造成的色偏方向，不改变定影完成度 |
| `bleached_halide_density_*` | material model | 漂白后可定影银盐的独立降阶光学系数 | 未定义时复用原始残留卤化银系数；不会把两类状态重新合并 |
| `optical_density_rgb` | developed medium | 由最终材料及冻结观察参数派生、经过组件化事故/颗粒合成的只读 RGB 总光学密度 | 扫描优先读取；`density_cmy` 只保留层效果、兼容、制版与分层职责 |
| `material_degradation` | `film` | 材料退化/过期/保存不当的统一强度 | 同时插值感光度损失、底雾、层间失衡与颗粒增加；不代表某一种具体老化机制 |
| `degradation_speed_loss_stops` | `film` | 满强度退化时的感光度损失 | 数值越大，退化材料形成的潜影越弱 |
| `degradation_fog_density_rgb` | `film` | 满强度退化底雾的 RGB 光学密度 | 定义退化底雾的强度与颜色方向 |
| `degradation_layer_balance` | `film` | 满强度退化的感色层相对感度 | 描述不同感色层老化速度不同造成的色平衡漂移 |
| `auxiliary_layer_amount` | `film` | 可清除附加层的初始量 | 表示 rem-jet、防光晕背层或保护/污染层的降阶材料池 |
| `auxiliary_layer_density_rgb` | `film` | 附加层单位剩余量的 RGB 光学密度 | 只有附加层清除不完全时进入最终介质密度 |
| `layer_sensitivity_matrix` | `film` | RGB 曝光如何落到三层乳剂 | 改变颜色如何形成 CMY 密度，属于胶片材料层 |
| `dye_absorption_matrix` | `film` | CMY 染料密度如何吸收 RGB 光 | 交叉项越强，颜色越“互染”、越不数码干净 |
| `dir_material_response_matrix` | `film` | 彩色材料 DIR 源层→目标层有效响应 | 全零关闭；非零时在彩显中抑制目标层，不是扫描锐化或饱和度滤镜 |
| `dir_diffusion_radius` | `film` | DIR 有界扩散相对短边尺度 | 更大则层内抑制更宽；黑白材料必须保持关闭 |
| `hd_gamma` | `film` | H-D 曲线中段斜率 | 底片密度分离更强，扫描后反差也更容易增强 |
| `density_min` | `film` | 最小密度 / 片基灰雾 | 黑位更抬、底片更“雾”，扫描后可能更灰 |
| `density_max` | `film` | 最大可形成密度 | 高曝光区域能形成更厚密度，高光压缩空间更大 |
| `log_exposure_toe` | `film` | 暗部趾部位置 | 暗部开始分离的位置变化，影响阴影可见性 |
| `log_exposure_shoulder` | `film` | 高光肩部位置 | 高光更早或更晚进入压缩，影响灯牌和天空 |
| `granularity_sigma` | `film` | 密度域颗粒 RMS 强度基准 | 颗粒更明显，尤其密度较高区域 |
| `grain_density_correlation_radius` | `film` | 颗粒空间相关半径，按画幅比例换算像素 | 颗粒更粗，细密噪声更少，大块感更明显 |
| `silver_grain_strength` | `film` | 实验性独立金属银颗粒强度；默认 0 | 只在最终介质确有残银时增加中性宽谱密度变化，不改变染料层颗粒 |
| `silver_grain_radius` / `silver_grain_clump_mix` | `film` | 金属银颗粒的相关半径与粗团聚混合 | 只改变残银空间纹理；不经过染料吸收矩阵，也不属于扫描噪声 |
| `emulsion_mtf_strength` | `film` | 乳剂有限解析力 / 输入高频抑制 | 数码锐边更不“浮”，但真实细节也可能被压掉 |
| `digital_artifact_suppression` | `film` | 对 ISP 锐化振铃的额外抑制 | JPEG/手机锐化边缘更柔，但过高会糊 |

`granularity_sigma` 只是材料基准。实际颗粒还会乘以冲洗得到的 `grain_factor`，空间尺度会乘以 `grain_radius_factor`，既有多尺度颗粒中最粗尺度的相对占比由 `grain_clump_factor` 调整；后者重新归一化权重，不会凭空增加第二套纹理。迫冲、过显、高温、显影疲劳、显影液类型和画幅会改变乳剂颗粒的幅度、尺度或团聚性；定影残留、药染和表面镀银仍按各自最终介质组分表达，不能冒充染料/乳剂颗粒。

#### 保存数组的固定语义

为了兼容旧 NPZ 和分层工具，三个数组长期并存，但用途不同：

| 字段 | 固定语义 | 是否为扫描权威 |
| --- | --- | --- |
| `density_cmy` | 历史三通道形成层代理；位于乳剂颗粒和冲洗后沉积代理之前，不承诺是纯染料 CMY | 否；仅供旧格式、兼容制版和分析 |
| `density_grain` | 历史复合层代理；包含乳剂颗粒以及药染、镀银等部分层空间事故代理 | 否；也不是独立颗粒扰动图 |
| `optical_density_rgb` | 从最终材料组分派生的只读 RGB 总光学密度 | 是；新介质的扫描与灯台只读取它 |

旧名 `density_grain` 为文件兼容而保留。Layer Pack 中旧文件名 `grain_layer.png` 实际显示 `abs(density_grain-density_cmy)` 的复合差异，可能同时包含颗粒与冲洗后事故；新 manifest 同时给出更准确的 `postprocess_density_delta_layer` 路径别名。它不能作为纯颗粒场使用，也不会为此默认增加一张大图数组。

#### 旧的响应与颗粒字段

材料配置里还留着一批旧字段：`color_matrix`、`base_fog`、`contrast`、`toe_strength`、`shoulder_strength`、`shoulder_point`、`saturation`、`grain_strength`、`grain_scales`、`grain_scale_weights`、`grain_midtone_mu`、`grain_midtone_sigma`。

这些字段不属于当前冲洗主流程。新的材料预设不要再靠 `contrast`、`saturation` 或 `grain_strength` 调底片。改材料时优先用特性曲线、密度范围、感色、染料吸收和颗粒基准。若只想改变最终正像，应改扫描预设里的饱和度、反差和滤色。

<a id="bath-parameters"></a>

### 冲洗参数

冲洗参数描述“这一次如何处理胶片材料”。它们不会永久改变 film stock，而是先进入一个简化显影动力学模型，推导出显影活性、显影进度、灰雾、最大密度限制、颗粒增长和肩部压缩，再影响最终底片密度。

| 参数 | 所属配置 | 直观含义 | 增大时通常发生什么 |
| --- | --- | --- | --- |
| `developer_type` | `chemistry/develop` | 显影液响应类型 | 改变显影速率、反差倾向、雾化、颗粒幅度/尺度/粗粒团聚和高光补偿方式；高锐度预设另显式启用首次显影邻接，不是扫描锐化 |
| `fixer_type` | `chemistry/develop` | 定影/清除 profile | 快速、硬膜和单浴具有不同疲劳容忍与清除倾向，不改变显影生成量 |
| `frame_size` | `chemistry/develop` | 画幅 / 放大倍率代理 | 半格/35mm 颗粒更显眼，中画幅和大画幅更细；不改变胶片材料本身 |
| `time_min` | `chemistry/develop` | 显影时间 | 时间更长通常显影更充分，反差/雾化/颗粒可能增加 |
| `concentration` | `chemistry/develop` | 药水浓度倍率 | 浓度更高会提高显影活性，但也更容易增加灰雾和反差 |
| `agitation` | `chemistry/develop` | 搅拌强度 | 搅拌更强会提高局部药水交换和显影活性 |
| `process_mode` | `chemistry/develop` | 药水执行模式 | 区分常规、单浴与反转程序所需的执行语义 |
| `compensation` | `chemistry/develop` | 补偿显影倾向 | 更强时更压高光、肩部更早介入 |
| `push_stops` | `chemistry` | 迫冲档数 | 中段反差、灰雾、颗粒都会变强；阴影可能更硬 |
| `silver_retention` | `chemistry` | 留银/漂白旁路强度 | 只降低漂白去银完成度，保留显影形成的影像银 |
| `silver_plating` | `chemistry` | 表面镀银事故强度 | 在冲洗后介质上增加斑驳中性金属银沉积，不改变原影像银材料池 |
| `light_leak_template` | `chemistry` | 潜影前漏光空间形态 | `auto` 保持历史局部边缘行为；另有 `local_edge`、`perimeter`、`half_frame`、`cone`，显示名称会随 GUI 语言切换 |
| `light_leak_strength` | `chemistry` | 漏光预曝光强度 | 新模板由形态控制覆盖、强度控制带限模板场的峰值尺度；`auto/local_edge` 保持历史响应。全部都在潜影形成前增加非负曝光，不是成片叠色或扫描 flare |
| `light_leak_secondary_template` | `chemistry` | 次级内置漏光形态 | `none` 保持单模板；与主形态在覆盖场上混合，不另乘一套强度 |
| `light_leak_mix` | `chemistry` | 漏光覆盖混合 | `max` / `add` 混合主形态、次级形态和 mask；`replace` 只用外部 mask |
| `light_leak_mask_path` | `chemistry` | 外部漏光覆盖图 | 读入后放到与内置模板相同的带限控制网格，只归一峰值不归一总能量 |
| `light_leak_placement` | `chemistry` | 漏光位置模式 | `sampled` 由种子决定入口；`manual` 使用入口边/中心/旋转，不重采样纹理 |
| `light_leak_identity_seed` | `chemistry` | 漏光结构的身份 | 设置后同一批可以共用同一套漏光形状 |
| `exposure_occlusion_dust` / `debris` | `chemistry` | 曝光之前的灰尘和碎屑 | 挡住到达乳剂的光，每格可以不同；全零关闭。不是扫描脏点 |
| `exposure_occlusion_hair` / `insect` | `chemistry` | 粘在片子上的毛发和飞虫 | 同一批位置更稳定；强度只改变多少和有多挡光 |
| `exposure_occlusion_identity_seed` | `chemistry` | 毛发/飞虫结构身份种子 | `None` 使用内置固定身份；改种子换一套粘在片上的几何。不是 sidecar 组契约 |
| `film_border.imprint_*` | 顶层 `film_border` | 激光点阵 / 光学琥珀 / 冲洗厂边字 | 片门超强光源，在 MTF 之后加法进入潜影；文字和时间都空则关闭。不是 RGB 水印 |
| `reticulation_strength` | `chemistry` | 浴间温差或操作失误造成的明胶乳剂网裂/皱缩强度 | 同一种子固定结构拓扑，强度线性缩放近零均值的宽谱密度起伏；不由单一 `temperature_c` 自动推断，也不改变片基或扫描器 |
| `dye_damage_strength` | `chemistry` | 非标准药液或明确事故造成的已形成染料损失 | 只在统一彩色流程中通过材料池算子减少染料；不是扫描降饱和，也不会由单一温度或药液疲劳自动开启 |
| `auxiliary_removal` | `chemistry` | 附加层去除完成度 | 越低则最终介质保留更多由材料定义的 rem-jet/背层密度 |
| `temperature_c` | `chemistry` | 显影温度 | 高于基准时反应更激烈，反差和颗粒略增 |
| `developer_exhaustion` | `chemistry` | 药水疲劳 | 最大密度下降，灰雾增加，反差可能变钝，颗粒更脏 |
| `fixer_exhaustion` | `chemistry` | 定影清除能力衰减 | 留下更多原始/漂白后银盐；不降低显影活性或已形成染料 |

<a id="scan-parameters"></a>

### 扫描 / 输出解释参数

| 参数 | 所属配置 | 直观含义 | 增大时通常发生什么 |
| --- | --- | --- | --- |
| `transmission_light_ev` | `scanner` | 负片和正片共享的透射光源亮度 | 改变 linear transmission raw；启用同光源片基去除时整体亮度可能被部分抵消，不等于后段输出曝光 |
| `transmission_light_temperature_k` | `scanner` | 共享透射光源色温 | 改变 raw 通道照明；关闭去片基时会保留在直接透射结果中 |
| `negative_backlight_*` / `light_table_*` | `scanner` | 旧 preset 的两组光源字段 | 新配置优先使用共享字段；旧配置按原解释迁移，不改变旧结果 |
| `remove_base_mask` | `scanner` | 是否使用已知片基、明确边框样本或兜底估计去除片基/色罩 | 开启后执行片基平衡；它不修改介质中的真实片基 |
| `invert_transmission` | `scanner` | 是否把透射采样转为正密度并反相解释 | 开启后进入通道重建与正像映射；关闭时保留输入正负关系 |
| `include_clear_base_border` | `scanner` | 是否在扫描结果周围显示派生片基参考边框 | 扩大观察画布；边框经过同一光源和传感器，但不写回 `optical_density_rgb` |
| `film_border` | 顶层片边配置 | 齿孔、厂印点阵、成印类型及相机打印 | 桌面片边只解析布局，手机厂印随材料形成；相机打印独立进入曝光。不要和片基观察边框混用 |
| `scanner_response_matrix` | `scanner` | 背光透过介质后的传感器 RGB 响应 | 位于去色罩之前，描述采样器；不是负片染料校正或最终滤色 |
| `scan_base_percentile` | `scanner` | 无边框、无已知片基样本时估计片基的高百分位 | full 生成型负片会使用已知 clear base；该项主要是外部 scan-only 兜底 |
| `negative_channel_matrix` | `scanner` | 去罩并转入密度域后的通道交叉重建 | 可补偿材料/扫描组合的染料串扰；不应写成所有负片共用的固定蓝绿增强 |
| `negative_channel_gamma` | `scanner` | 去罩后 RGB 密度通道的非线性重建曲线 | 分别改变三通道密度展开，适合温和校正蓝绿记录，不回写冲洗介质 |
| `negative_channel_compensation_enabled` | `scanner` | 启用扫描侧负片蓝绿通道补偿 | 只作用于去色罩后的 RGB 场景密度，不读取或反演材料染料矩阵；默认关闭以兼容旧结果 |
| `negative_channel_compensation_strength` | `scanner` | 扫描侧蓝绿通道修正的混合强度 | 越高通道分离越强；它不尝试把残银、底雾或银盐重新识别成染料；默认 0.35 |
| `print_reference_density` | `scanner` | 哪段正像 raw density 被映射到中灰附近 | 改变整体色平衡和中灰位置，三通道不一致会产生色偏 |
| `print_gamma` | `scanner` | 扫描/打印映射基础反差 | 正像反差更强，暗部和高光更容易分开 |
| `print_mapping_mode` | `scanner` | 正像映射曲线类型 | `printlike` 更像纸面展开，`sigmoid` 更像干净视频映射 |
| `print_color_shift` | `scanner` | log 域扫描/打印滤色 | 正值提高对应通道输出，比 RGB 乘法更像滤色片 |
| `print_color_bias` | `scanner` | RGB 乘法增益 | 直接改变输出通道，容易显得数码，建议少用 |
| `highlight_color_bias` | `scanner` | 只作用在达到阈值后的 RGB 通道增益；`1.00` 中性 | 某通道低于 `1.00` 会压低，高于 `1.00` 会增强；该显式高光控制不再被正片总体滤色强度二次衰减 |
| `projection_black_adaptation` | `scanner` | 正片灯台观察阶段的暗部适应 | 单调抬升极暗层次并保持 RGB 比例；不制造固定灰底，也不改写正片密度母版 |
| `scan_saturation` | `scanner` | 扫描输出色彩浓度 | 只改变最终正像饱和度，不回写冲洗介质 |
| `scan_normalize` | `scanner` | 是否定黑白点 | 开启后更像扫描软件自动展开黑白点 |
| `scan_normalize_strength` | `scanner` | 全局黑白点标定的混合强度 | 越高越接近扫描软件自动展开后的正常显示范围；过高会削弱原场景曝光差异并增加极端值裁切风险 |
| `scan_normalize_mode` | `scanner` | 黑白点归一化方式 | `luma` 只重映射亮度并对每个像素的 RGB 使用同一倍率，保留整体色偏与灯台色温；`rgb` 分别展开通道，更像自动白平衡 |
| `scan_black_percentile` | `scanner` | 黑点百分位 | 越高黑位越容易压实，暗部更厚但可能堵 |
| `scan_white_percentile` | `scanner` | 白点百分位 | 越低高光越容易拉白，天空/灯牌更容易顶 |

<a id="look-parameters"></a>

### GUI / CLI 微调参数

| 参数 | 所属配置 | 作用对象 | 增大时通常发生什么 |
| --- | --- | --- | --- |
| `exposure_ev` | `look` | 输入曝光代理 EV | 在冲洗/底片形成前调整输入曝光计量，不是药水参数；会改变底片密度整体变化 |
| `negative_contrast` | `look` | 胶片 H-D gamma 倍率 | 改变底片形成方式，属于负片层反差 |
| `print_contrast` | `look` | 扫描/打印 gamma 倍率 | 不改变底片，只改变正像解释反差 |
| `print_exposure_ev` | `look` | 扫描输出曝光 | 不改变底片，只改变最终正像整体亮暗 |
| `saturation_multiplier` | `look` | 胶片染料选择性 | 改变材料层染料吸收，不建议 scan-only 时当普通饱和度用 |
| `halation_multiplier` | `look` | halation 强度倍数 | 快速增加/减少光晕 |
| `grain_multiplier` | `look` | 颗粒强度倍数 | 只控制颗粒显著程度 |
| `grain_size_multiplier` | `look` | 颗粒尺寸倍数 | 只控制颗粒空间相关半径，底层按画幅比例换算 |
| `look_strength` | `look` | 体验型总体风格强度 | 同时改写 halation、颗粒和 H-D gamma，适合 GUI 粗调；严肃 preset 标定时不建议依赖 |

`look_strength` 不是物理参数，而是面向 GUI / CLI 的“一键味道浓淡”总控。它会直接修改运行时 film 参数，所以适合快速预览，不适合作为精细 film preset 的核心定义。制作严肃 preset 时，应把 `look_strength` 保持在 `1.0`，直接调胶片和扫描器参数。

### 正冲 / 反转 / Reversal

中文里“正冲”常被用来指反转冲洗：最终直接在胶片上得到正像，而不是得到负片再扫描反相。

黑白反转的典型流程是：

```text
第一次显影
-> 漂白去掉第一次显影形成的负像银
-> 清洗 / 清除漂白残留
-> 二次曝光或化学雾化
-> 第二次显影形成正像
-> 定影 / 水洗 / 干燥
```

彩色反转并不是不存在。彩色反转片一般对应 E-6 一类流程，目标是得到彩色正片/幻灯片。它和“彩色负片 + C-41 + 扫描反相”不是一回事。

#### 当前项目实现

`bw_reversal` 与 `color_reversal` 已进入统一银盐材料池主流程。它们与负片程序共享连续潜影、唯一卤化银池和基础算子。黑白反转在首次显影后直接移除第一次银像；彩色反转保留第一次银像到末段，与彩显形成的银一起漂白、定影。两者都通过剩余卤化银激活与二次显影形成正像，而不是扫描阶段反相。

这不表示彩负材料和反转材料可以混为一谈：材料 preset 仍应分别描述感光曲线、片基、染料与正片密度特性；药水兼容性也应保留差异。统一的是降阶状态转换语言和形成入口，不是具体材料或标准工艺参数。

最终介质继续保存为密度母版并声明自身极性。`positive_transparency_scan` 只负责观察正像透明介质，不能改变已经形成的银、染料或残留卤化银状态。

### 彩负黑白冲洗

彩色负片使用黑白显影程序时，材料仍然是彩色多层负片：感色矩阵、三层卤化银响应、橙色片基和材料光学参数不会被改写。黑白程序不进行染料偶合，最终主要留下宽谱吸收的金属银。因此它不同于使用真正黑白胶片，也不同于把彩色扫描结果简单去饱和。

项目用 `silver_on_color_material` 标识该最终介质，并把组合记录为 material/process mismatch。扫描仍可观察片基带来的颜色影响，但不能把它解释为冲洗时形成了彩色染料像。

<a id="troubleshooting"></a>

### 常见调参判断

| 现象 | 优先检查 | 建议 |
| --- | --- | --- |
| 负片输出整体发灰、黑白都不到位 | `scan_normalize`, `scan_normalize_strength`, `print_contrast` | 日常扫描先确认启用 `luma` 归一化并提高 strength；仍偏平再小幅提高 print contrast，诊断流程则保持关闭 |
| 曝光 EV 不再明显控制亮暗 | `scan_normalize` | 归一化会重新定黑白点，降低 strength 或关闭 |
| 颜色像 YCrCb、太干净 | `scan_normalize_mode`, `scan_normalize_strength` | 保持 `luma`，降低 strength，避免 `rgb` |
| 颜色寡淡 | `scan_saturation`, `print_contrast`, `print_color_shift` | 先调扫描饱和度，再调 print contrast，最后小幅 log 域滤色 |
| 整体偏红 | `print_color_shift`, `print_reference_density` | 不要用 RGB bias 硬压；优先微调 log 域滤色 |
| 红色不够浓 | `scan_saturation`, `dye_absorption_matrix`, `layer_sensitivity_matrix` | 若只是最终图，调 scan saturation；若要材料特性，做新 preset |
| 颗粒像盖在图上 | `emulsion_mtf_strength`, `grain_size_multiplier`, `digital_artifact_suppression` | 提高 MTF/伪影抑制，适当增大颗粒尺寸 |
| 细节被糊掉 | `emulsion_mtf_strength`, `digital_artifact_suppression` | 降低这两个值，尤其对干净相机图 |
| 光晕太硬 | `halation_softness`, `halation_gradient_suppression`, `halation_strength` | 提高软化和边缘补偿，略降强度 |
| 高光绿色/蓝色控制看不出 | `highlight_color_bias`, `highlight_bias_threshold` | 先确认高光亮度能达到阈值；`1.00` 中性，分别调绿色或蓝色增益，避免同时大幅改整体滤色 |
| 高光颜色抢戏 | `highlight_color_bias` | 把相应通道增益拉回 `1.00` 附近，先让整体扫描色彩成立 |

### 工作流建议

调 preset 时建议按这个顺序：

1. 固定输入图，关闭或降低 scan normalize，看底片/扫描基础是否正常。
2. 先调 `film` 和 `chemistry`，生成稳定的 `.npz` 冲洗介质。
3. 固定 `.npz`，只调 `scanner` 和扫描侧 `look`。
4. 最后再调输出格式、尺寸、sidecar、layer pack。

不要一次同时改胶片、冲洗和扫描参数。否则很难判断问题来自底片本身，还是扫描解释。

### 大图

试效果时可以用界面上的边长缩放，或命令行的 `--resize-long-edge`。正式输出再回到原来的尺寸。反复调扫描时，先保存冲洗结果，不要每次都重做冲洗。

<a id="process-fields"></a>

## 工艺程序与扫描解释字段

新的 develop preset 可以在保留现有药水条件的同时声明降阶工艺程序：

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

`program_key` 当前支持 `auto`、`bw_negative`、`color_negative`、`color_negative_bleach_bypass`、`bw_reversal`、`color_reversal` 和诊断用 `legacy_density`。除最后一项外，它们都属于银盐胶片的共享降阶算子层；它们不用于描述拍立得扩散显影、撕拉片接收层或银版工艺。

`first_silver_removal` 只作用于当前黑白反转程序。彩色反转的第一次银像保留至末段，与第二次显影形成的银一起由 `silver_bleach_completion` 和定影步骤去除；不要用该字段伪造彩色反转的实际去银顺序。

`experimental_color_negative_clear_reversal` 是主 GUI、编辑器与 CLI 均可见的实验流程。它在彩色负片的 `color_reversal` 后段使用 `mask_bleach_completion` 插入独立的色罩/染料漂白，使橙色色罩趋近透明支撑体，同时允许产生材料定义的成像染料损伤。该字段不是“更强的银漂白”，不会漂除透明塑料支撑体，也不代表 C-41/E-6 标准流程；默认标准流程中它始终为 `0`。

黑白 `program_key` 描述银显影拓扑，不重定义 film preset 的材料身份。将彩色 film preset 与 `bw_negative` 组合会得到保留彩色材料三层感色和橙色片基的银像负片；使用真正的黑白 film preset 才会得到单层、通常为透明片基的黑白材料。反过来，黑白 film preset 与 `color_negative` / `color_reversal` 仍走统一材料池：成色剂容量为零，因此不会凭空形成染料；标准漂白会去掉已显银像，漂白旁路则保留银像。两者都不应靠修改 scanner preset 相互伪装。

材料 preset 的原生 `medium_process` / `image_polarity` 与 develop preset 的 `program_key` 可以交叉组合。彩负 + `color_reversal`、反转片 + `color_negative`、黑白片 + 彩色程序都是有效交叉冲洗；最终 NPZ 的 `cross_process` 会记录材料与程序的色彩系统和极性。`first_development_completion` / `second_development_completion` 只区分反转程序内两次显影，并继续乘以由时间、温度、浓度、搅动和疲劳推导的全局完成度。

film preset 的 `cross_process_silver_development`、`cross_process_dye_coupling`、`cross_process_activation`、`cross_process_silver_bleach`、`cross_process_halide_fixing`、`cross_process_silver_removal`、`cross_process_dye_stability`、`cross_process_auxiliary_removal` 和 `cross_process_layer_balance` 只在材料类别与程序不匹配时生效。银显影、激活、漂白、定影、去银及附加层清除使用 0–1 的兼容效率；`cross_process_dye_coupling` 与 `cross_process_layer_balance` 是可大于 1 的比率，但仍受材料容量和反应上限约束。`cross_process_layer_balance` 会与 develop preset 的程序自身 `process_layer_balance` 相乘，请勿在两处重复填写同一偏色意图。默认均为中性值，保证旧 preset 兼容；这些字段不应进入 scanner preset。

显式 `program_key` 是形成流水线路由的权威来源。负片程序形成负像介质并声明 `negative_scan`；反转程序形成正像透明介质并声明 `positive_transparency_scan`。`auto` 根据材料 preset 的原生 `medium_process`、模式和旧式 develop 标记解析为同一套正式算子程序，不再回退 legacy。交叉冲洗应通过“材料 preset + 非原生 program preset”的组合表达，而不是修改 scanner preset 来伪造极性。

scanner preset 只拥有扫描和观看字段。即使完整 JSON 中出现 `film` 字段，新格式电子底片/正片在 scan-only 时也会优先使用自身 `optical_observation` 快照；scanner preset 不能借此改变片基、染料吸收或密度上下限。想改变这些材料属性必须重新冲洗并生成新的介质母版。

主 GUI 不再要求选择“负片扫描器”或“正片扫描器”。物理阶段始终是“透明介质 → 透射光源 → 传感器 raw”，用户分别控制 `remove_base_mask`（去片基/色罩）和 `invert_transmission`（反相）。介质契约只显示建议：负片通常开启两项，正片通常关闭两项；保留片基且不反相可直接得到灯台上的负片翻拍。`interpretation_mode=auto|negative|positive` 继续用于读取旧 preset/session，新的 GUI 会保存为 `manual`。

当前五种显式程序与 `auto` 均驱动正式材料池像素计算：负片与反转共享潜影和材料池，程序顺序决定最终正负极性。内置旧创作 preset 即使没有 `program_key` 也会通过默认 `auto` 进入统一路径；只有明确选择 `legacy_density` 才运行旧 H-D/正片代理对照。该模型仍是降阶状态转换，不应等同于严格化学仿真。

### 扫描解释字段

Scanner presets should declare their interpreter role explicitly:

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

Legacy bundled scanner presets still declare `negative_scan` or
`positive_transparency_scan`, but new sessions use explicit
`remove_base_mask` and `invert_transmission` controls. Both observe the same
transmission light and sensor capture. `transmission_scan` denotes a direct
non-inverted observation when metadata needs an explicit role.

旧负片 preset 会迁移为“去片基 + 反相”，旧正片透明片 preset 会迁移为“保留片基 + 不反相”。统一扫描编辑器同时列出两类 preset，并允许用户任意组合去片基与反相；材料的正负极性只用于建议，不再作为执行许可。

Experimental slide presets currently use separate material-side density controls
and light-table viewing controls:

- `positive_highlight_shoulder` / `positive_highlight_shoulder_width`: material-side highlight shoulder. These act while positive density is formed, keeping bright transparent areas from becoming a hard D-min clip.
- `positive_midtone_density`: material-side midtone density lift. This makes slide midtones denser before light-table viewing.
- `positive_shadow_toe` / `positive_shadow_toe_width`: material-side dense-shadow toe. These soften blocked positive shadows without turning the slide prototype into a wide-latitude negative workflow.
- `projection_white_softness`: light-table / projection interpretation rolloff. This softens display white after transmission, without changing the saved electronic positive density.
- `projection_black_adaptation`: light-table / projection interpretation black adaptation. This lifts very dark displayed areas without changing the saved electronic positive density or `light_table_raw`.

实验正片 preset 目前把材料侧密度控制和灯台观看控制分开：

- `positive_highlight_shoulder` / `positive_highlight_shoulder_width`：材料侧高光肩部，在正片密度形成时生效，避免亮部透明区直接硬贴 D-min。
- `positive_midtone_density`：材料侧中间调密度压实，在灯台观看之前改变电子正片密度。
- `positive_shadow_toe` / `positive_shadow_toe_width`：材料侧暗部 toe，缓和正片高密度暗部压死，但不把正片原型改成负片式宽容度。
- `projection_white_softness`：灯台 / 投影观看阶段的白点软滚降，只影响观看解释，不改变保存的电子正片密度。
- `projection_black_adaptation`：灯台 / 投影观看阶段的黑位适应，只提亮显示中的极暗区域，不改变保存的电子正片密度或 `light_table_raw`。

<a id="diagnostic"></a>

## 诊断与事故配方

#### `film/diagnostic_develop_sensitive` + `scanner/diagnostic_flat_scan`

Purpose: diagnostic preset pair for checking whether develop controls are actually changing the negative state and final render.

Characteristics:
- Very steep and narrow H-D curve, so time, temperature, concentration, exhaustion, compensation, and push controls produce visible density shifts.
- Low halation, grain, MTF smoothing, and scanner color styling, so those effects do not hide develop-stage changes.
- Scanner normalization is disabled. This is intentional: automatic black/white correction can flatten the difference between two develop recipes.
- Not intended as a daily creative look. Use it as a calibration target, then move back to normal film/scanner presets after the develop behavior is tuned.

Suggested CLI check:

```bash
python -m film_foundry.app.cli full input_images outputs/diagnostic_develop \
  --film-preset diagnostic_develop_sensitive \
  --develop-preset standard_color_negative \
  --scanner-preset diagnostic_flat_scan \
  --fast
```

### 冲洗事故字段

#### `develop/accident_kelp_light_leak`

Purpose: playful expert-mode accident preset for ruined chemistry, retained silver, murky stain, uneven development, and edge light leaks.

New develop fields:
- `light_leak_strength`: adds directional local edge exposure before latent-state formation; it no longer lights all four edges by default. Process-editor fields can add a secondary shape, external mask, or manual placement without changing this preset's defaults.
- `chemical_stain`: creates a murky, spatial CMY density deposit after material formation; it does not reduce developer activity or already formed silver/dye Dmax.
- `uneven_development`: creates a low-frequency local developer-activity field before material-pool conversion, so silver/dye formation—not a final overlay—becomes uneven.
- `silver_retention`: retains developed image silver by reducing bleach completion; use for bleach bypass, not surface plating.
- `silver_plating`: deposits patchy neutral surface silver after processing; use for mishandled/exhausted rapid monobath accidents.

Film presets may also define `material_degradation` (0–1) plus stock-specific full-strength `degradation_speed_loss_stops`, `degradation_fog_density_rgb`, and `degradation_layer_balance`. Degradation belongs to the material and should not be encoded as developer exhaustion.

Film presets can define `auxiliary_layer_amount` and `auxiliary_layer_density_rgb` for a reduced removable backing/rem-jet pool. Develop presets control its removal with `auxiliary_removal`; incomplete removal remains part of the developed medium, not a scanner effect.

These controls are bounded accident controls. They are meant to produce repeatable ruined-film behavior, not unbounded physical simulation.

<a id="performance"></a>

## 性能与计算策略

### 执行策略

| 策略 | 实现方式 |
| --- | --- |
| 预计算 | 将RGB／Smits基底、感光层响应与观察系数准备成小表 |
| 阶段复用 | 只改扫描时复用已形成介质，材料或曝光变化时重新形成 |
| 合并编辑 | 连续调参只提交最新预览，后台计算保持界面响应 |
| 独立预览 | 交互使用快速预览，正式输出使用所选尺寸与质量 |
| 全图参考、分块执行 | 统一计算片基、曝光和归一化参考，再按行块应用 |
| 减少内存峰值 | 按行积分光谱、复用临时数组、及时释放阶段历史 |
| 批次调度 | 按帧处理，支持取消与原参数恢复 |

潜影、底片与材料母带的压缩保存采用快速无损压缩，减少保存阶段的CPU开销；材料数组与精度不变，文件可能稍大。不压缩选项仍可独立选择。

### 借鉴来源

[SpektraLab的架构](https://github.com/JamesQiu2005/SpektraLab/blob/f1440bd1e22ffe0acf719852c51277b2f1692cb4/ARCHITECTURE.md)启发了设置预计算、已形成介质复用和缓冲生命周期安排。

[Fotufilm的谱表实现](https://github.com/DhaliwalX/fotufilm-engine/blob/c30bf7176bdff85cc3bb8a34453311ebba359d57/Sources/FotufilmCore/SpectralModel.swift)为按配置准备数据、有界缓存与构建协调提供了参考。

### 使用建议

调参时使用自动预览，输出前选择尺寸和质量。只改扫描曲线无需重新冲洗；需要更低开销时，可组合缩放、降阶与质量档位，并减少附属导出。

<a id="acknowledgements"></a>

## 致敬与借鉴说明

感谢以下项目与作者公开研究成果和工程经验：

| 项目／资料 | 借鉴方向 |
| --- | --- |
| [spektrafilm](https://github.com/andreavolpato/spektrafilm) · Andrea Volpato | 胶片、相纸与观察分阶段的光谱摄影研究 |
| [SpektraLab](https://github.com/JamesQiu2005/SpektraLab) | 设置预计算、形成与观察分离、阶段复用和缓冲生命周期；[架构说明](https://github.com/JamesQiu2005/SpektraLab/blob/f1440bd1e22ffe0acf719852c51277b2f1692cb4/ARCHITECTURE.md) |
| [Fotufilm](https://github.com/DhaliwalX/fotufilm-engine) | 按配置构建谱表、有界缓存与重复准备协调；[源码参考](https://github.com/DhaliwalX/fotufilm-engine/blob/c30bf7176bdff85cc3bb8a34453311ebba359d57/Sources/FotufilmCore/SpectralModel.swift) |
| Brian Smits 的 [RGB→光谱方法](https://doi.org/10.1080/10867651.1999.10487511)、[PBRT光谱说明](https://www.pbr-book.org/3ed-2018/Color_and_Radiometry/The_SampledSpectrum_Class) | 七基颜色分解的方法背景；本项目采用自有解析基谱 |

上述摄影项目提供设计参考；Film Foundry 的材料、工艺与计算由自身实现。[桌面性能策略](#performance)介绍具体应用。

也感谢 Python、NumPy、OpenCV、Pillow、Qt／PySide6、LittleCMS、python-lcms2 和 PyAV 的作者与维护者。

项目开发使用AI工具辅助。

<a id="source"></a>

## 源码范围与发布状态

本次公开源码包含桌面Python应用、共享材料与介质模型、预设、测试和图标，以及Android的interchange、kernel、workflow、platform、app五个模块、Gradle wrapper、合成夹具和设备检查工具。手机操作与支持范围见[手机指南](MOBILE_GUIDE.md)，编译要求见[Android说明](../android/README.md)。必要的桌面构建脚本与固定版本配置集中在[构建与打包](../packaging/README.md)；本机签名管理助手、SDK路径、密钥、用户照片和Agent记录不属于公开源码。

桌面版本为 **0.4.0**，Android为 **0.1.0**，见[更新记录](CHANGELOG.md#v040)。Python入口统一为`film_foundry`，历史介质和会话继续可读。桌面目前使用CPU，手机有可回退的GPU协同；两端不承诺逐像素一致。

### 运行与检查

```bash
python run_film_foundry_launcher.py
python run_film_foundry_launcher.py --self-check
python -m pytest
```

### 来源与发布准备

算法署名和许可保存在[第三方声明](../THIRD_PARTY_NOTICES.md)，依赖版本与原始许可材料见[第三方许可目录](../third_party_licenses/README.md)。当前防色带实现已替换为明确署名的lowbias32；旧版本的来源问题不被这次替换追溯消除。Android已记录实际运行时依赖与CameraX原生库，并将许可正文放入APK。发行时需要提供与二进制相符的项目源码及适用的依赖源码材料，不能把未来仓库链接当作尚未发布源码的替代。
