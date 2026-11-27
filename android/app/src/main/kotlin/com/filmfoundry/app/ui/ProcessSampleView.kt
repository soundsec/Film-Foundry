package com.filmfoundry.app.ui

import android.view.View
import android.widget.ImageView
import android.widget.LinearLayout
import android.widget.TextView
import com.filmfoundry.workflow.CameraEffectsPreview
import com.filmfoundry.workflow.CameraEffectsPreviewCache
import java.util.concurrent.Executor

internal typealias SampleRecipe = () -> Map<String,Any?>
internal typealias SamplePanel = (SampleRecipe) -> ProcessSampleView

/** A synthetic reference, independent of the photo bitmap and its save path. */
internal class ProcessSampleView(
    private val shell: MobileShell, executor: Executor, private val cache: CameraEffectsPreviewCache,
    private val request: () -> CameraEffectsPreview.Request
) : LinearLayout(shell.root.context) {
    private val title = TextView(context)
    private val photo = ImageView(context)
    private val work = LatestViewWork(executor) { shell.root.post(it) }
    private var previous: CameraEffectsPreview.Request? = null
    private var revision = 0L
    private val launch = Runnable { render() }

    init {
        orientation = VERTICAL
        title.textSize = 13f
        title.setTextColor(shell.ink)
        addView(title)
        photo.tag = "process_sample_image"
        photo.scaleType = ImageView.ScaleType.FIT_CENTER
        photo.setBackgroundColor(0xff161616.toInt())
        val height = resources.configuration.screenHeightDp / 6
        addView(photo, LayoutParams(-1,shell.dp(height.coerceIn(64,132))))
    }

    fun refresh() {
        removeCallbacks(launch)
        if (isAttachedToWindow && isShown) postDelayed(launch,180)
    }

    private fun render() {
        if (!isShown) return
        val next = runCatching(request).getOrElse {
            work.cancel()
            previous = null
            photo.setImageDrawable(null)
            title.text = shell.copy("工艺样张 · ","Process sample · ") + it.localizedMessage
            return
        }
        val last = previous
        if (last != null && next.film == last.film && next.capture == last.capture && next.selected == last.selected) return
        previous = next
        val token = ++revision
        title.text = shell.copy("工艺样张 · 更新中","Process sample · Updating")
        work.submit({ stale ->
            val volume = cache.render(next,emptyMap(),check = { if (stale()) throw InterruptedException() })
            val bitmap = sampleBitmap(volume)
            shell.root.post {
                if (!stale() && isShown && revision == token) {
                    photo.setImageBitmap(bitmap)
                    title.text = shell.copy("工艺样张 · 当前扫描","Process sample · Current scan")
                } else bitmap.recycle()
            }
        }, { error ->
            previous = null
            photo.setImageDrawable(null)
            title.text = shell.copy("样张未更新 · ","Sample unavailable · ") + error.localizedMessage
        })
    }

    override fun onVisibilityChanged(changedView: View, visibility: Int) {
        super.onVisibilityChanged(changedView,visibility)
        if (isAttachedToWindow) {
            if (isShown) refresh() else stop()
        }
    }
    override fun onAttachedToWindow() { super.onAttachedToWindow(); refresh() }
    override fun onDetachedFromWindow() { stop(); super.onDetachedFromWindow() }
    private fun stop() {
        removeCallbacks(launch)
        work.cancel()
        previous = null
        photo.setImageDrawable(null)
    }
}
