package com.filmfoundry.app.ui

import android.content.Context
import android.graphics.Color
import android.graphics.drawable.GradientDrawable
import android.os.Build
import android.view.Gravity
import android.view.MotionEvent
import android.view.View
import android.view.accessibility.AccessibilityManager
import android.widget.FrameLayout
import android.widget.LinearLayout
import android.widget.TextView
import android.widget.ScrollView
import android.view.ViewConfiguration

/** Presentation only: no camera, exposure or film transport callbacks. */
class FinderCoachOverlay(private val shell: MobileShell): FrameLayout(shell.root.context) {
    private val state=FinderCoachState()
    private val expire=Runnable { dismiss() }
    private val accessibility=context.getSystemService(Context.ACCESSIBILITY_SERVICE) as AccessibilityManager
    private val scroll=ScrollView(context).apply { isFillViewport=true;isVerticalScrollBarEnabled=false }
    private var pressX=0f;private var pressY=0f
    private val touchSlop=ViewConfiguration.get(context).scaledTouchSlop
    init {
        visibility=View.GONE;setBackgroundColor(0xA6000000.toInt());elevation=shell.dp(16).toFloat()
        isClickable=true;isFocusable=true
        val content=LinearLayout(context).apply {
            orientation=LinearLayout.VERTICAL;gravity=Gravity.CENTER_VERTICAL
            setPadding(shell.dp(12),shell.dp(8),shell.dp(12),shell.dp(8))
        }
        addView(scroll,LayoutParams(-1,-1).apply { setMargins(shell.dp(20),0,shell.dp(20),0) })
        scroll.addView(content,FrameLayout.LayoutParams(-1,-2))
        fun card(zh: String,en: String)=TextView(context).apply {
            text=shell.copy(zh,en);textSize=14f;setTextColor(Color.WHITE)
            setPadding(shell.dp(10),shell.dp(8),shell.dp(10),shell.dp(8))
            background=GradientDrawable().apply { setColor(0xE61A201C.toInt());cornerRadius=shell.dp(8).toFloat();setStroke(shell.dp(1),0xFFE8BF6D.toInt()) }
        }
        val heading=TextView(context).apply {
            text=shell.copy("取景器手势 · 可上下滑动，点按收起","Finder gestures · scroll to read, tap to close")
            textSize=14f;setTextColor(0xFFE8BF6D.toInt());gravity=Gravity.CENTER
        }
        content.addView(heading,LinearLayout.LayoutParams(-1,-2).apply { bottomMargin=shell.dp(8) })
        val row=LinearLayout(context).apply { orientation=if(resources.configuration.screenWidthDp/resources.configuration.fontScale<620) LinearLayout.VERTICAL else LinearLayout.HORIZONTAL }
        content.addView(row,LinearLayout.LayoutParams(-1,-2))
        fun cardParams()=if(row.orientation==LinearLayout.HORIZONTAL) LinearLayout.LayoutParams(0,-2,1f).apply { marginEnd=shell.dp(8) } else LinearLayout.LayoutParams(-1,-2).apply { bottomMargin=shell.dp(8) }
        row.addView(card("← 左侧读数\n按住 EV / 快门，再上下拖动\n双击恢复默认 / 自动", "← Side readouts\nHold EV / shutter, slide ↑↓\nDouble-tap: default / auto"),
            cardParams())
            row.addView(card("右侧过片 →\n手动过片：拍完点过片换下一格\n多曝：同一格重新上弦", "Transport →\nManual: Advance for next frame\nMulti: re-cock the same frame"),cardParams())
        scroll.addOnLayoutChangeListener { _,left,_,right,_,_,_,_,_->
            val orientation=if((right-left)/resources.displayMetrics.density/resources.configuration.fontScale<620) LinearLayout.VERTICAL else LinearLayout.HORIZONTAL
            if(row.orientation!=orientation) {
                row.orientation=orientation
                for(index in 0 until row.childCount) row.getChildAt(index).layoutParams=cardParams()
            }
        }
        content.addView(card("↓ 底部倍率：按住后左右拖动，双击复位\n画面可点按对焦、双指缩放；手动快门需镜头支持。", "↓ Bottom zoom: hold and slide ←→; double-tap to reset\nTap image to focus; pinch to zoom. Manual shutter needs lens support."),
            LinearLayout.LayoutParams(-1,-2).apply { topMargin=shell.dp(8) })
        contentDescription=shell.copy("取景器手势。左侧 EV 和快门按住上下滑，双击复位。底部倍率按住左右滑，双击复位。手动过片拍完需点过片，多曝留在当前格。点按收起。",
            "Finder gestures. Hold side EV or shutter and slide vertically; double-tap to reset. Hold bottom zoom and slide horizontally; double-tap to reset. Advance moves to the next frame, Multi keeps this frame. Tap to dismiss.")
        importantForAccessibility=View.IMPORTANT_FOR_ACCESSIBILITY_YES
        content.importantForAccessibility=View.IMPORTANT_FOR_ACCESSIBILITY_NO_HIDE_DESCENDANTS
        setOnClickListener { dismiss() }
    }
    fun reveal(autoDismiss: Boolean) {
        removeCallbacks(expire);state.show();visibility=View.VISIBLE;scroll.scrollTo(0,0)
        announceForAccessibility(contentDescription)
        if(autoDismiss && !accessibility.isTouchExplorationEnabled) {
            val duration=if(Build.VERSION.SDK_INT>=29) accessibility.getRecommendedTimeoutMillis(3000,AccessibilityManager.FLAG_CONTENT_TEXT) else 3000
            postDelayed(expire,duration.toLong())
        }
    }
    fun dismiss(): Boolean {
        val showing=state.visible;state.dismiss();visibility=View.GONE;removeCallbacks(expire);return showing
    }
    fun consume(event: MotionEvent): Boolean {
        val down=event.actionMasked==MotionEvent.ACTION_DOWN
        if(down) { pressX=event.x;pressY=event.y }
        val moved=kotlin.math.abs(event.x-pressX)>touchSlop || kotlin.math.abs(event.y-pressY)>touchSlop || event.pointerCount>1
        val consumed=state.touch(down,event.actionMasked in listOf(MotionEvent.ACTION_UP,MotionEvent.ACTION_CANCEL),moved,event.actionMasked==MotionEvent.ACTION_CANCEL)
        if(consumed) {
            removeCallbacks(expire)
            val local=MotionEvent.obtain(event);local.offsetLocation(-scroll.left.toFloat(),-scroll.top.toFloat())
            try { scroll.dispatchTouchEvent(local) } finally { local.recycle() }
            if(!state.visible) visibility=View.GONE
        }
        return consumed
    }
    fun stop() { dismiss();state.reset() }
    override fun onDetachedFromWindow() { stop();super.onDetachedFromWindow() }
}
