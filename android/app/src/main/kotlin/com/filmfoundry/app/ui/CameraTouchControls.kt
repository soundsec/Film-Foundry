package com.filmfoundry.app.ui

import android.annotation.SuppressLint
import android.graphics.Canvas
import android.graphics.Color
import android.graphics.Paint
import android.view.GestureDetector
import android.view.MotionEvent
import android.view.ScaleGestureDetector
import android.view.View
import android.widget.Button
import android.widget.FrameLayout
import androidx.camera.view.PreviewView
import com.filmfoundry.platform.CameraXCameraAdapter

/** Camera controls only: CameraX owns zoom, AF and AE; no synthetic exposure gain. */
@SuppressLint("ClickableViewAccessibility")
class CameraTouchControls(private val preview: PreviewView,private val shell: MobileShell,
    private val allowed: ()->Boolean,private val message: (String)->Unit,private val selectLens: (View)->Unit={},
    private val selectFlash: (View)->Unit={},private val flashAvailable: ()->Boolean={false},private val expert: FinderExpertPort?=null): FrameLayout(preview.context) {
    private var camera: CameraXCameraAdapter?=null
    private var ratio=1f
    private var focusSerial=0
    private var scaleSequence=false
    private var zoomPosted=false
    private var zoomSerial=0
    private var zoomRequest=0
    private var zoomConfirmLabel=false
    private val ink=Paint(Paint.ANTI_ALIAS_FLAG).apply { style=Paint.Style.STROKE;strokeWidth=shell.dp(2).toFloat();color=Color.WHITE }
    private var pointX=0f;private var pointY=0f;private var showPoint=false
    private val marker=object: View(context) {
        override fun onDraw(canvas: Canvas) {
            if(showPoint) { val r=shell.dp(24).toFloat();canvas.drawRoundRect(pointX-r,pointY-r,pointX+r,pointY+r,8f,8f,ink) }
        }
    }
    private val zoom=Button(context).apply {
        tag="camera_action";text="1.0×";contentDescription=shell.copy("缩放倍率；按住左右滑动，双击恢复1倍；取景内可双指缩放","Zoom ratio; press and slide horizontally, double tap to reset to 1x; or pinch in the viewfinder")
        setOnClickListener {
            val state=camera?.nativeZoom()
            if(allowed() && state!=null) {
                val steps=kotlin.math.ceil((state.maximum-state.minimum)*10).toInt().coerceAtLeast(1)
                shell.quickControls.dial("zoom",this,shell.copy("原生缩放","Native zoom"),shell.copy("左右滑动调整倍率。","Slide horizontally to zoom."),0,steps,
                    kotlin.math.round((ratio-state.minimum)*10).toInt(),
                    { String.format(java.util.Locale.ROOT,"%.1f×",(state.minimum+it/10f).coerceAtMost(state.maximum)) },
                    { if(allowed()) requestZoom((state.minimum+it/10f).coerceAtMost(state.maximum)) },
                    { shell.copy("相机倍率 ","Camera zoom ")+String.format(java.util.Locale.ROOT,"%.1f×",camera?.nativeZoom()?.ratio ?: ratio) })
            }
        }
    }
    private val lens=Button(context).apply { tag="camera_action";text=shell.copy("镜头","Lenses");contentDescription=shell.copy("快捷选择原生镜头","Quick native lens selection");setOnClickListener { if(allowed()) selectLens(this) } }
    private val flash=Button(context).apply { tag="camera_action";text=shell.copy("闪光 --","Flash --");setOnClickListener { if(allowed()) selectFlash(this) } }
    private val focus=Button(context).apply {
        tag="camera_action";text="AF";isEnabled=false
        contentDescription=shell.copy("自动对焦；点按取景画面对焦测光。手动对焦暂不可用。","Autofocus; tap the viewfinder to focus and meter. Manual focus is temporarily unavailable.")
    }
    private val scale=ScaleGestureDetector(context,object: ScaleGestureDetector.SimpleOnScaleGestureListener() {
        override fun onScaleBegin(detector: ScaleGestureDetector): Boolean { scaleSequence=true;return allowed() && camera != null }
        override fun onScale(detector: ScaleGestureDetector): Boolean { if(allowed()) requestZoom(ratio*detector.scaleFactor);return true }
    })
    private val taps=GestureDetector(context,object: GestureDetector.SimpleOnGestureListener() {
        override fun onDown(e: MotionEvent)=true
        override fun onSingleTapUp(e: MotionEvent): Boolean {
            if(scaleSequence || !allowed()) return false
            val adapter=camera ?: return false
            // The preview's factory owns letterboxing, rotation, crop and zoom mapping.
            if(preview.outputTransform == null) return false
            val serial=++focusSerial;pointX=e.x;pointY=e.y;showPoint=true;ink.color=Color.WHITE;marker.invalidate()
            adapter.meterAt(preview.meteringPointFactory.createPoint(e.x,e.y)) { focused,error ->
                if(serial == focusSerial && camera === adapter) {
                    ink.color=if(error == null) Color.rgb(224,192,128) else Color.LTGRAY;marker.invalidate()
                    message(if(error != null) shell.copy("本机暂不支持此处测光","Point metering is unavailable here") else if(focused) shell.copy("已对焦并测光","Focused and metered") else shell.copy("已请求测光","Metering requested"))
                    marker.postDelayed({ if(serial == focusSerial) { showPoint=false;marker.invalidate() } },1200)
                }
            }
            return true
        }
    })
    init {
        addView(marker,LayoutParams(-1,-1))
        val row=android.widget.LinearLayout(context)
        row.addView(lens,android.widget.LinearLayout.LayoutParams(0,shell.dp(48),1f))
        row.addView(zoom,android.widget.LinearLayout.LayoutParams(shell.dp(72),shell.dp(48)))
        row.addView(focus,android.widget.LinearLayout.LayoutParams(shell.dp(68),shell.dp(48)))
        row.addView(flash,android.widget.LinearLayout.LayoutParams(shell.dp(104),shell.dp(48)))
        shell.mountLensControls(row)
        for(button in listOf(zoom,lens,focus,flash)) { shell.styleButton(button);button.textSize=13f;button.setSingleLine(true);button.ellipsize=android.text.TextUtils.TruncateAt.END;shell.quickControls.bindPress(button) }
        shell.quickControls.bindPress(zoom) {
            val range=camera?.nativeZoom()
            if(allowed() && range!=null) requestZoom(1f.coerceIn(range.minimum,range.maximum),true)
        }
        preview.setOnTouchListener { _,event ->
            if(event.actionMasked == MotionEvent.ACTION_DOWN) scaleSequence=false
            if(event.pointerCount>1) scaleSequence=true
            scale.onTouchEvent(event);taps.onTouchEvent(event);true
        }
    }
    fun bind(adapter: CameraXCameraAdapter?) {
        if(camera !== adapter) {
            shell.closeQuickControls();zoomSerial++;zoomPosted=false;focusSerial++;showPoint=false;marker.invalidate()
        }
        camera=adapter
        ratio=adapter?.nativeZoom()?.ratio ?: 1f;updateLabel();zoom.isEnabled=adapter?.nativeZoom()?.let { it.maximum>it.minimum } == true
        lens.text=adapter?.lensLabel(shell.copy("zh","en")=="zh") ?: shell.copy("镜头","Lenses");lens.isEnabled=adapter!=null
        lens.tooltipText=lens.text
        val settings=adapter?.nativeRequests()
        focus.isEnabled=false;focus.text=if(settings==null) "--" else "AF"
        focus.tooltipText=shell.copy("点按画面对焦；MF 暂不可用","Tap the viewfinder to focus; MF temporarily unavailable")
        flash.isEnabled=adapter!=null && flashAvailable()
        flash.text=when { settings==null->shell.copy("闪光 --","Flash --");!flashAvailable()->shell.copy("闪光不可用","No flash");settings.torch->shell.copy("补光开","Torch on");settings.flash=="auto"->shell.copy("闪光自动","Flash auto");settings.flash=="on"->shell.copy("闪光开","Flash on");else->shell.copy("闪光关","Flash off") }
        flash.contentDescription=flash.text.toString()+shell.copy("；点按切换原生闪光或补光","; tap for native flash or torch")
    }
    private fun updateLabel() { zoom.text=String.format(java.util.Locale.ROOT,"%.1f×",ratio) }
    private fun requestZoom(value: Float,confirmBeforeLabel: Boolean=false) {
        val adapter=camera ?: return
        val state=adapter.nativeZoom() ?: return
        ratio=value.coerceIn(state.minimum,state.maximum);zoomConfirmLabel=confirmBeforeLabel;zoomRequest++
        if(!confirmBeforeLabel) updateLabel()
        if(zoomPosted) return
        zoomPosted=true;val serial=zoomSerial
        postOnAnimation {
            if(serial != zoomSerial || camera !== adapter) return@postOnAnimation
            zoomPosted=false
            if(!allowed()) { ratio=adapter.nativeZoom()?.ratio ?: 1f;updateLabel();return@postOnAnimation }
            val request=zoomRequest;val confirm=zoomConfirmLabel
            adapter.zoomTo(ratio) { error ->
                if(serial == zoomSerial && request == zoomRequest) {
                    if(error != null) { ratio=adapter.nativeZoom()?.ratio ?: 1f;updateLabel();shell.closeQuickControl("zoom");message(shell.copy("缩放未完成，请重试","Zoom did not complete; try again")) }
                    else { if(confirm) { ratio=adapter.nativeZoom()?.ratio ?: ratio;updateLabel() };shell.refreshQuickControls() }
                }
            }
        }
    }
}
