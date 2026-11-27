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
    internal var samplePanel: SamplePanel?=null
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
    private fun sections(root: LinearLayout,labels: List<Pair<String,String>>,sample: View?=null): List<LinearLayout> {
        val bar=LinearLayout(activity).apply { gravity=android.view.Gravity.CENTER_VERTICAL }
        root.addView(HorizontalScrollView(activity).apply { isHorizontalScrollBarEnabled=false;addView(bar) })
        val scroll=ScrollView(activity).apply { isFillViewport=true };val content=column();scroll.addView(content);root.addView(scroll,LinearLayout.LayoutParams(-1,0,1f))
        sample?.let(content::addView)
        val panels=labels.map { column().apply { setPadding(shell.dp(16),shell.dp(8),shell.dp(16),shell.dp(12));content.addView(this) } }
        val tabs=mutableListOf<Button>()
        fun select(index: Int) {
            panels.forEachIndexed { i,panel->panel.visibility=if(i==index) View.VISIBLE else View.GONE }
            tabs.forEachIndexed { i,tab->tab.isSelected=i==index }
            scroll.scrollTo(0,0)
        }
        labels.forEachIndexed { index,label->tabs+=shell.editorTab(bar,label.first,label.second) { select(index) } }
        select(0)
        return panels
    }

    private fun handleBack(dialog: AlertDialog,back: ()->Unit) {
        dialog.setCanceledOnTouchOutside(false)
        if(android.os.Build.VERSION.SDK_INT>=33) {
            val callback=android.window.OnBackInvokedCallback { back() }
            val dispatcher=dialog.onBackInvokedDispatcher
            dispatcher.registerOnBackInvokedCallback(android.window.OnBackInvokedDispatcher.PRIORITY_DEFAULT,callback)
            dialog.setOnDismissListener { dispatcher.unregisterOnBackInvokedCallback(callback) }
        }
        dialog.setOnKeyListener { _,key,event ->
            if(key==android.view.KeyEvent.KEYCODE_BACK) {
                if(event.action==android.view.KeyEvent.ACTION_UP && !event.isCanceled) back()
                true
            } else false
        }
    }
    private fun numericTools(parent: LinearLayout,specs: List<Parameter>,source: Map<String,Any?>,inherit: Map<String,Any?>?,fields: MutableMap<String,EditText>) {
        val controls=linkedMapOf<String,TouchParameterControl>()
        fun fallback(spec: Parameter): Double {
            val inherited=(inherit?.get(spec.key) as? Number)?.toDouble()
            if(inherited!=null) return inherited
            if(spec.key=="agitation_time_s") return (fields["time_min"]?.text?.toString()?.toDoubleOrNull() ?: (inherit?.get("time_min") as? Number)?.toDouble() ?: 8.0)*60
            return spec.initial ?: 0.0
        }
        specs.forEach { spec ->
            val input=field(if(inherit!=null) source[spec.key] else source[spec.key] ?: spec.initial);fields[spec.key]=input
            val control=TouchParameterControl(activity,shell,parent,input,c(spec.zh,spec.en),spec.range,spec.step,
                inheritedLabel=if(inherit!=null) c("全局","Global") else c("标准","Standard"),
                resetLabel=if(inherit!=null) c("沿用全局","Inherit global") else c("使用标准条件","Use standard condition"))
            controls[spec.key]=control;control.refresh(fallback(spec))
        }
        fields["time_min"]?.addTextChangedListener(object: android.text.TextWatcher {
            override fun beforeTextChanged(s: CharSequence?,start: Int,count: Int,after: Int)=Unit
            override fun onTextChanged(s: CharSequence?,start: Int,before: Int,count: Int) { specs.firstOrNull { it.key=="agitation_time_s" }?.let { controls[it.key]?.refresh(fallback(it)) } }
            override fun afterTextChanged(s: android.text.Editable?)=Unit
        })
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
    fun open(source: Map<String,Any?>,material: Map<String,Any?> = emptyMap(),formationProfile: Any?=com.filmfoundry.interchange.PhoneFormationProfile.LIGHTWEIGHT,apply: (Map<String,Any?>)->Unit) {
        // Ask the real process resolver; do not duplicate auto/cross material rules in the UI.
        val inspected=RecipeDrafts.inspectProcess(material,source,formationProfile)
        val suggested=if(inspected.program!="custom") inspected.program else
            RecipeDrafts.inspectProcess(material,(source-"process_steps")+("program_key" to "auto"),formationProfile).program
        val draft=ProcessProgramDraft(source,suggested)
        val root=column().apply { setPadding(shell.dp(12),shell.dp(12),shell.dp(12),shell.dp(8)) }
        shell.note(root,"自由冲洗台 · 调整药浴顺序与条件","Free processing · arrange baths and conditions")
        val draftStatus=shell.note(root,"草稿尚无修改","Draft unchanged")
        var refreshDraftStatus: ()->Unit={}
        lateinit var dialog: AlertDialog
        var editingBath=false
        fun returnToSequence() { (root.parent as? android.view.ViewGroup)?.removeView(root); dialog.setContentView(root); editingBath=false }
        shell.resetSection(root,c("冲洗草稿","Process draft")) { dialog.dismiss();open(source,material,formationProfile,apply) }
        lateinit var snapshot: SampleRecipe
        val sample=samplePanel?.invoke { mapOf("film" to material,"develop" to snapshot()) }
        val pages=sections(root,listOf("工序" to "Sequence","共用药液与条件" to "Shared chemistry & conditions","共用摇晃" to "Shared agitation","共用反应设置" to "Shared reactions","邻接与 DIR" to "Adjacency & DIR","工艺说明" to "Process guide"),sample)
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
        numericTools(pages[0],parameters.take(3),source,null,fields)
        val chemicals=baths(pages[1],source,false)
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
        snapshot=::processValues
        fun rawControls(): Map<String,Any?> = fields.mapValues { it.value.text.toString() }+mapOf(
            "developer_type" to chemical(chemicals.first),"fixer_type" to chemical(chemicals.second),"chemistry" to chemistry.selectedItemPosition)
        val initialControls=rawControls()
        fun hasChanges()=draft.hasChanges(initialControls,rawControls())
        var refreshBathSummary: ()->Unit={}
        refreshDraftStatus={ sample?.refresh();draftStatus.text=if(hasChanges()) c("已修改 · 尚未应用，返回时可选择应用或放弃","Modified · not applied. Back lets you apply or discard.") else c("草稿尚无修改","Draft unchanged") }
        fields.values.forEach { input -> input.addTextChangedListener(object: android.text.TextWatcher {
            override fun beforeTextChanged(s: CharSequence?,start: Int,count: Int,after: Int)=Unit
            override fun onTextChanged(s: CharSequence?,start: Int,before: Int,count: Int) { refreshDraftStatus();refreshBathSummary() }
            override fun afterTextChanged(s: android.text.Editable?)=Unit
        }) }
        listOf(chemicals.first,chemicals.second).forEach { picker -> picker.onItemSelectedListener=object: AdapterView.OnItemSelectedListener {
            override fun onNothingSelected(parent: AdapterView<*>?)=Unit
            override fun onItemSelected(parent: AdapterView<*>?,view: View?,position: Int,id: Long) { refreshDraftStatus();refreshBathSummary() }
        } }
        render={
            refreshDraftStatus()
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
                val summary=shell.note(detail,"","")
                refreshBathSummary={
                    val shared=fields.mapValues { (_,input)->input.text.toString().ifBlank { c("标准条件","Standard") } }+
                        mapOf("developer_type" to chemical(chemicals.first),"fixer_type" to chemical(chemicals.second))
                    val values=ProcessBathSummary.resolve(step,shared)
                    fun describe(value: ProcessBathSummary.Value): String {
                        val key=value.key;val raw=value.value
                        val label=when(key) { "developer_type"->c("显影液","Developer");"fixer_type"->c("定影液","Fixer");"strength"->c("作用比例","Reaction fraction")
                            else->parameters.firstOrNull { it.key==key }?.let { c(it.zh,it.en).substringBefore(" ·") } ?: key }
                        val display=if(key in listOf("developer_type","fixer_type")) {
                            val picker=if(key=="developer_type") chemicals.first else chemicals.second
                            val index=(picker.tag as List<*>).indexOf(raw)
                            if(index>=0) picker.adapter.getItem(index).toString() else raw.toString()
                        } else raw.toString()+when(key) { "time_min"->" min";"temperature_c"->" °C";"concentration"->"×";"agitation_time_s"->" s";else->"" }
                        return "$label $display"
                    }
                    val core=setOf("developer_type","fixer_type","time_min","temperature_c","concentration")
                    val inherited=values.filter { !it.overridden && it.key in core }.joinToString(" · ",transform=::describe)
                    val local=values.filter { it.overridden }.joinToString(" · ",transform=::describe)
                    val invalid=fields.values.any { it.text.isNotBlank() && it.text.toString().toDoubleOrNull()?.isFinite()!=true }
                    summary.text=listOfNotNull(inherited.takeIf { it.isNotEmpty() }?.let { c("沿用共用：","Inherited: ")+it },
                        local.takeIf { it.isNotEmpty() }?.let { c("本浴覆盖：","Bath overrides: ")+it },
                        if(invalid) c("共用条件有未完成输入，应用前需校验。","Shared conditions contain unfinished input; validate before applying.") else null).joinToString("\n")
                };refreshBathSummary()
                shell.shortcut(detail,"编辑这一浴","Edit this bath","药液、条件与作用比例","Chemistry, conditions and reaction fraction") {
                    runCatching { collect(fields).filterValues { it!=null } }.onSuccess { inherited -> editingBath=true
                        editStep(step,inherited,dialog,::returnToSequence,{ candidate ->
                            val values=processValues()
                            val baths=if(com.filmfoundry.interchange.RecipeJson.equivalent(candidate,step)) values else values+mapOf("program_key" to "custom",
                                "process_steps" to draft.steps.mapIndexed { i,row -> if(i==index) candidate else row })
                            mapOf("film" to material,"develop" to baths)
                        }) { edited ->
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
                draft.refreshResolved(RecipeDrafts.inspectProcess(material,values,formationProfile).program)
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
        fun applyDraft(): Boolean = runCatching { val value=processValues();apply(value);dialog.dismiss() }
            .onFailure { error.text=errorText(it) }.isSuccess
        var leavePrompt: AlertDialog?=null
        fun leave() {
            if(!hasChanges()) { dialog.dismiss();return }
            if(leavePrompt?.isShowing==true) return
            val prompt=AlertDialog.Builder(activity).setTitle(c("冲洗草稿尚未应用","Processing changes not applied"))
                .setMessage(c("这些修改还没有带回${if(applyToDevelopment) "本次冲洗" else "配置"}。应用后返回，还是放弃本次修改？","These changes have not reached ${if(applyToDevelopment) "this development" else "the configuration"}. Apply them before returning, or discard them?"))
                .setPositiveButton(c("应用并返回","Apply & return"),null)
                .setNegativeButton(c("放弃修改","Discard")) { _,_->dialog.dismiss() }
                .setNeutralButton(c("继续编辑","Keep editing"),null).create()
            leavePrompt=prompt
            prompt.setOnDismissListener { leavePrompt=null }
            prompt.show()
            prompt.getButton(AlertDialog.BUTTON_POSITIVE).setOnClickListener { applyDraft();prompt.dismiss() }
        }
        val footer=LinearLayout(activity);root.addView(footer)
        shell.action(footer,"返回","Back") { leave() }.layoutParams=LinearLayout.LayoutParams(0,-2,1f).apply { marginEnd=shell.dp(6) }
        shell.action(footer,"样张","Sample") { runCatching { onPreviewRecipe(mapOf("film" to material,"develop" to processValues())) }.onFailure { error.text=errorText(it) } }.layoutParams=LinearLayout.LayoutParams(0,-2,1f).apply { marginEnd=shell.dp(6) }
        shell.action(footer,if(applyToDevelopment) "应用到本次冲洗" else "应用配置",if(applyToDevelopment) "Apply to this development" else "Apply draft",true) { applyDraft() }.layoutParams=LinearLayout.LayoutParams(0,-2,1.5f)
        dialog=show(root)
        handleBack(dialog) { if(editingBath) returnToSequence() else leave() }
    }
    private fun editStep(source: Map<String,Any?>,inherit: Map<String,Any?>,dialog: AlertDialog,back: ()->Unit,preview: (Map<String,Any?>)->Map<String,Any?>,apply: (Map<String,Any?>)->Unit) {
        val root=column().apply { setPadding(shell.dp(12),shell.dp(12),shell.dp(12),shell.dp(8)) }
        shell.note(root,"当前药浴 · 完成后更新工作台草稿","Current bath · Done updates the workbench draft")
        shell.resetSection(root,c("当前药浴草稿","Current bath draft")) { editStep(source,inherit,dialog,back,preview,apply) }
        lateinit var candidate: SampleRecipe
        val sample=samplePanel?.invoke { preview(candidate()) }
        val pages=sections(root,listOf("条件" to "Conditions","药液" to "Chemistry","摇晃" to "Agitation","反应" to "Reaction"),sample)
        val action=Spinner(activity).apply { adapter=ArrayAdapter(activity,android.R.layout.simple_spinner_dropdown_item,actionNames);setSelection(ProcessSteps.actions.indexOf(source["action"])) };pages[1].addView(action)
        val label=EditText(activity).apply { setText(source["label"]?.toString().orEmpty()) };shell.field(pages[1],label,c("药浴名称（可选）","Bath name (optional)"))
        val chemicals=baths(pages[1],source,true);val fields=linkedMapOf<String,EditText>()
        numericTools(pages[0],parameters.take(3),source,inherit,fields)
        numericTools(pages[2],parameters.slice(3..4),source,inherit,fields);agitation(pages[2],fields,(inherit["time_min"] as? Number)?.toDouble() ?: 8.0)
        numericTools(pages[3],listOf(Parameter("strength","作用比例","Reaction fraction",0.0..1.0,.01,1.0))+parameters.drop(5),source,inherit,fields)
        val error=TextView(activity).apply { setTextColor(0xFFDD6860.toInt());maxLines=3 };root.addView(error)
        val footer=LinearLayout(activity);root.addView(footer)
        shell.action(footer,"取消","Cancel") { back() }.layoutParams=LinearLayout.LayoutParams(0,-2,1f).apply { marginEnd=shell.dp(6) }
        candidate={
                val value=source.toMutableMap();value.putAll(collect(fields).filterValues { it!=null });fields.keys.filter { fields.getValue(it).text.isBlank() }.forEach(value::remove)
                value["action"]=ProcessSteps.actions[action.selectedItemPosition]
                if(label.text.isBlank()) value.remove("label") else value["label"]=label.text.toString().trim()
                chemical(chemicals.first)?.let { value["developer_type"]=it } ?: value.remove("developer_type")
                chemical(chemicals.second)?.let { value["fixer_type"]=it } ?: value.remove("fixer_type")
                ProcessSteps.validateConditions(inherit+value);ProcessSteps.read(listOf(value))!!.single()
        }
        if(sample!=null) {
            val watcher=object: android.text.TextWatcher {
                override fun beforeTextChanged(s: CharSequence?,start: Int,count: Int,after: Int)=Unit
                override fun onTextChanged(s: CharSequence?,start: Int,before: Int,count: Int)=sample.refresh()
                override fun afterTextChanged(s: android.text.Editable?)=Unit
            }
            (fields.values+label).forEach { it.addTextChangedListener(watcher) }
            listOf(action,chemicals.first,chemicals.second).forEach { it.onItemSelectedListener=object: AdapterView.OnItemSelectedListener {
                override fun onNothingSelected(parent: AdapterView<*>?)=Unit
                override fun onItemSelected(parent: AdapterView<*>?,view: View?,position: Int,id: Long)=sample.refresh()
            } }
        }
        shell.action(footer,"完成","Done",true) {
            runCatching(candidate).onSuccess { apply(it);back() }.onFailure { error.text=errorText(it) }
        }.layoutParams=LinearLayout.LayoutParams(0,-2,1.5f)
        shell.decorate(root)
        dialog.setContentView(root)
    }
}
