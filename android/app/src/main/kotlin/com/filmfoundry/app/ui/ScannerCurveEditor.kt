package com.filmfoundry.app.ui

import android.app.AlertDialog
import android.graphics.*
import android.view.*
import android.widget.*
import androidx.activity.ComponentActivity
import com.filmfoundry.interchange.*
import java.util.Locale

/** Transactional tone/RGB point editor. This view never processes pixels. */
fun editScannerCurves(activity: ComponentActivity,shell: MobileShell,scanner: Map<String,Any?>,apply: (Map<String,Any?>)->Unit) {
    val curves=Array(4) { PiecewiseCurve.identity() }
    scanner["tone_curve"]?.let { curves[0]=PiecewiseCurve.parse(it) }
    scanner["color_curve_rgb"]?.let { raw -> val channels=raw as? List<*> ?: error("RGB curves need three channels");require(channels.size == 3);repeat(3){curves[it+1]=PiecewiseCurve.parse(channels[it],true)} }
    val panel=LinearLayout(activity).apply { orientation=LinearLayout.VERTICAL;setPadding(shell.dp(12),0,shell.dp(12),shell.dp(8)) }
    val tone=CheckBox(activity).apply { text=shell.copy("启用影调曲线","Enable tone curve");isChecked=scanner["tone_curve"] != null };panel.addView(tone)
    val rgb=CheckBox(activity).apply { text=shell.copy("启用 RGB 曲线","Enable RGB curves");isChecked=scanner["color_curve_rgb"] != null };panel.addView(rgb)
    val channel=Spinner(activity).apply { adapter=ArrayAdapter(activity,android.R.layout.simple_spinner_dropdown_item,listOf(shell.copy("影调","Tone"),"R","G","B")) };panel.addView(channel)
    shell.note(panel,"双击曲线附近加点；拖动调整。选中内部点后可删除，端点保留。","Double tap near the curve to add a point; drag to adjust. Select an interior point to delete it; endpoints stay.")
    val graph=CurveGraph(activity,shell);panel.addView(graph,LinearLayout.LayoutParams(-1,shell.dp(240)))
    val coordinate=LinearLayout(activity);panel.addView(coordinate)
    val x=EditText(activity).apply { hint="X";inputType=android.text.InputType.TYPE_CLASS_NUMBER or android.text.InputType.TYPE_NUMBER_FLAG_DECIMAL }
    val y=EditText(activity).apply { hint="Y";inputType=x.inputType or android.text.InputType.TYPE_NUMBER_FLAG_SIGNED }
    coordinate.addView(x,LinearLayout.LayoutParams(0,-2,1f));coordinate.addView(y,LinearLayout.LayoutParams(0,-2,1f))
    val actions=LinearLayout(activity);panel.addView(actions)
    fun refresh() { graph.curve=curves[channel.selectedItemPosition];graph.invalidate() }
    fun selection() { graph.curve.points.getOrNull(graph.selected)?.let { x.setText(String.format(Locale.ROOT,"%.4f",it.x));y.setText(String.format(Locale.ROOT,"%.4f",it.y)) } }
    fun edited(value: PiecewiseCurve) { val c=channel.selectedItemPosition;curves[c]=value;if(c == 0) tone.isChecked=true else rgb.isChecked=true;refresh();selection() }
    graph.changed=::edited;graph.selectionChanged=::selection
    shell.action(actions,"坐标应用","Set point") {
        runCatching { edited(graph.curve.move(graph.selected,x.text.toString().toDouble(),y.text.toString().toDouble())) }.onFailure { x.error=it.message }
    }.layoutParams=LinearLayout.LayoutParams(0,-2,1f)
    shell.action(actions,"删除点","Delete point") { runCatching { edited(graph.curve.remove(graph.selected));graph.selected=0;selection() }.onFailure { x.error=shell.copy("端点保留","Keep end points") } }.layoutParams=LinearLayout.LayoutParams(0,-2,1f)
    shell.action(panel,"当前通道恢复直线","Reset this channel") { edited(PiecewiseCurve.identity());graph.selected=0;selection() }
    channel.onItemSelectedListener=object: AdapterView.OnItemSelectedListener {
        override fun onNothingSelected(parent: AdapterView<*>?)=Unit
        override fun onItemSelected(parent: AdapterView<*>?,view: View?,position: Int,id: Long) { graph.selected=0;refresh();selection() }
    }
    lateinit var dialog: AlertDialog
        shell.resetSection(panel,shell.copy("全部曲线","All curves")) { dialog.dismiss();editScannerCurves(activity,shell,scanner,apply) }
        dialog=AlertDialog.Builder(activity).setTitle(shell.copy("影调与 RGB 曲线","Tone & RGB curves")).setView(ScrollView(activity).apply { addView(panel) })
        .setNegativeButton(shell.copy("取消","Cancel"),null).setPositiveButton(shell.copy("应用","Apply")) { _,_ ->
            apply(mapOf("tone_curve" to if(tone.isChecked) curves[0].payload() else null,
                "color_curve_rgb" to if(rgb.isChecked) (1..3).map { curves[it].payload() } else null))
        }.create()
    refresh();selection();dialog.show()
}

