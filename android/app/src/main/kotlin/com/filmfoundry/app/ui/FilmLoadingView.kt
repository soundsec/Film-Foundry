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
import kotlin.math.min

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
        private val paint=Paint(Paint.ANTI_ALIAS_FLAG)
        private val rect=RectF()
        private val path=Path()
        private val metal=LinearGradient(0f,0f,0f,520f,intArrayOf(0xff5a5951.toInt(),0xff252622.toInt(),0xff101210.toInt()),null,Shader.TileMode.CLAMP)
        private val cassette=LinearGradient(90f,0f,260f,0f,intArrayOf(0xff10120f.toInt(),0xff66675b.toInt(),0xff131511.toInt()),null,Shader.TileMode.CLAMP)
        private val film=LinearGradient(0f,190f,0f,410f,intArrayOf(face.edge,face.body,face.core),null,Shader.TileMode.CLAMP)
        private val rails=floatArrayOf(167f,188f,423f,446f)
        private val perforationRows=floatArrayOf(208f,389f)
        var thread=0f
        var approach=0f
        var dragging=false
        private var startX=0f
        private var startThread=0f
        private var moved=false
        private var scale=1f
        private var originX=0f
        private var originY=0f
        init { isClickable=true;contentDescription=copy("已放入胶卷的片仓；向右拖动片头装填，也可用自动牵片按钮","Film chamber with cassette inserted. Drag the leader right or use Thread film.") }

        override fun onDraw(canvas: Canvas) {
            super.onDraw(canvas)
            scale=min(width/1000f,height/520f).coerceAtLeast(.01f)
            originX=(width-1000f*scale)/2;originY=(height-520f*scale)/2
            canvas.save();canvas.translate(originX,originY);canvas.scale(scale,scale)
            // The eye travels up to the viewfinder, then through its dark glass.
            val t=(approach/.84f).coerceIn(0f,1f)
            val zoom=1f+15f*t*t
            canvas.translate(0f,190f*t);canvas.scale(zoom,zoom,500f,70f)
            box(canvas,30f,15f,970f,510f,28f,0xff151714.toInt(),metal)
            box(canvas,58f,145f,942f,466f,17f,Color.BLACK)
            // Viewfinder, always above the frame gate as in the reference.
            box(canvas,412f,0f,588f,128f,18f,0xff57594f.toInt())
            box(canvas,431f,15f,569f,111f,12f,0xff080b09.toInt())
            box(canvas,444f,29f,556f,95f,8f,0xff1e2924.toInt())
            line(canvas,456f,87f,545f,87f,3f,0xff718779.toInt())
            box(canvas,284f,193f,775f,416f,5f,0xff393b32.toInt())
            box(canvas,327f,208f,718f,400f,1f,0xff171c18.toInt())
            for(y in rails) {
                line(canvas,278f,y,781f,y,7f,0xff9b9b85.toInt())
                line(canvas,278f,y-3,781f,y-3,2f,0xffd1ccb4.toInt())
            }
            // Cassette already seated: the empty chamber is deliberately never shown.
            box(canvas,87f,161f,260f,455f,26f,0xff30352b.toInt(),cassette)
            if(com.filmfoundry.interchange.StillFrameFormat.is120(format)) {
                box(canvas,76f,161f,270f,180f,5f,0xff0a0e0b.toInt())
                box(canvas,76f,436f,270f,455f,5f,0xff0a0e0b.toInt())
            }
            box(canvas,107f,182f,239f,433f,8f,face.label)
            FilmFace.drawMotif(canvas,paint,107f,250f,239f,420f,face.motif,face.ink)
            canvas.save();canvas.rotate(-90f,173f,308f)
            text(canvas,"FILM FOUNDRY",173f,300f,20f,face.ink)
            text(canvas,if(com.filmfoundry.interchange.StillFrameFormat.is120(format)) "120" else "135",173f,329f,28f,face.ink);canvas.restore()
            // Receiving spool and fixed registration target.
            box(canvas,817f,174f,910f,451f,18f,0xff575c4d.toInt())
            box(canvas,831f,226f,896f,417f,5f,0xffb7b69c.toInt())
            for(i in 0..10) line(canvas,835f+i*5f,229f,835f+i*5f,414f,2f,0xff747c69.toInt())
            val end=475f+385f*thread
            path.reset();path.moveTo(246f,199f);path.lineTo(end-46f,199f)
            path.cubicTo(end-48f,247f,end-70f,263f,end,284f)
            path.lineTo(end,411f);path.lineTo(246f,411f);path.close()
            paint.shader=film;paint.style=Paint.Style.FILL;canvas.drawPath(path,paint);paint.shader=null
            if(!com.filmfoundry.interchange.StillFrameFormat.is120(format)) {
                canvas.save();canvas.clipPath(path)
                var x=260f-((thread*385f)%31f)
                while(x < end) {
                    for(y in perforationRows) box(canvas,x,y,x+17f,y+14f,3f,0xff0a100b.toInt())
                    x+=31f
                };canvas.restore()
            }
            if(thread < .97f && approach == 0f) {
                line(canvas,end-72f,333f,end-12f,333f,3f,0xffeee3bd.toInt())
                line(canvas,end-25f,320f,end-12f,333f,3f,0xffeee3bd.toInt())
                line(canvas,end-25f,346f,end-12f,333f,3f,0xffeee3bd.toInt())
            }
            canvas.restore()
            if(approach > .78f) {
                paint.shader=null;paint.color=Color.BLACK;paint.alpha=(((approach-.78f)/.13f).coerceIn(0f,1f)*255).toInt()
                canvas.drawRect(0f,0f,width.toFloat(),height.toFloat(),paint);paint.alpha=255
            }
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
        private fun box(c: Canvas,l: Float,t: Float,r: Float,b: Float,radius: Float,color: Int,shader: Shader?=null) {
            paint.style=Paint.Style.FILL;paint.color=color;paint.shader=shader;rect.set(l,t,r,b);c.drawRoundRect(rect,radius,radius,paint);paint.shader=null
        }
        private fun line(c: Canvas,x: Float,y: Float,x2: Float,y2: Float,w: Float,color: Int) {
            paint.color=color;paint.shader=null;paint.strokeWidth=w;paint.style=Paint.Style.STROKE;c.drawLine(x,y,x2,y2,paint);paint.style=Paint.Style.FILL
        }
        private fun text(c: Canvas,s: String,x: Float,y: Float,size: Float,color: Int) {
            paint.color=color;paint.textAlign=Paint.Align.CENTER;paint.textSize=size;paint.typeface=Typeface.DEFAULT_BOLD;c.drawText(s,x,y,paint)
        }
    }
}
