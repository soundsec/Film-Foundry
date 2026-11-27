package com.filmfoundry.app.ui

import android.app.AlertDialog
import android.graphics.*
import android.view.View
import android.widget.*
import android.text.Editable
import android.text.TextWatcher
import androidx.activity.ComponentActivity
import com.filmfoundry.workflow.FilmBorderPreview
import com.filmfoundry.workflow.FilmEdgeSettings
import com.filmfoundry.interchange.EdgeMarkPattern
import com.filmfoundry.interchange.RegisteredFilmPlate
import com.filmfoundry.interchange.FilmFactoryWindow
import com.filmfoundry.interchange.FilmStripWindow
import com.filmfoundry.interchange.FactoryLamp
import com.filmfoundry.interchange.FilmRebateGeometry

/** An explicit layout diagram: no option here claims to have exposed or scanned film rebate. */
fun showFilmBorderPreview(activity: ComponentActivity,shell: MobileShell,format: String,custom: String="",marks: Boolean=true,
    factorySettings: Map<String,Any?> = emptyMap(),previewSample: (Map<String,Any?>)->Unit={},save: (String,Boolean,Map<String,Any?>)->Unit={ _,_,_->}) {
    fun c(zh: String,en: String)=shell.copy(zh,en)
    val panel=LinearLayout(activity).apply { orientation=LinearLayout.VERTICAL;setPadding(shell.dp(20),shell.dp(12),shell.dp(20),shell.dp(16)) }
    shell.note(panel,"新卷可保留完整片边；厂印随材料与冲洗形成。","New rolls can retain film edges; factory marks respond to stock and processing.")
    var key=when(format) { "half"->"half_frame";"6x4.5","6x6","6x7","6x9"->"120";else->format }.takeIf { it in FilmBorderPreview.formats } ?: "135"
    val row="both";var text=custom.ifBlank { "FILM FOUNDRY" }+" 01";val top=false
    var showMarks=marks
    var patterns=FilmEdgeSettings.patterns(factorySettings)
    val glowInput=EditText(activity)
    val initialGlow=(patterns["rebate_glow"] as? Number)?.toDouble() ?: 0.0
    var formation=patterns["formation_mode"].toString().let { if(it=="sprocket_panorama" && key!="135") "attached" else it }
    var loading=(patterns["loading_offset"] as Number).toDouble()
    var batch=(patterns["factory_batch"] as Number).toInt()
    var codeStyle=patterns["factory_barcode"].toString()
    var marker=patterns["advance_marker_style"].toString()
    var leftward=patterns["advance_direction"]=="left"
    lateinit var lamp: EdgeLampControls
    lateinit var inkColour: EdgeLampControls
    fun settings()=patterns + lamp.value().record() +
        (if(patterns["factory_mark_type"]=="ink" || "factory_ink_transmission_rgb" in patterns)
            mapOf("factory_ink_transmission_rgb" to inkColour.value().rgb) else emptyMap()) +
        mapOf("formation_mode" to formation,"loading_offset" to loading,"factory_batch" to batch,"factory_barcode" to codeStyle,
        "rebate_glow" to ((glowInput.text.toString().toDoubleOrNull() ?: (initialGlow*100))/100),
        "advance_marker_style" to marker,"advance_direction" to if(leftward) "left" else "right")
    var halfSlot=1
    val paint=Paint(Paint.ANTI_ALIAS_FLAG)
    val preview=object: View(activity) {
        override fun onDraw(canvas: Canvas) {
            super.onDraw(canvas)
            val gate=when(key) { "half_frame"->320 to 240;"120"->when(format) { "6x4.5"->336 to 249;"6x7"->411 to 336;"6x9"->496 to 336;else->400 to 400 };else->480 to 320 }
            val vertical=key=="half_frame" || format=="6x4.5"
            val along=if(vertical) gate.second else gate.first
            val across=if(vertical) gate.first else gate.second
            val diagram=patterns+mapOf("formation_mode" to formation,"transport_axis" to "horizontal")
            val layout=FilmBorderPreview.layout(key,along,across,row,text,top,diagram)
            val w=if(vertical) layout.height else layout.width
            val h=if(vertical) layout.width else layout.height
            val scale=minOf(width.toFloat()/w,height.toFloat()/h)
            canvas.save();canvas.translate((width-w*scale)/2,(height-h*scale)/2);canvas.scale(scale,scale)
            if(vertical) { canvas.translate(w.toFloat(),0f);canvas.rotate(90f) }
            paint.color=Color.rgb(76,44,25);canvas.drawRect(0f,0f,layout.width.toFloat(),layout.height.toFloat(),paint)
            paint.color=Color.rgb(29,29,29);canvas.drawRect(0f,if(formation=="sprocket_panorama") 0f else layout.rebate.toFloat(),layout.width.toFloat(),if(formation=="sprocket_panorama") layout.height.toFloat() else (layout.height-layout.rebate).toFloat(),paint)
            val window=FilmStripWindow(halfSlot,layout.width,loading)
            val factory=FilmFactoryWindow(window,key=="half_frame")
            canvas.save();canvas.clipRect(0f,0f,layout.width.toFloat(),layout.height.toFloat())
            paint.color=Color.rgb(242,238,229)
            for(cell in -1..1) for(hole in layout.holes) {
                val dx=(window.shift+cell*layout.width).toFloat()
                val radius=(minOf(hole.right-hole.left,hole.bottom-hole.top)*.2).toFloat()
                canvas.drawRoundRect(hole.left.toFloat()+dx,hole.top.toFloat(),hole.right.toFloat()+dx,hole.bottom.toFloat(),radius,radius,paint)
            }
            canvas.restore()
            val cells=if(window.shift==0) 0..0 else if(window.shift>0) -1..0 else 0..1
            for(cell in cells) {
                val frame=(factory.plate.frame+cell).coerceAtLeast(0)
                fun pattern(key: String)=patterns[key]?.toString()?.takeIf { it.isNotBlank() }?.let(EdgeMarkPattern::decode)
                val upper=pattern("factory_pattern_top")
                val lower=if(patterns["factory_pattern_linked"]==true) upper else pattern("factory_pattern_bottom")
                val plate=RegisteredFilmPlate(text.substringBeforeLast(' '),batch,frame,codeStyle,marker,leftward,upper,lower,
                    patterns["factory_flip_top"]==true,patterns["factory_flip_bottom"]==true,(patterns["factory_barcode_seed"] as? Number)?.toInt() ?: 0,
                    com.filmfoundry.interchange.EdgeComposition.parse(patterns["factory_composition"]))
                canvas.save()
                canvas.clipRect(0f,0f,layout.width.toFloat(),layout.height.toFloat())
                canvas.translate((window.shift+cell*factory.plate.width-factory.origin).toFloat(),0f)
                canvas.clipRect(0f,0f,factory.plate.width.toFloat(),layout.height.toFloat())

                paint.color=when(patterns["factory_mark_type"]) {
                    "ink" -> inkColour.value().rgb.let { Color.rgb((76*it[0]).toInt(),(44*it[1]).toInt(),(25*it[2]).toInt()) }
                    "laser_etched" -> Color.rgb(231,226,210)
                    else -> Color.rgb(217,169,86)
                }
                paint.typeface=Typeface.MONOSPACE;paint.textSize=layout.textBand.fontSize.toFloat()
                if(showMarks) for(isTop in listOf(true,false)) {
                    val band=FilmBorderPreview.layout(key,along,across,row,text,isTop,diagram).textBand
                    val start=band.top.toInt();val height=maxOf(1,band.bottom.toInt()-start)
                    for(y in 0 until height) {
                        var run=-1
                        for(x in 0..factory.plate.width) {
                            if(x<factory.plate.width && plate.ink(x,y,isTop,factory.plate.width,height)) { if(run<0) run=x }
                            else if(run>=0) {
                                canvas.drawRect(run.toFloat(),(start+y).toFloat(),x.toFloat(),(start+y+1).toFloat(),paint);run=-1
                            }
                        }
                    }
                }
                canvas.restore()
            }
            paint.color=Color.rgb(160,160,160);paint.textSize=20f;val title=c("成像区域","IMAGE GATE")
            if(vertical) canvas.rotate(-90f,layout.width/2f,layout.height/2f)
            canvas.drawText(title,(layout.width-paint.measureText(title))/2f,layout.height/2f,paint);canvas.restore()
        }
    }
    val screen=activity.resources.displayMetrics.heightPixels
    val root=object: LinearLayout(activity) {
        init { orientation=VERTICAL }
        override fun onMeasure(w: Int,h: Int) {
            val limit=if(MeasureSpec.getMode(h)==MeasureSpec.UNSPECIFIED) screen else MeasureSpec.getSize(h)
            val height=minOf((screen*.72).toInt(),limit)
            preview.layoutParams?.height=minOf(shell.dp(220),(height*.43).toInt())
            super.onMeasure(w,MeasureSpec.makeMeasureSpec(height,MeasureSpec.EXACTLY))
        }
    }
    root.addView(preview,LinearLayout.LayoutParams(-1,shell.dp(220)))
    root.addView(ScrollView(activity).apply { addView(panel) },LinearLayout.LayoutParams(-1,0,1f))
    shell.note(panel,"普通 120 卷无齿孔；135 装入 120 时，画面延伸至两排齿孔和边字。冲扫样张可查看实际效果。","120 stock has no perforations. With 35 mm in a 120 camera, the image reaches the holes and lettering. Open the processed sample to see the result.")
    val print=CheckBox(activity).apply { this.text=c("打印厂印边字","Print factory edge text");isChecked=marks };panel.addView(print)
    val label=EditText(activity).apply { setSingleLine(true);filters=arrayOf(android.text.InputFilter.LengthFilter(32));setText(custom) }
    shell.field(panel,label,c("自定义边字","Custom edge text"),c("最多32个英文字母或空格。留空用材料名称，格号自动追加；仅用于新装卷。","Up to 32 English letters or spaces. Blank uses the stock name; frame numbers are automatic. New rolls only."))
    lamp=EdgeLampControls(activity,shell,panel,FactoryLamp.read(patterns))
    inkColour=EdgeLampControls(activity,shell,panel,FactoryLamp(0.0,FilmRebateGeometry.inkTransmission(patterns)),ink=true) { preview.invalidate() }
    TouchParameterControl(activity,shell,panel,glowInput,c("片边微漏光","Rebate light bleed"),0.0..100.0,1.0," %").refresh(initialGlow*100)
    val offsetInput=EditText(activity)
    val offsetTitle=c("上片偏移","Loading offset")
    TouchParameterControl(activity,shell,panel,offsetInput,offsetTitle,-50.0..50.0,0.5," %").refresh(loading*100)
    fun choices(title: String,options: List<Pair<String,String>>,value: String,onChoose: (String)->Unit) {
        panel.addView(TextView(activity).apply { text=title })
        val group=RadioGroup(activity).apply { orientation=LinearLayout.HORIZONTAL }
        val keys=mutableMapOf<Int,String>()
        for((key,label) in options) {
            val choice=RadioButton(activity).apply { id=View.generateViewId();text=label;minHeight=shell.dp(48) }
            group.addView(choice);keys[choice.id]=key
            if(key==value) group.check(choice.id)
        }
        panel.addView(HorizontalScrollView(activity).apply { isHorizontalScrollBarEnabled=false;addView(group) })
        group.setOnCheckedChangeListener { _,id->keys[id]?.let(onChoose) }
    }
    if(key=="half_frame") choices(c("半格预览","Half-frame preview"),listOf("1" to c("前半格","First half"),"2" to c("后半格","Second half")),"1") {
        halfSlot=it.toInt();preview.invalidate()
    }
    val batchInput=EditText(activity).apply {
        setSingleLine(true);inputType=android.text.InputType.TYPE_CLASS_NUMBER;setText(batch.toString())
    }
    shell.field(panel,batchInput,c("厂印批号","Factory batch"),c("0–4095；片序随过片递增。自绘下边模板优先。","0–4095. The frame advances with the roll. A custom lower stencil takes precedence."))
    fun update(): String? {
        loading=(offsetInput.text.toString().toDoubleOrNull() ?: (factorySettings["loading_offset"] as? Number)?.toDouble()?.times(100) ?: 0.0)/100
        batchInput.isEnabled=codeStyle!="none" && print.isChecked
        val entered=batchInput.text.toString().toIntOrNull()
        if(entered !in 0..4095 && batchInput.isEnabled) {
            batchInput.error=c("请输入 0–4095","Enter 0–4095");return null
        }
        batchInput.error=null
        if(entered!=null && entered in 0..4095) batch=entered
        val enteredText=label.text.toString()
        val value=runCatching {
            if(enteredText==custom && custom.matches(Regex("[A-Za-z0-9 -]{0,32}"))) custom
            else FilmEdgeSettings.customLabel(enteredText)
        }.getOrElse {
            label.error=c("请使用英文字母或空格","Use English letters or spaces");return null
        }
        val method=patterns["factory_mark_type"]
        lamp.view.visibility=if(print.isChecked && method in listOf("hard","laser")) View.VISIBLE else View.GONE
        inkColour.view.visibility=if(print.isChecked && method=="ink") View.VISIBLE else View.GONE
        label.error=null;label.isEnabled=print.isChecked;showMarks=print.isChecked
        text=value.ifBlank { "FILM FOUNDRY" }+" 01";preview.invalidate();return value
    }
    val watcher=object: TextWatcher {
        override fun beforeTextChanged(s: CharSequence?,start: Int,count: Int,after: Int)=Unit
        override fun onTextChanged(s: CharSequence?,start: Int,before: Int,count: Int) { update() }
        override fun afterTextChanged(s: Editable?)=Unit
    }
    for(input in listOf(label,batchInput,offsetInput)) input.addTextChangedListener(watcher)
    choices(c("片边形成方式","Rebate formation"),listOf("attached" to c("后加（默认）","Attached"),
        "native" to c("原生整片","Whole strip"))+
        (if(key=="135") listOf("sprocket_panorama" to c("135 装入 120","35 mm in 120")) else emptyList()),formation) {
        formation=it;preview.invalidate()
    }
    choices(c("条码","Barcode"),listOf("none" to c("关闭","Off"),"code128c" to "Code 128",
        "blocks" to c("双轨边码","Two-track")),codeStyle) { codeStyle=it;update() }
    choices(c("过片标记","Advance mark"),listOf("arrow" to c("箭头","Arrow"),"triangle" to "▶",
        "outline_pentagon" to c("镂空五边形","Outline pentagon"),"none" to c("仅片号","Number only")),marker) { marker=it;update() }
    panel.addView(CheckBox(activity).apply { text=c("向左过片","Advance left");isChecked=leftward
        setOnCheckedChangeListener { _,checked->leftward=checked;update() }
    })
    print.setOnCheckedChangeListener { _,_->update() };update()
    shell.action(panel,"编排上下边字…","Arrange factory marks…") {
        editEdgeComposition(activity,shell,patterns["factory_composition"]) { value ->
            patterns=patterns+("factory_composition" to value);preview.invalidate()
        }
    }
    shell.action(panel,"绘制片边图案…","Draw edge stencil…") {
        showEdgeMarkEditor(activity,shell,settings()) { result -> patterns=result;update();preview.invalidate() }
    }
    shell.action(panel,"预览冲洗与扫描样张…","Preview processed & scanned sample…") {
        update()?.let {
            val stock=if(key=="120") format else key
            val border=FilmEdgeSettings.create(stock,true,it.ifBlank { "FILM FOUNDRY" },marks=print.isChecked,patternSettings=settings())
            previewSample(mapOf("film_border" to if(key=="half_frame") border+("frame_number" to halfSlot) else border))
        }
    }
    val dialog=AlertDialog.Builder(activity).setTitle(c("齿孔与厂印边字","Sprockets & factory edge text")).setView(root)
        .setNegativeButton(c("取消","Cancel"),null).setPositiveButton(c("保存为新卷默认","Save for new rolls"),null).create()
    dialog.setOnShowListener { dialog.getButton(AlertDialog.BUTTON_POSITIVE).setOnClickListener { update()?.let { save(it,print.isChecked,settings());dialog.dismiss() } } }
    shell.decorate(panel);dialog.show()
}