/** Live scan-page tool. The independent definition editor above remains transactional. */
class ScannerCurvePanel(private val activity: ComponentActivity,private val shell: MobileShell,
    private val changed: (Map<String,Any?>)->Unit,private val committed: ()->Unit,
    private val started: ()->Unit={}): LinearLayout(activity) {
    private val state=ScannerCurveState()
    private val graph=CurveGraph(activity,shell)
    private val tabs=mutableListOf<View>()
    private val enabled=CheckBox(activity)
    private val coordinates=Button(activity)
    private val remove=Button(activity)
    private val histogramCaption=TextView(activity)
    private var histogram: DisplayHistogram?=null
    private var binding=false
    private var sourceKey: String?=null
    init {
        orientation=VERTICAL
        val channels=LinearLayout(activity).apply { gravity=Gravity.CENTER };addView(channels)
        listOf(shell.copy("影调","Tone"),"R","G","B").forEachIndexed { index,label ->
            val dot=CurveChannelDot(activity,shell,index).apply {
                contentDescription=label;isFocusable=true;setOnClickListener { state.select(index);graph.selected=0;refresh() }
            }
            tabs+=dot;channels.addView(dot,LayoutParams(shell.dp(48),shell.dp(44)))
        }
        histogramCaption.textSize=11f;addView(histogramCaption)
        addView(graph,LayoutParams(-1,shell.dp(150)))
        val tools=LinearLayout(activity).apply { gravity=Gravity.CENTER_VERTICAL };addView(tools)
        enabled.text=shell.copy("启用","On");tools.addView(enabled,LayoutParams(0,shell.dp(44),.8f))
        enabled.setOnCheckedChangeListener { _,checked -> if(!binding) { started();state.enable(checked);changed(state.payload());committed() } }
        shell.styleButton(coordinates);coordinates.minWidth=0;coordinates.textSize=12f;coordinates.setPadding(0,0,0,0)
        coordinates.contentDescription=shell.copy("输入当前点坐标","Enter selected point coordinates")
        tools.addView(coordinates,LayoutParams(0,shell.dp(44),1.5f));coordinates.setOnClickListener { editPoint() }
        remove.text="−";remove.contentDescription=shell.copy("删除选中点，端点保留","Delete selected point; endpoints stay")
        shell.styleButton(remove);remove.minWidth=0;remove.setPadding(0,0,0,0);tools.addView(remove,LayoutParams(0,shell.dp(44),.6f))
        remove.setOnClickListener { runCatching { val next=state.curve.remove(graph.selected);started();graph.selected=0;edit(next);committed() } }
        shell.action(tools,"↺","↺") { started();graph.selected=0;edit(PiecewiseCurve.identity());committed() }.apply {
            layoutParams=LayoutParams(0,shell.dp(44),.6f);minWidth=0;setPadding(0,0,0,0)
            contentDescription=shell.copy("当前通道恢复直线","Reset this channel")
        }
        graph.contentDescription=shell.copy("双击曲线加点，拖动调整；点坐标可精确输入","Double tap the curve to add a point; drag to adjust, or tap coordinates for precise input")
        graph.changed=::edit;graph.selectionChanged=::showPoint;graph.editStarted=started;graph.editFinished=committed
        refresh()
    }
    fun bind(scanner: Map<String,Any?>,key: String) {
        if(state.bind(scanner,force=sourceKey!=key)) refresh()
        sourceKey=key
    }
    fun snapshotDraft()=state.snapshot()
    fun restoreDraft(scanner: Map<String,Any?>,key: String,draft: ScannerCurveState.Draft?) {
        state.restore(scanner,draft);sourceKey=key;refresh()
    }
    fun bindHistogram(value: DisplayHistogram?,caption: String) {
        histogram=value;histogramCaption.text=caption+shell.copy(" · 线性直方图"," · Linear histogram")
        graph.histogramTrace.bind(value,state.channel);graph.invalidate()
    }
    private fun refresh() {
        binding=true
        try {
            graph.curve=state.curve;graph.selected=graph.selected.coerceIn(0,state.curve.points.lastIndex)
            graph.curveColor=intArrayOf(Color.rgb(225,203,158),Color.rgb(239,119,112),Color.rgb(125,204,140),Color.rgb(123,166,240))[state.channel]
            graph.histogramTrace.bind(histogram,state.channel)
            enabled.isChecked=state.enabled;tabs.forEachIndexed { i,tab->tab.isSelected=i==state.channel };showPoint();graph.invalidate()
        } finally { binding=false }
    }
    private fun edit(curve: PiecewiseCurve) { state.edit(curve);refresh();changed(state.payload()) }
    private fun showPoint() {
        state.curve.points.getOrNull(graph.selected)?.let { coordinates.text=String.format(Locale.ROOT,"%.3f / %.3f",it.x,it.y) }
        remove.isEnabled=graph.selected in 1 until state.curve.points.lastIndex
    }
    private fun editPoint() {
        val point=state.curve.points[graph.selected]
        val panel=LinearLayout(activity).apply { orientation=VERTICAL;setPadding(shell.dp(20),0,shell.dp(20),shell.dp(8)) }
        val x=EditText(activity).apply { inputType=android.text.InputType.TYPE_CLASS_NUMBER or android.text.InputType.TYPE_NUMBER_FLAG_DECIMAL;setText(point.x.toString()) }
        val y=EditText(activity).apply { inputType=x.inputType or android.text.InputType.TYPE_NUMBER_FLAG_SIGNED;setText(point.y.toString()) }
        shell.field(panel,x,shell.copy("输入 X · 0–1","Input X · 0–1"));shell.field(panel,y,shell.copy("输出 Y","Output Y"))
        val dialog=AlertDialog.Builder(activity).setTitle(shell.copy("当前点坐标","Selected point")).setView(panel)
            .setNegativeButton(shell.copy("取消","Cancel"),null).setPositiveButton(shell.copy("应用","Apply"),null).create()
        dialog.setOnShowListener { dialog.getButton(AlertDialog.BUTTON_POSITIVE).setOnClickListener {
            runCatching {
                val px=x.text.toString().toDouble();val py=y.text.toString().toDouble()
                require(px.isFinite() && px in 0.0..1.0 && py.isFinite()) { shell.copy("X 需在 0–1，Y 需为有限数值","X must be in 0–1; Y must be finite") }
                state.curve.move(graph.selected,px,py)
            }
                .onSuccess { started();edit(it);committed();dialog.dismiss() }.onFailure { x.error=it.message }
        } };dialog.show()
    }
}

