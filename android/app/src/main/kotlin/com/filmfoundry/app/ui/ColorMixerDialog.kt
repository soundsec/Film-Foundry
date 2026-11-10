package com.filmfoundry.app.ui

import android.app.AlertDialog
import android.view.View
import android.widget.*
import com.filmfoundry.interchange.ColorMixerSettings
import kotlin.math.roundToInt

internal fun showColorMixer(shell: MobileShell, parent: View, value: Any?,
    changed: (List<List<Double>>)->Unit, closed: (Boolean)->Unit) {
    ColorMixerSettings.normalize(value)
    val rows=Array(8) { i -> DoubleArray(3) { c ->
        (((value as? List<*>)?.get(i) as? List<*>)?.get(c) as? Number)?.toDouble() ?: 0.0
    } }
    val context=parent.context
    val body=LinearLayout(context).apply { orientation=LinearLayout.VERTICAL;setPadding(shell.dp(16),shell.dp(8),shell.dp(16),shell.dp(8)) }
    val colors=Spinner(context)
    colors.adapter=ArrayAdapter(context,android.R.layout.simple_spinner_dropdown_item,
        listOf("红" to "Red","橙" to "Orange","黄" to "Yellow","绿" to "Green","青" to "Aqua","蓝" to "Blue","紫" to "Purple","洋红" to "Magenta").map { shell.copy(it.first,it.second) })
    body.addView(colors)
    val bars=mutableListOf<SeekBar>();val captions=mutableListOf<TextView>()
    val names=listOf(shell.copy("色相 · °","Hue · °"),shell.copy("饱和度","Saturation"),shell.copy("明度","Lightness"))
    fun publish()=changed(rows.map { it.toList() })
    fun refresh() {
        for(c in 0..2) { val v=rows[colors.selectedItemPosition.coerceAtLeast(0)][c].roundToInt()
            bars[c].progress=v+(if(c==0) 30 else 100);captions[c].text="${names[c]}  $v" }
    }
    for(c in 0..2) {
        val caption=TextView(context);captions+=caption;body.addView(caption)
        val bar=SeekBar(context);bars+=bar;bar.max=if(c==0) 60 else 200;body.addView(bar)
        bar.contentDescription=names[c]
        bar.setOnSeekBarChangeListener(object: SeekBar.OnSeekBarChangeListener {
            override fun onProgressChanged(view: SeekBar?, progress: Int, user: Boolean) {
                if(user) { val v=progress-(if(c==0) 30 else 100);rows[colors.selectedItemPosition][c]=v.toDouble();caption.text="${names[c]}  $v";publish() }
            }
            override fun onStartTrackingTouch(view: SeekBar?) {}
            override fun onStopTrackingTouch(view: SeekBar?) {}
        })
    }
    colors.onItemSelectedListener=object: AdapterView.OnItemSelectedListener {
        override fun onItemSelected(p: AdapterView<*>?,v: View?,position: Int,id: Long)=refresh()
        override fun onNothingSelected(p: AdapterView<*>?) {}
    }
    for(all in listOf(false,true)) body.addView(Button(context).apply {
        text=if(all) shell.copy("重置全部颜色","Reset all colors") else shell.copy("重置当前颜色","Reset selected color")
        setOnClickListener { if(all) rows.forEach { it.fill(0.0) } else rows[colors.selectedItemPosition].fill(0.0);refresh();publish() }
    })
    var accepted=false
    val dialog=AlertDialog.Builder(context).setTitle(shell.copy("分色 HSL","Color Mixer · HSL"))
        .setView(ScrollView(context).apply { addView(body) })
        .setPositiveButton(shell.copy("完成","Done")) { _,_->accepted=true }
        .setNegativeButton(shell.copy("取消","Cancel"),null).create()
    dialog.setOnDismissListener { closed(accepted) };dialog.show();refresh()
}
