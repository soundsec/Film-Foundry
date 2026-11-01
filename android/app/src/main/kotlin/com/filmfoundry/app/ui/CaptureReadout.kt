package com.filmfoundry.app.ui

import android.graphics.Color
import android.graphics.Typeface
import android.graphics.drawable.GradientDrawable
import android.widget.*

/** Event-driven status panel: no timer, camera-frame analysis, bitmap or custom typeface. */
class CaptureReadout(private val shell: MobileShell): LinearLayout(shell.root.context) {
    private val values=linkedMapOf<String,TextView>()
    private val cells=linkedMapOf<String,LinearLayout>()
    init {
        orientation=VERTICAL;gravity=android.view.Gravity.CENTER_VERTICAL
        setPadding(shell.dp(6),shell.dp(5),shell.dp(6),shell.dp(5))
        background=GradientDrawable(GradientDrawable.Orientation.TOP_BOTTOM,intArrayOf(0xFF151A17.toInt(),0xFF080C0A.toInt())).apply {
            setStroke(shell.dp(1),0xFF343B32.toInt());cornerRadius=shell.dp(10).toFloat()
        }
        for((key,label) in listOf("iso" to "ISO","ev" to "EV","shutter" to shell.copy("快门","SPEED"),"wb" to "WB","lock" to shell.copy("AE 锁","AE LOCK"),"frame" to shell.copy("进度","FRAME"),"multi" to shell.copy("多曝","MULTI"),"print" to shell.copy("打印","PRINT"))) {
            val cell=LinearLayout(context).apply { orientation=VERTICAL;gravity=android.view.Gravity.CENTER;setPadding(shell.dp(4),0,shell.dp(4),0) }
            cells[key]=cell
            cell.background=android.graphics.drawable.StateListDrawable().apply {
                addState(intArrayOf(android.R.attr.state_selected),GradientDrawable().apply { setColor(0xFF384231.toInt());cornerRadius=shell.dp(8).toFloat() })
                addState(intArrayOf(android.R.attr.state_pressed),android.graphics.drawable.ColorDrawable(0xFF303B2E.toInt()))
            }
            addView(cell,LayoutParams(-1,0,1f))
            cell.addView(TextView(context).apply {
                text=label;setTextColor(0xFF829080.toInt());gravity=android.view.Gravity.CENTER;typeface=Typeface.MONOSPACE
                shell.fitFinderText(this,9f,7)
            },LayoutParams(-1,0,.4f))
            values[key]=TextView(context).apply {
                text="--";textSize=16f;setTextColor(0xFFE8BF6D.toInt());typeface=Typeface.create("monospace",Typeface.BOLD)
                gravity=android.view.Gravity.CENTER;shell.fitFinderText(this,16f,8)
                setShadowLayer(shell.dp(1).toFloat(),0f,0f,0x556BD48F)
                cell.addView(this,LayoutParams(-1,0,.6f))
            }
        }
    }
    fun value(key: String,value: String,description: String,active: Boolean=true) {
        val field=values.getValue(key)
        if(field.text.toString()!=value) field.text=value
        field.contentDescription=description
        cells.getValue(key).contentDescription=description+
            (if(cells.getValue(key).isClickable) shell.copy("；按下展开快捷控制","; press for quick controls") else "")+
            if(key=="ev") shell.copy("；双击恢复0 EV","; double tap to reset to 0 EV") else ""
        field.setTextColor(if(active) 0xFFE8BF6D.toInt() else Color.rgb(120,132,117))
    }
    fun onControl(key: String,action: (android.view.View)->Unit) { cells.getValue(key).apply { isClickable=true;isFocusable=true;setOnClickListener { action(this) };shell.quickControls.bindPress(this) } }
    fun onReset(key: String,action: ()->Unit) { shell.quickControls.bindPress(cells.getValue(key),action) }
}
