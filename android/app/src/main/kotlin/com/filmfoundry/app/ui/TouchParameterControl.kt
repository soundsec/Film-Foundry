package com.filmfoundry.app.ui

import android.app.AlertDialog
import android.view.View
import android.widget.*
import androidx.activity.ComponentActivity
import kotlin.math.roundToInt

internal fun LinearLayout.addParameterReadout(shell: MobileShell, title: String): Button {
    val row = LinearLayout(context).apply { gravity = android.view.Gravity.CENTER_VERTICAL }
    val label = TextView(context).apply { text = title; textSize = 14f; setTextColor(shell.ink) }
    row.addView(label, LinearLayout.LayoutParams(0, -2, 1f))
    val readout = Button(context).apply { tag = "parameter_value"; shell.styleButton(this) }
    row.addView(readout, LinearLayout.LayoutParams(-2, -2))
    addView(row, LinearLayout.LayoutParams(-1, -2))
    return readout
}

internal fun editParameterNumber(activity: ComponentActivity, shell: MobileShell, title: String, initial: String,
    inputType: Int, range: ClosedFloatingPointRange<Double>, detail: String, resetLabel: String,
    reset: () -> Unit, apply: (String) -> Boolean) {
    val panel=LinearLayout(activity).apply { orientation=LinearLayout.VERTICAL;setPadding(shell.dp(20),shell.dp(8),shell.dp(20),shell.dp(8)) }
    val draft=EditText(activity).apply { this.inputType=inputType;setText(initial);setSelectAllOnFocus(true) }
    shell.field(panel,draft,title,detail)
    val dialog=AlertDialog.Builder(activity).setTitle(title).setView(panel)
        .setNegativeButton(shell.copy("取消","Cancel"),null).setNeutralButton(resetLabel) { _,_->reset() }
        .setPositiveButton(shell.copy("应用","Apply"),null).create()
    dialog.setOnShowListener { dialog.getButton(AlertDialog.BUTTON_POSITIVE).setOnClickListener {
        val text=draft.text.toString().trim()
        val parsed=text.toDoubleOrNull()
        if(text.isNotEmpty() && (parsed==null || !parsed.isFinite() || parsed !in range)) {
            draft.error=shell.copy("请输入范围内的数值","Enter a value within the range")
        } else if(apply(text)) dialog.dismiss()
    } }
    dialog.show()
}

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
    private val slider=shell.parameterRuler()
    private val value: Button
    private var fallback=range.start
    init {
        val group=LinearLayout(activity).apply { orientation=LinearLayout.VERTICAL;tag=title }
        group.addView(input)
        input.visibility=View.GONE
        parent.addView(group,LinearLayout.LayoutParams(-1,-2).apply { bottomMargin=shell.dp(8) })
        slider.max=if(temperature) TemperatureScale.STEPS else ((range.endInclusive-range.start)/step).roundToInt()
        slider.contentDescription=title
        slider.resetValue=::reset
        value=group.addParameterReadout(shell,title)
        group.addView(slider,LinearLayout.LayoutParams(-1,shell.dp(48)))
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
        val detail=shell.copy("范围：${range.start}–${range.endInclusive}$unit；留空沿用$inheritedLabel。","Range: ${range.start}–${range.endInclusive}$unit; blank inherits $inheritedLabel.")
        editParameterNumber(activity,shell,title,if(active()) input.text.toString() else "",input.inputType,range,detail,resetLabel,::reset) { text ->
            onEditStarted()
            try {
                if(!prepareEdit()) return@editParameterNumber false
                input.error=null;input.setText(text);refresh();true
            } finally { onEditFinished() }
        }
    }
    private fun prepareEdit()=runCatching(beforeEdit).onFailure { Toast.makeText(activity,it.message,Toast.LENGTH_LONG).show() }.isSuccess
    private fun reset() { input.error=null;if(onReset!=null) onReset.invoke() else input.setText("");refresh() }
    private fun number(value: Double)=java.math.BigDecimal.valueOf(value).setScale(4,java.math.RoundingMode.HALF_UP).stripTrailingZeros().toPlainString()
}
