package com.filmfoundry.app.ui

import android.app.AlertDialog
import android.text.InputType
import android.view.View
import android.widget.*
import androidx.activity.ComponentActivity
import com.filmfoundry.app.session.ProcessingIncidentDraft
import kotlin.math.roundToInt

/** Three independent effects; switching tools never applies or clears another effect. */
class ProcessIncidentControls(private val activity: ComponentActivity,private val shell: MobileShell,parent: LinearLayout,
    private val draft: ProcessingIncidentDraft,private val changed: (finished: Boolean)->Unit) {
    private val labels=listOf("显影不匀" to "Uneven development","药液污染" to "Chemical stain","乳剂网裂" to "Reticulation")
    private var selected=0
    private var effective: Map<String,Any?> = emptyMap()
    private val summary=TextView(activity)
    private val explanation=TextView(activity)
    private val tools=mutableListOf<Button>()
    private val modes=mutableListOf<Button>()
    private val slider=SeekBar(activity)
    private val value=Button(activity)
    private val strengthRow=LinearLayout(activity)
    private lateinit var undo: Button
    private lateinit var redo: Button
    init {
        shell.note(parent,"冲洗事故 · 可组合","Processing incidents · combine effects")
        parent.addView(summary)
        val toolbar=LinearLayout(activity)
        parent.addView(HorizontalScrollView(activity).apply { isHorizontalScrollBarEnabled=false;addView(toolbar) })
        labels.forEachIndexed { index,label -> tools+=shell.editorTab(toolbar,label.first,label.second) { selected=index;refresh() } }
        val modeRow=LinearLayout(activity);parent.addView(modeRow)
        listOf("沿用" to "Inherit","关闭" to "Off","指定" to "Set").forEachIndexed { index,label ->
            modes+=shell.editorTab(modeRow,label.first,label.second) {
                val key=ProcessingIncidentDraft.keys[selected]
                val amount=when(index) { 0->null;1->0.0;else->draft.override(key)?.takeIf { it>0 } ?: (effective[key] as? Number)?.toDouble()?.takeIf { it>0 } ?: .35 }
                draft.set(key,amount);changed(true);refresh()
            }.apply { layoutParams=LinearLayout.LayoutParams(0,-2,1f) }
        }
        parent.addView(explanation)
        strengthRow.gravity=android.view.Gravity.CENTER_VERTICAL;parent.addView(strengthRow)
        slider.max=100;strengthRow.addView(slider,LinearLayout.LayoutParams(0,shell.dp(48),1f))
        shell.styleButton(value);value.minWidth=shell.dp(88);strengthRow.addView(value,LinearLayout.LayoutParams(-2,shell.dp(48)))
        slider.setOnSeekBarChangeListener(object: SeekBar.OnSeekBarChangeListener {
            override fun onProgressChanged(bar: SeekBar?,n: Int,user: Boolean) { if(user) { draft.set(ProcessingIncidentDraft.keys[selected],n/100.0);changed(false);refresh() } }
            override fun onStartTrackingTouch(bar: SeekBar?) { draft.beginGesture() }
            override fun onStopTrackingTouch(bar: SeekBar?) { draft.endGesture();changed(true);refresh() }
        })
        value.setOnClickListener {
            val key=ProcessingIncidentDraft.keys[selected]
            val input=EditText(activity).apply { inputType=InputType.TYPE_CLASS_NUMBER;setText(((draft.override(key) ?: .35)*100).roundToInt().toString());selectAll() }
            val panel=LinearLayout(activity).apply { orientation=LinearLayout.VERTICAL;setPadding(shell.dp(20),shell.dp(8),shell.dp(20),shell.dp(8)) }
            shell.field(panel,input,shell.copy("强度 · %","Strength · %"),shell.copy("范围 0–100","Range 0–100"),resettable=false)
            val dialog=AlertDialog.Builder(activity).setTitle(shell.copy(labels[selected].first,labels[selected].second)).setView(panel)
                .setNegativeButton(shell.copy("取消","Cancel"),null).setPositiveButton(shell.copy("应用","Apply"),null).create()
            dialog.setOnShowListener { dialog.getButton(AlertDialog.BUTTON_POSITIVE).setOnClickListener {
                val n=input.text.toString().toIntOrNull()
                if(n==null || n !in 0..100) input.error=shell.copy("请输入 0–100","Enter 0–100")
                else { draft.set(key,n/100.0);changed(true);refresh();dialog.dismiss() }
            } };dialog.show()
        }
        val actions=LinearLayout(activity);parent.addView(actions)
        shell.action(actions,"全部沿用","Inherit all") { draft.inheritAll();changed(true);refresh() }.layoutParams=LinearLayout.LayoutParams(0,-2,1f)
        shell.action(actions,"干净冲洗","Clean process") { draft.clean();changed(true);refresh() }.layoutParams=LinearLayout.LayoutParams(0,-2,1f)
        val history=LinearLayout(activity);parent.addView(history)
        undo=shell.action(history,"↶ 撤销","↶ Undo") { draft.undo();changed(true);refresh() }.apply { layoutParams=LinearLayout.LayoutParams(0,-2,1f);contentDescription=shell.copy("撤销事故调整","Undo incident adjustment") }
        redo=shell.action(history,"↷ 重做","↷ Redo") { draft.redo();changed(true);refresh() }.apply { layoutParams=LinearLayout.LayoutParams(0,-2,1f);contentDescription=shell.copy("重做事故调整","Redo incident adjustment") }
        refresh()
    }
    fun bind(values: Map<String,Any?>) { effective=values;refresh() }
    private fun refresh() {
        fun c(zh: String,en: String)=shell.copy(zh,en)
        fun state(index: Int): String {
            val key=ProcessingIncidentDraft.keys[index];val amount=draft.override(key)
            return when { amount==null->(effective[key] as? Number)?.toDouble()?.let { c("配方 ","Recipe ")+"${(it*100).roundToInt()}%" } ?: c("随药浴","Bath-driven")
                amount==0.0->c("关闭","Off");else->"${(amount*100).roundToInt()}%" }
        }
        summary.text=labels.indices.joinToString(" · ") { c(labels[it].first,labels[it].second)+" "+state(it) }
        tools.forEachIndexed { i,b -> b.isSelected=i==selected;b.contentDescription=c(labels[i].first,labels[i].second)+": "+state(i) }
        val key=ProcessingIncidentDraft.keys[selected];val amount=draft.override(key)
        modes.forEachIndexed { i,b -> b.isSelected=i==when { amount==null->0;amount==0.0->1;else->2 } }
        strengthRow.visibility=if(amount!=null) View.VISIBLE else View.GONE
        slider.progress=((amount ?: .35)*100).roundToInt();value.text="${slider.progress}%"
        slider.contentDescription=c(labels[selected].first,labels[selected].second);value.contentDescription=slider.contentDescription.toString()+c("强度，点按输入"," strength, tap to enter")
        explanation.text=when { amount==null->c("沿用配方与药浴条件产生的效果。","Uses the recipe and effects produced by bath conditions.")
            amount==0.0->c("只关闭这一项，其他事故与材料效果保留。","Disables only this effect; other incidents and material effects are retained.")
            else->c("独立强度，可与其他事故同时使用。","Independent strength; can be combined with other incidents.") }
        if(::undo.isInitialized) undo.isEnabled=draft.canUndo
        if(::redo.isInitialized) redo.isEnabled=draft.canRedo
    }
}
