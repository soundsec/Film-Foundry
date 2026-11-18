package com.filmfoundry.app.ui

import android.animation.Animator
import android.animation.AnimatorListenerAdapter
import android.animation.ValueAnimator
import android.content.Context
import android.graphics.*
import android.view.Gravity
import android.view.MotionEvent
import android.view.View
import android.view.ViewConfiguration
import android.view.accessibility.AccessibilityEvent
import android.view.animation.AccelerateDecelerateInterpolator
import android.widget.*

/** Code-drawn interpretation of the author's loading storyboard. No image/camera processing. */
class FilmLoadingView(
    context: Context,
    private val copy: (String,String)->String,
    rollName: String,
    material: String,
    private val format: String,
    film: Map<String,Any?>,
    capacity: Int,
    private val onCommit: ()->Unit,
    private val onExit: ()->Unit,
    private val onEnter: ()->Unit,
) : FrameLayout(context) {
    private val face=FilmFace.of(film,rollName)
    private val scene=Chamber(context)
    private val hint=TextView(context)
    private val pull=Button(context)
    private val direct=Button(context)
    private val skip=Button(context)
    private var animator: ValueAnimator?=null
    private var animationRevision=0
    private val transition=LoadingTransition()
    private val active get()=transition.stage != LoadingTransition.Stage.CLOSED
    private val submitting get()=transition.stage != LoadingTransition.Stage.THREADING
    private lateinit var heading: LinearLayout
    private lateinit var footer: LinearLayout
    private fun dp(n: Int)=(n*resources.displayMetrics.density).toInt()

    init {
        setBackgroundColor(Color.BLACK)
        addView(scene,LayoutParams(-1,-1).apply { topMargin=dp(56);bottomMargin=dp(104) })
        heading=LinearLayout(context).apply { orientation=LinearLayout.HORIZONTAL;gravity=Gravity.CENTER_VERTICAL;minimumHeight=dp(56);setPadding(dp(12),0,dp(144),0) }
        val back=Button(context).apply {
            text="";minWidth=0;minimumWidth=0;setPadding(0,0,0,0);setBackgroundColor(Color.BLACK)
            foreground=NavigationIconDrawable(NavigationIcon.BACK,android.content.res.ColorStateList.valueOf(Color.WHITE))
            contentDescription=copy("取消装填并返回","Cancel loading and return");setOnClickListener { onExit() }
        }
        heading.addView(back,LinearLayout.LayoutParams(dp(52),dp(56)))
        heading.addView(TextView(context).apply {
            text="$rollName · $material · ${if(com.filmfoundry.interchange.StillFrameFormat.is120(format)) "120 / "+format.replace('x','×') else if(format == "half") copy("135 / 半格","135 / half") else "135"} · $capacity"
            textSize=14f;setTextColor(Color.rgb(235,229,216));maxLines=2
        },LinearLayout.LayoutParams(0,-2,1f))
        addView(heading,LayoutParams(-1,-2,Gravity.TOP))
        footer=LinearLayout(context).apply { orientation=LinearLayout.VERTICAL;gravity=Gravity.CENTER;minimumHeight=dp(104);setPadding(dp(16),0,dp(16),0) }
        hint.text=copy("向右拖动片头，接上卷轴","Drag the film leader right onto the take-up spool")
        hint.textSize=14f;hint.setTextColor(Color.rgb(218,202,173));hint.gravity=Gravity.CENTER
        hint.accessibilityLiveRegion=View.ACCESSIBILITY_LIVE_REGION_POLITE
        footer.addView(hint,LinearLayout.LayoutParams(-1,-2))
        val row=LinearLayout(context).apply { gravity=Gravity.CENTER }
        for(button in listOf(pull,direct)) {
            button.isAllCaps=false;button.textSize=14f;button.minHeight=dp(48)
            button.setTextColor(Color.rgb(239,233,220));button.backgroundTintList=android.content.res.ColorStateList.valueOf(Color.rgb(45,42,36))
            row.addView(button,LinearLayout.LayoutParams(-2,-2).apply { leftMargin=dp(8);rightMargin=dp(8) })
        }
        pull.text=copy("自动牵片","Thread film");pull.setOnClickListener { threadAutomatically() }
        direct.text=copy("直接装填","Load directly");direct.setOnClickListener { skipAnimation() }
        footer.addView(row);addView(footer,LayoutParams(-1,-2,Gravity.BOTTOM))
        skip.text=copy("跳过动画","Skip animation");skip.isAllCaps=false;skip.textSize=14f
        skip.setTextColor(Color.WHITE);skip.backgroundTintList=android.content.res.ColorStateList.valueOf(Color.rgb(35,35,32))
        skip.setOnClickListener { skipAnimation() }
        addView(skip,LayoutParams(dp(136),-2,Gravity.TOP or Gravity.END).apply { rightMargin=dp(8) })
    }

    override fun onLayout(changed: Boolean,left: Int,top: Int,right: Int,bottom: Int) {
        super.onLayout(changed,left,top,right,bottom)
        scene.layout(0,heading.bottom,width,footer.top.coerceAtLeast(heading.bottom))
    }

    private fun threadAutomatically() {
        if(!active || submitting || animator != null) return
        scene.dragging=false
        animate(scene.thread,1f,380) { scene.thread=it;scene.invalidate() } then { submit(true) }
    }

    private fun submit(withAnimation: Boolean) {
        perform(transition.confirm(withAnimation))
    }

    private fun skipAnimation() {
        if(!active) return
        stopAnimation();scene.dragging=false;scene.thread=1f;scene.invalidate()
        perform(transition.skip())
        if(transition.stage == LoadingTransition.Stage.SAVING) {
            hint.text=copy("已跳过动画，正在保存装卷…","Animation skipped · saving the roll…")
            skip.text=copy("正在保存…","Saving…");skip.isEnabled=false
        }
    }

    private fun perform(action: LoadingTransition.Action) {
        if(!active) return
        when(action) {
            LoadingTransition.Action.COMMIT -> {
                pull.isEnabled=false;direct.isEnabled=false
                hint.text=copy("正在合盖装填…","Closing and loading…")
                onCommit()
            }
            LoadingTransition.Action.ENTER -> onEnter()
            LoadingTransition.Action.ANIMATE_ENTRY -> {
                heading.visibility=View.INVISIBLE;footer.visibility=View.INVISIBLE
                animate(0f,1f,1150) { scene.approach=it;scene.invalidate() } then { perform(transition.entryFinished()) }
            }
            LoadingTransition.Action.NONE -> Unit
        }
    }

    fun failed(message: String) {
        if(!active || !transition.failed()) return
        pull.isEnabled=true;direct.isEnabled=true;skip.isEnabled=true
        pull.text=copy("重试装填","Retry loading");hint.text=message;skip.text=copy("跳过动画","Skip animation")
    }

    fun loaded() {
        perform(transition.committed(ValueAnimator.areAnimatorsEnabled()))
    }

    private fun stopAnimation() { animationRevision++;animator?.removeAllListeners();animator?.removeAllUpdateListeners();animator?.cancel();animator=null }
    fun dispose() { transition.close();stopAnimation();scene.dragging=false }
    override fun onDetachedFromWindow() { dispose();super.onDetachedFromWindow() }

    private class Ending(private val attach: (()->Unit)->Unit) { infix fun then(end: ()->Unit)=attach(end) }
    private fun animate(from: Float,to: Float,ms: Long,update: (Float)->Unit): Ending {
        var done: ()->Unit={}
        val revision=++animationRevision
        if(!ValueAnimator.areAnimatorsEnabled()) {
            update(to);post { if(active && revision == animationRevision) done() }
        } else {
            val next=ValueAnimator.ofFloat(from,to).apply {
                duration=ms;interpolator=AccelerateDecelerateInterpolator()
                addUpdateListener { if(active && revision == animationRevision) update(it.animatedValue as Float) }
                addListener(object: AnimatorListenerAdapter() {
                    private var cancelled=false
                    override fun onAnimationCancel(animation: Animator) { cancelled=true }
                    override fun onAnimationEnd(animation: Animator) { if(revision == animationRevision) { animator=null;if(active && !cancelled) done() } }
                })
            }
            animator=next;next.start()
        }
        return Ending { done=it }
    }

    private inner class Chamber(context: Context): View(context) {
        private val picture=FilmChamberPicture(format,face)
        var thread: Float
            get()=picture.thread
            set(value) { picture.thread=value }
        var approach: Float
            get()=picture.approach
            set(value) { picture.approach=value }
        var dragging=false
        private var startX=0f
        private var startThread=0f
        private var moved=false
        private val scale get()=picture.scale
        private val originX get()=picture.originX
        private val originY get()=picture.originY
        init { isClickable=true;contentDescription=copy("已放入胶卷的片仓；向右拖动片头装填，也可用自动牵片按钮","Film chamber with cassette inserted. Drag the leader right or use Thread film.") }

        override fun onDraw(canvas: Canvas) {
            super.onDraw(canvas)
            picture.draw(canvas,width,height)
        }

        override fun onTouchEvent(event: MotionEvent): Boolean {
            if(!active || submitting || animator != null) return false
            val x=(event.x-originX)/scale;val y=(event.y-originY)/scale
            when(event.actionMasked) {
                MotionEvent.ACTION_DOWN -> {
                    if(y !in 185f..440f || x !in 235f..(505f+385f*thread)) return false
                    dragging=true;startX=event.x;startThread=thread;moved=false;parent.requestDisallowInterceptTouchEvent(true)
                }
                MotionEvent.ACTION_MOVE -> if(dragging) {
                    if(kotlin.math.abs(event.x-startX) > ViewConfiguration.get(context).scaledTouchSlop) moved=true
                    thread=(startThread+(event.x-startX)/(385f*scale)).coerceIn(0f,1f);invalidate()
                }
                MotionEvent.ACTION_UP -> if(dragging) {
                    dragging=false;parent.requestDisallowInterceptTouchEvent(false)
                    if(!moved) performClick()
                    if(thread >= .9f) threadAutomatically()
                    else animate(thread,0f,220) { thread=it;invalidate() }
                }
                MotionEvent.ACTION_CANCEL -> { dragging=false;parent.requestDisallowInterceptTouchEvent(false);animate(thread,0f,180) { thread=it;invalidate() } }
            }
            return true
        }
        override fun performClick(): Boolean { super.performClick();sendAccessibilityEvent(AccessibilityEvent.TYPE_VIEW_CLICKED);return true }
    }
}
