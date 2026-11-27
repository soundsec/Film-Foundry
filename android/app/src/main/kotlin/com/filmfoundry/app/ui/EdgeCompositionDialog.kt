package com.filmfoundry.app.ui

import android.app.AlertDialog
import android.text.InputType
import android.view.View
import android.widget.*
import androidx.activity.ComponentActivity
import com.filmfoundry.interchange.EdgeComposition

internal fun editEdgeComposition(activity: ComponentActivity, shell: MobileShell, value: Any?,
                                 save: (Map<String, Any>?) -> Unit) {
    fun c(zh: String,en: String) = shell.copy(zh,en)
    val plate = EdgeComposition.parse(value) ?: EdgeComposition.initial
    val panel = LinearLayout(activity).apply { orientation=LinearLayout.VERTICAL; setPadding(shell.dp(16),0,shell.dp(16),0) }
    val custom = CheckBox(activity).apply { text=c("自定义上下片边","Custom edge plate"); isChecked=value!=null }
    panel.addView(custom)
    val controls = LinearLayout(activity).apply { orientation=LinearLayout.VERTICAL }
    panel.addView(controls)
    fun choices(labels: List<String>, initial: Int, change: (Int)->Unit) {
        val group=RadioGroup(activity).apply { orientation=LinearLayout.HORIZONTAL }
        val buttons=labels.map { label -> RadioButton(activity).apply {
            id=View.generateViewId(); text=label; minHeight=shell.dp(48); group.addView(this)
        } }
        group.check(buttons[initial].id)
        group.setOnCheckedChangeListener { _,id -> change(buttons.indexOfFirst { it.id==id }) }
        controls.addView(HorizontalScrollView(activity).apply { isHorizontalScrollBarEnabled=false;addView(group) })
    }
    var font=plate.font
    val faces=listOf("grid","rounded","sans")
    choices(listOf(c("方格体","Square grid"),c("圆笔黑体","Rounded sans"),c("细线印刷体","Light sans")),faces.indexOf(font)) {
        font=faces[it]
    }
    val sides=listOf("top","bottom")
    val panels=sides.map { LinearLayout(activity).apply { orientation=LinearLayout.VERTICAL } }
    choices(listOf(c("上边","Top"),c("下边","Bottom")),0) { selected ->
        panels.forEachIndexed { i,p -> p.visibility=if(i==selected) View.VISIBLE else View.GONE }
    }
    shell.note(controls,"起点、终点按片宽百分比填写。起点较小的项目印在左边。","Enter start and end as percentages of frame width. Earlier starts print farther left.")
    val names=listOf(c("文字 / 厂标","Text / stencil"),c("片号","Frame"),c("方向","Direction"),c("编码","Code"))
    val defaults=listOf(4 to 45,48 to 55,58 to 64,66 to 99)
    val read=sides.mapIndexed { index,side ->
        val rows=if(index==0) plate.top else plate.bottom
        val body=panels[index]
        body.visibility=if(index==0) View.VISIBLE else View.GONE
        controls.addView(body)
        EdgeComposition.kinds.mapIndexed { i,kind ->
            val current=rows.find { it.kind==kind }
            val line=LinearLayout(activity)
            body.addView(line)
            val on=CheckBox(activity).apply { text=names[i]; isChecked=current!=null }
            line.addView(on,LinearLayout.LayoutParams(0,-2,1.5f))
            fun input(title: String, v: Double): EditText {
                val entry=EditText(activity).apply {
                    inputType=InputType.TYPE_CLASS_NUMBER or InputType.TYPE_NUMBER_FLAG_DECIMAL
                    setSingleLine(); setText(v.toString()); isEnabled=on.isChecked
                    contentDescription=names[i]+" "+title+" (%)"
                }
                line.addView(entry,LinearLayout.LayoutParams(0,-2,1f))
                return entry
            }
            val a=input(c("起点","Start"),current?.start ?: defaults[i].first.toDouble())
            val b=input(c("终点","End"),current?.end ?: defaults[i].second.toDouble())
            on.setOnCheckedChangeListener { _,checked -> a.isEnabled=checked;b.isEnabled=checked }
            val readSlot: ()->List<Any>? = {
                if(on.isChecked) listOf(kind,a.text.toString().toDouble(),b.text.toString().toDouble()) else null
            }
            readSlot
        }
    }
    val error=TextView(activity)
    controls.addView(error)
    fun sync() { controls.visibility=if(custom.isChecked) View.VISIBLE else View.GONE }
    custom.setOnCheckedChangeListener { _,_->sync() };sync()
    val dialog=AlertDialog.Builder(activity).setTitle(c("上下边字排版","Factory plate design"))
        .setView(ScrollView(activity).apply { addView(panel) }).setNegativeButton(c("取消","Cancel"),null)
        .setPositiveButton(c("应用","Apply"),null).create()
    dialog.setOnShowListener { dialog.getButton(AlertDialog.BUTTON_POSITIVE).setOnClickListener {
        if(!custom.isChecked) { save(null);dialog.dismiss() }
        else runCatching {
            EdgeComposition.parse(mapOf("font" to font,"top" to read[0].mapNotNull { it() },"bottom" to read[1].mapNotNull { it() }))
        }.onSuccess { save(it?.record());dialog.dismiss() }.onFailure {
            error.text=c("请检查两侧区间。起点须小于终点，同侧区间须错开，范围为 0–100%。","Check both sides. Use non-overlapping slots with start < end in 0–100%.")
        }
    } }
    shell.decorate(panel);dialog.show()
}
