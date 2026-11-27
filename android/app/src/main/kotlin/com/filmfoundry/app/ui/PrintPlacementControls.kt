package com.filmfoundry.app.ui

import android.widget.LinearLayout
import android.widget.SeekBar
import android.widget.TextView
import kotlin.math.roundToInt
import kotlin.math.log2

/** Normalized text placement, shared by the camera and settings editors. */
class PrintPlacementControls(private val shell: MobileShell,initial: Map<String,Any?>,compact: Boolean=false,
    private val changed: ()->Unit): LinearLayout(shell.root.context) {
    private val brightnessBar=shell.parameterRuler()
    private val xBar=shell.parameterRuler()
    private val yBar=shell.parameterRuler()
    val brightness get()=brightnessBar.progress/100.0
    val x get()=xBar.progress/100.0
    val y get()=yBar.progress/100.0
    init {
        orientation=VERTICAL
        fun slider(parent: LinearLayout,bar: SeekBar,zh: String,en: String,max: Int,value: Double) {
            val label=TextView(context).apply { textSize=12f };parent.addView(label)
            bar.max=max;bar.progress=(value*100).roundToInt().coerceIn(0,max)
            bar.contentDescription=shell.copy(zh,en);bar.minimumHeight=shell.dp(40)
            parent.addView(bar,LayoutParams(-1,-2))
            fun caption() {
                label.text=shell.copy(zh,en)+" · ${bar.progress}%" + if(bar===brightnessBar && bar.progress>0)
                    " (%+.2f EV)".format(java.util.Locale.ROOT,log2(bar.progress/100.0)) else ""
            }
            caption()
            bar.setOnSeekBarChangeListener(object: SeekBar.OnSeekBarChangeListener {
                override fun onProgressChanged(s: SeekBar?,p: Int,user: Boolean) { caption();changed() }
                override fun onStartTrackingTouch(s: SeekBar?)=Unit
                override fun onStopTrackingTouch(s: SeekBar?)=Unit
            })
        }
        slider(this,brightnessBar,"打印亮度","Imprint brightness",400,(initial["imprint_brightness"] as? Number)?.toDouble() ?: if(initial["imprint"]?.toString().isNullOrBlank()) 2.38 else 1.0)
        val corners=LinearLayout(context);addView(corners)
        listOf(Triple("左上","Top left",0 to 0),Triple("右上","Top right",100 to 0),
            Triple("左下","Bottom left",0 to 100),Triple("右下","Bottom right",100 to 100)).forEach { (zh,en,point)->
            shell.editorTab(corners,zh,en) { xBar.progress=point.first;yBar.progress=point.second }.apply {
                layoutParams=LayoutParams(0,-2,1f);minWidth=0;minimumWidth=0
            }
        }
        val coordinates=LinearLayout(context);addView(coordinates)
        val horizontal=LinearLayout(context).apply { orientation=VERTICAL };coordinates.addView(horizontal,LayoutParams(0,-2,1f))
        val vertical=LinearLayout(context).apply { orientation=VERTICAL };coordinates.addView(vertical,LayoutParams(0,-2,1f))
        slider(horizontal,xBar,"水平位置","Horizontal position",100,(initial["imprint_x"] as? Number)?.toDouble() ?: 0.0)
        slider(vertical,yBar,"垂直位置","Vertical position",100,(initial["imprint_y"] as? Number)?.toDouble() ?: if(initial["imprint_side"]=="top") 0.0 else 1.0)
        if(!compact) shell.note(this,"四角可快速定位，滑动位置可自由微调。新打印从 +1.25 EV 起，100% 为 0 EV。",
            "Choose a corner or adjust either position. New imprints start at +1.25 EV. 100% is 0 EV.")
    }
}
