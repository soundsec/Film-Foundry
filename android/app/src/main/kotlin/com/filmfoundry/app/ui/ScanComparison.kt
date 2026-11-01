package com.filmfoundry.app.ui

import android.graphics.Bitmap
import android.graphics.Color
import android.graphics.drawable.GradientDrawable
import android.view.Gravity
import android.view.MotionEvent
import android.view.View
import android.widget.*

/** One display-only baseline. No second scan, recipe mutation or material access on touch. */
class ScanComparison(private val shell: MobileShell,private val surface: FrameLayout) {
    private val context=surface.context
    private val baselineImage=ImageView(context).apply { scaleType=ImageView.ScaleType.FIT_CENTER;setBackgroundColor(Color.BLACK) }
    private val baseline=FrameLayout(context).apply {
        addView(baselineImage,FrameLayout.LayoutParams(-1,-1));visibility=View.GONE
        addView(TextView(context).apply {
            text=shell.copy("初始预览","Initial preview");setTextColor(Color.WHITE);textSize=12f
            setPadding(shell.dp(12),shell.dp(8),shell.dp(12),shell.dp(8));setBackgroundColor(0xB0161616.toInt())
        },FrameLayout.LayoutParams(-2,-2,Gravity.TOP or Gravity.START))
    }
    val button=Button(context).apply {
        text="◧";textSize=22f;minWidth=0;minimumWidth=0;setPadding(0,0,0,0)
        contentDescription=shell.copy("按住对照初始预览","Hold to compare with the initial preview");tooltipText=contentDescription
        setTextColor(Color.WHITE);background=GradientDrawable().apply { setColor(0xB0161616.toInt());cornerRadius=shell.dp(24).toFloat() }
        visibility=View.GONE
    }
    private var bitmap: Bitmap?=null
    private var angle=0
    private var source: String?=null
    private var suppressClick=false
    private var currentHistogram: DisplayHistogram?=null
    private var baselineHistogram: DisplayHistogram?=null
    var onHistogramChanged: (DisplayHistogram?,Boolean)->Unit={ _,_-> }
    init {
        baselineImage.addOnLayoutChangeListener { _,_,_,_,_,_,_,_,_ -> orientOutputView(baselineImage,angle) }
        surface.addView(baseline,FrameLayout.LayoutParams(-1,-1))
        surface.addView(button,FrameLayout.LayoutParams(shell.dp(48),shell.dp(48),Gravity.TOP or Gravity.END).apply { setMargins(shell.dp(8),shell.dp(8),shell.dp(8),0) })
        button.setOnTouchListener { _,event -> when(event.actionMasked) {
            MotionEvent.ACTION_DOWN -> { show(true);true }
            MotionEvent.ACTION_UP -> { show(false);suppressClick=true;try { button.performClick() } finally { suppressClick=false };true }
            MotionEvent.ACTION_CANCEL -> { show(false);true }
            MotionEvent.ACTION_MOVE -> true
            else -> false
        } }
        button.setOnClickListener { if(!suppressClick) show(baseline.visibility!=View.VISIBLE) }
    }
    fun bind(key: String) { if(source!=key) { clear();source=key } }
    fun offer(image: Bitmap,rotationDegrees: Int=0) {
        if(bitmap!=null || source==null || image.isRecycled) return
        bitmap=runCatching { image.copy(Bitmap.Config.ARGB_8888,false) }.getOrNull() ?: return
        angle=rotationDegrees;baselineImage.setImageBitmap(bitmap);orientOutputView(baselineImage,angle);button.visibility=View.VISIBLE
    }
    /** Called only after the matching preview bitmap and request receipt have been accepted. */
    fun acceptHistogram(value: DisplayHistogram) {
        currentHistogram=value
        if(bitmap!=null && baselineHistogram==null) baselineHistogram=value
        reportHistogram()
    }
    private fun reportHistogram() {
        val initial=baseline.visibility==View.VISIBLE
        onHistogramChanged(if(initial) baselineHistogram else currentHistogram,initial)
    }
    private fun show(enabled: Boolean) {
        baseline.visibility=if(enabled && bitmap!=null) View.VISIBLE else View.GONE
        button.isSelected=baseline.visibility==View.VISIBLE
        reportHistogram()
    }
    fun clear() {
        currentHistogram=null;baselineHistogram=null
        show(false);baselineImage.setImageDrawable(null);bitmap?.recycle();bitmap=null
        button.visibility=View.GONE;source=null
    }
}