internal class CurveChannelDot(context: android.content.Context,private val shell: MobileShell,private val channel: Int): View(context) {
    private val paint=Paint(Paint.ANTI_ALIAS_FLAG)
    override fun drawableStateChanged() { super.drawableStateChanged();invalidate() }
    override fun onDraw(canvas: Canvas) {
        val x=width/2f;val y=height/2f;val radius=shell.dp(9).toFloat()
        paint.style=Paint.Style.FILL
        paint.color=intArrayOf(Color.WHITE,Color.rgb(239,104,98),Color.rgb(112,195,128),Color.rgb(107,155,235))[channel]
        canvas.drawCircle(x,y,radius,paint)
        if(channel==0) { paint.color=Color.BLACK;canvas.drawArc(x-radius,y-radius,x+radius,y+radius,90f,180f,true,paint) }
        paint.style=Paint.Style.STROKE;paint.strokeWidth=shell.dp(1).toFloat();paint.color=if(shell.ivory) Color.DKGRAY else Color.LTGRAY
        canvas.drawCircle(x,y,radius,paint)
        if(isSelected) { paint.strokeWidth=shell.dp(2).toFloat();paint.color=if(shell.ivory) Color.rgb(129,92,46) else Color.rgb(215,178,120);canvas.drawCircle(x,y,shell.dp(15).toFloat(),paint) }
    }
}

