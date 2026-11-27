package com.filmfoundry.app.ui

import android.graphics.Color
import android.view.View
import android.widget.*
import android.text.InputType
import androidx.activity.ComponentActivity
import com.filmfoundry.interchange.FactoryLamp
import kotlin.math.pow
import kotlin.math.roundToInt

/** Brightness is directly accessible. The colour row expands for per-channel light mixing. */
internal class EdgeLampControls(activity: ComponentActivity, shell: MobileShell, parent: LinearLayout, initial: FactoryLamp, ink: Boolean=false,
                              onColourChanged: ()->Unit={}) {
    val view = LinearLayout(activity).apply { orientation=LinearLayout.VERTICAL }
    private val ev = EditText(activity)
    private val channels = List(3) { EditText(activity) }
    private val defaultPercent=if(ink) 18.0 else 100.0

    fun value() = FactoryLamp(ev.text.toString().toDoubleOrNull() ?: 0.0,
        channels.map { (it.text.toString().toDoubleOrNull() ?: defaultPercent)/100.0 })

    init {
        parent.addView(view)
        ev.inputType=InputType.TYPE_CLASS_NUMBER or InputType.TYPE_NUMBER_FLAG_DECIMAL or InputType.TYPE_NUMBER_FLAG_SIGNED
        ev.setText(initial.ev.toString())
        if(!ink) TouchParameterControl(activity,shell,view,ev,shell.copy("边字亮度","Lettering brightness"),-4.0..4.0,.25," EV").refresh(0.0)
        val row=LinearLayout(activity).also { view.addView(it) }
        val swatch=View(activity)
        row.addView(swatch,LinearLayout.LayoutParams(shell.dp(30),shell.dp(30)).apply { gravity=android.view.Gravity.CENTER_VERTICAL })
        val toggle=Button(activity).apply { isAllCaps=false;minHeight=shell.dp(48) }
        row.addView(toggle,LinearLayout.LayoutParams(0,-2,1f))
        val mixer=LinearLayout(activity).apply { orientation=LinearLayout.VERTICAL;visibility=View.GONE }
        view.addView(mixer)
        fun showColour() {
            val encoded=value().rgb.map {
                val srgb=if(it<=.0031308) 12.92*it else 1.055*it.pow(1.0/2.4)-.055
                (255*srgb).roundToInt().coerceIn(0,255)
            }
            swatch.setBackgroundColor(Color.rgb(encoded[0],encoded[1],encoded[2]))
            val title=if(ink) shell.copy("油墨透射色","Ink transmission") else shell.copy("边字灯色","Lettering lamp")
            toggle.text=title+"  "+encoded.joinToString("",prefix="#") { "%02X".format(it) }+
                if(mixer.visibility==View.VISIBLE) "  ▴" else "  ▾"
        }
        val names=if(ink) listOf(shell.copy("红光透过率","Red transmission"),shell.copy("绿光透过率","Green transmission"),shell.copy("蓝光透过率","Blue transmission"))
            else listOf(shell.copy("红光","Red light"),shell.copy("绿光","Green light"),shell.copy("蓝光","Blue light"))
        for(i in channels.indices) {
            val input=channels[i]
            input.inputType=InputType.TYPE_CLASS_NUMBER or InputType.TYPE_NUMBER_FLAG_DECIMAL
            input.setText((initial.rgb[i]*100).toString())
            TouchParameterControl(activity,shell,mixer,input,names[i],0.0..100.0,1.0," %").refresh(defaultPercent)
            input.addTextChangedListener(object: android.text.TextWatcher {
                override fun beforeTextChanged(s: CharSequence?,start: Int,count: Int,after: Int) {}
                override fun onTextChanged(s: CharSequence?,start: Int,before: Int,count: Int) { showColour();onColourChanged() }
                override fun afterTextChanged(s: android.text.Editable?) {}
            })
        }
        toggle.setOnClickListener { mixer.visibility=if(mixer.visibility==View.VISIBLE) View.GONE else View.VISIBLE;showColour() }
        showColour()
    }
}
