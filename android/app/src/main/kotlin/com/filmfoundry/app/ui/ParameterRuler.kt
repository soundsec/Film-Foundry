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

/** Relative touch input with the platform SeekBar's keyboard and accessibility range. */
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
    private var lastX = 0f
    private var position = 0f
    private var tracking = false
    private var fineMode = false
    private var ignore = false
    private var touchUpdate = false
    var resetValue: (() -> Unit)? = null

    private val taps = GestureDetector(context, object : GestureDetector.SimpleOnGestureListener() {
        override fun onDown(e: MotionEvent) = true
        override fun onDoubleTap(e: MotionEvent): Boolean {
            val reset = resetValue ?: return false
            ignore = true
            reset()
            return true
        }
    }).apply { setIsLongpressEnabled(false) }

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
        val center = width / 2f
        val middle = height / 2f
        val spacing = 8 * density
        val stepsPerTick = rulerStepsPerTick(max, fineMode).roundToInt().coerceAtLeast(1)
        val origin = progress.toFloat() / stepsPerTick
        val ticks = (center / spacing).toInt() + 1
        pen.color = tickColor
        pen.alpha = if (isEnabled) 65 else 30
        pen.strokeWidth = density
        canvas.drawLine(0f, middle, width.toFloat(), middle, pen)
        for (i in origin.toInt()-ticks..origin.toInt()+ticks) {
            if (i < 0 || i.toLong() * stepsPerTick > max) continue
            val x = center + (i - origin) * spacing
            val size = if (i % 5 == 0) 8 * density else 4 * density
            pen.alpha = if (isEnabled) 160 else 75
            pen.strokeWidth = density
            canvas.drawLine(x, middle-size, x, middle+size, pen)
        }
        pen.color = pointerColor
        pen.alpha = if (isEnabled) 255 else 100
        pen.strokeWidth = (if (fineMode) 5 else 3) * density
        canvas.drawLine(center, middle-12*density, center, middle+12*density, pen)
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

    private fun finish() {
        if (fineMode) {
            fineMode = false
            invalidate()
        }
        if (tracking) {
            tracking = false
            listener?.onStopTrackingTouch(this)
        }
        isPressed = false
        parent?.requestDisallowInterceptTouchEvent(false)
    }

    override fun onTouchEvent(event: MotionEvent): Boolean {
        if (!isEnabled) return false
        if (event.actionMasked == MotionEvent.ACTION_DOWN) {
            startX = event.x
            lastX = startX
            startY = event.y
            ignore = false
            position = progress.toFloat()
            parent?.requestDisallowInterceptTouchEvent(true)
        }
        if (resetValue != null) taps.onTouchEvent(event)
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
                    tracking = true
                    isPressed = true
                    listener?.onStartTrackingTouch(this)
                }
                val fine = dy < (if (fineMode) -24 else -30) * density
                val modeChanged = fineMode != fine
                if (modeChanged) {
                    fineMode = fine
                    performHapticFeedback(HapticFeedbackConstants.CLOCK_TICK)
                    invalidate()
                }
                val sensitivity = rulerStepsPerTick(max, fine)
                position = (position + (event.x-lastX) * sensitivity / (8*density)).coerceIn(0f, max.toFloat())
                lastX = event.x
                val next = position.roundToInt()
                if (next != progress) {
                    val stepsPerTick = sensitivity.roundToInt().coerceAtLeast(1)
                    val tick = next / stepsPerTick != progress / stepsPerTick
                    touchUpdate = true
                    progress = next
                    touchUpdate = false
                    if (tick && !modeChanged) performHapticFeedback(HapticFeedbackConstants.CLOCK_TICK)
                }
            }
            MotionEvent.ACTION_POINTER_DOWN -> { ignore = true; finish() }
            MotionEvent.ACTION_UP -> { if (!tracking && !ignore) performClick(); finish() }
            MotionEvent.ACTION_CANCEL -> finish()
        }
        return true
    }

    override fun performClick(): Boolean { super.performClick(); return true }
}
