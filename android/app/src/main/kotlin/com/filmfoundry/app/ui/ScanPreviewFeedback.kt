package com.filmfoundry.app.ui

import android.content.res.ColorStateList
import android.graphics.Color
import android.os.Handler
import android.os.Looper
import android.view.Gravity
import android.view.View
import android.widget.FrameLayout
import android.widget.ProgressBar

/** Delayed feedback for read-only work; fast updates never flash a spinner. UI thread only. */
class ScanPreviewFeedback(shell: MobileShell,surface: FrameLayout,private val delayMs: Long=180L) {
    private val handler=Handler(Looper.getMainLooper())
    private val indicator=ProgressBar(surface.context).apply {
        isIndeterminate=true;indeterminateTintList=ColorStateList.valueOf(Color.WHITE)
        visibility=View.GONE;importantForAccessibility=View.IMPORTANT_FOR_ACCESSIBILITY_NO
        isClickable=false;isFocusable=false
    }
    private var active=false
    private val show=Runnable { if(active) indicator.visibility=View.VISIBLE }
    init {
        surface.addView(indicator,FrameLayout.LayoutParams(shell.dp(24),shell.dp(24),Gravity.TOP or Gravity.START).apply {
            setMargins(shell.dp(12),shell.dp(12),shell.dp(12),shell.dp(12))
        })
    }
    fun begin() {
        active=true;handler.removeCallbacks(show)
        if(indicator.visibility!=View.VISIBLE) handler.postDelayed(show,delayMs)
    }
    fun finish() { active=false;handler.removeCallbacks(show);indicator.visibility=View.GONE }
}
