package com.filmfoundry.app.ui

import android.content.res.ColorStateList
import android.graphics.Typeface
import android.graphics.drawable.RippleDrawable
import android.view.Gravity
import android.view.MotionEvent
import android.view.View
import android.view.ViewConfiguration
import android.widget.LinearLayout
import android.widget.TextView
import kotlin.math.abs
import kotlin.math.roundToInt

/** The handle alone owns vertical drags. Editors retain their scrolling and drawing gestures. */
internal class ScanAdjustmentDrawer(private val shell: MobileShell) : LinearLayout(shell.root.context) {
    private val handle = LinearLayout(context)
    private val title = TextView(context)
    private val hint = TextView(context)
    val controls = LinearLayout(context)
    private val slop = ViewConfiguration.get(context).scaledTouchSlop
    private var tool: CharSequence = ""
    private var largeTool = false
    private var expanded = false
    private var draggedHeight: Int? = null
    private var openHeight = 0
    private var downY = 0f
    private var downX = 0f
    private var startHeight = 0

    init {
        orientation = VERTICAL
        setBackgroundColor(shell.palette.panel)
        handle.apply {
            tag = "scan_drawer_handle"
            gravity = Gravity.CENTER_VERTICAL
            minimumHeight = shell.dp(48)
            setPadding(shell.dp(16), 0, shell.dp(16), 0)
            background = RippleDrawable(ColorStateList.valueOf(shell.palette.border),
                android.graphics.drawable.ColorDrawable(shell.palette.panel), null)
            isFocusable = true
            setOnClickListener { settle(!expanded) }
            setOnTouchListener { _, event -> gesture(event) }
        }
        title.apply {
            textSize = 15f
            typeface = Typeface.DEFAULT_BOLD
            setTextColor(shell.ink)
        }
        hint.apply { textSize = 13f; setTextColor(shell.palette.muted) }
        handle.addView(title, LayoutParams(0, -2, 1f))
        handle.addView(hint)
        addView(View(context).apply { setBackgroundColor(shell.palette.border) }, LayoutParams(-1, shell.dp(1)))
        addView(handle, LayoutParams(-1, -2))
        controls.orientation = VERTICAL
        addView(controls, LayoutParams(-1, 0, 1f))
        settle(false)
    }

    fun selectTool(label: CharSequence, large: Boolean) {
        tool = label
        largeTool = large
        labelHandle()
        requestLayout()
    }

    private fun labelHandle() {
        title.text = if (tool.isEmpty()) shell.copy("扫描调整", "Scan controls") else
            shell.words.message("mobile.scan.drawer_tool", "扫描调整 · {tool}", "Scan controls · {tool}", "tool" to tool.toString())
        hint.text = if (expanded) shell.copy("下滑收起  ﹀", "Slide down  ↓") else shell.copy("上滑展开  ︿", "Slide up  ↑")
        handle.contentDescription = title.text.toString() + "，" + hint.text
    }

    fun settle(open: Boolean) {
        draggedHeight = null
        expanded = open
        controls.visibility = if (open) View.VISIBLE else View.GONE
        labelHandle()
        requestLayout()
    }

    private val closedHeight get() = maxOf(shell.dp(48), handle.measuredHeight) + shell.dp(1)

    override fun onMeasure(widthMeasureSpec: Int, heightMeasureSpec: Int) {
        measureChild(handle, widthMeasureSpec, heightMeasureSpec)
        val available = MeasureSpec.getSize(heightMeasureSpec)
        val closed = minOf(closedHeight, available)
        val preferred = if (largeTool) minOf(shell.dp(486), (available * .74f).roundToInt())
            else minOf(shell.dp(360), (available * .54f).roundToInt())
        openHeight = preferred.coerceIn(closed, maxOf(closed, available - shell.dp(96)))
        val wanted = draggedHeight?.coerceIn(closed, openHeight) ?: if (expanded) openHeight else closed
        super.onMeasure(widthMeasureSpec, MeasureSpec.makeMeasureSpec(wanted, MeasureSpec.EXACTLY))
    }

    private fun gesture(event: MotionEvent): Boolean {
        val delta = downY - event.rawY
        when (event.actionMasked) {
            MotionEvent.ACTION_DOWN -> {
                downY = event.rawY
                downX = event.rawX
                startHeight = height
                handle.isPressed = true
            }
            MotionEvent.ACTION_MOVE -> {
                if (draggedHeight == null && (abs(delta) <= slop || abs(delta) < abs(event.rawX - downX))) return true
                handle.isPressed = false
                draggedHeight = (startHeight + delta.roundToInt()).coerceIn(minOf(closedHeight, openHeight), openHeight)
                controls.visibility = View.VISIBLE
                requestLayout()
            }
            MotionEvent.ACTION_UP -> {
                handle.isPressed = false
                if (draggedHeight != null) settle(if (abs(delta) > slop) delta > 0 else expanded)
                else if (abs(delta) <= slop && abs(event.rawX - downX) <= slop) handle.performClick()
            }
            MotionEvent.ACTION_CANCEL -> { handle.isPressed = false; settle(expanded) }
        }
        return true
    }
}
