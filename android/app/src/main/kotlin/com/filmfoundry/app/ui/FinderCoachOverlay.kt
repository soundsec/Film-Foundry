package com.filmfoundry.app.ui

import android.graphics.Canvas
import android.graphics.Color
import android.graphics.Paint
import android.graphics.Rect
import android.view.Gravity
import android.view.MotionEvent
import android.view.View
import android.widget.Button
import android.widget.FrameLayout
import android.widget.LinearLayout
import android.widget.ScrollView
import android.widget.TextView
import com.filmfoundry.mobile.R

/** Each prompt follows the visible bounds of its live finder control. */
class FinderCoachOverlay(private val shell: MobileShell) : FrameLayout(shell.root.context) {
    private data class Step(val target: View, val text: String)
    private var steps = emptyList<Step>()
    private var index = 0
    private val bounds = Rect()
    private val origin = IntArray(2)
    private val ink = Paint(Paint.ANTI_ALIAS_FLAG).apply { color = 0xffefc372.toInt(); style = Paint.Style.STROKE; strokeWidth = shell.dp(2).toFloat() }
    private val message = TextView(context).apply { textSize = 16f; setTextColor(Color.WHITE) }
    private val next = Button(context).apply { setOnClickListener { advance() } }
    private val card = LinearLayout(context).apply {
        orientation = LinearLayout.VERTICAL
        setBackgroundColor(0xff202320.toInt())
        setPadding(shell.dp(12),shell.dp(8),shell.dp(12),shell.dp(8))
        addView(ScrollView(context).apply { addView(message) }, LinearLayout.LayoutParams(-1,0,1f))
        addView(LinearLayout(context).apply {
            addView(Button(context).apply {
                text = shell.copy("关闭","Close")
                setOnClickListener { dismiss() }
            }, LinearLayout.LayoutParams(0,-2,1f))
            addView(next, LinearLayout.LayoutParams(0,-2,1f))
        })
    }

    init {
        visibility = GONE
        elevation = shell.dp(16).toFloat()
        setWillNotDraw(false)
        isClickable = true
        addView(card)
    }

    fun reveal(): Boolean {
        if (visibility == VISIBLE) return true
        val finder = parent as? View ?: return false
        val candidates = mutableListOf<Step>()
        fun add(view: View?, zh: String, en: String) {
            if (view != null && view.isShown && view.isEnabled && view.getGlobalVisibleRect(Rect()))
                candidates += Step(view,shell.copy(zh,en))
        }
        add(finder.findViewWithTag("finder_readout_ev"),"EV · 按住上下滑动，双击归零。","EV · Hold and slide vertically. Double-tap to reset.")
        add(finder.findViewWithTag("finder_readout_shutter"),"快门速度 · 按住上下滑动，双击恢复自动。","Shutter speed · Hold and slide vertically. Double-tap for auto.")
        add(finder.findViewWithTag("capture_rail"),"多曝 · 在当前画格上再次上弦。","Multiple exposure · Re-cock on this frame.")
        add(finder.findViewWithTag("shutter"),"快门 · 点按拍摄当前画格。","Shutter · Tap to expose the current frame.")
        add(finder.findViewWithTag("capture_advance"),"过片 · 点按进入下一格。","Advance · Tap to move to the next frame.")
        add(finder.findViewById(R.id.finder_zoom),"倍率 · 按住左右滑动，双击回到 1×。","Zoom · Hold and slide horizontally. Double-tap for 1×.")
        add(finder.findViewById(R.id.finder_format),"采集格式 · 点按选择下一次曝光使用的格式。","Format · Tap to choose the format for the next exposure.")
        if (candidates.isEmpty()) return false
        steps = candidates
        index = 0
        visibility = VISIBLE
        showStep()
        return true
    }

    private fun advance() {
        do { index++ } while (index < steps.size && !steps[index].target.getGlobalVisibleRect(Rect()))
        if (index == steps.size) dismiss() else showStep()
    }

    private fun showStep() {
        message.text = "${index + 1} / ${steps.size}   ${steps[index].text}"
        next.text = if (index == steps.lastIndex) shell.copy("完成","Done") else shell.copy("下一项","Next")
        announceForAccessibility(message.text)
        requestLayout()
        invalidate()
    }

    override fun onMeasure(w: Int,h: Int) {
        val width = MeasureSpec.getSize(w)
        val height = MeasureSpec.getSize(h)
        setMeasuredDimension(width,height)
        card.measure(MeasureSpec.makeMeasureSpec(minOf(shell.dp(300),width/2),MeasureSpec.EXACTLY),
            MeasureSpec.makeMeasureSpec(minOf(shell.dp(144),height),MeasureSpec.EXACTLY))
    }

    private fun locate() {
        val target = steps.getOrNull(index)?.target
        if (target == null || !target.getGlobalVisibleRect(bounds)) { bounds.setEmpty(); return }
        getLocationOnScreen(origin)
        // Global visible rectangles are in root-view coordinates.
        rootView.getLocationOnScreen(rootOrigin)
        bounds.offset(rootOrigin[0]-origin[0],rootOrigin[1]-origin[1])
    }
    private val rootOrigin = IntArray(2)

    override fun onLayout(changed: Boolean,l: Int,t: Int,r: Int,b: Int) {
        locate()
        val gap = shell.dp(8)
        val left = (if (bounds.centerX() < width/2) bounds.right + gap else bounds.left - gap - card.measuredWidth)
            .coerceIn(0,width-card.measuredWidth)
        val top = (bounds.centerY()-card.measuredHeight/2).coerceIn(0,height-card.measuredHeight)
        card.layout(left,top,left+card.measuredWidth,top+card.measuredHeight)
    }

    override fun onDraw(canvas: Canvas) {
        locate()
        canvas.save()
        canvas.clipOutRect(bounds)
        canvas.drawColor(0x99000000.toInt())
        canvas.restore()
        canvas.drawRect(bounds,ink)
    }

    fun consume(event: MotionEvent): Boolean {
        if (visibility != VISIBLE) return false
        dispatchTouchEvent(event)
        return true
    }
    fun dismiss(): Boolean {
        val wasVisible = visibility == VISIBLE
        visibility = GONE
        steps = emptyList()
        return wasVisible
    }
    fun stop() { dismiss() }
}
