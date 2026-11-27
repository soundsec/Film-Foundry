package com.filmfoundry.app.ui

import android.content.res.ColorStateList
import android.graphics.Typeface
import android.graphics.drawable.RippleDrawable
import android.view.Gravity
import android.view.KeyEvent
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
    private val firstTip = TextView(context)
    val controls = LinearLayout(context)
    private val slop = ViewConfiguration.get(context).scaledTouchSlop
    private var tool: CharSequence = ""
    private var largeTool = false
    private var expanded = false
    private var draggedHeight: Int? = null
    private var openHeight = 0
    private var heldFraction: Float? = null
    private var availableHeight = 0
    private var downY = 0f
    private var downX = 0f
    private var startHeight = 0
    private var multiplePointers = false

    init {
        orientation = VERTICAL
        setBackgroundColor(shell.palette.panel)
        handle.apply {
            tag = "scan_drawer_handle"
            orientation = VERTICAL
            minimumHeight = shell.dp(48)
            setPadding(shell.dp(16), 0, shell.dp(16), 0)
            background = RippleDrawable(ColorStateList.valueOf(shell.palette.border),
                android.graphics.drawable.ColorDrawable(shell.palette.panel), null)
            isFocusable = true
            setOnClickListener { settle(!expanded) }
            setOnTouchListener { _, event -> gesture(event) }
            setOnKeyListener { _, key, event ->
                when (key) {
                    KeyEvent.KEYCODE_ENTER, KeyEvent.KEYCODE_DPAD_CENTER, KeyEvent.KEYCODE_SPACE -> {
                        if(event.action==KeyEvent.ACTION_UP && !event.isCanceled) performClick()
                        true
                    }
                    else -> false
                }
            }
        }
        title.apply {
            textSize = 15f
            typeface = Typeface.DEFAULT_BOLD
            setTextColor(shell.ink)
        }
        hint.apply { textSize = 13f; setTextColor(shell.palette.muted) }
        firstTip.apply {
            text = shell.copy("拖动滑钮 · 长按精调 · 双击复位 · 长按照片对照",
                "Drag slider · Hold for fine control · Double-tap to reset · Hold photo to compare")
            textSize = 13f
            setTextColor(shell.palette.muted)
            setPadding(0,shell.dp(8),0,0)
            visibility = View.GONE
        }
        handle.addView(firstTip)
        handle.addView(LinearLayout(context).apply {
            gravity = Gravity.CENTER_VERTICAL
            minimumHeight = shell.dp(48)
            addView(title, LayoutParams(0, -2, 1f))
            addView(hint)
        }, LayoutParams(-1,-2))
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
        hint.text = if (expanded) shell.copy("拖动调高度 · 点按收起", "Drag to resize · Tap to close") else shell.copy("上滑展开  ︿", "Slide up  ↑")
        handle.contentDescription = listOfNotNull(title.text, hint.text,
            firstTip.text.takeIf { firstTip.visibility == View.VISIBLE }).joinToString(" · ")
    }

    fun enter() {
        val prefs=context.getSharedPreferences("mobile_ui", 0)
        val first=!prefs.getBoolean("scan_slider_gestures_seen",false)
        firstTip.visibility=if(first) View.VISIBLE else View.GONE
        if(first) prefs.edit().putBoolean("scan_slider_gestures_seen",true).apply()
        heldFraction = null
        settle(false)
    }

    fun settle(open: Boolean) {
        if(open) firstTip.visibility=View.GONE
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
        availableHeight = available
        openHeight = (available * .8f).toInt()
        val closed = minOf(closedHeight, openHeight)
        val preferred = (available * (heldFraction ?: if (largeTool) .80f else .64f)).roundToInt()
        val wanted = (draggedHeight ?: if (expanded) preferred else closed).coerceIn(closed, openHeight)
        super.onMeasure(widthMeasureSpec, MeasureSpec.makeMeasureSpec(wanted, MeasureSpec.EXACTLY))
    }

    private fun gesture(event: MotionEvent): Boolean {
        val delta = downY - event.rawY
        when (event.actionMasked) {
            MotionEvent.ACTION_DOWN -> {
                multiplePointers = false
                downY = event.rawY
                downX = event.rawX
                startHeight = height
                handle.isPressed = true
            }
            MotionEvent.ACTION_POINTER_DOWN -> {
                multiplePointers = true
                handle.isPressed = false
                settle(expanded)
            }
            MotionEvent.ACTION_MOVE -> if (!multiplePointers) {
                if (draggedHeight == null && (abs(delta) <= slop || abs(delta) < abs(event.rawX - downX))) return true
                handle.isPressed = false
                draggedHeight = (startHeight + delta.roundToInt()).coerceIn(minOf(closedHeight, openHeight), openHeight)
                controls.visibility = View.VISIBLE
                requestLayout()
            }
            MotionEvent.ACTION_UP -> if (!multiplePointers) {
                handle.isPressed = false
                if (draggedHeight != null) {
                    val chosen = draggedHeight!!
                    val open = chosen > closedHeight + slop
                    if (open) heldFraction = chosen.toFloat() / availableHeight.coerceAtLeast(1)
                    settle(open)
                }
                else if (abs(delta) <= slop && abs(event.rawX - downX) <= slop) handle.performClick()
            }
            MotionEvent.ACTION_CANCEL -> { handle.isPressed = false; settle(expanded) }
        }
        return true
    }
}
