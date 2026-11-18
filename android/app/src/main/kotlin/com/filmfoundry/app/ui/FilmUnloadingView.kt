package com.filmfoundry.app.ui

import android.animation.Animator
import android.animation.AnimatorListenerAdapter
import android.animation.ValueAnimator
import android.content.Context
import android.graphics.Color
import android.view.Gravity
import android.view.View
import android.view.animation.AccelerateDecelerateInterpolator
import android.widget.Button
import android.widget.FrameLayout
import android.widget.LinearLayout
import android.widget.TextView

/** Leaves the finder, opens the back and takes the sealed roll out. Separate from loading. */
class FilmUnloadingView(
    context: Context,
    private val copy: (String, String) -> String,
    rollName: String,
    material: String,
    format: String,
    film: Map<String, Any?>,
    capacity: Int,
    private val onFinished: () -> Unit,
) : FrameLayout(context) {
    private val picture = FilmChamberPicture(format, FilmFace.of(film, rollName)).apply {
        thread = 1f
        approach = 1f
        door = 0f
        removed = 0f
    }
    private val scene = Scene(context)
    private val hint = TextView(context)
    private val skip = Button(context)
    private val transition = UnloadingTransition()
    private var animator: ValueAnimator? = null
    private var animationRevision = 0
    private fun dp(n: Int) = (n * resources.displayMetrics.density).toInt()

    init {
        setBackgroundColor(Color.BLACK)
        addView(scene, LayoutParams(-1, -1).apply { topMargin = dp(56); bottomMargin = dp(88) })
        val heading = LinearLayout(context).apply {
            orientation = LinearLayout.HORIZONTAL
            gravity = Gravity.CENTER_VERTICAL
            minimumHeight = dp(56)
            setPadding(dp(16), 0, dp(148), 0)
        }
        heading.addView(TextView(context).apply {
            text = "$rollName · $material · ${if (com.filmfoundry.interchange.StillFrameFormat.is120(format)) "120 / " + format.replace('x', '×') else if (format == "half") copy("135 / 半格", "135 / half") else "135"} · $capacity"
            textSize = 14f
            setTextColor(Color.rgb(235, 229, 216))
            maxLines = 2
        }, LinearLayout.LayoutParams(0, -2, 1f))
        addView(heading, LayoutParams(-1, -2, Gravity.TOP))
        val footer = LinearLayout(context).apply {
            orientation = LinearLayout.VERTICAL
            gravity = Gravity.CENTER
            minimumHeight = dp(88)
            setPadding(dp(16), 0, dp(16), 0)
        }
        hint.text = copy("退出取景器", "Leaving the viewfinder")
        hint.textSize = 14f
        hint.setTextColor(Color.rgb(218, 202, 173))
        hint.gravity = Gravity.CENTER
        footer.addView(hint, LinearLayout.LayoutParams(-1, -2))
        addView(footer, LayoutParams(-1, -2, Gravity.BOTTOM))
        skip.text = copy("跳过动画", "Skip animation")
        skip.isAllCaps = false
        skip.textSize = 14f
        skip.setTextColor(Color.WHITE)
        skip.backgroundTintList = android.content.res.ColorStateList.valueOf(Color.rgb(35, 35, 32))
        skip.setOnClickListener { skipRest() }
        addView(skip, LayoutParams(dp(136), -2, Gravity.TOP or Gravity.END).apply { rightMargin = dp(8) })
    }

    fun begin() {
        if (transition.reveal() != UnloadingTransition.Action.REVEAL) return
        animate(1f, 0f, 1100) { picture.approach = it; scene.invalidate() } then {
            if (transition.revealed() == UnloadingTransition.Action.OPEN) openDoor()
        }
    }

    private fun openDoor() {
        hint.text = copy("打开仓盖", "Opening the back")
        animate(0f, 1f, 640) { picture.door = it; scene.invalidate() } then {
            if (transition.opened() == UnloadingTransition.Action.REMOVE) takeOut()
        }
    }

    private fun takeOut() {
        hint.text = copy("取出胶卷", "Removing the roll")
        animate(1f, 0f, 700) { picture.thread = it; scene.invalidate() } then {
            animate(0f, 1f, 520) { picture.removed = it; scene.invalidate() } then {
                if (transition.removed() == UnloadingTransition.Action.LEAVE) onFinished()
            }
        }
    }

    private fun skipRest() {
        if (transition.skip() != UnloadingTransition.Action.LEAVE) return
        skip.isEnabled = false
        hint.text = copy("正在结束拍摄…", "Ending shooting…")
        animate(picture.approach, 1f, 280) { picture.approach = it; scene.invalidate() } then { onFinished() }
    }

    fun dispose() {
        transition.close()
        stopAnimation()
    }

    override fun onDetachedFromWindow() {
        dispose()
        super.onDetachedFromWindow()
    }

    private fun stopAnimation() {
        animationRevision++
        animator?.removeAllListeners()
        animator?.removeAllUpdateListeners()
        animator?.cancel()
        animator = null
    }

    private class Ending(private val attach: (() -> Unit) -> Unit) { infix fun then(end: () -> Unit) = attach(end) }

    private fun animate(from: Float, to: Float, ms: Long, update: (Float) -> Unit): Ending {
        var done: () -> Unit = {}
        val revision = ++animationRevision
        if (transition.stage == UnloadingTransition.Stage.CLOSED) return Ending { }
        if (!ValueAnimator.areAnimatorsEnabled()) {
            update(to)
            post { if (revision == animationRevision && transition.stage != UnloadingTransition.Stage.CLOSED) done() }
        } else {
            val next = ValueAnimator.ofFloat(from, to).apply {
                duration = ms
                interpolator = AccelerateDecelerateInterpolator()
                addUpdateListener { if (revision == animationRevision) update(it.animatedValue as Float) }
                addListener(object : AnimatorListenerAdapter() {
                    private var cancelled = false
                    override fun onAnimationCancel(animation: Animator) { cancelled = true }
                    override fun onAnimationEnd(animation: Animator) {
                        if (revision == animationRevision) {
                            animator = null
                            if (!cancelled && transition.stage != UnloadingTransition.Stage.CLOSED) done()
                        }
                    }
                })
            }
            animator = next
            next.start()
        }
        return Ending { done = it }
    }

    private inner class Scene(context: Context) : View(context) {
        init { contentDescription = copy("退出取景器、打开仓盖并取出胶卷", "Leave the viewfinder, open the back and remove the roll") }
        override fun onDraw(canvas: android.graphics.Canvas) {
            super.onDraw(canvas)
            picture.draw(canvas, width, height)
        }
    }
}