private class CurveGraph(context: android.content.Context,private val shell: MobileShell): View(context) {
    var curve=PiecewiseCurve.identity()
    var selected=0
    var changed: (PiecewiseCurve)->Unit={}
    var selectionChanged: ()->Unit={}
    var editFinished: ()->Unit={}
    var editStarted: ()->Unit={}
    var curveColor=Color.rgb(225,203,158)
    val histogramTrace=HistogramTrace()
    private val paint=Paint(Paint.ANTI_ALIAS_FLAG)
    private val margin=shell.dp(16).toFloat()
    private var dragging=false
    private fun px(x: Double)=margin+x.toFloat()*(width-2*margin)
    private fun py(y: Double)=height-margin-y.toFloat()*(height-2*margin)
    private fun point(e: MotionEvent)=CurvePoint(((e.x-margin)/(width-2*margin)).toDouble().coerceIn(0.0,1.0),((height-margin-e.y)/(height-2*margin)).toDouble().coerceIn(0.0,1.0))
    private fun nearest(e: MotionEvent)=curve.points.indices.minByOrNull { val p=curve.points[it];(px(p.x)-e.x)*(px(p.x)-e.x)+(py(p.y)-e.y)*(py(p.y)-e.y) }?.takeIf {
        kotlin.math.hypot((px(curve.points[it].x)-e.x).toDouble(),(py(curve.points[it].y)-e.y).toDouble()) <= shell.dp(22)
    }
    private val gestures=GestureDetector(context,object: GestureDetector.SimpleOnGestureListener() {
        override fun onDown(e: MotionEvent)=true
        override fun onDoubleTap(e: MotionEvent): Boolean {
            val existing=nearest(e)
            if(existing != null) { selected=existing;selectionChanged();invalidate();return true }
            val p=point(e)
            val close=curve.points.zipWithNext().any { (a,b) ->
                val ax=px(a.x);val ay=py(a.y);val dx=px(b.x)-ax;val dy=py(b.y)-ay
                val t=if(dx*dx+dy*dy == 0f) 0f else (((e.x-ax)*dx+(e.y-ay)*dy)/(dx*dx+dy*dy)).coerceIn(0f,1f)
                kotlin.math.hypot((e.x-ax-t*dx).toDouble(),(e.y-ay-t*dy).toDouble()) <= shell.dp(24)
            }
            if(close) runCatching { val value=curve.insert(p.x,p.y);selected=value.points.indexOfFirst { it.x == p.x };changed(value) }
            return true
        }
    })
    override fun onDraw(canvas: Canvas) {
        canvas.drawColor(Color.rgb(26,26,26))
        histogramTrace.draw(canvas,margin,margin,width-margin,height-margin,Color.argb(80,Color.red(curveColor),Color.green(curveColor),Color.blue(curveColor)))
        paint.color=Color.rgb(65,65,65);paint.strokeWidth=1f
        for(i in 0..4) { val f=i/4.0;canvas.drawLine(px(f),py(0.0),px(f),py(1.0),paint);canvas.drawLine(px(0.0),py(f),px(1.0),py(f),paint) }
        canvas.save();canvas.clipRect(margin,margin,width-margin,height-margin)
        paint.color=curveColor;paint.strokeWidth=shell.dp(2).toFloat()
        curve.points.zipWithNext().forEach { (a,b)->canvas.drawLine(px(a.x),py(a.y),px(b.x),py(b.y),paint) }
        curve.points.forEachIndexed { i,p -> paint.color=if(i == selected) Color.WHITE else curveColor;canvas.drawCircle(px(p.x),py(p.y),shell.dp(if(i == selected) 6 else 4).toFloat(),paint) }
        canvas.restore()
    }
    override fun performClick(): Boolean { super.performClick();return true }
    override fun onTouchEvent(event: MotionEvent): Boolean {
        if(event.actionMasked==MotionEvent.ACTION_DOWN) editStarted()
        gestures.onTouchEvent(event)
        when(event.actionMasked) {
            MotionEvent.ACTION_DOWN -> { nearest(event)?.let { selected=it;dragging=true;selectionChanged();invalidate() };parent.requestDisallowInterceptTouchEvent(true) }
            MotionEvent.ACTION_MOVE -> if(dragging && event.pointerCount == 1) { val p=point(event);runCatching { changed(curve.move(selected,p.x,p.y)) } }
            MotionEvent.ACTION_UP,MotionEvent.ACTION_CANCEL -> { dragging=false;parent.requestDisallowInterceptTouchEvent(false);editFinished();performClick() }
        }
        return true
    }
}
