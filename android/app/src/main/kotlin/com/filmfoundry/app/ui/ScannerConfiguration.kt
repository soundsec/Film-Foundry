package com.filmfoundry.app.ui

import android.app.AlertDialog
import android.text.InputType
import android.widget.*
import androidx.activity.ComponentActivity
import com.filmfoundry.interchange.BuiltinRecipeCatalog
import com.filmfoundry.platform.*
import com.filmfoundry.workflow.*
import java.util.UUID

/** Independent scanner definitions; changing a draft never changes a negative or saved photo. */
class ScannerConfiguration(private val activity: ComponentActivity,private val shell: MobileShell,
    private val builtin: BuiltinRecipeCatalog,storage: StorageAdapter,private val current: ()->Map<String,Any?>,
    private val changed: ()->Unit) {
    var onPreviewRecipe: (Map<String,Any?>)->Unit={}
    private val catalog=RecipeCatalog(PrivateRecipeCatalogStore(storage))
    private fun c(zh: String,en: String)=shell.copy(zh,en)
    fun open() {
        val own=catalog.scanners();val visible=builtin.dailyScanners()
        val items=listOf(c("当前扫描配置 · 新建副本","Current scanner · new copy"))+visible.map { c(it.labelZh,it.labelEn)+c(" · 内置"," · built-in") }+own.map { it.label+c(" · 自存"," · saved") }
        AlertDialog.Builder(activity).setTitle(c("扫描预设","Scanner presets")).setItems(items.toTypedArray()) { _,index ->
            runCatching {
                if(index==0) edit(section(current(),"scanner"),c("我的扫描预设","My scanner preset"))
                else if(index<=visible.size) { val item=visible[index-1];edit(section(item.recipe,"scanner"),c(item.labelZh,item.labelEn)) }
                else { val item=own[index-visible.size-1]
                    AlertDialog.Builder(activity).setTitle(item.label).setItems(arrayOf(c("编辑并另存副本","Edit a copy"),c("重命名","Rename"),c("删除预设…","Delete preset…"))) { _,action ->
                        when(action) {
                            0->edit(item.recipe,item.label)
                            1->askConfigurationName(activity,shell,c("重命名扫描预设","Rename scanner preset")) { catalog.rename("scanner",item.id,it);changed() }
                            else->AlertDialog.Builder(activity).setTitle(c("删除这个预设？","Delete this preset?"))
                                .setMessage(c("已保存的底片、照片和冻结任务不受影响。","Saved negatives, photos and frozen tasks stay unchanged."))
                                .setNegativeButton(c("取消","Cancel"),null).setPositiveButton(c("删除预设","Delete preset")) { _,_->catalog.remove("scanner",item.id);changed() }.show()
                        }
                    }.show()
                }
            }.onFailure { Toast.makeText(activity,it.message,Toast.LENGTH_LONG).show() }
        }.show()
    }
    private fun section(recipe: Map<String,Any?>,key: String)=(recipe[key] as? Map<*,*>)?.entries?.associate { it.key.toString() to it.value }.orEmpty()
    private fun edit(source: Map<String,Any?>,name: String) {
        var draft=source.toMap()
        val panel=LinearLayout(activity).apply { orientation=LinearLayout.VERTICAL;setPadding(shell.dp(20),shell.dp(8),shell.dp(20),shell.dp(12)) }
        shell.note(panel,"另存扫描预设，之后在扫描页选择。仅改变观察与解释，不修改底片或已有照片。空白参数沿用原预设。","Save a scanner preset, then select it when scanning. Only readout and interpretation change; negatives and saved photos stay. Blank fields retain the source preset.")
        val title=EditText(activity).apply { setText(name);setSingleLine(true) };shell.field(panel,title,c("预设名称（1–64字）","Preset name (1–64 characters)"))
        val fields=linkedMapOf<String,EditText>()
        val ranges=mapOf("transmission_light_ev" to -8.0..8.0,"transmission_light_temperature_k" to 1000.0..20000.0,
            "scan_saturation" to 0.0..3.0,"scan_normalize_strength" to 0.0..1.0,"scan_black_percentile" to 0.0..100.0,"scan_white_percentile" to 0.0..100.0)
        for((key,zh,en) in listOf(
            Triple("transmission_light_ev","光源曝光补偿 · EV（−8–8）","Light exposure · EV (−8–8)"),
            Triple("transmission_light_temperature_k","光源色温 · K（1000–20000）","Light temperature · K (1000–20000)"),
            Triple("scan_saturation","饱和度 · 倍率（0–3）","Saturation · multiplier (0–3)"),
            Triple("scan_normalize_strength","影调归一化强度（0–1）","Tone normalization strength (0–1)"),
            Triple("scan_black_percentile","黑点 · 百分位（0–100）","Black point · percentile (0–100)"),
            Triple("scan_white_percentile","白点 · 百分位（0–100）","White point · percentile (0–100)"))) {
            fields[key]=EditText(activity).apply { inputType=InputType.TYPE_CLASS_NUMBER or InputType.TYPE_NUMBER_FLAG_DECIMAL or InputType.TYPE_NUMBER_FLAG_SIGNED;setText(source[key]?.toString().orEmpty());shell.field(panel,this,c(zh,en)) }
        }
        val switches=listOf(Triple("remove_base_mask","片基透射校正","Film-base transmission correction"),Triple("scan_normalize","全图影调归一化","Whole-frame normalization"))
            .associate { (key,zh,en)->key to CheckBox(activity).apply { text=c(zh,en);isChecked=source[key]!=false;panel.addView(this) } }
        shell.action(panel,"影调与 RGB 曲线…","Tone & RGB curves…") { editScannerCurves(activity,shell,draft) { draft=draft+it } }
        shell.action(panel,"高级：完整设备与扫描定义…","Advanced: complete scanner definition…") {
            editConfigurationJson(activity,shell,c("扫描定义","Scanner definition"),draft) { value ->
                val values=(value as? Map<*,*>)?.entries?.associate { it.key.toString() to it.value } ?: error("Expected scanner object")
                draft=RecipeDrafts.scanner(values)
                fields.forEach { (key,field)->field.setText(draft[key]?.toString().orEmpty()) };switches.forEach { (key,check)->check.isChecked=draft[key]!=false }
            }
        }
        fun scannerValues(): Map<String,Any?> {
                val label=title.text.toString().trim();require(label.isNotBlank() && label.length<=64)
                val values=draft.toMutableMap()
                fields.forEach { (key,field)->val text=field.text.toString().trim();if(text.isNotEmpty()) {
                    val value=text.toDoubleOrNull()
                    if(value==null || !value.isFinite() || value !in ranges.getValue(key)) {
                        field.error=c("允许范围：${ranges[key]}","Allowed range: ${ranges[key]}");field.requestFocus()
                        throw IllegalArgumentException("${shell.fieldLabel(field)}: ${field.error}")
                    };values[key]=value
                } }
                val black=(values["scan_black_percentile"] as? Number)?.toDouble() ?: 0.5
                val white=(values["scan_white_percentile"] as? Number)?.toDouble() ?: 99.5
                require(black<white) { c("黑点百分位必须小于白点百分位。","Black percentile must be below white percentile.") }
                switches.forEach { (key,check)->values[key]=check.isChecked }
                return RecipeDrafts.scanner(values)
        }
        shell.action(panel,"预览扫描草稿…","Preview scanner draft…") { runCatching { onPreviewRecipe(mapOf("scanner" to scannerValues())) }.onFailure { Toast.makeText(activity,it.message,Toast.LENGTH_LONG).show() } }
        val error=TextView(activity);panel.addView(error)
        lateinit var dialog: AlertDialog
        shell.resetSection(panel,c("扫描预设草稿","Scanner preset draft")) { dialog.dismiss();edit(source,name) }
        dialog=AlertDialog.Builder(activity).setTitle(c("扫描预设编辑器","Scanner preset editor")).setView(ScrollView(activity).apply { addView(panel) })
            .setNegativeButton(c("取消","Cancel"),null).setPositiveButton(c("另存预设","Save new preset"),null).create()
        dialog.setOnShowListener { dialog.getButton(AlertDialog.BUTTON_POSITIVE).setOnClickListener {
            runCatching {
                catalog.saveScanner(UUID.randomUUID().toString(),title.text.toString().trim(),scannerValues());changed()
            }.onSuccess { dialog.dismiss();Toast.makeText(activity,c("扫描预设已保存，可在扫描页选择","Scanner preset saved; select it on the scan page"),Toast.LENGTH_LONG).show() }.onFailure { error.text=it.message }
        } };shell.decorate(panel);dialog.show()
    }
}
