package com.filmfoundry.app.ui

import android.graphics.Bitmap
import android.graphics.Color
import android.view.ViewConfiguration
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
        },FrameLayout.LayoutParams(-2,-2,Gravity.BOTTOM or Gravity.CENTER_HORIZONTAL))
    }
    private val slop=ViewConfiguration.get(context).scaledTouchSlop
    private var downX=0f
    private var downY=0f
    private val hold=Runnable { if(surface.isShown) show(true) }
    private var bitmap: Bitmap?=null
    private var angle=0
    private var source: String?=null
    private var currentHistogram: DisplayHistogram?=null
    private var baselineHistogram: DisplayHistogram?=null
    var onHistogramChanged: (DisplayHistogram?,Boolean)->Unit={ _,_-> }
    init {
        baselineImage.addOnLayoutChangeListener { _,_,_,_,_,_,_,_,_ -> orientOutputView(baselineImage,angle) }
        surface.addView(baseline,FrameLayout.LayoutParams(-1,-1))
        surface.contentDescription=shell.copy("扫描预览，长按对照初始预览","Scan preview, hold to compare with the initial preview")
        surface.setOnLongClickListener {
            if(bitmap==null) false else { show(baseline.visibility!=View.VISIBLE);true }
        }
        surface.setOnTouchListener { _,event ->
            if(bitmap==null) return@setOnTouchListener false
            when(event.actionMasked) {
                MotionEvent.ACTION_DOWN -> {
                    downX=event.x;downY=event.y
                    surface.postDelayed(hold,ViewConfiguration.getLongPressTimeout().toLong())
                }
                MotionEvent.ACTION_MOVE -> if(kotlin.math.abs(event.x-downX)>slop || kotlin.math.abs(event.y-downY)>slop) release()
                MotionEvent.ACTION_POINTER_DOWN,MotionEvent.ACTION_CANCEL,MotionEvent.ACTION_UP -> release()
            }
            true
        }
        surface.addOnAttachStateChangeListener(object: View.OnAttachStateChangeListener {
            override fun onViewAttachedToWindow(view: View)=Unit
            override fun onViewDetachedFromWindow(view: View)=release()
        })
    }
    fun bind(key: String) { if(source!=key) { clear();source=key } }
    fun offer(image: Bitmap,rotationDegrees: Int=0) {
        if(bitmap!=null || source==null || image.isRecycled) return
        bitmap=runCatching { image.copy(Bitmap.Config.ARGB_8888,false) }.getOrNull() ?: return
        angle=rotationDegrees;baselineImage.setImageBitmap(bitmap);orientOutputView(baselineImage,angle)
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
        reportHistogram()
    }
    private fun release() {
        surface.removeCallbacks(hold)
        if(baseline.visibility==View.VISIBLE) show(false)
    }
    fun clear() {
        surface.removeCallbacks(hold)
        currentHistogram=null;baselineHistogram=null
        show(false);baselineImage.setImageDrawable(null);bitmap?.recycle();bitmap=null
        source=null
    }
}
