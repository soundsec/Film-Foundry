package com.filmfoundry.app.ui

import android.content.Context
import android.graphics.Canvas
import android.graphics.Paint
import android.view.GestureDetector
import android.view.HapticFeedbackConstants
import android.view.MotionEvent
import android.view.ViewConfiguration
import android.widget.SeekBar
import kotlin.math.abs
import kotlin.math.roundToInt

/** Number of native progress steps per 8 dp of horizontal travel. */
internal fun rulerStepsPerTick(max: Int, fine: Boolean): Float {
    val coarse = maxOf(1, (max / 50f).roundToInt())
    return when {
        !fine -> coarse.toFloat()
        max > 500 -> 1f
        else -> coarse * .2f
    }
}

/** Absolute slider with long-press fine adjustment with the platform SeekBar's keyboard and accessibility range. */
internal class ParameterRuler(
    context: Context, private val tickColor: Int, private val pointerColor: Int,
    private val fineLabel: String
) : SeekBar(context) {
    private val density = resources.displayMetrics.density
    private val slop = ViewConfiguration.get(context).scaledTouchSlop
    private val pen = Paint(Paint.ANTI_ALIAS_FLAG)
    private var listener: OnSeekBarChangeListener? = null
    private var startX = 0f
    private var startY = 0f
    private var fineOrigin = 0f

    private var tracking = false
    private var fineMode = false
    private var ignore = false
    private var touchUpdate = false
    var resetValue: (() -> Unit)? = null

    private val taps = GestureDetector(context, object : GestureDetector.SimpleOnGestureListener() {
        override fun onDown(e: MotionEvent) = true
        override fun onLongPress(e: MotionEvent) {
            if (ignore || tracking || !isPressed) return
            beginTracking()
            fineMode = true
            fineOrigin = progress.toFloat()
            performHapticFeedback(HapticFeedbackConstants.LONG_PRESS)
            invalidate()
        }
        override fun onDoubleTap(e: MotionEvent): Boolean {
            val reset = resetValue ?: return false
            ignore = true
            reset()
            return true
        }
    })

    init {
        background = null
        minimumHeight = (48 * density).roundToInt()
        super.setOnSeekBarChangeListener(object : OnSeekBarChangeListener {
            override fun onProgressChanged(bar: SeekBar, progress: Int, fromUser: Boolean) {
                // A listener may synchronously restore an inherited value.
                val user = fromUser || touchUpdate
                touchUpdate = false
                if (fromUser && !tracking) listener?.onStartTrackingTouch(bar)
                listener?.onProgressChanged(bar, progress, user)
                if (fromUser && !tracking) listener?.onStopTrackingTouch(bar)
            }
            override fun onStartTrackingTouch(bar: SeekBar) = Unit
            override fun onStopTrackingTouch(bar: SeekBar) = Unit
        })
    }

    override fun setOnSeekBarChangeListener(value: OnSeekBarChangeListener?) { listener = value }

    override fun onMeasure(widthMeasureSpec: Int, heightMeasureSpec: Int) {
        super.onMeasure(widthMeasureSpec, heightMeasureSpec)
        setMeasuredDimension(measuredWidth, resolveSize(maxOf(measuredHeight, minimumHeight), heightMeasureSpec))
    }

    override fun onDraw(canvas: Canvas) {
        val middle = height / 2f
        val left = trackInset
        val right = width - left
        val fraction = if (max == 0) 0f else progress.toFloat() / max
        val rtl = layoutDirection == LAYOUT_DIRECTION_RTL
        val x = left + (right - left) * (if (rtl) 1f - fraction else fraction)
        pen.strokeCap = Paint.Cap.ROUND
        pen.strokeWidth = 4 * density
        pen.color = tickColor
        pen.alpha = if (isEnabled) 135 else 60
        canvas.drawLine(left, middle, right, middle, pen)
        pen.color = pointerColor
        pen.alpha = if (isEnabled) 255 else 100
        canvas.drawLine(if (rtl) right else left, middle, x, middle, pen)
        canvas.drawCircle(x, middle, (if (fineMode) 10 else 8) * density, pen)
        if (fineMode) {
            pen.textSize = 12 * density
            canvas.drawText(fineLabel, 4 * density, pen.textSize, pen)
        }
        if (isFocused && !isInTouchMode) {
            pen.style = Paint.Style.STROKE
            pen.strokeWidth = density
            canvas.drawRect(density, density, width-density, height-density, pen)
            pen.style = Paint.Style.FILL
        }
    }

    private val trackInset get() = minOf(width / 2f, 12 * density)

    private fun beginTracking() {
        if (tracking) return
        tracking = true
        listener?.onStartTrackingTouch(this)
    }

    private fun moveThumb(x: Float) {
        val span = (width - 2 * trackInset).coerceAtLeast(1f)
        val rtl = layoutDirection == LAYOUT_DIRECTION_RTL
        val fraction = (x - trackInset) / span
        val position = if (fineMode) fineOrigin + (x - startX) * (if (rtl) -1 else 1) *
            minOf(rulerStepsPerTick(max,true) / (8 * density), max / span * .2f)
        else (if (rtl) 1f - fraction else fraction) * max
        touchUpdate = true
        try { progress = position.roundToInt().coerceIn(0,max) } finally { touchUpdate = false }
    }

    private fun finish() {
        fineMode = false
        if (tracking) {
            tracking = false
            listener?.onStopTrackingTouch(this)
        }
        isPressed = false
        invalidate()
        parent?.requestDisallowInterceptTouchEvent(false)
    }

    override fun onTouchEvent(event: MotionEvent): Boolean {
        if (!isEnabled) return false
        if (event.actionMasked == MotionEvent.ACTION_DOWN) {
            startX = event.x
            isPressed = true
            startY = event.y
            ignore = false
            parent?.requestDisallowInterceptTouchEvent(true)
        }
        taps.onTouchEvent(event)
        when (event.actionMasked) {
            MotionEvent.ACTION_MOVE -> if (!ignore) {
                val dx = event.x - startX
                val dy = event.y - startY
                if (!tracking) {
                    if (maxOf(abs(dx), abs(dy)) <= slop) return true
                    if (abs(dx) <= abs(dy)) {
                        ignore = true
                        parent?.requestDisallowInterceptTouchEvent(false)
                        return true
                    }
                    beginTracking()
                }
                moveThumb(event.x)
            }
            MotionEvent.ACTION_POINTER_DOWN -> { ignore = true; finish() }
            MotionEvent.ACTION_UP -> { if (!ignore && !tracking) {
                    beginTracking()
                    moveThumb(event.x)
                    performClick()
                }
                finish() }
            MotionEvent.ACTION_CANCEL -> finish()
        }
        return true
    }

    override fun performClick(): Boolean { super.performClick(); return true }
}
