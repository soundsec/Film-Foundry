package com.filmfoundry.app.ui

import android.app.AlertDialog
import android.text.InputType
import android.view.View
import android.widget.*
import androidx.activity.ComponentActivity
import com.filmfoundry.interchange.ProcessSteps
import com.filmfoundry.workflow.RecipeDrafts

/** Transactional bath workbench. No roll or material is changed until the caller applies its draft. */
class ProcessProgramEditor(private val activity: ComponentActivity,private val shell: MobileShell) {
    var onPreviewRecipe: (Map<String,Any?>)->Unit={}
    var applyToDevelopment=false
    private fun c(zh: String,en: String)=shell.copy(zh,en)
    private val actionNames get()=ProcessSteps.actions.map { ProcessPresentation.action(it,c("zh","en")=="en") }
    private data class Parameter(val key: String,val zh: String,val en: String,val range: ClosedFloatingPointRange<Double>,val step: Double,val initial: Double?)
    private val parameters=listOf(
        Parameter("time_min","时间 · 分钟","Duration · minutes",.1..120.0,.1,8.0),
        Parameter("temperature_c","温度 · °C","Temperature · °C",0.0..60.0,.1,20.0),
        Parameter("concentration","浓度倍率","Concentration",.05..4.0,.05,1.0),
        Parameter("agitation_time_s","累计摇晃 · 秒","Active agitation · seconds",0.0..7200.0,1.0,null),
        Parameter("agitation","摇晃强度","Agitation intensity",0.0..3.5,.05,1.0),
        Parameter("developer_exhaustion","显影液耗竭","Developer exhaustion",0.0..1.0,.01,0.0),
        Parameter("fixer_exhaustion","定影液耗竭","Fixer exhaustion",0.0..1.0,.01,0.0),
        Parameter("push_stops","迫冲／减感 · 档","Push / pull · stops",-6.0..6.0,.1,0.0),
        Parameter("compensation","高光补偿","Highlight compensation",0.0..1.0,.01,0.0),
        Parameter("silver_retention","留银比例","Silver retention",0.0..1.0,.01,0.0),
        Parameter("dye_damage_strength","染料损伤","Dye damage",0.0..1.0,.01,0.0))
    private fun column()=LinearLayout(activity).apply { orientation=LinearLayout.VERTICAL }
    private fun field(source: Any?)=EditText(activity).apply {
        inputType=InputType.TYPE_CLASS_NUMBER or InputType.TYPE_NUMBER_FLAG_DECIMAL or InputType.TYPE_NUMBER_FLAG_SIGNED
        setText(source?.toString().orEmpty())
    }
    private class Sections(val panels: List<LinearLayout>,val entrances: LinearLayout?,val atRoot: ()->Boolean,val up: ()->Boolean) {
        operator fun get(index: Int)=panels[index]
    }
    private fun sections(root: LinearLayout,labels: List<Pair<String,String>>,hierarchical: Boolean=false): Sections {
        val bar=LinearLayout(activity).apply { gravity=android.view.Gravity.CENTER_VERTICAL }
        root.addView(HorizontalScrollView(activity).apply { isHorizontalScrollBarEnabled=false;addView(bar) })
        val scroll=ScrollView(activity).apply { isFillViewport=true };val content=column();scroll.addView(content);root.addView(scroll,LinearLayout.LayoutParams(-1,0,1f))
        val panels=labels.map { column().apply { setPadding(shell.dp(16),shell.dp(8),shell.dp(16),shell.dp(12));content.addView(this) } }
        val tabs=mutableListOf<Button>();var selected=0
        val path=TextView(activity).apply { textSize=14f;setPadding(shell.dp(8),0,shell.dp(8),0) }
        lateinit var up: Button
        fun select(index: Int) {
            selected=index
            panels.forEachIndexed { i,panel->panel.visibility=if(i==index) View.VISIBLE else View.GONE }
            tabs.forEachIndexed { i,tab->tab.isSelected=i==index };scroll.scrollTo(0,0)
            if(hierarchical) {
                up.visibility=if(index==0) View.GONE else View.VISIBLE
                path.text=if(index==0) c(labels[0].first,labels[0].second) else "› "+c(labels[index].first,labels[index].second)
            }
        }
        val entrances=if(hierarchical) column().also { links ->
            up=shell.action(bar,"‹ 工序","‹ Sequence") { select(0) };bar.addView(path)
            labels.drop(1).forEachIndexed { n,label -> shell.shortcut(links,label.first,label.second) { select(n+1) } }
        } else null
        if(!hierarchical) labels.forEachIndexed { index,label->tabs+=shell.editorTab(bar,label.first,label.second) { select(index) } }
        select(0)
        return Sections(panels,entrances,{selected==0},{if(selected==0) false else {select(0);true}})
    }
    private fun hierarchicalBack(dialog: AlertDialog,pages: Sections) {
        if(android.os.Build.VERSION.SDK_INT>=33) {
            val callback=android.window.OnBackInvokedCallback { if(!pages.up()) dialog.cancel() }
            val dispatcher=dialog.onBackInvokedDispatcher
            dispatcher.registerOnBackInvokedCallback(android.window.OnBackInvokedDispatcher.PRIORITY_DEFAULT,callback)
            dialog.setOnDismissListener { dispatcher.unregisterOnBackInvokedCallback(callback) }
        }
        dialog.setOnKeyListener { _,key,event ->
            if(key==android.view.KeyEvent.KEYCODE_BACK && !pages.atRoot()) {
                if(event.action==android.view.KeyEvent.ACTION_UP) pages.up()
                true
            } else false
        }
    }
    private fun numericTools(parent: LinearLayout,specs: List<Parameter>,source: Map<String,Any?>,inherit: Map<String,Any?>?,fields: MutableMap<String,EditText>) {
        val bar=LinearLayout(activity);parent.addView(HorizontalScrollView(activity).apply { isHorizontalScrollBarEnabled=false;addView(bar) })
        val bodies=specs.map { column().also(parent::addView) };val tabs=mutableListOf<Button>()
        val controls=linkedMapOf<String,TouchParameterControl>()
        fun fallback(spec: Parameter): Double {
            val inherited=(inherit?.get(spec.key) as? Number)?.toDouble()
            if(inherited!=null) return inherited
            if(spec.key=="agitation_time_s") return (fields["time_min"]?.text?.toString()?.toDoubleOrNull() ?: (inherit?.get("time_min") as? Number)?.toDouble() ?: 8.0)*60
            return spec.initial ?: 0.0
        }
        specs.forEachIndexed { index,spec ->
            val input=field(if(inherit!=null) source[spec.key] else source[spec.key] ?: spec.initial);fields[spec.key]=input
            val control=TouchParameterControl(activity,shell,bodies[index],input,c(spec.zh,spec.en),spec.range,spec.step,
                inheritedLabel=if(inherit!=null) c("全局","Global") else c("标准","Standard"),
                resetLabel=if(inherit!=null) c("沿用全局","Inherit global") else c("使用标准条件","Use standard condition"))
            controls[spec.key]=control;control.refresh(fallback(spec))
            tabs+=shell.editorTab(bar,spec.zh.substringBefore(" ·"),spec.en.substringBefore(" ·")) {
                bodies.forEachIndexed { i,body->body.visibility=if(i==index) View.VISIBLE else View.GONE }
                tabs.forEachIndexed { i,tab->tab.isSelected=i==index }
            }
        }
        fields["time_min"]?.addTextChangedListener(object: android.text.TextWatcher {
            override fun beforeTextChanged(s: CharSequence?,start: Int,count: Int,after: Int)=Unit
            override fun onTextChanged(s: CharSequence?,start: Int,before: Int,count: Int) { specs.firstOrNull { it.key=="agitation_time_s" }?.let { controls[it.key]?.refresh(fallback(it)) } }
            override fun afterTextChanged(s: android.text.Editable?)=Unit
        })
        bodies.forEachIndexed { i,body->body.visibility=if(i==0) View.VISIBLE else View.GONE };tabs.firstOrNull()?.isSelected=true
    }
    private fun baths(panel: LinearLayout,source: Map<String,Any?>,inherit: Boolean): Pair<Spinner,Spinner> {
        fun picker(key: String,zh: String,en: String,ids: List<String>,labels: List<String>): Spinner {
            shell.note(panel,zh,en)
            val extra=(source[key] as? String)?.takeIf { it !in ids }
            val choices=(if(inherit) listOf<String?>(null) else emptyList())+ids+listOfNotNull(extra)
            val names=(if(inherit) listOf(c("沿用全局药液","Inherit global bath")) else emptyList())+labels+listOfNotNull(extra?.let { c("自定义：","Custom: ")+it })
            val selectedId=source[key] as? String ?: "standard"
            return Spinner(activity).apply {
                adapter=ArrayAdapter(activity,android.R.layout.simple_spinner_dropdown_item,names);tag=choices
                setSelection(if(inherit && key !in source) 0 else choices.indexOf(selectedId).coerceAtLeast(0));panel.addView(this)
            }
        }
        return picker("developer_type","显影液","Developer",ProcessSteps.developers,listOf(c("标准","Standard"),c("微粒","Fine grain"),c("补偿型","Compensating"),c("高锐度颗粒型","Acutance grain"),c("高反差","High contrast"),c("迫冲型","Push"),c("衰退药液","Exhausted"),c("单浴","Monobath"))) to
            picker("fixer_type","定影液","Fixer",ProcessSteps.fixers,listOf(c("标准","Standard"),c("快速","Rapid"),c("坚膜","Hardening"),c("单浴","Monobath")))
    }
    private fun chemical(picker: Spinner)=(picker.tag as List<*>)[picker.selectedItemPosition] as? String
    private fun agitation(panel: LinearLayout,fields: Map<String,EditText>,inheritedMinutes: Double=8.0) {
        shell.action(panel,"记录手机摇晃…","Record phone agitation…") {
            recordBathAgitation(activity,shell) { seconds,strength ->
                val limit=(fields["time_min"]?.text?.toString()?.toDoubleOrNull() ?: inheritedMinutes)*60
                fields["agitation_time_s"]?.setText(String.format(java.util.Locale.ROOT,"%.2f",seconds.coerceAtMost(limit)))
                fields["agitation"]?.setText(String.format(java.util.Locale.ROOT,"%.3f",strength))
                if(seconds>limit) Toast.makeText(activity,c("累计摇晃已限制到该药浴时长","Active time was limited to this bath duration"),Toast.LENGTH_LONG).show()
            }
        }
    }
    private fun collect(fields: Map<String,EditText>): Map<String,Any?> = fields.mapValues { (key,input) ->
        val text=input.text.toString().trim();if(text.isEmpty()) null else text.toDoubleOrNull()?.takeIf { it.isFinite() } ?: error("$key: invalid number")
    }
    private fun errorText(error: Throwable): String {
        val raw=error.message ?: c("请检查药浴参数","Check bath settings")
        return if(raw.contains("agitation time exceeds bath duration")) c("累计摇晃不能超过药浴时长，请检查时间与摇晃。","Active agitation cannot exceed bath duration. Check duration and agitation.") else raw
    }
    @Suppress("DEPRECATION")
    private fun show(root: LinearLayout): AlertDialog = AlertDialog.Builder(activity).setView(root).create().also {
        shell.decorate(root);it.show();it.window?.setLayout(android.view.ViewGroup.LayoutParams.MATCH_PARENT,android.view.ViewGroup.LayoutParams.MATCH_PARENT)
        it.window?.setSoftInputMode(android.view.WindowManager.LayoutParams.SOFT_INPUT_ADJUST_RESIZE)
    }
    fun open(source: Map<String,Any?>,material: Map<String,Any?> = emptyMap(),apply: (Map<String,Any?>)->Unit) {
        // Ask the real process resolver; do not duplicate auto/cross material rules in the UI.
        val inspected=RecipeDrafts.inspectProcess(material,source)
        val suggested=if(inspected.program!="custom") inspected.program else
            RecipeDrafts.inspectProcess(material,(source-"process_steps")+("program_key" to "auto")).program
        val draft=ProcessProgramDraft(source,suggested)
        val root=column().apply { setPadding(shell.dp(12),shell.dp(12),shell.dp(12),shell.dp(8)) }
        shell.note(root,"自由冲洗台 · 调整药浴顺序与条件","Free processing · arrange baths and conditions")
        lateinit var dialog: AlertDialog
        shell.resetSection(root,c("冲洗草稿","Process draft")) { dialog.dismiss();open(source,material,apply) }
        val pages=sections(root,listOf("工序" to "Sequence","共用药液与条件" to "Shared chemistry & conditions","共用摇晃" to "Shared agitation","共用反应设置" to "Shared reactions","邻接与 DIR" to "Adjacency & DIR","工艺说明" to "Process guide"),hierarchical=true)
        val free=CheckBox(activity).apply { text=c("自由组合药浴","Custom bath sequence");isChecked=draft.free };pages[0].addView(free)
        val chemistry=Spinner(activity).apply { adapter=ArrayAdapter(activity,android.R.layout.simple_spinner_dropdown_item,listOf(c("常规药液适配","Native chemistry"),c("交叉药液适配（自动程序）","Cross chemistry (automatic program)")));setSelection(if(source["chemistry"]=="cross") 1 else 0) };pages[0].addView(chemistry)
        val automatic=shell.note(pages[0],"当前顺序来自所选工艺。可直接编辑一浴；应用修改后转为自由组合。开启自由组合可增删或调整顺序。","This sequence comes from the selected process. Editing a bath creates a custom sequence when applied. Enable Custom bath sequence to add, remove or reorder baths.")
        val editor=column();pages[0].addView(editor)
        val templateKeys=listOf("color_negative","bw_negative","color_negative_bleach_bypass","color_reversal","bw_reversal")
        val templatePanel=column();editor.addView(templatePanel)
        val templates=Spinner(activity).apply { adapter=ArrayAdapter(activity,android.R.layout.simple_spinner_dropdown_item,listOf(c("彩色负片","Color negative"),c("黑白负片","B&W negative"),c("彩负留银／漂白旁路","Color negative bleach bypass"),c("彩色反转","Color reversal"),c("黑白反转","B&W reversal")));setSelection(templateKeys.indexOf(suggested).coerceAtLeast(0)) };templatePanel.addView(templates)
        val strip=LinearLayout(activity);val stripScroll=HorizontalScrollView(activity).apply { isHorizontalScrollBarEnabled=false;addView(strip) };val detail=column()
        lateinit var render: ()->Unit
        shell.action(templatePanel,"以所选工艺替换草稿步骤…","Replace draft steps from template…") {
            AlertDialog.Builder(activity).setMessage(c("替换当前草稿步骤？原胶卷与已有冲洗不变。","Replace these draft steps? Captures and existing developments stay unchanged."))
                .setNegativeButton(c("取消","Cancel"),null).setPositiveButton(c("替换","Replace")) { _,_->draft.fill(templateKeys[templates.selectedItemPosition]);render() }.show()
        }
        editor.addView(stripScroll);editor.addView(detail)
        val fields=linkedMapOf<String,EditText>()
        val chemicals=baths(pages[1],source,false)
        numericTools(pages[1],parameters.take(3),source,null,fields)
        numericTools(pages[2],parameters.slice(3..4),source,null,fields);agitation(pages[2],fields)
        shell.note(pages[2],"累计秒数不能超过浴时长；留空使用标准摇晃。","Active seconds cannot exceed bath duration; blank uses standard agitation.")
        numericTools(pages[3],parameters.drop(5),source,null,fields)
        val spatial=listOf(Triple("development_adjacency_strength","首显邻接强度 · 0–1","First-development adjacency · 0–1"),
            Triple("development_adjacency_radius","邻接半径／参考短边","Adjacency / reference short edge"),Triple("dir_process_effectiveness","DIR 有效性 · 0–1","DIR effectiveness · 0–1"))
        spatial.forEach { (key,zh,en)->fields[key]=field(source[key]).also { shell.field(pages[4],it,c(zh,en),c("留空沿用材料配置。","Blank inherits material settings.")) } }
        shell.note(pages[5],"药浴按顺序作用于材料。每一步可继承共用条件，或单独选择药液、时间、温度与摇晃。","Baths act on material in order. Each bath can inherit shared conditions or override chemistry, duration, temperature and agitation.")
        shell.note(pages[5],"显影、反转激活、漂白和定影各有作用；药液与材料的交互、耗竭、静置和相邻浴温差可以形成事故。主冲洗页的干净冲洗可关闭自动事故。","Development, reversal activation, bleaching and fixing act separately. Material interactions, exhaustion, still baths and temperature changes between baths can produce incidents. Clean processing on the preparation page disables automatic incidents.")
        shell.note(pages[5],"邻接只改变第一次显影，DIR只改变第一次彩显。设置在开始任务时冻结；已完成结果保持不变。","Adjacency affects the first development; DIR affects the first color development. A task freezes its settings; existing results stay unchanged.")
        fun processValues()=draft.build(collect(fields)+mapOf("developer_type" to chemical(chemicals.first),"fixer_type" to chemical(chemicals.second),"chemistry" to if(chemistry.selectedItemPosition==1) "cross" else "native"))
        render={
            editor.visibility=View.VISIBLE;templatePanel.visibility=if(draft.free) View.VISIBLE else View.GONE
            automatic.visibility=if(draft.free) View.GONE else View.VISIBLE;chemistry.isEnabled=!draft.free
            chemistry.visibility=if(!draft.free && source["program_key"] in listOf(null,"auto","custom")) View.VISIBLE else View.GONE
            strip.removeAllViews();detail.removeAllViews()
            draft.steps.forEachIndexed { index,step ->
                shell.editorTab(strip,"${index+1} · "+(step["label"] ?: actionNames[ProcessSteps.actions.indexOf(step["action"])]),"${index+1} · "+(step["label"] ?: actionNames[ProcessSteps.actions.indexOf(step["action"])])) { draft.select(index);render() }.apply {
                    isSelected=index==draft.selected
                    if(isSelected) post { if(parent===strip && isSelected) stripScroll.smoothScrollTo(left-shell.dp(12),0) }
                }
            }
            val index=draft.selected;val step=draft.steps.getOrNull(index)
            if(step==null) shell.note(detail,"没有药浴。添加一步，或从工艺填充。","No baths yet. Add one or fill from a template.")
            else {
                val title=step["label"] ?: actionNames[ProcessSteps.actions.indexOf(step["action"])]
                shell.note(detail,"第 ${index+1}/${draft.steps.size} 浴 · $title","Bath ${index+1}/${draft.steps.size} · $title")
                val overrides=step.keys.count { it in parameters.map { p->p.key } || it in listOf("developer_type","fixer_type") }
                shell.note(detail,if(overrides==0) "沿用共用药液与条件" else "已单独设置 $overrides 项条件",if(overrides==0) "Uses shared chemistry and conditions" else "$overrides bath-specific settings")
                shell.shortcut(detail,"编辑这一浴","Edit this bath","药液、条件与作用比例","Chemistry, conditions and reaction fraction") {
                    runCatching { collect(fields).filterValues { it!=null } }.onSuccess { inherited -> editStep(step,inherited) { edited ->
                        if(draft.edit(index,edited)) free.isChecked=draft.free
                        render()
                    } }
                        .onFailure { Toast.makeText(activity,errorText(it),Toast.LENGTH_LONG).show() }
                }
                val actions=LinearLayout(activity);detail.addView(actions)
                shell.action(actions,"前移","Earlier") { draft.move(-1);render() }.apply { layoutParams=LinearLayout.LayoutParams(0,-2,1f).apply { marginEnd=shell.dp(6) };isEnabled=draft.free && index>0 }
                shell.action(actions,"后移","Later") { draft.move(1);render() }.apply { layoutParams=LinearLayout.LayoutParams(0,-2,1f).apply { marginEnd=shell.dp(6) };isEnabled=draft.free && index<draft.steps.lastIndex }
                shell.action(actions,"移除","Remove") { draft.remove();render() }.apply { layoutParams=LinearLayout.LayoutParams(0,-2,1f);isEnabled=draft.free }
            }
        }
        shell.action(editor,"＋ 添加药浴","＋ Add bath") {
            if(draft.steps.size>=32) Toast.makeText(activity,c("最多32浴","Maximum 32 baths"),Toast.LENGTH_LONG).show()
            else AlertDialog.Builder(activity).setTitle(c("选择处理作用","Choose process action")).setItems(actionNames.toTypedArray()) { _,index->
                if(!draft.free) { draft.setFree(true,suggested);free.isChecked=true }
                draft.add(ProcessSteps.actions[index]);render()
            }.show()
        }
        val error=TextView(activity).apply { setTextColor(0xFFDD6860.toInt());maxLines=3 }
        fun refreshNative() {
            if(!draft.free) {
                val values=draft.build(mapOf("chemistry" to if(chemistry.selectedItemPosition==1) "cross" else "native"))
                draft.refreshResolved(RecipeDrafts.inspectProcess(material,values).program)
            }
        }
        var chemistryPosition=chemistry.selectedItemPosition
        chemistry.onItemSelectedListener=object: AdapterView.OnItemSelectedListener {
            override fun onNothingSelected(parent: AdapterView<*>?)=Unit
            override fun onItemSelected(parent: AdapterView<*>?,view: View?,position: Int,id: Long) {
                if(position!=chemistryPosition) { chemistryPosition=position;runCatching { refreshNative();render() }.onFailure { error.text=errorText(it) } }
            }
        }
        free.setOnCheckedChangeListener { _,checked ->
            runCatching { draft.setFree(checked,templateKeys[templates.selectedItemPosition]);refreshNative();render() }.onFailure { error.text=errorText(it) }
        };render()
        root.addView(error)
        val footer=LinearLayout(activity);root.addView(footer)
        shell.action(footer,"取消","Cancel") { dialog.dismiss() }.layoutParams=LinearLayout.LayoutParams(0,-2,1f).apply { marginEnd=shell.dp(6) }
        shell.action(footer,"样张","Sample") { runCatching { onPreviewRecipe(mapOf("film" to material,"develop" to processValues())) }.onFailure { error.text=errorText(it) } }.layoutParams=LinearLayout.LayoutParams(0,-2,1f).apply { marginEnd=shell.dp(6) }
        shell.action(footer,if(applyToDevelopment) "应用到本次冲洗" else "应用配置",if(applyToDevelopment) "Apply to this development" else "Apply draft",true) { runCatching { processValues() }.onSuccess { apply(it);dialog.dismiss() }.onFailure { error.text=errorText(it) } }.layoutParams=LinearLayout.LayoutParams(0,-2,1.5f)
        pages.entrances?.let { pages[0].addView(it) }
        dialog=show(root);hierarchicalBack(dialog,pages)
    }
    private fun editStep(source: Map<String,Any?>,inherit: Map<String,Any?>,apply: (Map<String,Any?>)->Unit) {
        val root=column().apply { setPadding(shell.dp(12),shell.dp(12),shell.dp(12),shell.dp(8)) }
        shell.note(root,"当前药浴 · 完成后更新工作台草稿","Current bath · Done updates the workbench draft")
        lateinit var dialog: AlertDialog
        shell.resetSection(root,c("当前药浴草稿","Current bath draft")) { dialog.dismiss();editStep(source,inherit,apply) }
        val pages=sections(root,listOf("药液" to "Chemistry","条件" to "Conditions","摇晃" to "Agitation","反应" to "Reaction"))
        val action=Spinner(activity).apply { adapter=ArrayAdapter(activity,android.R.layout.simple_spinner_dropdown_item,actionNames);setSelection(ProcessSteps.actions.indexOf(source["action"])) };pages[0].addView(action)
        val label=EditText(activity).apply { setText(source["label"]?.toString().orEmpty()) };shell.field(pages[0],label,c("药浴名称（可选）","Bath name (optional)"))
        val chemicals=baths(pages[0],source,true);val fields=linkedMapOf<String,EditText>()
        numericTools(pages[1],parameters.take(3),source,inherit,fields)
        numericTools(pages[2],parameters.slice(3..4),source,inherit,fields);agitation(pages[2],fields,(inherit["time_min"] as? Number)?.toDouble() ?: 8.0)
        numericTools(pages[3],listOf(Parameter("strength","作用比例","Reaction fraction",0.0..1.0,.01,1.0))+parameters.drop(5),source,inherit,fields)
        val error=TextView(activity).apply { setTextColor(0xFFDD6860.toInt());maxLines=3 };root.addView(error)
        val footer=LinearLayout(activity);root.addView(footer)
        shell.action(footer,"取消","Cancel") { dialog.dismiss() }.layoutParams=LinearLayout.LayoutParams(0,-2,1f).apply { marginEnd=shell.dp(6) }
        shell.action(footer,"完成","Done",true) {
            runCatching {
                val value=source.toMutableMap();value.putAll(collect(fields).filterValues { it!=null });fields.keys.filter { fields.getValue(it).text.isBlank() }.forEach(value::remove)
                value["action"]=ProcessSteps.actions[action.selectedItemPosition]
                if(label.text.isBlank()) value.remove("label") else value["label"]=label.text.toString().trim()
                chemical(chemicals.first)?.let { value["developer_type"]=it } ?: value.remove("developer_type")
                chemical(chemicals.second)?.let { value["fixer_type"]=it } ?: value.remove("fixer_type")
                ProcessSteps.validateConditions(inherit+value);ProcessSteps.read(listOf(value))!!.single()
            }.onSuccess { apply(it);dialog.dismiss() }.onFailure { error.text=errorText(it) }
        }.layoutParams=LinearLayout.LayoutParams(0,-2,1.5f)
        dialog=show(root)
    }
}
