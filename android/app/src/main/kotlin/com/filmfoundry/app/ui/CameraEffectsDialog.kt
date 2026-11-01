package com.filmfoundry.app.ui

import android.app.AlertDialog
import android.graphics.Bitmap
import android.os.Handler
import android.os.Looper
import android.text.Editable
import android.text.TextWatcher
import android.widget.*
import androidx.activity.ComponentActivity
import com.filmfoundry.interchange.FloatNd
import com.filmfoundry.workflow.CameraEffectsPreview
import com.filmfoundry.workflow.CameraEffectsPreviewCache
import com.filmfoundry.workflow.CameraPrintSettings
import com.filmfoundry.workflow.frozenRecipe
import java.util.concurrent.Executor

/** Draft-only camera editor. The worker coalesces edits; no capture, library or media writes. */
class CameraEffectsDialog(private val activity: ComponentActivity,private val shell: MobileShell,private val worker: Executor,
    private val samples: CameraEffectsPreviewCache=CameraEffectsPreviewCache()) {
    private fun c(zh: String,en: String)=shell.copy(zh,en)
    fun open(initial: Map<String,Any?>,film: Map<String,Any?>,capture: Map<String,Any?>,
        selected: Map<String,Any?> = emptyMap(),previewOnly: Boolean=false,imprintOnly: Boolean=false,
        initialAutoAdvance: Boolean?=null,saveAutoAdvance: (Boolean)->Unit={},save: (Map<String,Any?>)->Unit): AlertDialog {
        val request=CameraEffectsPreview.Request(film,capture,selected)
        val landscapePrint=imprintOnly && activity.resources.configuration.orientation==android.content.res.Configuration.ORIENTATION_LANDSCAPE
        val panel=LinearLayout(activity).apply { orientation=LinearLayout.VERTICAL;setPadding(shell.dp(20),shell.dp(12),shell.dp(20),shell.dp(18)) }
        if(!imprintOnly) shell.note(panel,if(previewOnly) "合成样张使用当前材料、药浴与扫描。下面的曝光、碎屑和打印仅供试看片，不写入配置。" else "合成样张使用当前材料与默认冲扫。保存仅影响之后拍摄。",
            if(previewOnly) "Synthetic sample with this material, process and scanner. Exposure, debris and imprint controls are for this preview only." else "Synthetic sample with this stock and default processing. Save affects future exposures.")
        val preview=ImageView(activity).apply { scaleType=ImageView.ScaleType.FIT_CENTER;setBackgroundColor(0xFF161616.toInt()) }
        val screen=activity.resources.displayMetrics.heightPixels
        val root=object: LinearLayout(activity) {
            init { orientation=if(landscapePrint) HORIZONTAL else VERTICAL;tag="camera_effects_layout" }
            override fun onMeasure(w: Int,h: Int) {
                val limit=if(android.view.View.MeasureSpec.getMode(h)==android.view.View.MeasureSpec.UNSPECIFIED) screen else android.view.View.MeasureSpec.getSize(h)
                val height=minOf((screen*(if(landscapePrint) .68 else .72)).toInt(),limit)
                preview.layoutParams?.height=if(landscapePrint) 0 else minOf(shell.dp(220),(height*.43).toInt())
                super.onMeasure(w,android.view.View.MeasureSpec.makeMeasureSpec(height,android.view.View.MeasureSpec.EXACTLY))
            }
        }
        val sampleColumn=LinearLayout(activity).apply { orientation=LinearLayout.VERTICAL }
        val previewParent=if(landscapePrint) sampleColumn else root
        previewParent.addView(preview,if(landscapePrint) LinearLayout.LayoutParams(-1,0,1f) else LinearLayout.LayoutParams(-1,minOf(shell.dp(220),(screen*.30).toInt())))
        if(landscapePrint) root.addView(sampleColumn,LinearLayout.LayoutParams(0,-1,.43f))
        root.addView(ScrollView(activity).apply { isFillViewport=true;addView(panel) },
            if(landscapePrint) LinearLayout.LayoutParams(0,-1,.57f) else LinearLayout.LayoutParams(-1,0,1f))
        val clean=CheckBox(activity).apply { text=c("对照：不加相机效果","Compare: without camera effects");visibility=if(imprintOnly) android.view.View.GONE else android.view.View.VISIBLE };panel.addView(clean)
        val message=TextView(activity).apply { setPadding(shell.dp(16),shell.dp(4),shell.dp(16),shell.dp(4));textSize=12f }
        previewParent.addView(message,1,LinearLayout.LayoutParams(-1,-2))
        val effectSummary=TextView(activity).apply { setPadding(shell.dp(16),0,shell.dp(16),shell.dp(4));textSize=12f;maxLines=2;ellipsize=android.text.TextUtils.TruncateAt.END }
        previewParent.addView(effectSummary,2,LinearLayout.LayoutParams(-1,-2))
        var body=(initial["seed"] as? Number)?.toLong() ?: 14023L;var gate=(initial["gate_seed"] as? Number)?.toLong() ?: body
        var changed: ()->Unit={}
        val exposureCaption=TextView(activity).apply { visibility=if(imprintOnly) android.view.View.GONE else android.view.View.VISIBLE };panel.addView(exposureCaption)
        val exposure=SeekBar(activity).apply { max=80;progress=40;visibility=if(imprintOnly) android.view.View.GONE else android.view.View.VISIBLE };panel.addView(exposure)
        val autoAdvance=initialAutoAdvance?.let { value -> CheckBox(activity).apply {
            text=c("自动过片","Auto advance");isChecked=value;panel.addView(this)
        } }
        val sectionBar=LinearLayout(activity)
        val sectionScroll=HorizontalScrollView(activity).apply { isHorizontalScrollBarEnabled=false;addView(sectionBar);visibility=if(imprintOnly) android.view.View.GONE else android.view.View.VISIBLE };panel.addView(sectionScroll)
        val effectsPanel=LinearLayout(activity).apply { orientation=LinearLayout.VERTICAL };panel.addView(effectsPanel)
        val dirtPanel=LinearLayout(activity).apply { orientation=LinearLayout.VERTICAL };panel.addView(dirtPanel)
        val printPanel=LinearLayout(activity).apply { orientation=LinearLayout.VERTICAL };panel.addView(printPanel)
        val sections=listOf(effectsPanel,dirtPanel,printPanel);val sectionTabs=mutableListOf<Button>()
        fun selectSection(index: Int) { sections.forEachIndexed { i,v->v.visibility=if(i==index) android.view.View.VISIBLE else android.view.View.GONE };sectionTabs.forEachIndexed { i,v->v.isSelected=i==index } }
        listOf("漏光" to "Light leaks","片门" to "Gate dirt","打印" to "Imprint").forEachIndexed { i,label->sectionTabs+=shell.editorTab(sectionBar,label.first,label.second) { selectSection(i) } }
        selectSection(if(imprintOnly) 2 else 0)
        val leakLabel=TextView(activity);effectsPanel.addView(leakLabel)
        val leak=SeekBar(activity).apply { max=100;progress=((initial["leak"] as? Number)?.toDouble()?.times(100))?.toInt() ?: 0 };effectsPanel.addView(leak)
        fun slider(bar: SeekBar,action: ()->Unit) { bar.setOnSeekBarChangeListener(object: SeekBar.OnSeekBarChangeListener {
            override fun onProgressChanged(s: SeekBar?,p: Int,user: Boolean)=action()
            override fun onStartTrackingTouch(s: SeekBar?)=Unit;override fun onStopTrackingTouch(s: SeekBar?)=Unit
        }) }
        fun strength() { leakLabel.text=c("漏光总强度 ${leak.progress}%（0关闭）","Overall leak ${leak.progress}% (0 off)") }
        fun exposureCaption() { val ev=(exposure.progress-40)/10.0;exposureCaption.text=c("样张曝光 %+.1f EV".format(java.util.Locale.ROOT,ev),"Sample exposure %+.1f EV".format(java.util.Locale.ROOT,ev)) }
        exposureCaption();slider(exposure) { exposureCaption();changed() }
        strength();slider(leak) { strength();changed() }
        val shapes=CameraEffectsPreview.shapes;val sides=CameraEffectsPreview.sides
        val shapeLabels=listOf(c("边缘带","Edge band"),c("局部边角","Local corner"),c("半幅","Half frame"),c("周边","Perimeter"),c("锥形","Cone"))
        val sideLabels=listOf(c("左侧","Left"),c("右侧","Right"),c("顶部","Top"),c("底部","Bottom"))
        val components=(initial["leak_components"] as? List<*>)?.map { (it as Map<*,*>).entries.associate { v->v.key.toString() to v.value }.toMutableMap() }?.toMutableList()
            ?: mutableListOf(mutableMapOf<String,Any?>("shape" to (initial["leak_shape"] ?: "edge"),"side" to (initial["leak_side"] ?: "left"),"strength" to 1.0,"seed" to 0L))
        var selectedComponent=0
        val componentStrip=LinearLayout(activity)
        val componentButtons=mutableListOf<Button>()
        effectsPanel.addView(HorizontalScrollView(activity).apply { isHorizontalScrollBarEnabled=false;addView(componentStrip) })
        val rows=LinearLayout(activity).apply { orientation=LinearLayout.VERTICAL };effectsPanel.addView(rows)
        lateinit var renderRows: ()->Unit
        fun picker(parent: LinearLayout,labels: List<String>,index: Int,update: (Int)->Unit): Spinner {
            val view=Spinner(activity).apply { adapter=ArrayAdapter(activity,android.R.layout.simple_spinner_dropdown_item,labels);setSelection(index.coerceAtLeast(0)) };parent.addView(view)
            var previous=view.selectedItemPosition
            view.onItemSelectedListener=object: AdapterView.OnItemSelectedListener {
                override fun onNothingSelected(p: AdapterView<*>?)=Unit
                override fun onItemSelected(p: AdapterView<*>?,v: android.view.View?,position: Int,id: Long) { if(position!=previous) { previous=position;update(position);changed() } }
            };return view
        }
        renderRows={
            rows.removeAllViews();componentStrip.removeAllViews();componentButtons.clear()
            selectedComponent=selectedComponent.coerceIn(0,(components.size-1).coerceAtLeast(0))
            components.forEachIndexed { index,component ->
                val amount=(component["strength"] as? Number)?.toDouble() ?: 1.0
                componentButtons+=shell.editorTab(componentStrip,"${index+1}"+(if(amount==0.0) " · 关" else ""),"${index+1}"+(if(amount==0.0) " · Off" else "")) { selectedComponent=index;renderRows() }.apply {
                    isSelected=index==selectedComponent;contentDescription=c("漏光分量 ${index+1}","Leak component ${index+1}")+if(amount==0.0) c("，关闭",", off") else ""
                    if(isSelected) post { if(parent===componentStrip && isSelected) requestRectangleOnScreen(android.graphics.Rect(0,0,width,height),true) }
                }
            }
            val component=components.getOrNull(selectedComponent)
            if(component==null) shell.note(rows,"暂无漏光分量。添加后可分别设置形状、位置与强度。","No leak components. Add one to choose its shape, position and strength.")
            else {
                shell.note(rows,"漏光分量 ${selectedComponent+1}","Leak component ${selectedComponent+1}")
                picker(rows,shapeLabels,shapes.indexOf(component["shape"])) { component["shape"]=shapes[it] }
                picker(rows,sideLabels,sides.indexOf(component["side"])) { component["side"]=sides[it] }
                val weight=TextView(activity);rows.addView(weight)
                val bar=SeekBar(activity).apply { max=100;progress=(((component["strength"] as? Number)?.toDouble() ?: 1.0)*100).toInt().coerceIn(0,100) };rows.addView(bar)
                fun caption() {
                    weight.text=c("分量强度 ${bar.progress}% · 0 关闭","Component strength ${bar.progress}% · 0 off")
                    componentButtons.getOrNull(selectedComponent)?.apply {
                        text="${selectedComponent+1}"+if(bar.progress==0) c(" · 关"," · Off") else ""
                        contentDescription=c("漏光分量 ${selectedComponent+1}","Leak component ${selectedComponent+1}")+" · ${bar.progress}%"
                    }
                }
                caption();slider(bar) { component["strength"]=bar.progress/100.0;caption();changed() }
                shell.action(rows,"移除当前分量","Remove this component") { components.removeAt(selectedComponent);renderRows();changed() }
            }
        }
        renderRows()
        shell.action(effectsPanel,"添加漏光分量（最多8个）","Add leak component (up to 8)") {
            if(components.size>=8) Toast.makeText(activity,c("最多8个分量","Maximum 8 components"),Toast.LENGTH_SHORT).show()
            else { components+=mutableMapOf<String,Any?>("shape" to "corner","side" to "right","strength" to .6,"seed" to java.security.SecureRandom().nextLong());selectedComponent=components.lastIndex;renderRows();changed() }
        }
        shell.note(effectsPanel,"多个分量会叠加曝光；每个分量的位置身份保存后固定。","Components add exposure and keep their identity after saving.")
        shell.note(dirtPanel,"片门脏物","Gate dirt")
        shell.note(dirtPanel,"灰尘、碎屑、毛发与飞虫可同时使用，位置保持固定。齿孔与边字在胶卷设置中。","Combine dust, debris, hair and insects with fixed positions. Sprockets and edge markings are in film settings.")
        val dirt=listOf("dust" to c("片门灰尘","Gate dust"),"debris" to c("片门碎屑","Gate debris"),"hair" to c("毛发","Hair"),"insect" to c("飞虫","Insect"))
            .associate { (key,label)->key to CheckBox(activity).apply { text=label;isChecked=initial[key]==true;dirtPanel.addView(this);setOnCheckedChangeListener { _,_->changed() } } }
        val printContent=if(landscapePrint) LinearLayout(activity).apply { orientation=LinearLayout.VERTICAL } else printPanel
        val printPosition=if(landscapePrint) LinearLayout(activity).apply { orientation=LinearLayout.VERTICAL;visibility=android.view.View.GONE } else printPanel
        if(landscapePrint) {
            val tabs=LinearLayout(activity);printPanel.addView(tabs)
            val buttons=mutableListOf<Button>()
            fun select(position: Boolean) {
                printContent.visibility=if(position) android.view.View.GONE else android.view.View.VISIBLE
                printPosition.visibility=if(position) android.view.View.VISIBLE else android.view.View.GONE
                buttons.forEachIndexed { i,button->button.isSelected=(i==1)==position }
            }
            buttons+=shell.editorTab(tabs,"内容样式","Content") { select(false) }
            buttons+=shell.editorTab(tabs,"亮度位置","Placement") { select(true) }
            buttons.forEach { it.layoutParams=LinearLayout.LayoutParams(0,-2,1f) }
            printPanel.addView(printContent);printPanel.addView(printPosition);select(false)
        } else shell.note(printPanel,"文字打印","Text imprint")
        val imprint=EditText(activity).apply { setSingleLine(true);inputType=android.text.InputType.TYPE_CLASS_TEXT or android.text.InputType.TYPE_TEXT_FLAG_NO_SUGGESTIONS
            imeOptions=android.view.inputmethod.EditorInfo.IME_FLAG_NO_EXTRACT_UI or android.view.inputmethod.EditorInfo.IME_ACTION_DONE
            filters=arrayOf(android.text.InputFilter.LengthFilter(24));setText(initial["imprint"]?.toString().orEmpty()) }
        if(landscapePrint) {
            imprint.hint=c("打印内容 · 空白不打印","Text · blank disables printing")
            imprint.contentDescription=c("相机文字打印","Camera text imprint");printContent.addView(imprint)
        } else shell.field(printContent,imprint,c("相机文字打印","Camera text imprint"),c("最多24个英文字母、数字、空格或英文标点；空白不打印。","Up to 24 English letters, digits, spaces or ASCII punctuation. Blank disables printing."),resettable=false)
        val separators=LinearLayout(activity);printContent.addView(separators)
        for((symbol,zh,en) in listOf(Triple(":","冒号","colon"),Triple("/","斜线","slash"),Triple("-","短横线","hyphen"),Triple(".","句点","dot"))) {
            Button(activity).apply { text=symbol;isFocusable=false;contentDescription=c("插入$zh","Insert $en");minimumHeight=shell.dp(44);shell.styleButton(this)
                setOnClickListener {
                    val a=imprint.selectionStart.coerceAtLeast(0);val b=imprint.selectionEnd.coerceAtLeast(0)
                    val start=minOf(a,b);val end=maxOf(a,b)
                    imprint.text.replace(start,end,symbol);imprint.requestFocus()
                };separators.addView(this,LinearLayout.LayoutParams(0,-2,1f)) }
        }
        var printStyle=initial["imprint_style"]?.toString() ?: "optical_amber"
        picker(printContent,listOf(c("关闭打印","No imprint"),c("激光点阵","Laser dots"),c("光学琥珀","Optical amber"),c("实验室绿色","Lab green")),CameraEffectsPreview.printStyles.indexOf(printStyle)) { printStyle=CameraEffectsPreview.printStyles[it] }
        val placement=PrintPlacementControls(shell,initial,compact=landscapePrint) { changed() };printPosition.addView(placement)
        imprint.addTextChangedListener(object: TextWatcher { override fun beforeTextChanged(s: CharSequence?,start: Int,count: Int,after: Int)=Unit;override fun onTextChanged(s: CharSequence?,start: Int,before: Int,count: Int) { changed() };override fun afterTextChanged(s: Editable?)=Unit })
        shell.action(effectsPanel,"重设机身漏光身份","New body leak identity") { body=java.security.SecureRandom().nextLong();changed() }
        shell.action(dirtPanel,"清理／重排片门脏物","Rearrange gate dirt") { gate=java.security.SecureRandom().nextLong();changed() }
        fun snapshot(): Map<String,Any?> {
            val text=CameraPrintSettings.normalizeText(imprint.text.toString())
            val valid=CameraPrintSettings.validText(text)
            val error=c("最多24个英文字母、数字或英文标点，不支持中文和表情。","Use up to 24 English letters, digits or ASCII punctuation; no CJK or emoji.")
            imprint.error=if(valid) null else error
            require(valid) { error }
            val camera=if(imprintOnly) initial else initial+mapOf("seed" to body,"gate_seed" to gate,"leak" to leak.progress/100.0,"leak_components" to components.map { it.toMap() })+dirt.mapValues { it.value.isChecked }
            return CameraPrintSettings.update(camera,text,printStyle,if(placement.y<.5) "top" else "bottom",placement.brightness,placement.x,placement.y)
        }
        val handler=Handler(Looper.getMainLooper());val revision=java.util.concurrent.atomic.AtomicInteger();val closed=java.util.concurrent.atomic.AtomicBoolean();var inFlight=false
        var displayed: Bitmap?=null
        fun bitmap(volume: FloatNd): Bitmap {
            val pixels=IntArray(volume.shape[0]*volume.shape[1]) { i -> var color=0xFF000000.toInt();for(c in 0..2) color=color or ((volume.values[i*3+c].coerceIn(0f,1f)*255).toInt() shl (16-c*8));color }
            return Bitmap.createBitmap(pixels,volume.shape[1],volume.shape[0],Bitmap.Config.ARGB_8888)
        }
        lateinit var launch: Runnable
        launch=Runnable {
            if(closed.get() || activity.isDestroyed || inFlight) return@Runnable
            val camera=runCatching { frozenRecipe(snapshot()).also(CameraEffectsPreview::validate) }.getOrElse { message.text=it.message;return@Runnable }
            val version=revision.get();inFlight=true;message.text=c("正在更新示例…","Updating sample…")
            val ev=(exposure.progress-40)/10.0
            val comparison=clean.isChecked
            worker.execute {
                val rendered=runCatching { samples.render(request,camera,ev,comparison) {
                    if(closed.get() || version!=revision.get() || Thread.currentThread().isInterrupted) throw InterruptedException("Sample superseded")
                }.let(::bitmap) }
                activity.runOnUiThread {
                    inFlight=false
                    if(closed.get() || activity.isDestroyed || version!=revision.get()) rendered.getOrNull()?.recycle()
                    else rendered.onSuccess { image -> val previous=displayed;displayed=image;preview.setImageBitmap(image);previous?.recycle();message.text=c("效果示例 · 不改变已拍摄的底片","Sample · existing exposures stay unchanged") }.onFailure { message.text=c("样张未更新：","Sample did not update: ")+it.message }
                    if(!closed.get() && version!=revision.get()) handler.post(launch)
                }
            }
        }
        changed={
            val names=mutableListOf<String>()
            if(!imprintOnly) {
                val count=if(leak.progress>0) components.count { ((it["strength"] as? Number)?.toDouble() ?: 1.0)>0 } else 0
                if(count>0) names+=c("漏光 $count 项","$count leak components")
                dirt.filterValues { it.isChecked }.values.forEach { names+=it.text.toString() }
            }
            if(printStyle!="none" && imprint.text.isNotBlank()) names+=c("打印","Imprint")
            effectSummary.text=(if(clean.isChecked) c("对照中 · 已配置：","Comparing · configured: ") else c("已配置：","Configured: "))+
                names.takeIf { it.isNotEmpty() }?.joinToString(" · ").orEmpty().ifEmpty { if(imprintOnly) c("未启用打印","No imprint") else c("无相机效果","No camera effects") }
            revision.incrementAndGet();handler.removeCallbacks(launch);handler.postDelayed(launch,150)
        }
        clean.setOnCheckedChangeListener { _,_->changed() }
        fun enlarge() {
            val source=displayed ?: run { Toast.makeText(activity,c("样张生成后即可放大","Enlarge once the sample is ready"),Toast.LENGTH_SHORT).show();return }
            val enlarged=source.copy(Bitmap.Config.ARGB_8888,false)
            val view=TouchPhotoView(activity).apply { setImageBitmap(enlarged) }
            val frame=FrameLayout(activity).apply { addView(view,FrameLayout.LayoutParams(-1,(activity.resources.displayMetrics.heightPixels*.62).toInt())) }
            val enlargedDialog=AlertDialog.Builder(activity).setTitle(c("样张 · 双指缩放","Sample · pinch to zoom")).setView(frame)
                .setPositiveButton(c("关闭","Close"),null).create()
            enlargedDialog.setOnDismissListener { view.dispose();view.setImageDrawable(null);enlarged.recycle() }
            enlargedDialog.show()
        }
        preview.setOnClickListener { enlarge() }
        if(!landscapePrint) shell.action(panel,"放大查看当前效果","Enlarge current sample") { enlarge() }
        val dialog=AlertDialog.Builder(activity).setTitle(if(previewOnly) c("预设样张","Preset sample") else if(imprintOnly) c("文字打印","Text imprint") else c("相机效果","Camera effects")).setView(root)
            .setNegativeButton(c("取消","Cancel"),null).setPositiveButton(if(previewOnly) c("关闭","Close") else c("保存到之后的曝光","Save for future exposures"),null).create()
        dialog.setOnDismissListener { closed.set(true);handler.removeCallbacks(launch);preview.setImageDrawable(null);displayed?.recycle();displayed=null }
        dialog.setOnShowListener { dialog.getButton(AlertDialog.BUTTON_POSITIVE).setOnClickListener {
            runCatching { if(!previewOnly) snapshot().also(CameraEffectsPreview::validate).also(save).also { autoAdvance?.let { saveAutoAdvance(it.isChecked) } } }.onSuccess { dialog.dismiss() }.onFailure { message.text=it.message }
        } };shell.decorate(panel);dialog.show()
        if(landscapePrint) dialog.window?.setLayout((activity.resources.displayMetrics.widthPixels*.94).toInt(),android.view.ViewGroup.LayoutParams.WRAP_CONTENT)
        dialog.window?.setSoftInputMode(android.view.WindowManager.LayoutParams.SOFT_INPUT_ADJUST_RESIZE or android.view.WindowManager.LayoutParams.SOFT_INPUT_STATE_ALWAYS_HIDDEN)
        changed()
        return dialog
    }
}
