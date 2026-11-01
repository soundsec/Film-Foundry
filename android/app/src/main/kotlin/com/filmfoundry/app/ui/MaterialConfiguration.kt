package com.filmfoundry.app.ui

import android.app.AlertDialog
import android.text.InputType
import android.widget.*
import androidx.activity.ComponentActivity
import com.filmfoundry.interchange.BuiltinRecipeCatalog
import com.filmfoundry.platform.PrivateRecipeCatalogStore
import com.filmfoundry.platform.StorageAdapter
import com.filmfoundry.workflow.RecipeCatalog
import com.filmfoundry.workflow.RecipeDrafts
import com.filmfoundry.interchange.RecipeJson
import com.filmfoundry.interchange.SpectralOptics
import com.filmfoundry.interchange.PhoneMaterialPhenomena
import java.util.UUID

/** Independent material draft; never edits a loaded roll or an exposed frame. */
class MaterialConfiguration(private val activity: ComponentActivity,private val shell: MobileShell,
    private val builtin: BuiltinRecipeCatalog,storage: StorageAdapter,private val current: ()->Map<String,Any?>) {
    var onPreviewRecipe: (Map<String,Any?>)->Unit={}
    private val catalog=RecipeCatalog(PrivateRecipeCatalogStore(storage))
    fun editCopy(source: Map<String,Any?>,label: String)=edit(source,label)
    fun open() {
        val picker=MaterialPicker(activity,shell,builtin,catalog.films(),runCatching { RecipeDrafts.filmFromRecipe(current()) }.getOrDefault(emptyMap()))
        val panel=LinearLayout(activity).apply { orientation=LinearLayout.VERTICAL;setPadding(shell.dp(16),shell.dp(8),shell.dp(16),shell.dp(12));addView(picker) }
        fun selected(): Map<String,Any?> { val item=picker.selected ?: error(shell.copy("请选择匹配的材料","Choose a matching material"));return if(item.id=="current") RecipeDrafts.filmFromRecipe(current()) else item.film }
        val dialog=AlertDialog.Builder(activity).setTitle(shell.copy("材料预设","Material presets")).setView(panel)
            .setNegativeButton(shell.copy("取消","Cancel"),null).setNeutralButton(shell.copy("预览","Preview"),null)
            .setPositiveButton(shell.copy("复制并编辑","Copy & edit"),null).create()
        dialog.setOnShowListener {
            dialog.getButton(AlertDialog.BUTTON_NEUTRAL).setOnClickListener { runCatching { onPreviewRecipe(picker.sampleRecipe+("film" to selected())) }.onFailure { Toast.makeText(activity,it.message,Toast.LENGTH_LONG).show() } }
            dialog.getButton(AlertDialog.BUTTON_POSITIVE).setOnClickListener { runCatching { val film=selected();val label=picker.selectedLabel;dialog.dismiss();edit(film,label) }.onFailure { Toast.makeText(activity,it.message,Toast.LENGTH_LONG).show() } }
        };shell.decorate(panel);dialog.show()
    }
    private fun edit(source: Map<String,Any?>,label: String) {
        var draft=source.toMap()
        val panel=LinearLayout(activity).apply { orientation=LinearLayout.VERTICAL;setPadding(shell.dp(16),0,shell.dp(16),0) }
        shell.note(panel,"另存为新材料；已装胶卷不变。颗粒属于材料，冲洗时形成在底片上。","Save a new material; loaded rolls stay unchanged. Material grain forms in the developed negative.")
        val name=EditText(activity).apply { setText(label);setSingleLine(true) };shell.field(panel,name,shell.copy("材料名称","Material name"))
        fun numeric(title: String,value: Any?,parent: LinearLayout=panel): EditText=EditText(activity).also {
            it.inputType=InputType.TYPE_CLASS_NUMBER or InputType.TYPE_NUMBER_FLAG_DECIMAL
            it.setText(value?.toString().orEmpty());shell.field(parent,it,title)
        }
        val contrast=numeric(shell.copy("曝光响应强度 0.25–3（空白沿用）","Exposure response 0.25–3 (blank: keep)"),source["contrast"])
        val grain=numeric(shell.copy("颗粒强度 0–0.25（0关闭；空白装卷时选择）","Grain 0–0.25 (0 off; blank chooses on loading)"),source["grain_strength"])
        val base=source["film_base_density_rgb"] as? List<*>
        val bases=listOf("R","G","B").mapIndexed { i,c -> numeric(shell.copy("片基密度 $c（0–4）","Base density $c (0–4)"),base?.getOrNull(i)) }
        val process=Spinner(activity).apply {
            adapter=ArrayAdapter(activity,android.R.layout.simple_spinner_dropdown_item,listOf(shell.copy("彩色材料","Color material"),shell.copy("黑白材料","Black & white material")))
            setSelection(if(source["color_process"] in listOf("bw","black_white","monochrome")) 1 else 0)
        };shell.note(panel,"材料类型","Material type");panel.addView(process)
        val polarity=Spinner(activity).apply {
            adapter=ArrayAdapter(activity,android.R.layout.simple_spinner_dropdown_item,listOf(shell.copy("负片材料（自动负片程序）","Negative material (automatic negative process)"),shell.copy("反转片材料（自动反转程序）","Reversal material (automatic reversal process)")))
            setSelection(if(source["image_polarity"]=="positive") 1 else 0)
        };shell.note(panel,"原生极性／材料用途","Native polarity / stock intent");panel.addView(polarity)
        shell.note(panel,"材料用途决定自动冲洗程序；片基密度独立配置，自由冲洗可交叉搭配。","Material intent selects the automatic process. Base density is configured separately; free processing allows cross-pairing.")
        val spectralStatus=TextView(activity);panel.addView(spectralStatus)
        fun caption() { spectralStatus.text=shell.copy("曝光谱：","Exposure spectrum: ")+(draft["spectral_exposure_profile"] as? Map<*,*>)?.get("model").let { it ?: shell.copy("矩阵","Matrix") }+
            shell.copy("；材料吸收谱：","; material spectrum: ")+(if(draft[SpectralOptics.MATERIAL_KEY] == null) shell.copy("关闭","off") else shell.copy("已定义","defined")) }
        caption()
        val expert=CheckBox(activity).apply { text=shell.copy("展开高级材料定义","Show advanced material definitions") };panel.addView(expert)
        val advanced=LinearLayout(activity).apply { orientation=LinearLayout.VERTICAL;visibility=android.view.View.GONE };panel.addView(advanced)
        expert.setOnCheckedChangeListener { _,checked -> advanced.visibility=if(checked) android.view.View.VISIBLE else android.view.View.GONE }
        fun advancedNumber(key: String,zh: String,en: String)=numeric(shell.copy(zh,en),source[key],advanced)
        val mtf=advancedNumber("emulsion_mtf_strength","MTF 柔化强度 0–1（0关闭）","MTF softness 0–1 (0 off)")
        val mtfRadius=advancedNumber("emulsion_blur_radius","MTF 半径／参考短边 0–0.05","MTF radius / reference short edge 0–0.05")
        val halo=advancedNumber("halation_strength","Halation 强度 0–2（0关闭）","Halation strength 0–2 (0 off)")
        val haloThreshold=advancedNumber("halation_threshold","高光阈值 0–16","Highlight threshold 0–16")
        val haloSoftness=advancedNumber("halation_softness","高光过渡宽度 0.001–16","Highlight transition width .001–16")
        val haloCore=advancedNumber("halation_core_radius","光晕核心半径／参考短边 0–0.25","Halation core / reference short edge 0–.25")
        val haloOuter=advancedNumber("halation_outer_radius","光晕外圈半径／参考短边 0–0.25","Halation outer / reference short edge 0–.25")
        val dirRadius=advancedNumber("dir_diffusion_radius","DIR 扩散半径／参考短边（默认0.003）","DIR diffusion / reference short edge (default .003)")
        shell.note(advanced,"银粒只作用于保留银影，和染料颗粒独立；充分漂白后自动不产生银粒。片基光导从指定材料边缘进入，不是随机相机漏光。乳剂高光扩散在感光层内，层选择性光晕是片基回返。新曝光设置只用于之后装填的卷。","Silver grain belongs to retained silver, separately from dye grain; full bleaching removes its source. Light piping enters declared material edges, separately from camera leaks. Emulsion spread stays within layers; selective halation is support return. New exposure settings apply to subsequently loaded rolls.")
        val phenomenonNumbers=listOf(
            Triple("silver_grain_strength","独立银粒强度 0–0.5（0关闭）","Independent silver grain 0–.5 (0 off)"),
            Triple("silver_grain_radius","银粒半径／参考短边 0–0.1","Silver radius / reference short edge 0–.1"),
            Triple("silver_grain_clump_mix","银粒团聚混合 0–1","Silver clump mix 0–1"),
            Triple("light_piping_strength","片基光导强度 0–2（需选择边缘）","Light piping 0–2 (choose an edge)"),
            Triple("light_piping_depth","片基光导深入／参考短边 0.000001–1","Piping depth / reference short edge .000001–1"),
            Triple("emulsion_highlight_spread_strength","乳剂高光扩散 0–2（0关闭）","Emulsion highlight spread 0–2 (0 off)"),
            Triple("emulsion_highlight_spread_radius","乳剂扩散半径／参考短边 0.000001–0.1","Emulsion radius / reference short edge .000001–.1"),
            Triple("emulsion_highlight_spread_threshold","乳剂高光阈值 0–16","Emulsion highlight threshold 0–16"),
            Triple("emulsion_highlight_spread_softness","乳剂高光过渡 0.001–16","Emulsion highlight transition .001–16"))
        val phenomenonFields=phenomenonNumbers.associate { (key,zh,en)->key to advancedNumber(key,zh,en) }
        val haloModels=listOf("compatibility_rgb","layer_selective")
        shell.note(advanced,"光晕回返方式","Halation return mode")
        val haloModel=Spinner(activity).apply { adapter=ArrayAdapter(activity,android.R.layout.simple_spinner_dropdown_item,listOf(shell.copy("RGB 暖色回返","Warm RGB return"),shell.copy("直接感光层回返","Direct layer return")));setSelection(haloModels.indexOf(source["halation_return_model"] ?: "compatibility_rgb").coerceAtLeast(0)) };advanced.addView(haloModel)
        shell.note(advanced,"片基光导边缘（按最终材料方向）","Light-piping edge (final material orientation)")
        val pipingEdge=Spinner(activity).apply { adapter=ArrayAdapter(activity,android.R.layout.simple_spinner_dropdown_item,listOf(shell.copy("关闭／无指定边缘","Off / no declared edge"),shell.copy("上","Top"),shell.copy("右","Right"),shell.copy("下","Bottom"),shell.copy("左","Left"),shell.copy("两条长边","Long edges"),shell.copy("两条短边","Short edges"),shell.copy("所有边缘","All edges")));setSelection(PhoneMaterialPhenomena.LIGHT_PIPING_EDGES.indexOf(source["light_piping_edge_mode"] ?: "none").coerceAtLeast(0)) };advanced.addView(pipingEdge)
        fun jsonField(key: String,zh: String,en: String,fallback: Any?=null) {
            shell.action(advanced,zh,en) { editConfigurationJson(activity,shell,shell.copy(zh,en),draft[key] ?: fallback) { value ->
                val candidate=draft+(key to value)+( "color_process" to if(process.selectedItemPosition == 1) "bw" else "color_negative")
                draft=RecipeDrafts.material(candidate);caption()
            } }
        }
        jsonField("grain_scales","颗粒尺度混合…","Grain scales…",listOf(.0012,.0035,.010))
        jsonField("grain_scale_weights","颗粒尺度权重…","Grain scale weights…",listOf(.70,.22,.08))
        jsonField("halation_color","Halation 回返颜色…","Halation return color…",listOf(1.0,.42,.16))
        jsonField("halation_layer_return_weights","光晕：感光层回返权重…","Halation layer return weights…",listOf(1.0,.42,.16))
        jsonField("halation_spread_scale_weights","光晕：紧凑／主场／宽泛混合…","Halation compact / main / veil mix…",listOf(0.0,1.0,0.0))
        jsonField("light_piping_layer_weights","片基光导：感光层权重…","Light-piping layer weights…",listOf(1.0,.45,.18))
        jsonField("emulsion_highlight_spread_layer_radius_scales","乳剂扩散：层半径倍率…","Emulsion spread layer radius scales…",listOf(.85,1.0,1.15))
        jsonField("emulsion_highlight_spread_layer_strengths","乳剂扩散：层强度…","Emulsion spread layer strengths…",listOf(.9,1.0,1.08))
        val identity=List(3) { y -> List(3) { x -> if(x == y) 1.0 else 0.0 } }
        jsonField("layer_sensitivity_matrix","层感光矩阵…","Layer sensitivity matrix…",identity)
        jsonField("dye_absorption_matrix","染料吸收矩阵…","Dye absorption matrix…",identity)
        jsonField("dir_material_response_matrix","DIR 源层→目标层矩阵…","DIR source → target layer matrix…",List(3) { List(3) { 0.0 } })
        shell.note(advanced,"DIR 为彩色材料的层间抑制；矩阵行是源层，列是目标层，每列总和不超过0.95。零矩阵关闭；反转程序默认不启用，可在自由冲洗台设置有效性。手机使用有界全幅工作网格。","DIR inhibits color-layer development: rows are sources, columns targets, each column sums to at most .95. Zero disables it. Reversal defaults to off; override effectiveness in the processing workbench. Phone uses a bounded full-frame grid.")
        jsonField("clear_support_density_rgb","支撑片基颜色／密度 RGB…","Support base color / RGB density…",listOf(.04,.04,.04))
        jsonField("retained_halide_density_rgb","残余卤化银密度…","Residual halide density…",listOf(0.0,0.0,0.0))
        shell.note(advanced,"交叉兼容性属于材料本身；不同药液顺序与性格会读取这些值。下面定义的是创作响应，不是品牌胶卷的实测常数。","Cross compatibility belongs to the material. Bath order and character use these responses. These are creative definitions, not measured branded-stock constants.")
        jsonField("cross_process_layer_balance","交叉冲洗：三层响应比例…","Cross process: layer reaction balance…",listOf(1.0,1.0,1.0))
        val reactions=listOf(
            Triple("cross_process_silver_development","交叉显影效率 0–1","Cross development efficiency 0–1"),
            Triple("cross_process_dye_coupling","交叉染料耦合 0–2","Cross dye coupling 0–2"),
            Triple("cross_process_activation","交叉反转激活 0–1","Cross reversal activation 0–1"),
            Triple("cross_process_halide_fixing","交叉定影效率 0–1","Cross fixing efficiency 0–1"),
            Triple("cross_process_dye_stability","交叉染料稳定性 0–1","Cross dye stability 0–1"),
            Triple("cross_process_auxiliary_removal","交叉附加层清除 0–1","Cross auxiliary removal 0–1"),
            Triple("experimental_mask_bleach_susceptibility","片基色罩漂白敏感度 0–1","Base-mask bleach susceptibility 0–1"),
            Triple("experimental_mask_bleach_dye_damage","片基漂白伴随染料损伤 0–1","Mask-bleach dye damage 0–1"))
        val reactionFields=reactions.associate { (key,zh,en)->key to advancedNumber(key,zh,en) }
        jsonField("hd_gamma","材料层响应斜率…","Material layer response slopes…",listOf(.62,.62,.62))
        jsonField("log_exposure_toe","材料层趾部曝光…","Material layer toe exposures…",listOf(-2.1,-2.1,-2.1))
        jsonField("log_exposure_shoulder","材料层肩部曝光…","Material layer shoulder exposures…",listOf(.6,.6,.6))
        jsonField("density_max","材料层密度容量…","Material layer density capacities…",listOf(2.0,2.0,2.0))
        jsonField("auxiliary_layer_density_rgb","附加层／背层密度…","Auxiliary / backing density…",listOf(.08,.07,.06))
        shell.action(advanced,"使用 Smits 创作曝光谱","Use Smits creative exposure basis") {
            runCatching {
                val profiles=activity.assets.open("phone_smits_profiles.json").use { RecipeJson.objectMap(it.readBytes().toString(Charsets.UTF_8)) }
                draft=draft+("spectral_exposure_profile" to profiles[if(process.selectedItemPosition == 1) "1" else "3"]);caption()
            }.onFailure { Toast.makeText(activity,it.message,Toast.LENGTH_LONG).show() }
        }
        shell.note(advanced,"Smits 使用本项目解析创作基底，不是实测胶片或真实场景光谱的唯一恢复。改变黑白／彩色层数后，应重新选择对应曝光谱。","Smits uses this project's analytic creative basis. It is not measured stock data or a unique scene spectrum. Re-select the basis after changing the layer count.")
        jsonField("spectral_exposure_profile","编辑曝光光谱 JSON…","Exposure spectrum JSON…")
        shell.action(advanced,"关闭曝光谱，使用层矩阵","Disable spectrum, use layer matrix") { draft=draft+("spectral_exposure_profile" to null);caption() }
        jsonField(SpectralOptics.MATERIAL_KEY,"材料吸收谱 JSON…","Material absorbance JSON…",mapOf(
            "model" to "normalized_pool_absorbance_v1","profile_id" to "custom_absorbance",
            "coefficient_unit" to "normalized_final_pool_v1","component_names" to SpectralOptics.components,
            "wavelengths_nm" to listOf(400,450,500,550,600,650,700),"component_absorbance" to List(9) { List(7) { 0.0 } }))
        shell.note(advanced,"新建吸收谱是全零草稿，需要自行编辑。关闭材料谱可填写 null；RGB 光学母版始终保留。","New absorbance data starts at zero and needs editing. Use null to disable the material spectrum. The RGB optical master is retained.")
        fun materialValues(): Map<String,Any?> {
                val text=name.text.toString().trim();require(text.isNotBlank() && text.length <= 64)
                fun number(field: EditText,range: ClosedFloatingPointRange<Double>): Double {
                    val v=field.text.toString().toDoubleOrNull()
                    if(v==null || !v.isFinite() || v !in range) {
                        field.error=shell.copy("请输入 $range 范围内的数值","Enter a value within $range");field.requestFocus()
                        error("${shell.fieldLabel(field)}: ${field.error}")
                    };return v
                }
                val film=draft.toMutableMap();film["name"]=text;film["color_process"]=if(process.selectedItemPosition == 1) "bw" else "color_negative"
                film["image_polarity"]=if(polarity.selectedItemPosition==1) "positive" else "negative"
                if(contrast.text.isNotBlank()) film["contrast"]=number(contrast,.25..3.0)
                if(grain.text.isNotBlank()) film["grain_strength"]=number(grain,0.0..0.25)
                for((key,field) in reactionFields) if(field.text.isNotBlank()) film[key]=number(field,0.0..if(key=="cross_process_dye_coupling") 2.0 else 1.0)
                for((key,field,range) in listOf(
                    Triple("emulsion_mtf_strength",mtf,0.0..1.0),Triple("emulsion_blur_radius",mtfRadius,0.0..0.05),
                    Triple("halation_strength",halo,0.0..2.0),Triple("halation_threshold",haloThreshold,0.0..16.0),
                    Triple("halation_softness",haloSoftness,0.001..16.0),Triple("halation_core_radius",haloCore,0.0..0.25),Triple("halation_outer_radius",haloOuter,0.0..0.25)))
                    if(field.text.isNotBlank()) film[key]=number(field,range)
                if(dirRadius.text.isNotBlank()) film["dir_diffusion_radius"]=number(dirRadius,0.000001..1.0)
                for((key,field) in phenomenonFields) if(field.text.isNotBlank()) film[key]=field.text.toString().toDoubleOrNull() ?: error(key)
                film["halation_return_model"]=haloModels[haloModel.selectedItemPosition]
                film["light_piping_edge_mode"]=PhoneMaterialPhenomena.LIGHT_PIPING_EDGES[pipingEdge.selectedItemPosition]
                if(bases.any { it.text.isNotBlank() }) film["film_base_density_rgb"]=bases.map { number(it,0.0..4.0) }
                return RecipeDrafts.material(film)
        }
        shell.action(panel,"预览材料草稿…","Preview material draft…") { runCatching { onPreviewRecipe(mapOf("film" to materialValues())) }.onFailure { Toast.makeText(activity,it.message,Toast.LENGTH_LONG).show() } }
        lateinit var dialog: AlertDialog
        shell.resetSection(panel,shell.copy("材料草稿","Material draft")) { dialog.dismiss();edit(source,label) }
        dialog=AlertDialog.Builder(activity).setTitle(shell.copy("材料配置","Material configuration"))
            .setView(ScrollView(activity).apply { addView(panel) }).setNegativeButton(shell.copy("取消","Cancel"),null)
            .setPositiveButton(shell.copy("另存材料","Save new material"),null).create()
        dialog.setOnShowListener { dialog.getButton(AlertDialog.BUTTON_POSITIVE).setOnClickListener {
            runCatching {
                catalog.saveFilm(UUID.randomUUID().toString(),name.text.toString().trim(),materialValues())
            }.onSuccess { dialog.dismiss();Toast.makeText(activity,shell.copy("材料已保存，可在装卷时选择","Material saved; choose it when loading film"),Toast.LENGTH_LONG).show() }
                .onFailure { name.error=it.message ?: shell.copy("参数无效","Invalid values") }
        } };shell.decorate(panel);dialog.show()
    }
}
