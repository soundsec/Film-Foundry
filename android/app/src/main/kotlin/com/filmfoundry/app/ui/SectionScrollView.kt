package com.filmfoundry.app.ui

import android.content.Context
import android.view.View
import android.view.ViewGroup
import android.widget.HorizontalScrollView
import android.widget.SeekBar
import android.view.MotionEvent
import android.view.ViewConfiguration
import android.widget.ScrollView
import kotlin.math.abs

/** Page through sections outside horizontally interactive controls. Vertical drags stay with the native scroll view. */
internal class SectionScrollView(context: Context) : ScrollView(context) {
    var turnPage: (Int) -> Unit = {}
    private val slop = ViewConfiguration.get(context).scaledTouchSlop
    private var downX = 0f
    private var downY = 0f
    private var undecided = false
    private var paging = false
    private var singleTouch = false

    // Decide from the DOWN target. A nested strip still owns its gesture at either edge.
    private fun ownsHorizontalDrag(view: View, x: Float, y: Float): Boolean {
        if (view is HorizontalScrollView || view is SeekBar || view is CurveGraph) return true
        if (view !is ViewGroup) return false
        for (i in view.childCount - 1 downTo 0) {
            val child=view.getChildAt(i)
            if (child.visibility != View.VISIBLE) continue
            val localX=x+view.scrollX-child.x
            val localY=y+view.scrollY-child.y
            if (localX >= 0 && localX < child.width && localY >= 0 && localY < child.height) {
                return ownsHorizontalDrag(child, localX, localY)
            }
        }
        return false
    }

    override fun dispatchTouchEvent(event: MotionEvent): Boolean {
        when (event.actionMasked) {
            MotionEvent.ACTION_DOWN -> {
                downX = event.x
                downY = event.y
                undecided = !ownsHorizontalDrag(this, event.x, event.y)
                paging = false
                singleTouch = true
                super.dispatchTouchEvent(event)
                return true
            }
            MotionEvent.ACTION_POINTER_DOWN -> {
                singleTouch = false
                undecided = false
            }
            MotionEvent.ACTION_MOVE -> if (undecided) {
                val dx = abs(event.x - downX)
                val dy = abs(event.y - downY)
                if (maxOf(dx, dy) > slop) {
                    undecided = false
                    paging = dx > dy * 1.5f
                    if (paging) {
                        val cancel = MotionEvent.obtain(event)
                        cancel.action = MotionEvent.ACTION_CANCEL
                        super.dispatchTouchEvent(cancel)
                        cancel.recycle()
                    }
                }
            }
            MotionEvent.ACTION_UP -> if (paging && singleTouch) {
                val dx = event.x - downX
                if (abs(dx) >= 3 * slop) {
                    val forward = if (layoutDirection == LAYOUT_DIRECTION_RTL) dx > 0 else dx < 0
                    turnPage(if (forward) 1 else -1)
                }
            }
        }
        return paging || super.dispatchTouchEvent(event)
    }
}
