package com.filmfoundry.app.ui

import android.app.AlertDialog
import android.widget.*
import androidx.activity.ComponentActivity
import com.filmfoundry.interchange.BuiltinRecipeCatalog
import com.filmfoundry.workflow.*

/** Shared touch picker for loading and material configuration. Stable IDs survive filtering. */
class MaterialPicker(private val activity: ComponentActivity,private val shell: MobileShell,builtins: BuiltinRecipeCatalog,
    own: List<NamedRecipe>,current: Map<String,Any?>): LinearLayout(activity) {
    data class Choice(val id: String,val name: String,val film: Map<String,Any?>,val origin: String)
    private val all=builtins.materials.map { Choice("builtin:"+it.id,shell.copy(it.labelZh,it.labelEn),it.recipe["film"] as Map<String,Any?>,"official") }+
        listOf(Choice("current",shell.copy("当前分享码／默认材料","Current share / default stock"),current,"custom"))+
        own.map { Choice("saved:"+it.id,it.label.ifBlank { it.id },it.recipe,"custom") }
    private val sampleRecipes=com.filmfoundry.app.session.SeasonalUnlock(activity).let { unlock ->
        if(unlock.unlocked) unlock.pairings().associate { ("builtin:"+it.materialId) to it.recipe } else emptyMap()
    }
    private val daily=builtins.dailyMaterials().map { "builtin:"+it.id }.toSet()
    private val revealed=mutableSetOf<String>()
    private val buttons=List(3) { Button(activity) }
    private val choose=Button(activity)
    private var sampleExecutor: java.util.concurrent.ExecutorService?=null
    private var sampleWork: LatestViewWork?=null
    private val sampleCache=CameraEffectsPreviewCache(4L*1048576)
    private val count=TextView(activity)
    private var filter=MaterialSelection.Filter()
    private var visible=emptyList<Choice>()
    private var selectedId: String?=null
    var onChanged: (Choice?)->Unit={}
    val selected: Choice? get()=visible.firstOrNull { it.id==selectedId }
    val sampleRecipe: Map<String,Any?> get()=sampleRecipes[selected?.id].orEmpty()
    val selectedLabel: String get()=selected?.let(::label).orEmpty()
    init {
        orientation=VERTICAL
        val row=LinearLayout(activity);addView(row)
        buttons.forEachIndexed { i,b -> row.addView(b,LayoutParams(0,-2,1f));shell.styleButton(b);b.textSize=12f;b.setOnClickListener { chooseFilter(i) } }
        addView(choose);addView(count);shell.styleButton(choose)
        choose.maxLines=2;choose.setOnClickListener { chooseMaterial() }
        refresh(null)
    }
    private fun c(zh: String,en: String)=shell.copy(zh,en)
    private fun label(item: Choice)=MaterialSelection.displayName(item.film,item.name,c("zh","en")=="en")
    private fun refresh(keep: String?) {
        visible=all.filter { (it.origin!="official" || it.id in daily || it.id in revealed) && filter.accepts(it.film,it.origin) }.sortedBy { val tags=MaterialSelection.tags(it.film);(if(tags.polarity=="positive") 2 else 0)+(if(tags.color=="bw") 1 else 0) }
        buttons[0].text=when(filter.polarity) { "negative"->c("负片 ▾","Negative ▾");"positive"->c("反转片 ▾","Reversal ▾");else->c("胶卷类型 ▾","Stock type ▾") }
        buttons[1].text=when(filter.color) { "bw"->c("黑白 ▾","B&W ▾");"color"->c("彩色 ▾","Color ▾");else->c("色彩 ▾","Color type ▾") }
        buttons[2].text=when(filter.origin) { "official"->c("官方 ▾","Official ▾");"custom"->c("自定义 ▾","Custom ▾");else->c("来源 ▾","Source ▾") }
        buttons[0].isActivated=filter.polarity!="all";buttons[1].isActivated=filter.color!="all";buttons[2].isActivated=filter.origin!="all"
        count.text=c("${visible.size} 种材料","${visible.size} materials")
        select(visible.firstOrNull { it.id==keep }?.id ?: visible.firstOrNull()?.id)
    }
    private fun chooseFilter(index: Int) {
        val values=when(index) { 0->listOf("all","negative","positive");1->listOf("all","color","bw");else->listOf("all","official","custom") }
        val labels=when(index) { 0->listOf(c("全部胶卷类型","All stock types"),c("负片","Negative"),c("反转片","Reversal"));1->listOf(c("彩色与黑白","Color and B&W"),c("彩色","Color"),c("黑白","B&W"));else->listOf(c("全部来源","All sources"),c("官方预设","Official presets"),c("自定义／分享材料","Custom / shared materials")) }
        val current=when(index) { 0->filter.polarity;1->filter.color;else->filter.origin }
        AlertDialog.Builder(activity).setTitle(c("筛选材料","Filter materials")).setSingleChoiceItems(labels.toTypedArray(),values.indexOf(current)) { dialog,i ->
            val keep=selected?.id;filter=when(index) { 0->filter.copy(polarity=values[i]);1->filter.copy(color=values[i]);else->filter.copy(origin=values[i]) }
            refresh(keep);dialog.dismiss()
        }.setNegativeButton(c("取消","Cancel"),null).show()
    }
    fun selectBuiltIn(id: String) {
        val key="builtin:$id";require(all.any { it.id==key })
        revealed+=key;filter=MaterialSelection.Filter();refresh(key)
    }
    private fun refreshChoice() { choose.text=selectedLabel.ifBlank { c("选择材料","Choose stock") };choose.isEnabled=selected!=null }
    private fun select(id: String?) {
        selectedId=id
        refreshChoice()
        onChanged(selected)
    }
    private fun chooseMaterial() {
        val options=visible.map { item ->
            val tags=MaterialSelection.tags(item.film)
            VisualPresetPicker.Choice(item.id,label(item),(if(tags.color=="bw") c("黑白","B&W") else c("彩色","Color"))+" · "+
                (if(tags.polarity=="positive") c("反转片","Reversal") else c("负片","Negative"))+(if(item.id in sampleRecipes) c(" · 专属搭配样张"," · Paired sample") else c(" · 标准冲洗示意"," · Standard-process sample")))
        }
        if(sampleWork==null) {
            sampleExecutor=java.util.concurrent.Executors.newSingleThreadExecutor { task -> Thread({ android.os.Process.setThreadPriority(android.os.Process.THREAD_PRIORITY_BACKGROUND);task.run() },"FilmFoundry-stock-samples") }
            sampleWork=LatestViewWork(sampleExecutor!!) { activity.runOnUiThread(it) }
        }
        val sampler=sampleWork!!
        VisualPresetPicker(activity,shell).show(c("选择材料","Choose stock"),"material",options,selected?.id,
            samples={ items,deliver ->
                sampler.submit({ stale ->
                    val control=WorkflowControl(cancelRequested=stale)
                    for(item in items) {
                        control.checkCancel()
                        val bitmap=runCatching {
                            val film=all.first { it.id==item.id }.film
                            val rgb=sampleCache.render(CameraEffectsPreview.Request(film,selected=sampleRecipes[item.id].orEmpty(),longEdge=160),emptyMap(),check=control::checkCancel)
                            observationBitmap(OpticalPreviewSession.Observation(rgb.shape[1],rgb.shape[0],rgb.values,rgb.shape[1],rgb.shape[0],emptyMap()),control)
                        }.getOrElse { control.checkCancel();null }
                        activity.runOnUiThread { if(stale() || activity.isDestroyed) bitmap?.recycle() else deliver(item.id,bitmap) }
                    }
                },{ items.forEach { deliver(it.id,null) } })
                ({ sampler.cancel() })
            },selected={},closed={ accepted,item ->
                if(accepted && item!=null && visible.any { it.id==item.id }) select(item.id)
            })
    }
    override fun onDetachedFromWindow() {
        sampleWork?.cancel();sampleExecutor?.shutdownNow();sampleExecutor=null;sampleWork=null;sampleCache.clear()
        super.onDetachedFromWindow()
    }
}
