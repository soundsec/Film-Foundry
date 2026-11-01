package com.filmfoundry.app.ui

import android.app.AlertDialog
import android.graphics.Canvas
import android.graphics.Paint
import android.graphics.Color
import android.view.MotionEvent
import android.view.View
import android.widget.*
import androidx.activity.ComponentActivity
import com.filmfoundry.interchange.EdgeMarkPattern
import com.filmfoundry.workflow.FilmEdgeSettings

/** Edits a small stencil only. Applying never changes a previously loaded roll. */
fun showEdgeMarkEditor(activity: ComponentActivity,shell: MobileShell,initial: Map<String,Any?>,save: (Map<String,Any?>)->Unit) {
    fun c(zh: String,en: String)=shell.copy(zh,en)
    val settings=FilmEdgeSettings.patterns(initial).toMutableMap()
    var active="factory_pattern_top"
    val panel=LinearLayout(activity).apply { orientation=LinearLayout.VERTICAL;setPadding(shell.dp(18),shell.dp(12),shell.dp(18),shell.dp(12)) }
    shell.note(panel,"点按切换方格，拖动连续涂画或擦除。图案替换材料名称区域，格号仍自动追加。", "Tap to toggle a cell; drag to draw or erase. The stencil replaces the stock-name area; frame numbers stay automatic.")
    val linked=CheckBox(activity).apply { text=c("上下共用图案","Use the same stencil");isChecked=settings["factory_pattern_linked"]==true };panel.addView(linked)
    val side=Spinner(activity).apply { adapter=ArrayAdapter(activity,android.R.layout.simple_spinner_dropdown_item,listOf(c("上片边","Top edge"),c("下片边","Bottom edge"))) };panel.addView(side)
    val flips=listOf("factory_flip_top" to c("上边翻转 180°","Top: rotate 180°"),"factory_flip_bottom" to c("下边翻转 180°","Bottom: rotate 180°"))
    for((key,title) in flips) panel.addView(CheckBox(activity).apply { text=title;isChecked=settings[key]==true;setOnCheckedChangeListener { _,value -> settings[key]=value } })
    panel.addView(TextView(activity).apply { text=c("成印方式","Marking method") })
    val method=Spinner(activity).apply {
        adapter=ArrayAdapter(activity,android.R.layout.simple_spinner_dropdown_item,listOf(c("清晰印字（曝光）","Hard-edged exposure"),c("激光式曝光","Laser-style exposure")))
        setSelection(if(settings["factory_mark_type"]=="laser") 1 else 0)
    };panel.addView(method)
    val spreadText=TextView(activity);panel.addView(spreadText)
    val spread=SeekBar(activity).apply { max=30;progress=((settings["factory_mark_spread"] as Number).toDouble()*10).toInt() };panel.addView(spread)
    fun refreshMethod() { spread.isEnabled=method.selectedItemPosition==1;spreadText.text=c("材料扩散倍率","Material spread")+" · "+String.format(java.util.Locale.ROOT,"%.1f",(settings["factory_mark_spread"] as Number).toDouble()) }
    method.onItemSelectedListener=object: AdapterView.OnItemSelectedListener {
        override fun onItemSelected(parent: AdapterView<*>?,view: View?,position: Int,id: Long) { settings["factory_mark_type"]=if(position==1) "laser" else "hard";refreshMethod() }
        override fun onNothingSelected(parent: AdapterView<*>?)=Unit
    }
    spread.setOnSeekBarChangeListener(object: SeekBar.OnSeekBarChangeListener {
        override fun onProgressChanged(bar: SeekBar?,value: Int,fromUser: Boolean) { settings["factory_mark_spread"]=value/10.0;refreshMethod() }
        override fun onStartTrackingTouch(bar: SeekBar?)=Unit
        override fun onStopTrackingTouch(bar: SeekBar?)=Unit
    });refreshMethod()
    val code=EditText(activity).apply { setSingleLine(false);maxLines=3;hint="FFEM1:…";setHorizontallyScrolling(false) }
    val grid=object: View(activity) {
        var columns=32;var rows=8;var cells=BooleanArray(columns*rows)
        var drawing: Boolean?=null;var previous: Pair<Int,Int>?=null
        var moveCanvas=false
        val cell=shell.dp(18);val paint=Paint()
        fun pattern()=EdgeMarkPattern.fromCells(columns,rows,cells)
        fun load(value: String) {
            val pattern=value.takeIf { it.isNotBlank() }?.let(EdgeMarkPattern::decode) ?: EdgeMarkPattern(32,8,ByteArray(32))
            columns=pattern.width;rows=pattern.height;cells=BooleanArray(columns*rows) { pattern.ink(it%columns,it/columns) }
            layoutParams=LinearLayout.LayoutParams(columns*cell+1,rows*cell+1);requestLayout();invalidate()
        }
        override fun onDraw(canvas: Canvas) {
            canvas.drawColor(Color.rgb(41,36,31));paint.color=Color.rgb(224,179,111)
            for(y in 0 until rows) for(x in 0 until columns) if(cells[y*columns+x]) canvas.drawRect((x*cell).toFloat(),(y*cell).toFloat(),((x+1)*cell).toFloat(),((y+1)*cell).toFloat(),paint)
            paint.color=Color.rgb(102,93,83);paint.strokeWidth=1f
            for(x in 0..columns) canvas.drawLine((x*cell).toFloat(),0f,(x*cell).toFloat(),height.toFloat(),paint)
            for(y in 0..rows) canvas.drawLine(0f,(y*cell).toFloat(),width.toFloat(),(y*cell).toFloat(),paint)
        }
        override fun onTouchEvent(event: MotionEvent): Boolean {
            if(moveCanvas) return false
            if(event.actionMasked==MotionEvent.ACTION_UP || event.actionMasked==MotionEvent.ACTION_CANCEL) {
                drawing=null;previous=null;parent.requestDisallowInterceptTouchEvent(false);return true
            }
            if(event.actionMasked!=MotionEvent.ACTION_DOWN && event.actionMasked!=MotionEvent.ACTION_MOVE) return false
            if(event.x<0 || event.y<0) return true
            val x=(event.x/cell).toInt();val y=(event.y/cell).toInt();if(x !in 0 until columns || y !in 0 until rows) return true
            if(event.actionMasked==MotionEvent.ACTION_DOWN) { drawing=!cells[y*columns+x];previous=null;parent.requestDisallowInterceptTouchEvent(true) }
            val value=drawing ?: return true;val last=previous ?: (x to y);val count=maxOf(kotlin.math.abs(x-last.first),kotlin.math.abs(y-last.second),1)
            for(i in 0..count) { val px=kotlin.math.round(last.first+(x-last.first)*i.toDouble()/count).toInt();val py=kotlin.math.round(last.second+(y-last.second)*i.toDouble()/count).toInt();cells[py*columns+px]=value }
            previous=x to y;settings[active]=pattern().encode();invalidate();return true
        }
    }
    // The grid scrolls horizontally instead of shrinking touch targets to tiny cells.
    val holder=LinearLayout(activity).apply { orientation=LinearLayout.VERTICAL;addView(grid) }
    panel.addView(HorizontalScrollView(activity).apply { isHorizontalScrollBarEnabled=true;isScrollbarFadingEnabled=false;addView(holder) })
    panel.addView(CheckBox(activity).apply { text=c("移动画布","Move canvas");setOnCheckedChangeListener { _,value -> grid.moveCanvas=value } })
    fun reload() { grid.load(settings[active]?.toString().orEmpty());code.setText(settings[active]?.toString().orEmpty());code.error=null }
    fun syncSide() { side.isEnabled=!linked.isChecked;if(linked.isChecked) side.setSelection(0);active=if(side.selectedItemPosition==1 && !linked.isChecked) "factory_pattern_bottom" else "factory_pattern_top";reload() }
    side.onItemSelectedListener=object: AdapterView.OnItemSelectedListener {
        override fun onItemSelected(parent: AdapterView<*>?,view: View?,position: Int,id: Long) { syncSide() }
        override fun onNothingSelected(parent: AdapterView<*>?)=Unit
    }
    linked.setOnCheckedChangeListener { _,value -> settings["factory_pattern_linked"]=value;syncSide() }
    shell.action(panel,"清空图案","Clear stencil") { grid.cells.fill(false);settings[active]=grid.pattern().encode();grid.invalidate();code.setText(settings[active].toString()) }
    shell.action(panel,"恢复材料文字","Use stock text") { settings[active]="";reload() }
    shell.field(panel,code,c("图案编码","Stencil code"),c("粘贴后点导入。编码只保存方格，不是可读条码标准。","Paste and tap Import. This code stores cells, not a barcode standard."))
    shell.action(panel,"导入编码","Import code") {
        runCatching { EdgeMarkPattern.decode(code.text.toString()).encode() }.onSuccess { settings[active]=it;reload() }
            .onFailure { code.error=c("编码无效，最大128×32格","Invalid code; maximum size is 128×32 cells") }
    }
    shell.action(panel,"生成编码","Generate code") { code.setText(grid.pattern().encode());code.selectAll() }
    syncSide()
    val dialog=AlertDialog.Builder(activity).setTitle(c("片边点阵图案","Film-edge stencil"))
        .setView(ScrollView(activity).apply { addView(panel) }).setNegativeButton(c("取消","Cancel"),null)
        .setPositiveButton(c("应用","Apply")) { _,_-> save(FilmEdgeSettings.patterns(settings)) }.create()
    shell.decorate(panel);dialog.show()
}
