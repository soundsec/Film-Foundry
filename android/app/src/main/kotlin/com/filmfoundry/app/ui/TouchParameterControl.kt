package com.filmfoundry.app.ui

import android.app.AlertDialog
import android.view.View
import android.widget.*
import androidx.activity.ComponentActivity
import kotlin.math.roundToInt

/** Touch editing of an existing draft field. Values and validation stay with its owner. */
class TouchParameterControl(
    private val activity: ComponentActivity,private val shell: MobileShell,parent: LinearLayout,
    private val input: EditText,private val title: String,private val range: ClosedFloatingPointRange<Double>,
    private val step: Double,private val unit: String="",private val active: ()->Boolean={ true },
    private val beforeEdit: ()->Unit={},
    private val inheritedLabel: String=shell.copy("预设","Preset"),
    private val resetLabel: String=shell.copy("沿用预设","Use preset"),
    private val onReset: (()->Unit)?=null,
    private val onEditStarted: ()->Unit={},
    private val onEditFinished: ()->Unit={},
    private val temperature: Boolean=false,
) {
    private val slider=SeekBar(activity)
    private val value=Button(activity)
    private var fallback=range.start
    init {
        val group=shell.field(parent,input,title,restore=::reset)
        input.visibility=View.GONE
        val row=LinearLayout(activity).apply { gravity=android.view.Gravity.CENTER_VERTICAL }
        slider.max=if(temperature) TemperatureScale.STEPS else ((range.endInclusive-range.start)/step).roundToInt()
        slider.contentDescription=title
        row.addView(slider,LinearLayout.LayoutParams(0,shell.dp(48),1f))
        shell.styleButton(value);value.minWidth=shell.dp(104);value.minHeight=shell.dp(48)
        row.addView(value,LinearLayout.LayoutParams(-2,-2));group.addView(row)
        value.setOnClickListener { editNumber() }
        slider.setOnSeekBarChangeListener(object: SeekBar.OnSeekBarChangeListener {
            override fun onProgressChanged(bar: SeekBar?,progress: Int,user: Boolean) {
                if(user) {
                    if(!prepareEdit()) { refresh();return }
                    input.error=null
                    input.setText(number(if(temperature) TemperatureScale.kelvin(progress) else (range.start+progress*step).coerceIn(range)))
                }
            }
            override fun onStartTrackingTouch(bar: SeekBar?) { onEditStarted() }
            override fun onStopTrackingTouch(bar: SeekBar?) { onEditFinished() }
        })
        input.addTextChangedListener(object: android.text.TextWatcher {
            override fun beforeTextChanged(s: CharSequence?,start: Int,count: Int,after: Int)=Unit
            override fun onTextChanged(s: CharSequence?,start: Int,before: Int,count: Int)=refresh()
            override fun afterTextChanged(s: android.text.Editable?)=Unit
        })
        refresh()
    }
    fun refresh(default: Double=fallback) {
        fallback=default.coerceIn(range)
        val explicit=if(active()) input.text.toString().toDoubleOrNull()?.takeIf { it.isFinite() && it in range } else null
        val shown=explicit ?: fallback
        slider.progress=if(temperature) TemperatureScale.progress(shown) else ((shown-range.start)/step).roundToInt().coerceIn(0,slider.max)
        value.text=number(shown)+unit+(if(explicit==null) " · "+inheritedLabel else "")
        value.contentDescription=title+": "+value.text+shell.copy("，点按输入数值",". Tap to enter a value")
    }
    private fun editNumber() {
        val panel=LinearLayout(activity).apply { orientation=LinearLayout.VERTICAL;setPadding(shell.dp(20),shell.dp(8),shell.dp(20),shell.dp(8)) }
        val draft=EditText(activity).apply { inputType=input.inputType;setText(if(active()) input.text.toString() else "");setSelectAllOnFocus(true) }
        shell.field(panel,draft,title,shell.copy("范围：${range.start}–${range.endInclusive}$unit；留空沿用$inheritedLabel。","Range: ${range.start}–${range.endInclusive}$unit; blank inherits $inheritedLabel."))
        val dialog=AlertDialog.Builder(activity).setTitle(title).setView(panel)
            .setNegativeButton(shell.copy("取消","Cancel"),null)
            .setNeutralButton(resetLabel) { _,_->reset() }
            .setPositiveButton(shell.copy("应用","Apply"),null).create()
        dialog.setOnShowListener { dialog.getButton(AlertDialog.BUTTON_POSITIVE).setOnClickListener {
            val text=draft.text.toString().trim();val parsed=text.toDoubleOrNull()
            if(text.isNotEmpty() && (parsed==null || !parsed.isFinite() || parsed !in range)) {
                draft.error=shell.copy("请输入范围内的数值","Enter a value within the range");return@setOnClickListener
            }
            onEditStarted()
            try { if(!prepareEdit()) return@setOnClickListener
                input.error=null;input.setText(text);refresh();dialog.dismiss()
            } finally { onEditFinished() }
        } }
        dialog.show()
    }
    private fun prepareEdit()=runCatching(beforeEdit).onFailure { Toast.makeText(activity,it.message,Toast.LENGTH_LONG).show() }.isSuccess
    private fun reset() { input.error=null;if(onReset!=null) onReset.invoke() else input.setText("");refresh() }
    private fun number(value: Double)=java.math.BigDecimal.valueOf(value).setScale(4,java.math.RoundingMode.HALF_UP).stripTrailingZeros().toPlainString()
}
