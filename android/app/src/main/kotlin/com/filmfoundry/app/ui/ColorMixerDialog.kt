package com.filmfoundry.app.ui

import android.graphics.Color
import android.graphics.drawable.GradientDrawable
import android.widget.*
import com.filmfoundry.interchange.ColorMixerSettings
import kotlin.math.roundToInt

/** Inline editor for the scanner's eight existing H/S/L rows. */
internal class ColorMixerPanel(
    private val shell: MobileShell, parent: LinearLayout,
    private val editable: () -> Boolean,
    private val start: () -> Unit, private val finish: () -> Unit,
    private val changed: (List<List<Double>>) -> Unit
) {
    private val context = parent.context
    private val rows = Array(8) { DoubleArray(3) }
    private val buttons = mutableListOf<Button>()
    private val bars = mutableListOf<ParameterRuler>()
    private val captions = mutableListOf<TextView>()
    private val names = listOf(shell.copy("色相 · °","Hue · °"), shell.copy("饱和度","Saturation"), shell.copy("明度","Lightness"))
    private val colors = listOf("红" to "Red","橙" to "Orange","黄" to "Yellow","绿" to "Green","青" to "Aqua","蓝" to "Blue","紫" to "Purple","洋红" to "Magenta")
    private var selected = 0
    private var editing = false

    init {
        val strip = LinearLayout(context)
        parent.addView(HorizontalScrollView(context).apply { isHorizontalScrollBarEnabled=false;addView(strip) })
        val paints = listOf("#d65248","#ee9244","#e4c647","#589952","#4db9b4","#588ce5","#9873c6","#d361ac")
        colors.forEachIndexed { index, name ->
            val button = shell.editorTab(strip,name.first,name.second) { selected=index; refresh() }
            val mark = GradientDrawable().apply {
                shape=GradientDrawable.OVAL
                setColor(Color.parseColor(paints[index]))
                setBounds(0,0,shell.dp(14),shell.dp(14))
            }
            button.setCompoundDrawables(mark,null,null,null)
            button.compoundDrawablePadding=shell.dp(4)
            button.minHeight=shell.dp(48)
            buttons.add(button)
        }
        names.forEachIndexed { channel, name ->
            val caption=TextView(context).apply { setTextColor(shell.ink) }
            captions.add(caption)
            parent.addView(caption)
            val bar=shell.parameterRuler()
            bars.add(bar)
            bar.max=if(channel==0) 60 else 200
            bar.contentDescription=name
            bar.resetValue = {
                if (editable() && rows[selected][channel] != 0.0) {
                    start()
                    rows[selected][channel] = 0.0
                    publish()
                    finish()
                }
            }
            parent.addView(bar)
            bar.setOnSeekBarChangeListener(object : SeekBar.OnSeekBarChangeListener {
                override fun onStartTrackingTouch(view: SeekBar) {
                    editing=editable()
                    if(editing) start()
                }
                override fun onProgressChanged(view: SeekBar, progress: Int, user: Boolean) {
                    if(!user) return
                    if(!editable()) { refresh();return }
                    if(!editing) start()
                    rows[selected][channel]=(progress-view.max/2).toDouble()
                    publish()
                    if(!editing) finish()
                }
                override fun onStopTrackingTouch(view: SeekBar) {
                    if(editing) finish()
                    editing=false
                }
            })
        }
        for(all in listOf(false,true)) {
            shell.action(parent,if(all) "重置全部颜色" else "重置当前颜色",if(all) "Reset all colors" else "Reset selected color") {
                if(editable()) {
                    start()
                    if(all) rows.forEach { it.fill(0.0) } else rows[selected].fill(0.0)
                    publish()
                    finish()
                }
            }
        }
        refresh()
    }

    fun bind(value: Any?) {
        ColorMixerSettings.normalize(value)
        val source=value as? List<*>
        for(i in rows.indices) {
            val row=source?.get(i) as? List<*>
            for(c in 0..2) rows[i][c]=(row?.get(c) as? Number)?.toDouble() ?: 0.0
        }
        refresh()
    }

    private fun publish() {
        refresh()
        changed(rows.map { it.toList() })
    }

    private fun refresh() {
        if (!buttons[selected].isSelected) shell.selectEditorTab(buttons,selected)
        buttons.forEachIndexed { i, button ->
            button.text=shell.copy(colors[i].first,colors[i].second)+(if(rows[i].any { it!=0.0 }) " •" else "")
        }
        bars.forEachIndexed { c,bar ->
            val v=rows[selected][c].roundToInt()
            bar.progress=v+bar.max/2
            captions[c].text="${names[c]}  $v"
        }
    }
}
