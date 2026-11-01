package com.filmfoundry.app.ui

import android.text.InputType
import android.widget.*
import androidx.activity.ComponentActivity
import com.filmfoundry.interchange.BuiltinRecipeCatalog
import com.filmfoundry.workflow.frozenRecipe
import com.filmfoundry.workflow.*
import com.filmfoundry.platform.*
import com.filmfoundry.interchange.RecipeShareCodec
import java.util.UUID
import com.filmfoundry.app.session.ProcessingIncidentDraft

/** Parameter editing only; validation and execution still belong to the shared workflow. */
class StageRecipeControls(private val activity: ComponentActivity,private val shell: MobileShell,
    private var builtins: BuiltinRecipeCatalog,private val storage: StorageAdapter,private val current: ()->Map<String,Any?>) {
    private val outputFormat=Button(activity)
    private val outputQuality=Button(activity)
    private val automaticOutput=Button(activity)
    var onAutomaticOutputChanged: ()->Unit={}
    fun automaticOutputSettings()=if(scanHistory!=null) ScanOutputSettings.from(checkNotNull(sharedScanDraft)) else delivery
    fun automaticOutputLabel(): String {
        val value=automaticOutputSettings()
        return (if(value.format=="JPEG") "JPG" else value.format)+" · ${value.actualBits} bit"+(if(value.format=="JPEG") " · Q${value.jpegQuality}" else "")
    }
    private val prefs=activity.getSharedPreferences("stage_controls",0)
    private var delivery=ScanOutputSettings(prefs.getString("output_format","PNG") ?: "PNG",
        prefs.getInt("output_bits",if(prefs.getInt("output_quality",1)==0) 8 else 16),
        ScanOutputSettings.jpegLevels.minBy { kotlin.math.abs(it-prefs.getInt("jpeg_quality",100)) })
    private fun qualityLabels(value: ScanOutputSettings)=when(value.format) {
        "JPEG" -> listOf(copy("低 · 40","Low · 40"),copy("中 · 70","Medium · 70"),copy("高 · 100","High · 100"),copy("沿用原质量 · ${value.jpegQuality}","Original quality · ${value.jpegQuality}"))
        "TIFF" -> listOf("8 bit","16 bit")
        else -> listOf(copy("标准 · PNG 8位","Standard · PNG 8-bit"),copy("精细 · PNG 16位","Fine · PNG 16-bit"))
    }
    private fun paintDelivery() {
        outputFormat.text=(if(delivery.format=="JPEG") "JPG" else delivery.format)+" ▾"
        outputQuality.text=qualityLabels(delivery)[delivery.qualityIndex]+(if(delivery.format=="TIFF") copy(" · 固定"," · fixed") else " ▾")
        outputFormat.isEnabled=!scanBusy;outputQuality.isEnabled=!scanBusy && delivery.format!="TIFF"
        automaticOutput.text=copy("自动扫描产出：","Auto-scan output: ")+automaticOutputLabel()+" ▾"
    }
    var rotationDegrees=OutputOrientation.normalized(prefs.getInt("output_rotation_degrees",0));private set
    fun rotateOutput() { if(!scanBusy) scanEdit { rotationDegrees=OutputOrientation.normalized(rotationDegrees+90);updateOutputInformation() } }
    private var preferLoadedRecipe=prefs.getBoolean("prefer_loaded_recipe",false)
    private val time=EditText(activity)
    private val cross=CheckBox(activity)
    private val chemistryOverride=CheckBox(activity)
    private val chemistryGroup=LinearLayout(activity).apply { orientation=LinearLayout.VERTICAL }
    private val processChoice=Button(activity)
    private val processSequence=TextView(activity)
    private val processAdjustments=TextView(activity)
    private var selectDevelopment: (Int)->Unit={}
    private var developmentSection=0
    fun backDevelopment(): Boolean {
        if(developmentSection==0) return false
        selectDevelopment(0);return true
    }
    var onResumeDevelopment: ()->Unit={}
    private var processes=emptyList<NamedProcessRecipe>()
    private var userScanners=emptyList<NamedRecipe>()
    private var visibleScanners=builtins.scanners
    var onPreviewRecipe: (Map<String,Any?>)->Unit={}
    var onStyleSamples: (List<Pair<String,Map<String,Any?>>>,(String,android.graphics.Bitmap?)->Unit)->(()->Unit)={ _,_->({}) }
    var onStyleReference: ()->Map<String,Any?>?={ null }
    private var activeScannerKey="current"
    private var selectedProcessKey=prefs.getString("process_key","current") ?: "current"
    private var reloadingCatalog=false
    private var selectingScanPreset=false
    private val scanSessions=ScanEditorSessions()
    private var scanHistory: ScanEditHistory?=null
    private var scanSessionKey: String?=null
    private var sharedScanDraft: ScanEditorDraft?=null
    private var scanBase: Map<String,Any?>?=null
    private var scanBaseLabel: String?=null
    private var scanBusy=false
    var onHistoryChanged: (Boolean,Boolean)->Unit={_,_->}
    private var activeFinishSelection=-1
    private var processOverlay: Map<String,Any?> = runCatching { RecipeShareCodec.decode(prefs.getString("process_overlay","")).let { section(it,"develop") } }.getOrDefault(emptyMap())
    private val processSummary=TextView(activity)
    private val processOutcome=TextView(activity)
    private var processAlternative: Button?=null
    private var alternativeId="phone_native_8"
    private var processMaterialLabel: String?=null
    private val maskBleach=CheckBox(activity)
    private var maskBleachOverride=prefs.getBoolean("mask_bleach_override",prefs.getBoolean("mask_bleach_extra",false))
    private val maskCaption=TextView(activity)
    private val maskStrength=SeekBar(activity)
    private val processCapability=TextView(activity)
    private val scannerCapability=TextView(activity)
    private var fallbackProcess: Map<String,Any?> = emptyMap()
    private var processMaterial: Map<String,Any?> = emptyMap()
    private var developmentContext: String?=null
    fun bindDevelopment(recipe: Map<String,Any?>,material: Map<String,Any?> = emptyMap(),label: String?=null,contextId: String?=null) {
        val changed=contextId!=developmentContext;developmentContext=contextId
        fallbackProcess=recipe;processMaterial=material;processMaterialLabel=label;updateProcessSummary()
        if(changed) selectDevelopment(0)
    }
    var onOpenProcessCatalog: ()->Unit={}
    var onOpenComputeSettings: ()->Unit={}
    lateinit var processCard: LinearLayout
        private set
    lateinit var developmentExtras: LinearLayout
        private set
    lateinit var developmentQuickOptions: LinearLayout
        private set
    private var timeTouch: TouchParameterControl?=null
    lateinit var finishCard: LinearLayout
        private set
    lateinit var scanExtras: LinearLayout
        private set
    lateinit var scanOutput: LinearLayout
        private set
    private var scannerOverlay: Map<String,Any?> = runCatching { RecipeShareCodec.decode(prefs.getString("scanner_overlay","")).let { section(it,"scanner") } }.getOrDefault(emptyMap())
    private var fallbackScanner: Map<String,Any?> = emptyMap()
    private var scanAdvancedOpen=false
    private val auto=CheckBox(activity)
    private val colorStrength=SeekBar(activity)
    private val colorCaption=TextView(activity)
    private val finish=Spinner(activity)
    private val brightness=SeekBar(activity)
    private val brightnessCaption=TextView(activity)
    private var brightnessOverride=prefs.getBoolean("print_brightness_override",false)
    private val incidents=if(prefs.contains("incident_overrides")) runCatching {
        ProcessingIncidentDraft(section(RecipeShareCodec.decode(prefs.getString("incident_overrides","")),"develop"))
    }.getOrElse { ProcessingIncidentDraft() } else ProcessingIncidentDraft.legacy(prefs.getInt("incident_style",0),prefs.getInt("incident_strength",35))
    private var incidentControls: ProcessIncidentControls?=null
    var onObservationChanged: ()->Unit={}
        set(value) { field={ if(!selectingScanPreset) { updateScanInformation();updateScannerCapabilities();scanHistory?.change(scanDraft());publishHistory();value() } } }
    val autoPreviewEnabled get()=auto.isChecked
    private val grid=EditText(activity)
    private val scanner=Spinner(activity)
    private val scannerStyle=Button(activity)
    private val manual=CheckBox(activity)
    private val inversionMode=Spinner(activity)
    private val baseMode=Spinner(activity)
    private val interpretationInfo=TextView(activity)
    private val baseSwitch=Switch(activity)
    private val inversionSwitch=Switch(activity)
    private var lastInterpretation: Pair<Boolean,Boolean>?=null
    private var interpretationDefaults: Pair<Boolean,Boolean>?=null
    private val normalize=CheckBox(activity)
    private val numbers=linkedMapOf<String,Pair<EditText,ClosedFloatingPointRange<Double>>>()
    private val touchNumbers=linkedMapOf<String,TouchParameterControl>()
    private var curveEditor: ScannerCurvePanel?=null
    private var histogramPanel: ScanHistogramPanel?=null
    private var displayedHistogram: DisplayHistogram?=null
    private var initialHistogram=false
    private var histogramPending=true
    fun showScanHistogram(value: DisplayHistogram?,initial: Boolean) {
        displayedHistogram=value;initialHistogram=initial;updateHistogram()
    }
    fun histogramNeedsRefresh() { histogramPending=true;updateHistogram() }
    fun histogramReady() { histogramPending=false;updateHistogram() }
    private fun updateHistogram() {
        val value=displayedHistogram
        val label=if(value==null) copy("直方图 · 等待预览","Histogram · waiting for preview") else
            (if(initialHistogram) copy("初始扫描直方图","Initial scan histogram") else copy("扫描结果直方图","Scan output histogram"))+" · sRGB · ${value.width}×${value.height}"+
                (if(histogramPending && !initialHistogram) copy(" · 待刷新"," · Refresh pending") else "")
        histogramPanel?.bind(value,label,histogramPending,initialHistogram);curveEditor?.bindHistogram(value,label)
    }
    private val styleSummary=TextView(activity)
    private val outputSummary=TextView(activity)
    private var sourceId: String?=null
    private var sourcePath: String?=null
    private var sourceSize: Pair<Int,Int>?=null
    private fun copy(zh: String,en: String)=shell.copy(zh,en)
    private fun scanDraft()=ScanEditorDraft(scanBase,scanBaseLabel,finish.selectedItemPosition,scannerKey(),scannerOverlay,
        if(brightnessOverride) brightness.progress else null,manual.isChecked,numbers.mapValues { it.value.first.text.toString() },
        if(manual.isChecked) normalize.isChecked else true,baseMode.selectedItemPosition,inversionMode.selectedItemPosition,
        grid.text.toString(),auto.isChecked,curveEditor?.snapshotDraft(),rotationDegrees,delivery.format,delivery.bits,delivery.jpegQuality).detached()
    private fun publishHistory() { onHistoryChanged(!scanBusy && scanHistory?.canUndo==true,!scanBusy && scanHistory?.canRedo==true) }
    private fun curveBindingKey()="$scanSessionKey:${finish.selectedItemPosition}:${scannerKey()}:$sourceId:$sourcePath"
    private fun restoreCurves(draft: ScannerCurveState.Draft?=null) {
        val panel=curveEditor ?: return
        val key=curveBindingKey()
        if(draft!=null) { panel.restoreDraft(emptyMap(),key,draft);return }
        // Curve initialization is independent of invalid text in another tool.
        // Keep recipe validation errors visible while clearing any old panel state.
        runCatching {
            val keys=setOf("tone_curve","color_curve_rgb")
            val baseline=section(editingObservation(scanBase ?: current(),fallbackScanner,
                includeOutput=false,includeAdjustments=false),"scanner")
            panel.restoreDraft(baseline.filterKeys { it in keys }+scannerOverlay.filterKeys { it in keys },key,null)
        }.onFailure { panel.restoreDraft(emptyMap(),key,null) }
    }
    fun setScanBusy(busy: Boolean) { scanBusy=busy;outputFormat.isEnabled=!busy;outputQuality.isEnabled=!busy && delivery.format!="TIFF";automaticOutput.isEnabled=!busy;publishHistory() }
    fun beginScanEdit() { scanHistory?.begin() }
    fun finishScanEdit() { scanHistory?.commit();save();publishHistory() }
    private fun scanEdit(action: ()->Unit) {
        val before=scanDraft()
        beginScanEdit();val previous=selectingScanPreset;selectingScanPreset=true
        val result=try { runCatching(action) } finally { selectingScanPreset=previous }
        if(result.isFailure) {
            restoreScanDraft(scanHistory?.cancel() ?: before);scanHistory?.synchronize(scanDraft());onObservationChanged()
            Toast.makeText(activity,result.exceptionOrNull()?.message,Toast.LENGTH_LONG).show();return
        }
        onObservationChanged();finishScanEdit()
    }
    fun undoScan() { if(!scanBusy) scanHistory?.undo()?.let { restoreScanDraft(it);scanHistory?.synchronize(scanDraft());onObservationChanged();save() } }
    fun redoScan() { if(!scanBusy) scanHistory?.redo()?.let { restoreScanDraft(it);scanHistory?.synchronize(scanDraft());onObservationChanged();save() } }
    private fun restoreScanDraft(draft: ScanEditorDraft) {
        val previous=selectingScanPreset;selectingScanPreset=true
        try {
            scanBase=draft.base;scanBaseLabel=draft.label
            val keys=listOf("current")+visibleScanners.map { "builtin:"+it.id }+userScanners.map { "saved:"+it.id }
            scanner.setSelection(keys.indexOf(draft.scannerKey).coerceAtLeast(0));activeScannerKey=scannerKey()
            finish.setSelection(draft.style);activeFinishSelection=draft.style
            scannerOverlay=draft.overlay;manual.isChecked=draft.manual
            numbers.forEach { (key,pair)->pair.first.error=null;pair.first.setText(draft.numbers[key].orEmpty()) }
            normalize.isChecked=draft.normalize;brightnessOverride=draft.gain!=null;brightness.progress=draft.gain ?: 40
            baseMode.setSelection(draft.baseMode);inversionMode.setSelection(draft.inversionMode)
            grid.error=null;grid.setText(draft.grid);auto.isChecked=draft.autoPreview;rotationDegrees=draft.rotationDegrees
            delivery=ScanOutputSettings.from(draft);paintDelivery()
            refreshSimple();updateScannerCapabilities();restoreCurves(draft.curveDraft)
        } finally { selectingScanPreset=previous }
        publishHistory()
    }
    fun beginScanSession(resultId: String,path: String,fallback: Map<String,Any?>,savedRecipe: Map<String,Any?>?=null,savedId: String?=null,resumeSession: Boolean=false,initialRotation: Int=0) {
        val changed=sourceId!=resultId || sourcePath!=path
        if(sharedScanDraft==null) sharedScanDraft=scanDraft()
        scanHistory?.change(scanDraft())
        fallbackScanner=fallback
        val key=ScanEditorSessions.key(resultId,path,savedId,if(resumeSession && !changed) scanSessionKey else null)
        val history=scanSessions.open(key) {
            if(savedRecipe!=null) ScanEditorDraft.saved(savedRecipe,copy("照片保存时配置","Saved photo settings"),auto.isChecked)
            else {
                val base=current()
                val resolved=if(preferLoadedRecipe || !base.containsKey("scanner")) base+("scanner" to fallbackScanner) else base
                sharedScanDraft!!.copy(base=frozenRecipe(resolved),label=null,baseMode=0,inversionMode=0,curveDraft=null,rotationDegrees=initialRotation)
            }
        }
        scanHistory=history;scanSessionKey=key
        bindScanSource(resultId,path)
        val draft=if(changed && savedRecipe==null) history.current.copy(baseMode=0,inversionMode=0) else history.current
        restoreScanDraft(draft);history.synchronize(scanDraft());publishHistory()
    }
    private fun clearScanSessions() {
        // Leave the photo before saving workspace defaults or applying a new loading/share recipe.
        // Restore while its history still guards save() from publishing temporary photo edits.
        sharedScanDraft?.let(::restoreScanDraft)
        scanSessions.clear();scanHistory=null;scanSessionKey=null;sharedScanDraft=null;scanBase=null;scanBaseLabel=null
        publishHistory()
    }
    fun editingObservation(base: Map<String,Any?>,fallback: Map<String,Any?>,reference: Map<String,Any?>?=null,
        includeOutput: Boolean=true,includeAdjustments: Boolean=true)=observation(base,fallback,reference,includeOutput,includeAdjustments,true)
    fun currentScanObservation(fallback: Map<String,Any?>,reference: Map<String,Any?>?=null)=editingObservation(scanBase ?: current(),fallback,reference)
    init {
        val develop=shell.card(MobileShell.Page.DEVELOP,"冲洗工艺","Processing")
        develop.addView(processCapability)
        fun developSection()=LinearLayout(activity).apply { orientation=LinearLayout.VERTICAL;develop.addView(this) }
        val route=developSection();val processDetails=developSection();val incidentPanel=developSection();val bleachPanel=developSection();val after=developSection()
        developmentExtras=after
        shell.action(route,"处理方式与加速…","Processing & acceleration…") { if(!scanBusy) onOpenComputeSettings() }
        shell.note(route,"冲洗方案","Process recipe")
        shell.styleButton(processChoice);route.addView(processChoice,LinearLayout.LayoutParams(-1,-2))
        processChoice.setOnClickListener {
            val official=PhoneProcessPresets.entries.associateBy { it.id }
            val entries=listOf(ProcessChoiceDialog.Entry(0,currentProcessLabel(),0))+processes.mapIndexed { i,item ->
                val builtin=official[item.id]
                val group=when { builtin==null->3;item.recipe["developer_type"]!=null->2;item.recipe["program_key"] !in listOf(null,"auto")->1;else->0 }
                ProcessChoiceDialog.Entry(i+1,item.label,group)
            }
            val selected=processes.indexOfFirst { it.id==selectedProcessKey }+1
            ProcessChoiceDialog(activity,shell).show(entries,selected) { index ->
                selectProcess(processes.getOrNull(index-1)?.id ?: "current")
            }
        }
        processSummary.setPadding(0,shell.dp(8),0,shell.dp(8));route.addView(processSummary)
        processSequence.textSize=12f;processSequence.maxLines=3;processSequence.ellipsize=android.text.TextUtils.TruncateAt.END;route.addView(processSequence);processAdjustments.textSize=12f;route.addView(processAdjustments)
        route.addView(processOutcome)
        developmentQuickOptions=LinearLayout(activity).apply { orientation=LinearLayout.VERTICAL;route.addView(this) }
        shell.styleButton(automaticOutput);developmentQuickOptions.addView(automaticOutput)
        processAlternative=shell.action(route,"按胶卷推荐冲洗","Use the recommended process") {
            processes.firstOrNull { it.id==alternativeId }?.let { selectProcess(it.id) }
        }.apply { visibility=android.view.View.GONE }
        fun openWorkbench() {
            runCatching { ProcessProgramEditor(activity,shell).apply { onPreviewRecipe=this@StageRecipeControls.onPreviewRecipe;applyToDevelopment=true }.open(section(development(current()),"develop"),processMaterial.ifEmpty { section(current(),"film") }) { value ->
                clearProcessOverrides();processOverlay=value;updateProcessSummary();save()
            } }.onFailure { Toast.makeText(activity,it.message,Toast.LENGTH_LONG).show() }
        }
        shell.shortcut(route,"自由冲洗台","Free processing","查看药浴顺序，逐浴调配或组合新流程","Review the sequence, adjust baths or compose a new process",::openWorkbench)
        shell.shortcut(route,"药浴条件","Bath conditions","共用时长与药液适配；单浴条件可继续深入","Shared duration and chemistry; continue into an individual bath") { selectDevelopment(1) }
        shell.shortcut(route,"冲洗事故","Processing incidents","显影不匀、药液污染与乳剂网裂，可组合","Combine uneven development, stains and reticulation") { selectDevelopment(2) }
        shell.shortcut(route,"追加片基漂白","Additional base bleaching","处理材料中的可漂白色罩","Treat the material's bleachable mask") { selectDevelopment(3) }
        shell.shortcut(route,"扫描与保存详情","Scan & save details","查看自动扫描说明与本卷照片","Review automatic scanning and this roll's photos") { selectDevelopment(4) }
        shell.action(route,"工艺样张","Process sample") { runCatching { onPreviewRecipe(observation(development(current()),emptyMap())) }.onFailure { Toast.makeText(activity,it.message,Toast.LENGTH_LONG).show() } }
        shell.shortcut(processDetails,"逐浴调配","Adjust individual baths","温度、时间、浓度、摇晃与反应比例","Temperature, duration, concentration, agitation and reaction fractions",::openWorkbench)
        shell.note(processDetails,"下面的时长是所有未单独设定药浴的共用值。单浴设置在自由冲洗台中覆盖。","The duration below is shared by baths without their own setting. Override an individual bath in Free processing.")
        maskBleach.text=copy("追加片基漂白（实验）","Add base-mask bleaching (experimental)")
        maskBleach.isChecked=prefs.getBoolean("mask_bleach_extra",false);bleachPanel.addView(maskBleach)
        bleachPanel.addView(maskCaption)
        maskStrength.max=100;maskStrength.progress=prefs.getInt("mask_bleach_strength",100);bleachPanel.addView(maskStrength)
        fun maskChanged() { maskStrength.isEnabled=maskBleach.isChecked;maskCaption.text=copy("片基漂白：${maskStrength.progress}%","Base-mask bleaching: ${maskStrength.progress}%") }
        maskChanged();maskBleach.setOnClickListener { maskBleachOverride=true;maskChanged();save();updateProcessSummary() }
        maskStrength.setOnSeekBarChangeListener(object: SeekBar.OnSeekBarChangeListener {
            override fun onProgressChanged(s: SeekBar?,p: Int,user: Boolean) { maskChanged();if(user) { maskBleachOverride=true;save();updateProcessSummary() } }
            override fun onStartTrackingTouch(s: SeekBar?)=Unit;override fun onStopTrackingTouch(s: SeekBar?)=Unit
        })
        shell.explanation(bleachPanel,"片基漂白说明","About base bleaching") { notes -> shell.note(notes,
            "减少可漂白色罩，保留材料支撑层本身的颜色；作用取决于材料，并可能使影像染料褪色。扫描去色罩只改变观察。",
            "Reduces bleachable mask while retaining the support's own color. Material response can also fade image dyes. Scanner mask removal changes readout only.") }
        shell.action(bleachPanel,"片基漂白沿用工艺","Use process base bleaching") { maskBleachOverride=false;updateProcessSummary();save() }
        incidentControls=ProcessIncidentControls(activity,shell,incidentPanel,incidents) { finished -> updateProcessSummary();if(finished) save() }
        shell.resetSection(incidentPanel,copy("事故组合","Processing incidents")) { incidents.inheritAll();updateProcessSummary();save() }
        shell.resetSection(bleachPanel,copy("片基漂白","Base bleaching")) { maskBleachOverride=false;maskStrength.progress=100;updateProcessSummary();save() }
        time.hint=copy("显影时间（分钟，默认 8）","Development time (minutes, default 8)")
        time.inputType=InputType.TYPE_CLASS_NUMBER or InputType.TYPE_NUMBER_FLAG_DECIMAL
        time.setText(prefs.getString("time",""));timeTouch=TouchParameterControl(activity,shell,processDetails,time,copy("共用药浴时长 · 分钟","Shared bath duration · minutes"),.1..120.0,.1,
            onReset={ time.error=null;time.setText("");updateProcessSummary();save() })
        time.addTextChangedListener(object: android.text.TextWatcher {
            override fun beforeTextChanged(s: CharSequence?,start: Int,count: Int,after: Int)=Unit
            override fun onTextChanged(s: CharSequence?,start: Int,before: Int,count: Int) { updateProcessSummary() }
            override fun afterTextChanged(s: android.text.Editable?)=Unit
        })
        cross.text=copy("交叉冲洗","Cross process")
        cross.isChecked=prefs.getBoolean("cross",false)
        chemistryOverride.text=copy("额外指定常规／交叉药液","Override native / cross chemistry")
        val chemistryPanel=LinearLayout(activity).apply { orientation=LinearLayout.VERTICAL }
        processDetails.addView(chemistryGroup)
        shell.explanation(chemistryGroup,"自动工艺的药液适配","Chemistry for automatic processing") { it.addView(chemistryPanel) }
        chemistryOverride.isChecked=prefs.getBoolean("chemistry_override",cross.isChecked);chemistryPanel.addView(chemistryOverride);chemistryPanel.addView(cross)
        shell.resetSection(processDetails,copy("沿用工艺条件","Process conditions")) {
            time.error=null;time.setText("");chemistryOverride.isChecked=false;updateProcessSummary();save()
        }
        shell.resetSection(route,copy("冲洗配置","Process settings")) {
            selectProcess("current")
        }
        cross.isEnabled=chemistryOverride.isChecked;chemistryOverride.setOnCheckedChangeListener { _,checked->cross.isEnabled=checked }
        shell.action(route,"更多操作…","More…") {
            android.app.AlertDialog.Builder(activity).setTitle(copy("冲洗操作","Processing actions"))
                .setItems(arrayOf(copy("药液与冲洗预设管理","Manage bath and process recipes"),copy("继续未完成冲洗","Resume unfinished development"),copy("冲洗操作指南","Processing guide"))) { _,i ->
                    when(i) { 0->onOpenProcessCatalog();1->onResumeDevelopment();else->shell.openGuide(MobileShell.Page.DEVELOP) }
                }.setNegativeButton(copy("取消","Cancel"),null).show()
        }
        val developPanels=listOf(route,processDetails,incidentPanel,bleachPanel,after)
        val developTitles=listOf("冲洗准备" to "Prepare development","药浴条件" to "Bath conditions","冲洗事故" to "Processing incidents","追加片基漂白" to "Additional base bleaching","扫描与保存" to "Scan & save")
        val up=shell.action(shell.developSteps,"‹ 冲洗准备","‹ Preparation") { backDevelopment() }
        val breadcrumb=TextView(activity).apply { setPadding(shell.dp(8),0,shell.dp(8),0);textSize=14f }
        shell.developSteps.gravity=android.view.Gravity.CENTER_VERTICAL;shell.developSteps.addView(breadcrumb)
        fun selectDevelop(index: Int) {
            developmentSection=index
            developPanels.forEachIndexed { i,panel->panel.visibility=if(i==index) android.view.View.VISIBLE else android.view.View.GONE }
            val title=copy(developTitles[index].first,developTitles[index].second)
            (develop.getChildAt(0) as TextView).text=title
            up.visibility=if(index==0) android.view.View.GONE else android.view.View.VISIBLE
            breadcrumb.text=if(index==0) copy("选择方案，按需深入调整","Choose a recipe, then refine as needed") else "› $title"
            shell.scrollToTop(MobileShell.Page.DEVELOP)
        }
        selectDevelopment=::selectDevelop;selectDevelop(0)
        processCard=develop
        val scan=shell.card(MobileShell.Page.SCAN,"扫描参数","Scan settings")
        scan.setPadding(shell.dp(12),shell.dp(4),shell.dp(12),shell.dp(8));scan.background=null
        (scan.layoutParams as LinearLayout.LayoutParams).bottomMargin=0
        finishCard=scan
        scan.addView(scannerCapability)
        styleSummary.textSize=12f;styleSummary.maxLines=1;styleSummary.ellipsize=android.text.TextUtils.TruncateAt.END;styleSummary.setTextColor(if(shell.ivory) 0xFF686257.toInt() else 0xFFB5AEA1.toInt())
        styleSummary.visibility=android.view.View.GONE;scan.addView(styleSummary)
        histogramPanel=ScanHistogramPanel(shell,shell.scanOverlaySurface,prefs.getBoolean("scan_histogram_visible",false)) { shown -> prefs.edit().putBoolean("scan_histogram_visible",shown).apply() }
        fun toolPanel()=LinearLayout(activity).apply { orientation=LinearLayout.VERTICAL;scan.addView(this) }
        val exposure=toolPanel();val colors=toolPanel();val curves=toolPanel()
        val light=toolPanel();val base=toolPanel();val levels=toolPanel();val scanDetails=toolPanel();val advanced=toolPanel()
        scanOutput=scanDetails
        scanExtras=advanced
        fun resetScan(keys: Set<String>,lightness: Boolean=false,polarity: Boolean=false)=scanEdit {
            scannerOverlay=scannerOverlay.filterKeys { it !in keys }
            keys.forEach { numbers[it]?.first?.let { field->field.error=null;field.setText("") } }
            if(lightness) { brightnessOverride=false;brightness.progress=40 }
            if(polarity) { baseMode.setSelection(0);inversionMode.setSelection(0) }
            if("scan_normalize" in keys) {
                val baseline=editingObservation(scanBase ?: current(),fallbackScanner,includeOutput=false,includeAdjustments=false)
                normalize.isChecked=section(baseline,"scanner")["scan_normalize"]!=false
            }
            refreshSimple()
            if("tone_curve" in keys || "color_curve_rgb" in keys) restoreCurves()
            save();onObservationChanged()
        }
        shell.action(shell.scanPresetBar,"↺","↺") {
            android.app.AlertDialog.Builder(activity).setTitle(copy("恢复当前预设参数？","Reset this preset's parameters?"))
                .setMessage(copy("去片基、反相与输出设置保留。","Base correction, inversion and output settings are retained."))
                .setNegativeButton(copy("取消","Cancel"),null).setPositiveButton(copy("恢复","Reset")) { _,_->
                    resetScan(scannerOverlay.keys+numbers.keys+setOf("scan_normalize"),lightness=true)
                }.show()
        }.apply { layoutParams=LinearLayout.LayoutParams(shell.dp(48),shell.dp(48));contentDescription=copy("恢复当前风格参数","Reset current style parameters") }
        shell.resetSection(exposure,copy("增益与对比度","Gain & contrast")) { resetScan(setOf("print_contrast"),lightness=true) }
        shell.resetSection(curves,copy("预设曲线","Preset curves")) { resetScan(setOf("tone_curve","color_curve_rgb")) }
        shell.resetSection(base,copy("片基与极性","Base & polarity")) { resetScan(setOf("remove_base_mask","invert_transmission"),polarity=true) }
        shell.resetSection(levels,copy("预设影调","Preset tone")) { resetScan(setOf("scan_normalize","scan_normalize_strength","scan_black_percentile","scan_white_percentile")) }
        shell.resetSection(advanced,copy("设备响应","Device response")) { resetScan(setOf("scanner_response_matrix","device_response_rgb","scanner_light_color")) }
        finish.adapter=ArrayAdapter(activity,android.R.layout.simple_spinner_dropdown_item,listOf(copy("推荐成片","Recommended"),copy("柔和","Soft"),copy("浓郁","Rich"),copy("原配方","Original recipe")))
        val preserve=prefs.getBoolean("manual",false) || scannerOverlay.isNotEmpty() || prefs.getInt("scanner",0)>0 || runCatching { current().containsKey("scanner") }.getOrDefault(false)
        finish.setSelection(PhoneFinishRecipes.styles.indexOf(prefs.getString("finish_style",if(preserve) "custom" else "ready")).coerceAtLeast(0));activeFinishSelection=finish.selectedItemPosition;finish.visibility=android.view.View.GONE;advanced.addView(finish)
        shell.styleButton(scannerStyle)
        scannerStyle.maxLines=2;scannerStyle.ellipsize=android.text.TextUtils.TruncateAt.END
        shell.scanPresetBar.addView(scannerStyle,0,LinearLayout.LayoutParams(0,-2,1f).apply { marginEnd=shell.dp(6) })
        scannerStyle.setOnClickListener {
            if(scanBusy) return@setOnClickListener
            val keys=listOf("current")+visibleScanners.map { "builtin:"+it.id }+userScanners.map { "saved:"+it.id }
            val options=(0..2).map { VisualPresetPicker.Choice("finish:"+PhoneFinishRecipes.styles[it],finish.adapter.getItem(it).toString()) }+
                keys.mapIndexed { i,key->VisualPresetPicker.Choice("scanner:$key",scanner.adapter.getItem(i).toString()) }
            val selected=if(finish.selectedItemPosition in 0..2) "finish:"+PhoneFinishRecipes.styles[finish.selectedItemPosition] else "scanner:"+scannerKey()
            val before=scanDraft();beginScanEdit()
            fun candidate(id: String): ScanEditorDraft {
                val index=options.indexOfFirst { it.id==id }
                return before.copy(style=if(index<3) index else 3,scannerKey=if(index<3) "current" else keys[index-3],
                    overlay=emptyMap(),gain=null,manual=false,numbers=emptyMap(),curveDraft=null)
            }
            VisualPresetPicker(activity,shell).show(copy("扫描风格","Scan style"),"scanner",options,selected,
                samples={ items,deliver ->
                    val reference=runCatching(onStyleReference).getOrNull()
                    val recipes=items.mapNotNull { item -> runCatching {
                        item.id to observation(scanBase ?: current(),fallbackScanner,reference,includeOutput=true,editor=true,draftOverride=candidate(item.id).copy(grid="160"))
                    }.getOrNull() }
                    val ready=recipes.map { it.first }.toSet()
                    items.filter { it.id !in ready }.forEach { deliver(it.id,null) }
                    onStyleSamples(recipes,deliver)
                },selected={ item -> restoreScanDraft(candidate(item.id));onObservationChanged() },closed={ accepted,_ ->
                    if(accepted) finishScanEdit() else {
                        val restored=scanHistory?.cancel() ?: before
                        restoreScanDraft(restored);scanHistory?.synchronize(scanDraft());onObservationChanged();save()
                    }
                })
        }
        exposure.addView(brightnessCaption);brightness.max=80;brightness.progress=(prefs.getInt("print_brightness",0)+40).coerceIn(0,80);exposure.addView(brightness)
        fun brightnessChanged() { brightnessCaption.text=copy("扫描增益：%+.1f EV".format(java.util.Locale.ROOT,(brightness.progress-40)/20.0),"Scan gain: %+.1f EV".format(java.util.Locale.ROOT,(brightness.progress-40)/20.0)) }
        brightness.setOnSeekBarChangeListener(object: SeekBar.OnSeekBarChangeListener {
            override fun onProgressChanged(bar: SeekBar?,value: Int,user: Boolean) { brightnessChanged();if(user) { brightnessOverride=true;onObservationChanged() } }
            override fun onStartTrackingTouch(bar: SeekBar?) { beginScanEdit() }
            override fun onStopTrackingTouch(bar: SeekBar?) { finishScanEdit() }
        });brightnessChanged()
        outputSummary.textSize=15f;outputSummary.setTextColor(if(shell.ivory) 0xFF292721.toInt() else 0xFFEFEAE1.toInt());outputSummary.setPadding(0,shell.dp(8),0,shell.dp(8));scanDetails.addView(outputSummary)
        grid.hint=copy("成片长边（像素，2–8192，留空等于底片）","Output long edge (px, 2–8192, blank = film)")
        grid.inputType=InputType.TYPE_CLASS_NUMBER
        grid.setText(prefs.getString("grid",""));shell.field(scanDetails,grid,copy("成片长边 · 像素（2–8192）","Output long edge · pixels (2–8192)"),copy("留空保持底片尺寸，不改变底片本身。","Blank keeps the negative dimensions. The negative itself stays unchanged."))
        val edges=LinearLayout(activity);scanDetails.addView(edges)
        for(edge in listOf(1280,1920,3840)) shell.action(edges,"$edge","$edge") { grid.setText(edge.toString()) }.layoutParams=LinearLayout.LayoutParams(0,-2,1f)
        shell.action(scanDetails,"按底片尺寸","Use film size") { grid.setText("") }
        scanner.visibility=android.view.View.GONE;advanced.addView(scanner)
        colorCaption.visibility=android.view.View.GONE;colors.addView(colorCaption)
        colorStrength.visibility=android.view.View.GONE;colorStrength.max=300;colorStrength.progress=100;colors.addView(colorStrength)
        colorStrength.setOnSeekBarChangeListener(object: SeekBar.OnSeekBarChangeListener {
            override fun onProgressChanged(bar: SeekBar?,value: Int,user: Boolean) {
                colorCaption.text=copy("饱和度：$value%","Saturation: $value%")
                if(user) { val amount=value/100.0;scannerOverlay=scannerOverlay+("scan_saturation" to amount)
                    if(manual.isChecked) numbers["scan_saturation"]?.first?.setText(amount.toString());onObservationChanged() }
            }
            override fun onStartTrackingTouch(bar: SeekBar?) { beginScanEdit() }
            override fun onStopTrackingTouch(bar: SeekBar?) { finishScanEdit() }
        })
        shell.action(advanced,"恢复推荐成片","Reset to recommended") {
            android.app.AlertDialog.Builder(activity).setTitle(copy("恢复推荐成片？","Reset to recommended?"))
                .setMessage(copy("恢复推荐的风格参数；去片基、反相与输出设置保留。","Restores recommended style parameters; base correction, inversion and output settings are retained."))
                .setNegativeButton(copy("取消","Cancel"),null).setPositiveButton(copy("恢复推荐","Reset")) { _,_->scanEdit {
            scannerOverlay=emptyMap();manual.isChecked=false;scanner.setSelection(0);activeScannerKey=scannerKey();finish.setSelection(0);brightnessOverride=false
            refreshSimple();save();onObservationChanged()
                } }.show()
        }
        auto.text=copy("参数修改后自动刷新预览","Refresh preview after parameter changes");auto.isChecked=prefs.getBoolean("auto_preview",true);scanDetails.addView(auto)
        paintDelivery()
        shell.note(scanDetails,"扫描产出格式","Scan output format");scanDetails.addView(outputFormat)
        shell.note(scanDetails,"输出质量 · TIFF始终16位","Output quality · TIFF is always 16-bit");scanDetails.addView(outputQuality)
        shell.styleButton(outputFormat);shell.styleButton(outputQuality)
        outputFormat.setOnClickListener { if(!scanBusy) {
            android.app.AlertDialog.Builder(activity).setTitle(copy("扫描产出格式","Scan output format"))
                .setSingleChoiceItems(arrayOf("JPG","PNG","TIFF"),delivery.formatIndex) { dialog,index ->
                    dialog.dismiss();if(!scanBusy) scanEdit { delivery=delivery.select(index,delivery.qualityIndex);paintDelivery() }
                }.setNegativeButton(copy("取消","Cancel"),null).show()
        } }
        outputQuality.setOnClickListener { if(!scanBusy && delivery.format!="TIFF") {
            OutputQualityControls.scan(activity,shell,delivery,false) { chosen ->
                if(!scanBusy) scanEdit { delivery=chosen;paintDelivery() }
            }
        } }
        automaticOutput.setOnClickListener {
            if(scanBusy) return@setOnClickListener
            OutputQualityControls.scan(activity,shell,automaticOutputSettings(),true) { chosen ->
                if(!scanBusy) {
                    if(scanHistory!=null) sharedScanDraft=chosen.applyTo(checkNotNull(sharedScanDraft))
                    else { delivery=chosen;sharedScanDraft=null }
                    prefs.edit().putString("output_format",chosen.format).putInt("output_bits",chosen.bits).putInt("jpeg_quality",chosen.jpegQuality).apply()
                    paintDelivery();onAutomaticOutputChanged()
                }
            }
        }
        shell.action(scanDetails,"处理方式与加速…","Processing & acceleration…") { if(!scanBusy) onOpenComputeSettings() }
        val initialDelivery=delivery
        val initialGrid=grid.text.toString();val initialAuto=auto.isChecked
        shell.resetSection(scanDetails,copy("输出与预览","Output & preview")) { scanEdit {
            grid.error=null;grid.setText(initialGrid);auto.isChecked=initialAuto;rotationDegrees=0;delivery=initialDelivery;paintDelivery();save();onObservationChanged()
        } }
        curveEditor=ScannerCurvePanel(activity,shell,{ values -> scannerOverlay=scannerOverlay+values;onObservationChanged() },::finishScanEdit,::beginScanEdit).also { curves.addView(it) }
        shell.action(advanced,"沿用扫描预设曲线","Use preset curves") { resetScan(setOf("tone_curve","color_curve_rgb")) }
        shell.action(advanced,"另存当前扫描配置","Save scanner configuration") {
            askConfigurationName(activity,shell,copy("扫描配置名称","Scanner configuration name")) { label ->
                val values=RecipeDrafts.scanner(section(editingObservation(scanBase ?: current(),fallbackScanner),"scanner")-OutputOrientation.KEY-ScanDelivery.keys)
                val id=UUID.randomUUID().toString();RecipeCatalog(PrivateRecipeCatalogStore(storage)).saveScanner(id,label,values)
                reloadCatalog(id);save();onObservationChanged()
            }
        }
        manual.text=copy("使用自定义扫描器参数","Use custom scanner settings");manual.isChecked=prefs.getBoolean("manual",false);advanced.addView(manual)
        fun activateScanner() {
            if(manual.isChecked) return
            val values=editingObservation(scanBase ?: current(),fallbackScanner,includeOutput=false);val effective=section(values,"scanner")
            // Touching a new adjustment must not reactivate unrelated drafts that were switched off.
            numbers.values.forEach { it.first.setText("") }
            normalize.isChecked=(effective["scan_normalize"] ?: values["scan_normalize"]) as? Boolean ?: true
            manual.isChecked=true
        }
        val levelTabs=LinearLayout(activity);levels.addView(levelTabs)
        val levelPanels=List(3) { LinearLayout(activity).apply { orientation=LinearLayout.VERTICAL;levels.addView(this) } }
        val levelButtons=mutableListOf<Button>()
        listOf("强度" to "Strength","黑点" to "Black","白点" to "White").forEachIndexed { index,label ->
            levelButtons+=shell.action(levelTabs,label.first,label.second) {
                levelPanels.forEachIndexed { i,panel->panel.visibility=if(i==index) android.view.View.VISIBLE else android.view.View.GONE }
                levelButtons.forEachIndexed { i,tab->tab.isSelected=i==index }
            }.apply { layoutParams=LinearLayout.LayoutParams(0,-2,1f) }
        }
        levelPanels.forEachIndexed { i,panel->panel.visibility=if(i==0) android.view.View.VISIBLE else android.view.View.GONE };levelButtons[0].isSelected=true
        fun numeric(parent: LinearLayout,key: String,zh: String,en: String,range: ClosedFloatingPointRange<Double>) {
            val field=EditText(activity).apply { hint=copy(zh,en);inputType=InputType.TYPE_CLASS_NUMBER or InputType.TYPE_NUMBER_FLAG_DECIMAL or InputType.TYPE_NUMBER_FLAG_SIGNED;setText(prefs.getString(key,"")) }
            numbers[key]=field to range
            val step=when(key) { "transmission_light_temperature_k"->100.0;"scan_black_percentile","scan_white_percentile"->0.1;"scan_normalize_strength","scan_saturation"->0.01;else->0.05 }
            touchNumbers[key]=TouchParameterControl(activity,shell,parent,field,copy(zh,en),range,step,
                unit=if(key=="transmission_light_temperature_k") " K" else "",active={ manual.isChecked },beforeEdit=::activateScanner,
                onReset={ resetScan(setOf(key)) },onEditStarted=::beginScanEdit,onEditFinished=::finishScanEdit,temperature=key=="transmission_light_temperature_k")
        }
        numeric(light,"transmission_light_ev","光源曝光 · EV","Light exposure · EV",-8.0..8.0)
        numeric(light,"transmission_light_temperature_k","灯台光源色温","Scan lamp temperature",1000.0..20000.0)
        numeric(exposure,"print_contrast","对比度 · 倍率","Contrast · multiplier",0.25..2.0)
        numeric(colors,"scan_saturation","饱和度 · 倍率","Saturation · multiplier",0.0..3.0)
        numeric(levelPanels[0],"scan_normalize_strength","影调归一化强度","Tone normalization strength",0.0..1.0)
        numeric(levelPanels[1],"scan_black_percentile","黑点百分位","Black percentile",0.0..100.0)
        numeric(levelPanels[2],"scan_white_percentile","白点百分位","White percentile",0.0..100.0)
        for((field,key,zh,en) in listOf(
            listOf(normalize,"normalize","全帧影调归一化","Whole-frame normalization"))) {
            val check=field as CheckBox;check.text=copy(zh as String,en as String);check.isChecked=prefs.getBoolean(key as String,true);levels.addView(check)
            check.setOnClickListener {
                val chosen=check.isChecked
                runCatching { scanEdit { activateScanner();check.isChecked=chosen;save();onObservationChanged() } }.onFailure { Toast.makeText(activity,it.message,Toast.LENGTH_LONG).show() }
            }
        }
        baseMode.adapter=ArrayAdapter(activity,android.R.layout.simple_spinner_dropdown_item,listOf("auto","on","off"))
        inversionMode.adapter=ArrayAdapter(activity,android.R.layout.simple_spinner_dropdown_item,listOf("auto","on","off"))
        baseMode.setSelection(prefs.getInt("base_mode",0).takeIf { it in 0..2 } ?: 0)
        inversionMode.setSelection(prefs.getInt("inversion_mode",0).takeIf { it in 0..2 } ?: 0)
        baseMode.visibility=android.view.View.GONE;inversionMode.visibility=android.view.View.GONE;base.addView(baseMode);base.addView(inversionMode)
        base.addView(baseSwitch);base.addView(inversionSwitch);base.addView(interpretationInfo)
        baseSwitch.minHeight=shell.dp(48);inversionSwitch.minHeight=shell.dp(48)
        baseSwitch.setOnClickListener { scanEdit { baseMode.setSelection(if(baseSwitch.isChecked) 1 else 2);save();onObservationChanged() } }
        inversionSwitch.setOnClickListener { scanEdit { inversionMode.setSelection(if(inversionSwitch.isChecked) 1 else 2);save();onObservationChanged() } }
        shell.action(base,"恢复自动匹配","Restore automatic matching") { scanEdit {
            baseMode.setSelection(0);inversionMode.setSelection(0);save();onObservationChanged()
        } }
        shell.note(base,"系统按最终底片选择初始开关；可手动更改，切换风格不会影响它们。去片基只影响扫描，不是冲洗漂白。","The final film determines the initial switches. Override either one manually; style changes keep both. Base correction changes scanning, not chemical bleaching.")
        fun enabled() { refreshTouchNumbers() }
        manual.setOnCheckedChangeListener { _,_->enabled() };enabled()
        shell.action(advanced,"共享扫描定义 JSON…","Scanner definition JSON…") {
            runCatching { editConfigurationJson(activity,shell,copy("扫描定义","Scanner definition"),section(editingObservation(scanBase ?: current(),fallbackScanner),"scanner")-OutputOrientation.KEY-ScanDelivery.keys) { value ->
                val map=(value as? Map<*,*>)?.entries?.associate { it.key.toString() to it.value } ?: error("Expected scanner object")
                val definition=RecipeDrafts.scanner(map-OutputOrientation.KEY-ScanDelivery.keys)
                scanEdit {
                    baseMode.setSelection(when(definition["remove_base_mask"]) { true->1;false->2;else->0 })
                    inversionMode.setSelection(when(definition["invert_transmission"]) { true->1;false->2;else->0 })
                    scannerOverlay=definition-StageOverrideValues.interpretationKeys;manual.isChecked=false
                }
            } }.onFailure { Toast.makeText(activity,it.message,Toast.LENGTH_LONG).show() }
        }
        shell.action(advanced,"清除高级覆盖，沿用预设","Clear overrides, use preset") { scanEdit { scannerOverlay=emptyMap();manual.isChecked=false } }
        fun editDevice(key: String,zh: String,en: String,initial: Any) {
            shell.action(advanced,zh,en) {
                runCatching {
                    val values=section(editingObservation(scanBase ?: current(),fallbackScanner),"scanner")-OutputOrientation.KEY-ScanDelivery.keys
                    editConfigurationJson(activity,shell,copy(zh,en),values[key] ?: initial) { value ->
                        scanEdit { scannerOverlay=RecipeDrafts.scanner(values+(key to value)) }
                    }
                }.onFailure { Toast.makeText(activity,it.message,Toast.LENGTH_LONG).show() }
            }
        }
        editDevice("scanner_response_matrix","扫描设备响应矩阵…","Scanner device response matrix…",List(3) { y -> List(3) { x -> if(x==y) 1.0 else 0.0 } })
        editDevice("device_response_rgb","扫描设备 RGB 响应曲线…","Scanner device RGB response curves…",List(3) { listOf(listOf(0.0,0.0),listOf(1.0,1.0)) })
        shell.note(advanced,"设备响应先作用于透射采样和片基参考，再去色罩与反相；它独立于成片 RGB／影调曲线，不改变底片或观片光源。","Device response processes transmission samples and base reference before mask removal and inversion. It is separate from output RGB/tone curves and does not alter film or viewing light.")
        reloadCatalog()
        finish.onItemSelectedListener=object: AdapterView.OnItemSelectedListener {
            override fun onNothingSelected(parent: AdapterView<*>?)=Unit
            override fun onItemSelected(parent: AdapterView<*>?,view: android.view.View?,position: Int,id: Long) {
                if(selectingScanPreset || position!=finish.selectedItemPosition) return
                if(position != activeFinishSelection) { activeFinishSelection=position;scannerOverlay=emptyMap();manual.isChecked=false;refreshSimple();save();onObservationChanged() }
            }
        }
        scanner.onItemSelectedListener=object: AdapterView.OnItemSelectedListener {
            override fun onNothingSelected(parent: AdapterView<*>?)=Unit
            override fun onItemSelected(parent: AdapterView<*>?,view: android.view.View?,position: Int,id: Long) {
                if(selectingScanPreset || reloadingCatalog || position != scanner.selectedItemPosition) return
                val key=scannerKey()
                if(key != activeScannerKey) { activeScannerKey=key;scannerOverlay=emptyMap();manual.isChecked=false;finish.setSelection(3);refreshSimple();save();onObservationChanged() }
            }
        }
        val watcher=object: android.text.TextWatcher {
            override fun beforeTextChanged(s: CharSequence?,start: Int,count: Int,after: Int)=Unit
            override fun onTextChanged(s: CharSequence?,start: Int,before: Int,count: Int) { onObservationChanged() }
            override fun afterTextChanged(s: android.text.Editable?)=Unit
        }
        (numbers.values.map { it.first }+grid).forEach { it.addTextChangedListener(watcher) }
        auto.setOnCheckedChangeListener { _,_->onObservationChanged() }
        manual.setOnCheckedChangeListener { _,_->enabled();save();onObservationChanged() }
        val panels=listOf(base,light,exposure,levels,colors,curves,scanDetails,advanced)
        val toolKeys=listOf("base","light","brightness","levels","color","curves","output","more")
        val tabs=mutableListOf<Button>()
        fun selectTool(index: Int) {
            panels.forEachIndexed { i,panel -> panel.visibility=if(i==index) android.view.View.VISIBLE else android.view.View.GONE }
            tabs.forEachIndexed { i,tab -> tab.isSelected=i==index }
            scanAdvancedOpen=toolKeys[index]=="more";prefs.edit().putString("scan_touch_tool",toolKeys[index]).apply();shell.scrollToTop(MobileShell.Page.SCAN)
            (scan.getChildAt(0) as TextView).text=tabs[index].text
            scan.getChildAt(0).visibility=android.view.View.GONE
            tabs[index].post { tabs[index].requestRectangleOnScreen(android.graphics.Rect(0,0,tabs[index].width,tabs[index].height),true) }
        }
        listOf("底片" to "Film","光源" to "Light","明暗" to "Tone","归一化" to "Normalize","色彩" to "Color","曲线" to "Curves","输出" to "Output","更多" to "More").forEachIndexed { index,label ->
            tabs+=shell.action(shell.scanTools,label.first,label.second) { selectTool(index) }.apply {
                layoutParams=LinearLayout.LayoutParams(-2,-2).apply { marginEnd=shell.dp(4) };minWidth=shell.dp(76);minHeight=shell.dp(48)
            }
        }
        updateHistogram()
        selectTool(toolKeys.indexOf(prefs.getString("scan_touch_tool","base")).coerceAtLeast(0))
    }
    private fun section(recipe: Map<String,Any?>,key: String)=
        (recipe[key] as? Map<*,*>)?.entries?.associate { it.key.toString() to it.value }.orEmpty()
    private fun currentProcessLabel()=copy("当前配方／装卷时工艺","Current recipe / loaded process")
    private fun selectedProcess()=processes.firstOrNull { it.id==selectedProcessKey }
    private fun clearProcessOverrides() {
        processOverlay=emptyMap();maskBleachOverride=false
        chemistryOverride.isChecked=false;time.error=null;time.setText("")
    }
    /** An explicit choice is a complete commit, including reselecting a preset after custom baths. */
    private fun selectProcess(key: String) {
        require(key=="current" || processes.any { it.id==key })
        selectedProcessKey=key;clearProcessOverrides()
        updateProcessSummary();save()
    }
    fun development(base: Map<String,Any?>,fallback: Map<String,Any?> = fallbackProcess): Map<String,Any?> {
        val develop=((selectedProcess()?.recipe ?: if(!preferLoadedRecipe && base.containsKey("develop")) section(base,"develop") else fallback)+processOverlay).toMutableMap()
        develop.putAll(StageOverrideValues.bleach(develop,if(maskBleachOverride) maskBleach.isChecked else null,maskStrength.progress/100.0))
        if(chemistryOverride.isChecked) develop["chemistry"]=if(cross.isChecked) "cross" else "native"
        val text=time.text.toString().trim()
        time.error=null
        if(text.isNotBlank()) {
            val minutes=text.toDoubleOrNull()
            if(minutes==null || !minutes.isFinite() || minutes !in 0.1..120.0) {
                time.error=copy("请输入 0.1–120 分钟","Enter 0.1–120 minutes")
                throw IllegalArgumentException("${shell.fieldLabel(time)}: ${time.error}")
            }
            develop["time_min"]=minutes
        }
        return base + ("develop" to RecipeDrafts.process(incidents.applyTo(develop)))
    }
    private fun updateProcessSummary() {
        val prepared=runCatching { section(development(current()),"develop") }
        val values=prepared.getOrDefault(processOverlay)
        incidentControls?.bind(values)
        val bleaching=(values["mask_bleach_completion"] as? Number)?.toDouble() ?: 0.0
        maskBleach.isChecked=bleaching>0;maskStrength.isEnabled=maskBleach.isChecked
        if(bleaching>0) maskStrength.progress=(bleaching*100).toInt().coerceIn(0,100)
        maskCaption.text=copy("片基漂白：${if(bleaching>0) maskStrength.progress else 0}%","Base-mask bleaching: ${if(bleaching>0) maskStrength.progress else 0}%")
        timeTouch?.refresh((values["time_min"] as? Number)?.toDouble() ?: 8.0)
        val material=processMaterial.ifEmpty { section(current(),"film") }
        val outcome=if(material.isEmpty() || prepared.isFailure) null else runCatching { RecipeDrafts.inspectProcess(material,values) }.getOrNull()
        val count=(values["process_steps"] as? List<*>)?.size
        val automatic=count==null && values["program_key"] in listOf(null,"auto")
        chemistryOverride.visibility=if(automatic) android.view.View.VISIBLE else android.view.View.GONE
        cross.visibility=chemistryOverride.visibility
        chemistryGroup.visibility=chemistryOverride.visibility
        val program=ProcessPresentation.program(outcome?.program ?: values["program_key"] as? String,copy("zh","en")=="en")
        val routeName=if(count==null) program else copy("自定义药浴 · $count 步","Custom baths · $count steps")
        val stock=processMaterialLabel ?: MaterialSelection.displayName(material,material["name"]?.toString().orEmpty(),copy("zh","en")=="en")
        val result=outcome?.let { ProcessPresentation.result(it,copy("zh","en")=="en") }
        val selected=selectedProcess()?.label ?: currentProcessLabel()
        processChoice.text=if(count!=null) copy("自由药浴 · $count 步","Custom baths · $count steps") else selected
        processChoice.contentDescription=copy("当前冲洗方案：","Current process: ")+processChoice.text+copy("，点按选择",", tap to choose")
        processSummary.text=if(prepared.isFailure) copy("参数需要检查，请到药浴页修正后再开始。","Check the settings under Baths before starting.") else
            copy("材料：","Material: ")+stock+"\n"+copy("工艺：","Process: ")+routeName+
            (result?.let { "\n"+copy("预期底片：","Expected film: ")+it } ?: "")
        processSequence.text=outcome?.steps?.mapIndexed { index,step ->
            "${index+1} "+ProcessPresentation.action(step["action"],copy("zh","en")=="en")+
                if((step["strength"] as? Number)?.toDouble()==0.0) copy("（关闭）"," (off)") else ""
        }?.joinToString(" → ").orEmpty()
        processSequence.contentDescription=processSequence.text
        val adjustedBaths=(values["process_steps"] as? List<*>)?.count { raw ->
            (raw as? Map<*,*>)?.any { (key,value)->value!=null && (key !in listOf("action","label","strength") || key=="strength" && (value as? Number)?.toDouble()!=1.0) }==true
        } ?: 0
        val activeIncidents=ProcessingIncidentDraft.keys.count { ((values[it] as? Number)?.toDouble() ?: 0.0)>0.0 }
        processAdjustments.text=listOfNotNull(
            if(adjustedBaths>0) copy("单独设置 $adjustedBaths 浴","$adjustedBaths bath overrides") else null,
            if(activeIncidents>0) copy("开启 $activeIncidents 项事故","$activeIncidents incidents enabled") else null,
            if(bleaching>0) copy("追加片基漂白","Additional base bleaching") else null).joinToString(" · ")
        processAdjustments.visibility=if(processAdjustments.text.isEmpty()) android.view.View.GONE else android.view.View.VISIBLE
        val noImage=outcome?.variableImage==false
        processOutcome.visibility=if(noImage) android.view.View.VISIBLE else android.view.View.GONE
        processOutcome.text=if(noImage && outcome?.colorMaterial==false) copy("这套工艺会清除银像，或只留下均匀底色。此黑白材料没有染料层，不能形成彩色影像。","These baths clear the silver image or leave a uniform density. This B&W stock has no dye-forming layers.")
            else if(noImage) copy("这套药浴可能清除影像或只留下均匀底色。可改用推荐工艺，也可继续实验。","These baths may clear the image or leave uniform density. Use the recommended process or continue experimenting.") else ""
        alternativeId=if(outcome?.colorMaterial==false && outcome.program=="color_negative") "phone_silver_bypass" else "phone_native_8"
        processAlternative?.apply {
            visibility=if(noImage) android.view.View.VISIBLE else android.view.View.GONE
            text=if(alternativeId=="phone_silver_bypass") copy("改用彩色负片工艺 · 留银","Use color negative · retain silver") else copy("改用胶卷推荐工艺","Use the recommended process")
        }
        runCatching { PhoneRecipeCapabilities.inspect(mapOf("film" to processMaterial.ifEmpty { section(current(),"film") },"develop" to values),PhoneRecipeCapabilities.Stage.DEVELOP) }
            .onSuccess { report ->
                processCapability.text=capabilityText(shell,report)
                processCapability.visibility=if(report.warnings.isEmpty()) android.view.View.GONE else android.view.View.VISIBLE
            }
    }
    fun observation(sourceBase: Map<String,Any?>,fallback: Map<String,Any?>,reference: Map<String,Any?>?=null,includeOutput: Boolean=true,includeAdjustments: Boolean=true,editor: Boolean=false,draftOverride: ScanEditorDraft?=null): Map<String,Any?> {
        val draft=draftOverride ?: if(!editor && scanHistory!=null) sharedScanDraft!! else scanDraft()
        val base=draft.base ?: sourceBase
        val inherited=preferLoadedRecipe && draft.base==null
        val keys=listOf("current")+visibleScanners.map { "builtin:"+it.id }+userScanners.map { "saved:"+it.id }
        val position=keys.indexOf(draft.scannerKey)
        val custom=when {
            position <= 0 -> if(!inherited && base.containsKey("scanner")) section(base,"scanner") else fallback
            position <= visibleScanners.size -> section(visibleScanners[position-1].recipe,"scanner")
            else -> userScanners[position-visibleScanners.size-1].recipe
        }
        val style=PhoneFinishRecipes.styles.getOrElse(draft.style) { "ready" }
        val value=((if(style == "custom") custom else PhoneFinishRecipes.observation(if(inherited) base-"scanner" else base,custom,style))+(if(includeAdjustments) draft.overlay else emptyMap())).toMutableMap()
        if(includeAdjustments && draft.manual) {
            for((key,pair) in numbers) {
                pair.first.error=null
                val text=draft.numbers[key].orEmpty().trim();if(text.isBlank()) continue
                val number=text.toDoubleOrNull()
                if(number==null || !number.isFinite() || number !in pair.second) {
                    pair.first.error=copy("允许范围：${pair.second}","Allowed range: ${pair.second}")
                    throw IllegalArgumentException("${shell.fieldLabel(pair.first)}: ${pair.first.error}")
                }
                if(key!="print_contrast") value[key]=number
            }
            value["scan_normalize"]=draft.normalize
        }
        val interpreted=StageOverrideValues.interpretation(value,draft.baseMode,draft.inversionMode)
        value.clear();value.putAll(interpreted)
        value.remove(OutputOrientation.KEY);ScanDelivery.keys.forEach(value::remove)
        if(includeOutput) {
        if(draft.rotationDegrees!=0) value[OutputOrientation.KEY]=draft.rotationDegrees
        value["output_format"]=draft.outputFormat;value["output_bits"]=draft.outputBits;value["jpeg_quality"]=draft.jpegQuality
        val edge=draft.grid.trim()
        grid.error=null
        if(edge.isBlank()) value.remove("scan_grid_long_edge") else {
            val pixels=edge.toIntOrNull()
            if(pixels==null || pixels !in 2..8192) {
                grid.error=copy("请输入 2–8192 的整数","Enter a whole number from 2–8192")
                throw IllegalArgumentException("${shell.fieldLabel(grid)}: ${grid.error}")
            }
            value["scan_grid_long_edge"]=pixels
        }
        }
        val look=section(base,"look").toMutableMap()
        if(includeAdjustments && draft.manual) draft.numbers["print_contrast"]?.takeIf { it.isNotBlank() }?.let { look["print_contrast"]=it.toDouble() }
        if(includeAdjustments && draft.gain!=null) look["print_exposure_ev"]=(draft.gain-40)/20.0
        val manualColor=includeAdjustments && draft.manual && draft.numbers["scan_saturation"]?.isNotBlank()==true
        val adjusted=if(style=="ready" && reference!=null) RollDirectReference.recommend(value,look,reference,includeAdjustments && draft.gain!=null,includeAdjustments && ("scan_saturation" in draft.overlay || manualColor)) else value to look
        val payload=(base-StageOverrideValues.interpretationKeys)-(if(includeOutput) setOf("scan_grid_long_edge") else emptySet())
        return frozenRecipe(payload + ("scanner" to adjusted.first)+("look" to adjusted.second))
    }
    private fun refreshSimple() {
        scannerStyle.text=if(finish.selectedItemPosition==3 && scanner.adapter!=null)
            copy("扫描风格：","Style: ")+scanner.selectedItem.toString() else copy("选择扫描风格…","Choose scanner style…")
        val amount=runCatching { section(editingObservation(scanBase ?: current(),fallbackScanner),"scanner")["scan_saturation"] as? Number }.getOrNull()?.toDouble() ?: 1.0
        colorStrength.progress=(amount*100).toInt().coerceIn(0,300);colorCaption.text=copy("饱和度：${colorStrength.progress}%","Saturation: ${colorStrength.progress}%")
        if(!brightnessOverride) {
            val ev=runCatching { section(scanBase ?: current(),"look")["print_exposure_ev"] as? Number }.getOrNull()?.toDouble() ?: 0.0
            brightness.progress=(ev*20+40).toInt().coerceIn(0,80)
        }
        refreshTouchNumbers()
        updateScanInformation()
    }
    fun reflectInterpretation(metadata: Map<String,Any?>) {
        lastInterpretation=(metadata["remove_base_mask"]==true) to (metadata["invert_transmission"]==true)
        val defaults=metadata["interpretation_defaults"] as? Map<*,*>
        interpretationDefaults=if(defaults?.get("remove_base_mask") is Boolean && defaults["invert_transmission"] is Boolean)
            (defaults["remove_base_mask"]==true) to (defaults["invert_transmission"]==true) else lastInterpretation
        refreshInterpretationSwitches()
    }
    private fun refreshInterpretationSwitches() {
        val actual=interpretationDefaults ?: lastInterpretation
        baseSwitch.isEnabled=actual!=null;inversionSwitch.isEnabled=actual!=null
        baseSwitch.isChecked=when(baseMode.selectedItemPosition) { 1->true;2->false;else->actual?.first ?: false }
        inversionSwitch.isChecked=when(inversionMode.selectedItemPosition) { 1->true;2->false;else->actual?.second ?: false }
        baseSwitch.text=copy("去片基／色罩","Base / mask correction")+if(baseMode.selectedItemPosition==0) copy(" · 自动"," · Auto") else ""
        inversionSwitch.text=copy("反相","Inversion")+if(inversionMode.selectedItemPosition==0) copy(" · 自动"," · Auto") else ""
        interpretationInfo.text=if(actual==null) copy("读取底片后自动匹配","Matches automatically after reading film") else
            if(baseMode.selectedItemPosition==0 && inversionMode.selectedItemPosition==0) copy("已按最终底片自动匹配","Matched to the final film") else copy("手动调整 · 恢复自动可重新匹配底片","Manual override · restore automatic matching to follow the film")
    }
    fun bindScanSource(resultId: String,opticalPath: String,width: Int?=null,height: Int?=null) {
        if(sourceId!=resultId || sourcePath!=opticalPath) { sourceId=resultId;sourcePath=opticalPath;sourceSize=null;lastInterpretation=null;interpretationDefaults=null;baseMode.setSelection(0);inversionMode.setSelection(0);showScanHistogram(null,false);histogramNeedsRefresh() }
        if(width!=null && height!=null) sourceSize=width to height
        updateScanInformation()
    }
    private fun updateOutputInformation() {
        val size=sourceSize
        if(size==null) { outputSummary.text=if(sourceId==null) copy("选择底片后显示尺寸","Select a negative to see dimensions") else copy("底片尺寸：读取中…","Negative dimensions: loading…");return }
        val original=copy("底片：","Negative: ")+"${size.first} × ${size.second} px"
        val edge=grid.text.toString().trim()
        val output=runCatching {
            val pixels=if(edge.isEmpty()) null else edge.toIntOrNull() ?: error("invalid size")
            OpticalPreviewSession.outputDimensions(size.first,size.second,pixels)
        }
        outputSummary.text=original+"\n"+output.fold({ val size=OutputOrientation.size(it.width,it.height,rotationDegrees);copy("预计成片：","Output: ")+"${size.first} × ${size.second} px" },{ copy("成片尺寸：请检查长边设置","Output: check the long-edge setting") })
    }
    private fun updateScanInformation() {
        updateOutputInformation();refreshInterpretationSwitches()
        val label=if(finish.selectedItemPosition==3) (if(scannerKey()=="current") scanBaseLabel else null) ?: scanner.selectedItem?.toString() ?: copy("原配方","Original recipe") else finish.selectedItem?.toString().orEmpty()
        runCatching {
            val recipe=scanBase ?: current()
            val effective=editingObservation(recipe,fallbackScanner,includeOutput=false)
            val baseline=editingObservation(recipe,fallbackScanner,includeOutput=false,includeAdjustments=false)
            val changes=ScanStyleChanges.between(baseline,effective)
            val names=changes.map { when(it) {
                ScanStyleChanges.Kind.BRIGHTNESS->copy("增益","gain")
                ScanStyleChanges.Kind.CONTRAST->copy("对比度","contrast")
                ScanStyleChanges.Kind.COLOR->copy("色彩","color")
                ScanStyleChanges.Kind.CURVES->copy("曲线","curves")
                ScanStyleChanges.Kind.LIGHT->copy("光源","light")
                ScanStyleChanges.Kind.TONE->copy("归一化","normalization")
                ScanStyleChanges.Kind.DEVICE->copy("设备","device")
            } }
            styleSummary.text=label+(if(changes.isEmpty()) "" else copy(" · 自定义"," · Custom"))
            scannerStyle.text=copy("风格 · ","Style · ")+styleSummary.text
            scannerStyle.contentDescription=scannerStyle.text.toString()+(if(names.isEmpty()) "" else copy("。已调整：",". Adjusted: ")+names.joinToString("、"))+copy("。点按选择预设",". Tap to choose a preset")
            scannerStyle.tooltipText=scannerStyle.contentDescription
            curveEditor?.bind(section(effective,"scanner"),curveBindingKey())
        }.onFailure {
            styleSummary.text=label+copy(" · 自定义 · 参数需检查"," · Custom · check settings")
            scannerStyle.text=copy("风格 · ","Style · ")+styleSummary.text;scannerStyle.contentDescription=scannerStyle.text
        }
    }
    private fun refreshTouchNumbers(recipe: Map<String,Any?>?=null) {
        val values=recipe ?: runCatching { editingObservation(scanBase ?: current(),fallbackScanner,includeOutput=false) }.getOrDefault(emptyMap())
        val effective=section(values,"scanner")
        val defaults=LightTableSettings().toRecipe()+ScannerInterpretation().toRecipe()+mapOf("scan_saturation" to 1.0,"print_contrast" to 1.0)
        touchNumbers.forEach { (key,control)->
            val actual=if(key=="print_contrast") section(values,"look")[key] else effective[key] ?: values[key]
            val number=(actual as? Number)?.toDouble()?.takeIf { it.isFinite() } ?: (defaults.getValue(key) as Number).toDouble()
            control.refresh(number)
        }
        if(!manual.isChecked) {
            normalize.isChecked=(effective["scan_normalize"] ?: values["scan_normalize"]) as? Boolean ?: true
        }
    }
    private fun updateScannerCapabilities() {
        runCatching { editingObservation(scanBase ?: current(),fallbackScanner).let { it to PhoneRecipeCapabilities.inspect(it,PhoneRecipeCapabilities.Stage.SCAN) } }.onSuccess { (values,report) ->
            scannerCapability.text=capabilityText(shell,report)
            scannerCapability.visibility=if(report.warnings.isEmpty()) android.view.View.GONE else android.view.View.VISIBLE
            refreshTouchNumbers(values)
        }
    }
    fun bindScanner(fallback: Map<String,Any?>) { fallbackScanner=fallback;refreshSimple();updateScannerCapabilities() }
    fun useLoadedPairing() {
        clearScanSessions()
        preferLoadedRecipe=true
        baseMode.setSelection(0);inversionMode.setSelection(0)
        scannerOverlay=emptyMap();manual.isChecked=false;finish.setSelection(3);scanner.setSelection(0)
        selectedProcessKey="current";clearProcessOverrides()
        brightnessOverride=true;brightness.progress=40;refreshSimple();updateProcessSummary();save();onObservationChanged()
    }
    fun reflectRecommendation(exposure: Double?,saturation: Double?) {
        if(!brightnessOverride && exposure != null) brightness.progress=((exposure*20)+40).toInt().coerceIn(0,80)
        if("scan_saturation" !in scannerOverlay && saturation != null) {
            colorStrength.progress=(saturation*100).toInt().coerceIn(0,300)
            touchNumbers["scan_saturation"]?.refresh(saturation)
        }
    }
    private fun scannerKey(): String=when(val p=scanner.selectedItemPosition) { 0,-1 -> "current";in 1..visibleScanners.size -> "builtin:"+visibleScanners[p-1].id;else -> userScanners.getOrNull(p-visibleScanners.size-1)?.let { "saved:"+it.id } ?: "current" }
    fun updateBuiltins(catalog: BuiltinRecipeCatalog) { builtins=catalog;reloadCatalog() }
    fun reloadCatalog(selectScannerId: String?=null) {
        val savedDraft=selectScannerId?.let { scanDraft().withSavedScanner(it) }
        reloadingCatalog=true
        try {
        val previousScannerKey=if(scanner.adapter == null) prefs.getString("scanner_key",null) ?: visibleScanners.getOrNull(prefs.getInt("scanner",0)-1)?.let { "builtin:"+it.id } ?: "current" else scannerKey()
        val processKey=selectedProcessKey
        visibleScanners=builtins.dailyScanners(previousScannerKey?.removePrefix("builtin:"))
        userScanners=RecipeCatalog(PrivateRecipeCatalogStore(storage)).scanners()
        val scannerKeys=listOf("current")+visibleScanners.map { "builtin:"+it.id }+userScanners.map { "saved:"+it.id }
        val scannerKey=selectScannerId?.let { "saved:$it" } ?: when {
            previousScannerKey == "current" || previousScannerKey?.contains(':') == true -> previousScannerKey
            prefs.getInt("scanner",0) > visibleScanners.size && userScanners.any { it.id == previousScannerKey } -> "saved:$previousScannerKey"
            else -> "builtin:$previousScannerKey"
        }
        scanner.adapter=ArrayAdapter(activity,android.R.layout.simple_spinner_dropdown_item,listOf(copy("当前配方／装卷时配置","Current recipe / loaded scanner"))+visibleScanners.map { copy(it.labelZh,it.labelEn) }+userScanners.map { it.label })
        scanner.setSelection(scannerKeys.indexOf(scannerKey).coerceAtLeast(0))
        activeScannerKey=scannerKeys.getOrNull(scanner.selectedItemPosition) ?: "current"
        processes=PhoneProcessPresets.visible(processKey).map { NamedProcessRecipe(it.id,copy(it.labelZh,it.labelEn),it.recipe) }+
            ProcessRecipeCatalog(PrivateRecipeCatalogStore(storage,"phone_process_recipes.json")).list()
        // Catalog refresh restores selection without clearing an in-progress custom bath draft.
        selectedProcessKey=processKey.takeIf { key->key=="current" || processes.any { it.id==key } } ?: "current"
        updateProcessSummary()
        if(savedDraft!=null) restoreScanDraft(savedDraft) else refreshSimple()
        } finally { reloadingCatalog=false }
    }
    fun useSharedRecipe(base: Map<String,Any?>) {
        clearScanSessions()
        preferLoadedRecipe=false
        clearProcessOverrides();maskBleach.isChecked=false
        inversionMode.setSelection(0);baseMode.setSelection(0)
        scannerOverlay=emptyMap();manual.isChecked=false
        finish.setSelection(3);incidents.reset();brightness.progress=40;brightnessOverride=false
        selectedProcessKey="current";scanner.setSelection(0);grid.setText(section(base,"scanner")["scan_grid_long_edge"]?.toString().orEmpty());updateProcessSummary();save();onObservationChanged()
    }
    fun save() {
        prefs.edit().putBoolean("mask_bleach_extra",maskBleach.isChecked).putBoolean("mask_bleach_override",maskBleachOverride).putInt("mask_bleach_strength",maskStrength.progress).apply()
        prefs.edit().putString("process_overlay",if(processOverlay.isEmpty()) "" else RecipeShareCodec.encode(mapOf("develop" to processOverlay))).apply()
        val processEdit=prefs.edit().putBoolean("prefer_loaded_recipe",preferLoadedRecipe).putString("time",time.text.toString()).putBoolean("cross",cross.isChecked).putBoolean("chemistry_override",chemistryOverride.isChecked)
            .putString("incident_overrides",RecipeShareCodec.encode(mapOf("develop" to incidents.snapshot())))
            .putString("process_key",selectedProcessKey)
        // A photo's scratch edits never become the default for another photo or an automatic development scan.
        if(scanHistory!=null) { processEdit.apply();return }
        val edit=processEdit.putString("grid",grid.text.toString())
            .putString("finish_style",PhoneFinishRecipes.styles.getOrElse(finish.selectedItemPosition) { "ready" }).putInt("print_brightness",brightness.progress-40)
            .putBoolean("print_brightness_override",brightnessOverride)
            .putString("incident_overrides",RecipeShareCodec.encode(mapOf("develop" to incidents.snapshot())))
            .putString("scanner_overlay",RecipeShareCodec.encode(mapOf("scanner" to scannerOverlay))).putString("scanner_key",scannerKey())
            .putString("process_key",selectedProcessKey).putBoolean("expert",scanAdvancedOpen).putBoolean("auto_preview",auto.isChecked)
            .putString("output_format",delivery.format).putInt("output_bits",delivery.bits).putInt("jpeg_quality",delivery.jpegQuality)
            .putInt("scanner",scanner.selectedItemPosition).putInt("output_rotation_degrees",rotationDegrees)
            .putBoolean("manual",manual.isChecked).putInt("inversion_mode",inversionMode.selectedItemPosition).putInt("base_mode",baseMode.selectedItemPosition).putBoolean("normalize",normalize.isChecked)
        numbers.forEach { (key,value)->edit.putString(key,value.first.text.toString()) };edit.apply()
    }
}
