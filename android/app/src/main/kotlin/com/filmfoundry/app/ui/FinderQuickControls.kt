package com.filmfoundry.app.ui

import android.annotation.SuppressLint
import android.graphics.Canvas
import android.graphics.Paint
import android.graphics.RadialGradient
import android.graphics.Shader
import android.graphics.Typeface
import android.graphics.drawable.GradientDrawable
import android.os.SystemClock
import android.view.MotionEvent
import android.view.View
import android.view.accessibility.AccessibilityManager
import android.widget.*

/** Small nonmodal editor shared by side readouts and the finder bottom bar. */
class FinderQuickControls(private val shell: MobileShell) {
    private val context=shell.root.context
    private val session=FinderQuickSession()
    private val host=FrameLayout(context)
    private var card: View?=null
    private var anchor: View?=null
    private val location=IntArray(2)
    private var timeout: Runnable?=null
    private var confirmation: (()->Unit)?=null
    private var pressToken: Any?=null
    private var cancelPress: (()->Unit)?=null
    private val amber=0xFFE8BF6D.toInt()
    private val touchConfig=android.view.ViewConfiguration.get(context)
    private val taps=FinderButtonTaps(android.view.ViewConfiguration.getDoubleTapTimeout().toLong(),touchConfig.scaledTouchSlop.toFloat(),touchConfig.scaledDoubleTapSlop.toFloat())
    init { shell.mountQuickControls(host);shell.onCaptureTouchDown=::outside }
    val isOpen get()=session.key!=null
    fun dismissIf(key: String) { if(session.key==key) dismiss() }
    fun dismiss(clearTaps: Boolean=true) {
        if(clearTaps) { cancelPress?.invoke();taps.cancel() }
        timeout?.let(host::removeCallbacks);timeout=null;session.dismiss()
        anchor?.isSelected=false;anchor?.isPressed=false;anchor=null;card=null;confirmation=null;host.removeAllViews()
    }
    /** Resettable controls separate quick taps from a short hold, retaining one touch target. */
    @SuppressLint("ClickableViewAccessibility")
    fun bindPress(source: View,reset: (()->Unit)?=null) {
        var dial: ArcDialView?=null
        var revision=-1L
        var pointer=-1
        var reveal: Runnable?=null
        var token: Any?=null
        var downX=0f;var downY=0f;var lastX=0f;var lastY=0f
        var activated=false;var wasOpen=false
        val horizontal=generateSequence(source as View?) { it.parent as? View }.none { it===shell.captureHud }
        fun stopTimer() { reveal?.let(source::removeCallbacks);reveal=null }
        fun activate() {
            if(token==null || pressToken!==token || activated || pointer<0 || !source.isEnabled || !source.isAttachedToWindow) return
            stopTimer();activated=true;taps.revealed()
            // An already-open dial can be adjusted again without toggling it closed.
            if(reset==null || anchor!==source || !isOpen) source.performClick()
            if(token==null || pressToken!==token) return
            revision=if(anchor===source) session.revision else -1
            dial=(card as? ArcDialView)?.takeIf { anchor===source }
            dial?.startFromAnchor(downX,downY)
            dial?.moveFromAnchor(lastX,lastY)
            if(anchor===source && revision==session.revision) schedule(true)
        }
        fun finish(event: MotionEvent,cancelled: Boolean) {
            val owner=token
            if(owner==null || pressToken!==owner) return
            stopTimer();source.isPressed=false;source.parent?.requestDisallowInterceptTouchEvent(false)
            val finishing=dial;dial=null;pointer=-1
            pressToken=null;cancelPress=null;token=null
            finishing?.finishFromAnchor(cancelled)
            if(anchor===source && revision==session.revision) schedule()
            revision=-1
            val double=taps.up(event.eventTime,event.rawX,event.rawY,cancelled)
            if(double && reset!=null) { dismiss();reset() }
            else if(!cancelled && !activated && wasOpen && anchor===source) dismiss(false)
        }
        source.setOnTouchListener { view,event ->
            when(event.actionMasked) {
                MotionEvent.ACTION_DOWN->{
                    cancelPress?.invoke()
                    val owner=Any();token=owner;pressToken=owner
                    cancelPress={
                        if(token===owner) {
                            stopTimer();val finishing=dial;token=null;dial=null;pointer=-1
                            finishing?.finishFromAnchor(true)
                            if(anchor===source && revision==session.revision) schedule()
                            source.isPressed=false;source.parent?.requestDisallowInterceptTouchEvent(false)
                            if(pressToken===owner) { pressToken=null;cancelPress=null }
                        }
                    }
                    downX=event.rawX;downY=event.rawY;lastX=downX;lastY=downY
                    activated=false;wasOpen=anchor===source && isOpen;revision=if(wasOpen) session.revision else -1
                    taps.down(source,event.eventTime,downX,downY)
                    pointer=event.getPointerId(0);view.isPressed=true;view.parent?.requestDisallowInterceptTouchEvent(true)
                    if(reset==null) activate()
                    else {
                        // While deciding between a quick tap and a hold, keep an existing dial alive.
                        if(wasOpen) schedule(true)
                        reveal=Runnable { if(pressToken===owner) activate() }.also { source.postDelayed(it,taps.revealDelayMs) }
                    }
                }
                MotionEvent.ACTION_MOVE->{
                    lastX=event.rawX;lastY=event.rawY;taps.move(lastX,lastY)
                    if(pointer>=0 && event.pointerCount==1 && event.getPointerId(0)==pointer) {
                        if(!activated && taps.shouldReveal(event.eventTime,lastX,lastY,horizontal)) activate()
                        else if(activated) dial?.moveFromAnchor(lastX,lastY)
                    } else finish(event,true)
                }
                MotionEvent.ACTION_UP->finish(event,false)
                MotionEvent.ACTION_CANCEL,MotionEvent.ACTION_POINTER_DOWN->finish(event,true)
            }
            true // The original source owns the stream, including release outside its bounds.
        }
        // Android accessibility owns its double-tap gesture; expose an explicit named action too.
        if(reset!=null) source.accessibilityDelegate=object: View.AccessibilityDelegate() {
            override fun onInitializeAccessibilityNodeInfo(host: View,info: android.view.accessibility.AccessibilityNodeInfo) {
                super.onInitializeAccessibilityNodeInfo(host,info)
                info.addAction(android.view.accessibility.AccessibilityNodeInfo.AccessibilityAction(android.view.accessibility.AccessibilityNodeInfo.ACTION_LONG_CLICK,shell.copy("恢复此参数默认值","Reset this parameter")))
            }
            override fun performAccessibilityAction(host: View,action: Int,args: android.os.Bundle?): Boolean {
                if(action==android.view.accessibility.AccessibilityNodeInfo.ACTION_LONG_CLICK && host.isEnabled) { dismiss();reset();return true }
                return super.performAccessibilityAction(host,action,args)
            }
        }
    }
    fun refreshConfirmed() { confirmation?.invoke() }
    private fun outside(x: Float,y: Float) {
        if(!isOpen) return
        fun contains(view: View?): Boolean {
            if(view==null || !view.isShown) return false
            view.getLocationOnScreen(location)
            val localX=x-location[0];val localY=y-location[1]
            return if(view is ArcDialView) view.hits(localX,localY) else localX>=0 && localX<view.width && localY>=0 && localY<view.height
        }
        if(!contains(card) && !contains(anchor)) dismiss()
    }
    private fun schedule(held: Boolean=false) {
        timeout?.let(host::removeCallbacks);timeout=null
        session.touch(SystemClock.uptimeMillis(),held)
        val manager=context.getSystemService(android.content.Context.ACCESSIBILITY_SERVICE) as AccessibilityManager
        if(held || manager.isTouchExplorationEnabled || !isOpen) return
        val revision=session.revision
        timeout=Runnable { if(session.expire(revision,SystemClock.uptimeMillis())) dismiss() }.also { host.postDelayed(it,3250) }
    }
    private fun begin(key: String,source: View): Boolean {
        if(session.key==key) { dismiss(false);return false }
        dismiss(false);session.toggle(key,SystemClock.uptimeMillis());anchor=source;source.isSelected=true
        return true
    }
    private fun open(key: String,source: View,title: String,detail: String): LinearLayout? {
        if(!begin(key,source)) return null
        val panel=LinearLayout(context).apply {
            orientation=LinearLayout.VERTICAL;setPadding(shell.dp(14),shell.dp(8),shell.dp(14),shell.dp(10))
            background=GradientDrawable().apply { setColor(0xF51A201C.toInt());cornerRadius=shell.dp(16).toFloat();setStroke(shell.dp(1),0xFF697461.toInt()) }
            elevation=shell.dp(8).toFloat();isClickable=true
        }
        val heading=LinearLayout(context).apply { gravity=android.view.Gravity.CENTER_VERTICAL }
        heading.addView(label(title,15f),LinearLayout.LayoutParams(0,-2,1f))
        heading.addView(Button(context).apply { text="×";tag="camera_action";contentDescription=shell.copy("收起快捷调整","Close quick adjustment");setOnClickListener { dismiss() };shell.styleButton(this) },LinearLayout.LayoutParams(shell.dp(44),shell.dp(44)))
        panel.addView(heading)
        panel.addView(label(detail,11f).apply { setTextColor(0xFFB4BFAF.toInt()) })
        // The card alone receives touches; the rest of the finder remains native AF/AE.
        val scroll=ScrollView(context).apply { isFillViewport=false;addView(panel) }
        host.addView(scroll,FrameLayout.LayoutParams(shell.dp(256),-2,android.view.Gravity.START or android.view.Gravity.TOP).apply { setMargins(shell.dp(100),shell.dp(8),shell.dp(108),shell.dp(66)) })
        card=scroll;schedule();return panel
    }
    private fun label(value: String,size: Float)=TextView(context).apply { text=value;textSize=size;setTextColor(amber);setPadding(0,shell.dp(3),0,shell.dp(3)) }
    fun info(key: String,source: View,title: String,detail: String,actionTitle: String?=null,action: (()->Unit)?=null) {
        val panel=open(key,source,title,detail) ?: return
        if(actionTitle!=null && action!=null) panel.addView(Button(context).apply { text=actionTitle;tag="capture_option";shell.styleButton(this);setOnClickListener { dismiss();action() } },LinearLayout.LayoutParams(-1,-2))
    }
    fun choices(key: String,source: View,title: String,detail: String,labels: List<String>,selected: Int,choose: (Int)->Unit) {
        val panel=open(key,source,title,detail) ?: return
        for((index,text) in labels.withIndex()) panel.addView(Button(context).apply {
            this.text=(if(index==selected) "●  " else "○  ")+text;tag="capture_option";shell.styleButton(this)
            setOnClickListener { dismiss();choose(index) }
        },LinearLayout.LayoutParams(-1,-2))
    }
    fun dial(key: String,source: View,title: String,detail: String,lower: Int,upper: Int,initial: Int,
        format: (Int)->String,change: (Int)->Unit,confirmed: (()->String)?=null) {
        if(!begin(key,source)) return
        val wheel=ArcDialView(source,FinderDial(lower,upper,initial),title,detail,format,change,confirmed)
        host.addView(wheel,FrameLayout.LayoutParams(-1,-1));card=wheel
        confirmation={ wheel.describe() };schedule()
    }
    /** A two-detent finder dial. One release commits once; cancelling restores the starting value. */
    fun switch(key: String,source: View,title: String,initial: Boolean,current: ()->Boolean,change: (Boolean)->Unit) {
        if(!begin(key,source)) return
        val wheel=ArcDialView(source,FinderDial(0,1,if(initial) 1 else 0),title,"",
            { if(it==1) shell.copy("开","ON") else shell.copy("关","OFF") },{ change(it==1) },null,
            { if(current()) 1 else 0 })
        host.addView(wheel,FrameLayout.LayoutParams(-1,-1));card=wheel
        confirmation={ wheel.synchronizeSwitch() };schedule()
    }
    @SuppressLint("ClickableViewAccessibility")
    private inner class ArcDialView(private val source: View,private val state: FinderDial,private val title: String,private val detail: String,
        private val format: (Int)->String,private val changed: (Int)->Unit,private val confirmed: (()->String)?,
        private val switchValue: (()->Int)?=null): View(context) {
        private val paint=Paint(Paint.ANTI_ALIAS_FLAG).apply { typeface=Typeface.MONOSPACE;textAlign=Paint.Align.CENTER }
        private var geometry: FinderArcGeometry?=null
        private var face: Shader?=null
        private val bottom=generateSequence(source as View?) { it.parent as? View }.none { it===shell.captureHud }
        private val gesture=FinderDialGesture(state,bottom)
        private var dragged=false
        private var directActive=false
        private var switchStart: Int?=null
        private val slop=android.view.ViewConfiguration.get(context).scaledTouchSlop
        init { isFocusable=true;describe() }
        fun describe() {
            val direction=if(bottom) shell.copy("左右滑动调节","slide horizontally to adjust") else shell.copy("上下滑动调节","slide vertically to adjust")
            contentDescription=title+" · "+format(state.value)+" · "+direction+shell.copy("；也可轻点刻度选择","; or tap a scale value")+". "+(confirmed?.invoke() ?: "")
            tooltipText=title+" · "+direction
        }
        fun synchronizeSwitch() {
            if(switchStart==null) switchValue?.let { state.select(it()) }
            describe();invalidate()
        }
        override fun onSizeChanged(w: Int,h: Int,oldw: Int,oldh: Int) {
            super.onSizeChanged(w,h,oldw,oldh)
            getLocationOnScreen(location);val originX=location[0];val originY=location[1]
            source.getLocationOnScreen(location)
            val sourceX=location[0]-originX+source.width/2f;val sourceY=location[1]-originY+source.height/2f
            shell.captureHud.getLocationOnScreen(location)
            val railRight=location[0]-originX+shell.captureHud.width
            source.getLocationOnScreen(location)
            val boundary=if(bottom) (location[1]-originY).toFloat() else railRight.toFloat()
            val unit=resources.displayMetrics.density
            geometry=FinderArcGeometry.place(w.toFloat(),h.toFloat(),sourceX,sourceY,boundary,bottom,unit)
            val g=geometry!!
            face=RadialGradient(g.x,g.y,g.radius,intArrayOf(0xE8182118.toInt(),0xD0182018.toInt(),0xB0101711.toInt()),floatArrayOf(0f,.5f,1f),Shader.TileMode.CLAMP)
        }
        fun hits(x: Float,y: Float)=geometry?.contains(x,y,shell.dp(10).toFloat())==true
        override fun onDraw(canvas: Canvas) {
            super.onDraw(canvas)
            val g=geometry ?: return
            canvas.save()
            // The pivot and the other half remain behind the rail, never painted over it.
            if(g.bottom) canvas.clipRect(0f,0f,width.toFloat(),g.boundary) else canvas.clipRect(g.boundary,0f,width.toFloat(),height.toFloat())
            paint.style=Paint.Style.FILL;paint.shader=face;paint.alpha=255;canvas.drawCircle(g.x,g.y,g.radius,paint);paint.shader=null
            paint.style=Paint.Style.STROKE;paint.color=0xFFA4AD92.toInt();paint.strokeWidth=shell.dp(1).toFloat()
            canvas.drawCircle(g.x,g.y,g.radius,paint)
            paint.color=0xFF59644D.toInt();canvas.drawCircle(g.x,g.y,g.radius*.23f,paint)
            for(offset in -4..4) {
                val index=state.value+offset;if(index !in state.lower..state.upper) continue
                val degrees=g.axis-offset*22f+gesture.fraction*22f
                if(kotlin.math.abs(degrees-g.axis)>84f) continue
                val angle=Math.toRadians(degrees.toDouble());val c=kotlin.math.cos(angle).toFloat();val a=kotlin.math.sin(angle).toFloat()
                paint.style=Paint.Style.STROKE;paint.color=if(offset==0) amber else 0xFF9DA98C.toInt();paint.strokeWidth=shell.dp(if(offset%3==0) 2 else 1).toFloat()
                canvas.drawLine(g.x+c*g.radius*.87f,g.y+a*g.radius*.87f,g.x+c*g.radius*.97f,g.y+a*g.radius*.97f,paint)
                if(offset%3!=0 && switchValue==null) continue
                paint.style=Paint.Style.FILL;paint.textSize=shell.dp(if(offset==0) 18 else 13).toFloat()
                paint.typeface=if(offset==0) Typeface.create("monospace",Typeface.BOLD) else Typeface.MONOSPACE
                val number=format(index).removeSuffix(" EV")
                val maxWidth=g.radius*.65f
                if(paint.measureText(number)>maxWidth) paint.textSize*=maxWidth/paint.measureText(number)
                val half=paint.measureText(number)/2
                val tx=if(g.bottom) g.x+c*g.radius*.62f else maxOf(g.boundary+half+shell.dp(4),g.x+c*g.radius*.62f)
                val proposedY=g.y+a*g.radius*.62f-(paint.ascent()+paint.descent())/2
                val ty=if(g.bottom) minOf(g.boundary-paint.descent()-shell.dp(4),proposedY) else proposedY
                canvas.drawText(number,tx,ty,paint)
            }
            // Fixed wedge at the outer edge, aimed inward at the active scale value.
            val axis=Math.toRadians(g.axis.toDouble());val c=kotlin.math.cos(axis).toFloat();val a=kotlin.math.sin(axis).toFloat()
            val tipX=g.x+c*(g.radius-shell.dp(6));val tipY=g.y+a*(g.radius-shell.dp(6))
            val backX=g.x+c*(g.radius+shell.dp(7));val backY=g.y+a*(g.radius+shell.dp(7))
            paint.style=Paint.Style.STROKE;paint.color=amber;paint.strokeWidth=shell.dp(3).toFloat();paint.strokeCap=Paint.Cap.ROUND
            canvas.drawLine(backX-a*shell.dp(5),backY+c*shell.dp(5),tipX,tipY,paint)
            canvas.drawLine(tipX,tipY,backX+a*shell.dp(5),backY-c*shell.dp(5),paint)
            paint.strokeCap=Paint.Cap.BUTT;canvas.restore()
        }
        private fun commit() { if(card!==this) return;describe();invalidate();schedule();if(switchValue==null) changed(state.value) }
        fun startFromAnchor(screenX: Float,screenY: Float) {
            if(card!==this) return
            synchronizeSwitch();switchStart=if(switchValue!=null) state.value else null
            gesture.begin(screenX,screenY);dragged=false;schedule(true)
        }
        fun moveFromAnchor(screenX: Float,screenY: Float) {
            if(card!==this) return
            val moved=gesture.move(screenX,screenY,shell.dp(24).toFloat())
            if(kotlin.math.abs(gesture.distance)>slop) dragged=true
            if(moved) commit();invalidate();if(card===this) schedule(true)
        }
        fun finishFromAnchor(cancelled: Boolean=false) {
            if(card!==this) return
            val before=switchStart;switchStart=null
            if(cancelled && before!=null) state.select(before)
            gesture.finish();describe();invalidate();schedule()
            if(!cancelled && before!=null && state.value!=before) {
                changed(state.value);synchronizeSwitch()
            }
        }
        override fun onInitializeAccessibilityNodeInfo(info: android.view.accessibility.AccessibilityNodeInfo) {
            super.onInitializeAccessibilityNodeInfo(info)
            info.className=if(switchValue!=null) "android.widget.Switch" else "android.widget.SeekBar"
            if(switchValue!=null) {
                info.isCheckable=true;info.isChecked=state.value==1
                info.addAction(android.view.accessibility.AccessibilityNodeInfo.AccessibilityAction.ACTION_CLICK)
            } else info.rangeInfo=android.view.accessibility.AccessibilityNodeInfo.RangeInfo.obtain(android.view.accessibility.AccessibilityNodeInfo.RangeInfo.RANGE_TYPE_INT,state.lower.toFloat(),state.upper.toFloat(),state.value.toFloat())
            info.addAction(android.view.accessibility.AccessibilityNodeInfo.AccessibilityAction.ACTION_SCROLL_FORWARD)
            info.addAction(android.view.accessibility.AccessibilityNodeInfo.AccessibilityAction.ACTION_SCROLL_BACKWARD)
            info.addAction(android.view.accessibility.AccessibilityNodeInfo.AccessibilityAction.ACTION_DISMISS)
        }
        override fun performAccessibilityAction(action: Int,args: android.os.Bundle?): Boolean {
            if(card!==this) return false
            if(action==android.view.accessibility.AccessibilityNodeInfo.ACTION_DISMISS) { dismiss();return true }
            if(switchValue!=null && action==android.view.accessibility.AccessibilityNodeInfo.ACTION_CLICK) {
                state.select(1-state.value);changed(state.value);synchronizeSwitch();return true
            }
            val delta=when(action) { android.view.accessibility.AccessibilityNodeInfo.ACTION_SCROLL_FORWARD->1;android.view.accessibility.AccessibilityNodeInfo.ACTION_SCROLL_BACKWARD->-1;else->return super.performAccessibilityAction(action,args) }
            schedule();if(state.select(state.value+delta)) {
                if(switchValue!=null) { changed(state.value);synchronizeSwitch() } else commit()
            };return true
        }
        override fun onTouchEvent(event: MotionEvent): Boolean {
            if(card!==this) return false
            when(event.actionMasked) {
                MotionEvent.ACTION_DOWN->{
                    if(!hits(event.x,event.y)) return false
                    taps.cancel()
                    directActive=true
                    startFromAnchor(event.rawX,event.rawY);parent?.requestDisallowInterceptTouchEvent(true)
                }
                MotionEvent.ACTION_MOVE->{
                    if(directActive && event.pointerCount==1) moveFromAnchor(event.rawX,event.rawY)
                }
                MotionEvent.ACTION_POINTER_DOWN->{ directActive=false;parent?.requestDisallowInterceptTouchEvent(false);finishFromAnchor(true) }
                MotionEvent.ACTION_UP,MotionEvent.ACTION_CANCEL->{
                    if(event.actionMasked==MotionEvent.ACTION_UP && directActive && !dragged) geometry?.let { g ->
                        var angle=Math.toDegrees(kotlin.math.atan2((event.y-g.y).toDouble(),(event.x-g.x).toDouble())).toFloat()-g.axis
                        while(angle>180f) angle-=360f
                        while(angle< -180f) angle+=360f
                        if(state.select(state.value-kotlin.math.round(angle/22f).toInt())) commit()
                    }
                    directActive=false;parent?.requestDisallowInterceptTouchEvent(false);finishFromAnchor(event.actionMasked==MotionEvent.ACTION_CANCEL)
                    if(event.actionMasked==MotionEvent.ACTION_UP) performClick()
                }
            };return true
        }
        override fun performClick(): Boolean { super.performClick();return true }
    }
}
