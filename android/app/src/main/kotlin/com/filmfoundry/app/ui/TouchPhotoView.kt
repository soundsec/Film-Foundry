package com.filmfoundry.app.ui

import android.annotation.SuppressLint
import android.content.Context
import android.graphics.Matrix
import android.graphics.Bitmap
import android.graphics.Canvas
import android.graphics.Paint
import android.graphics.RectF
import android.graphics.drawable.Drawable
import android.view.GestureDetector
import android.view.MotionEvent
import android.view.ScaleGestureDetector
import android.widget.ImageView

/** Bounded display transform only. Never alters the saved image or optical master. */
@SuppressLint("ClickableViewAccessibility")
class TouchPhotoView(context: Context): ImageView(context) {
    var orientationDegrees=0
        set(value) { val angle=com.filmfoundry.platform.OutputOrientation.normalized(value);if(field==angle) return;field=angle;cancelTurn();zoom=1f;dx=0f;dy=0f;transform();invalidate() }
    private val identity=ViewerPageIdentity()
    val displayedPage get()=identity.displayed
    val pageLoading get()=identity.pending!=null
    private var upgrade: Pair<ViewerPageIdentity.Token,Bitmap>?=null
    fun requestPage(key: String)=identity.request(key).also { upgrade=null;detailRequested=false }
    var onDetailRequested: ()->Boolean={ false }
    private var detailRequested=false
    fun retryDetail() { detailRequested=false }
    private fun detail() { if(!liveDisplay && zoom>1.3f && !detailRequested) detailRequested=onDetailRequested() }
    fun retainPage(token: ViewerPageIdentity.Token): Boolean =identity.preview(token)
    var liveDisplay=false
        set(value) { field=value;if(!value) { liveBitmap=null;livePose=LiveSheetPose() } }
    private var liveBitmap: Bitmap?=null
    private var filmAspect=1.5f
    @Volatile var livePose=LiveSheetPose();private set
    fun beginLive(aspect: Float) { cancelTurn();filmAspect=aspect;livePose=LiveSheetPose();liveDisplay=true }
    fun presentLivePixels(token: ViewerPageIdentity.Token,w: Int,h: Int,pixels: IntArray): Boolean {
        if(!liveDisplay || (identity.pending!=token && (identity.pending!=null || identity.displayed!=token))) return false
        val bitmap=liveBitmap?.takeIf { it.width==w && it.height==h && !it.isRecycled }
            ?: Bitmap.createBitmap(w,h,Bitmap.Config.ARGB_8888).also { liveBitmap=it }
        // One UI-thread copy, instead of one Bitmap and one setPixels call per output row.
        bitmap.setPixels(pixels,0,w,0,0,w,h)
        if(identity.pending==token) { identity.commit(token);neighborsClear();setImageBitmap(bitmap) }
        else if((drawable as? android.graphics.drawable.BitmapDrawable)?.bitmap===bitmap) invalidate()
        else setImageBitmap(bitmap)
        return true
    }
    fun presentLivePage(token: ViewerPageIdentity.Token,bitmap: Bitmap): Boolean {
        if(identity.pending==token) return presentPage(token,bitmap)
        if(identity.pending!=null || identity.displayed!=token) return false
        setImageBitmap(bitmap);return true
    }
    fun previewPage(token: ViewerPageIdentity.Token,bitmap: Bitmap?): Boolean {
        if(token!=identity.pending) return false
        if(bitmap==null || bitmap.isRecycled) {
            // A cold page keeps the outgoing pixels, but never labels them as the new page.
            cancelTurn();neighborsClear();return true
        }
        if(!identity.preview(token)) return false
        neighborsClear();setImageBitmap(bitmap);return true
    }
    fun presentPage(token: ViewerPageIdentity.Token,bitmap: Bitmap): Boolean {
        if(token!=identity.pending) return false
        if(paging || isTurning) { upgrade=token to bitmap;return true }
        val samePage=identity.displayed==token
        if(!identity.commit(token)) return false
        if(!samePage) neighborsClear()
        val savedZoom=zoom;val savedX=dx;val savedY=dy
        setImageBitmap(bitmap)
        if(samePage) { zoom=savedZoom;dx=savedX;dy=savedY;transform() }
        return true
    }
    fun clearPage() { upgrade=null;identity.clear();neighborsClear();setImageDrawable(null) }
    private fun upgradeWhenIdle() { val ready=upgrade;upgrade=null;ready?.let { presentPage(it.first,it.second) } }
    private fun neighborsClear() { previous=null;next=null;hasPrevious=false;hasNext=false }
    private var zoom=1f;private var dx=0f;private var dy=0f
    var onPrevious: ()->Unit={};var onNext: ()->Unit={}
    private var pinched=false
    private var offset=0f
    private var startX=0f;private var startY=0f
    private var lastX=0f;private var lastY=0f
    private var edgeDistance=0f;private var pageDistance=0f
    private var paging=false
    private var velocity: android.view.VelocityTracker?=null
    private var animation: android.animation.ValueAnimator?=null
    private var previous: Bitmap?=null;private var next: Bitmap?=null
    private var previousAngle=0;private var nextAngle=0
    private var hasPrevious=false;private var hasNext=false
    fun neighborPreview(delta: Int): Bitmap?=(if(delta<0) previous else next)?.takeUnless { it.isRecycled }
    var isTurning=false;private set
    private val paint=Paint(Paint.ANTI_ALIAS_FLAG or Paint.FILTER_BITMAP_FLAG)
    fun neighbors(owner: ViewerPageIdentity.Token,before: Bitmap?,after: Bitmap?,canPrevious: Boolean,canNext: Boolean,beforeAngle: Int=0,afterAngle: Int=0) {
        if(!identity.neighborsMatch(owner)) return
        previous=before;next=after;previousAngle=beforeAngle;nextAngle=afterAngle;hasPrevious=canPrevious;hasNext=canNext;invalidate()
    }
    fun navigate(delta: Int) {
        if(isTurning || !(if(delta<0) hasPrevious else hasNext)) return
        if(zoom!=1f) { zoom=1f;dx=0f;dy=0f;transform() }
        settle(if(delta<0) 1 else -1)
    }
    fun cancelTurn() { animation?.cancel();animation=null;isTurning=false;offset=0f;paging=false;edgeDistance=0f;pageDistance=0f;invalidate() }
    fun dispose() { upgrade=null;cancelTurn();velocity?.recycle();velocity=null;previous=null;next=null }
    private fun settle(direction: Int) {
        animation?.cancel();isTurning=direction!=0
        animation=android.animation.ValueAnimator.ofFloat(offset,if(direction==0) 0f else direction*width.toFloat()).apply {
            duration=190;interpolator=android.view.animation.DecelerateInterpolator()
            addUpdateListener { offset=it.animatedValue as Float;invalidate() }
            addListener(object: android.animation.AnimatorListenerAdapter() {
                private var cancelled=false
                override fun onAnimationCancel(a: android.animation.Animator) { cancelled=true }
                override fun onAnimationEnd(a: android.animation.Animator) {
                    if(!cancelled) {
                        if(direction!=0) { if(direction<0) onNext() else onPrevious() }
                        else upgradeWhenIdle()
                    }
                }
            });start()
        }
    }
    override fun onDraw(canvas: Canvas) {
        canvas.save();canvas.translate(offset,0f);super.onDraw(canvas);canvas.restore()
        fun adjacent(bitmap: Bitmap?,x: Float,angle: Int) {
            if(bitmap==null || bitmap.isRecycled) return
            val shape=com.filmfoundry.platform.OutputOrientation.size(bitmap.width,bitmap.height,angle)
            val scale=minOf(width.toFloat()/shape.first,height.toFloat()/shape.second)
            canvas.save();canvas.translate(x+width/2f,height/2f);canvas.rotate(angle.toFloat());canvas.scale(scale,scale)
            canvas.drawBitmap(bitmap,-bitmap.width/2f,-bitmap.height/2f,paint);canvas.restore()
        }
        if(offset>0f) adjacent(previous,offset-width,previousAngle) else if(offset<0f) adjacent(next,offset+width,nextAngle)
    }
    private val scale=ScaleGestureDetector(context,object: ScaleGestureDetector.SimpleOnScaleGestureListener() {
        override fun onScaleBegin(detector: ScaleGestureDetector): Boolean { pinched=true;cancelTurn();return true }
        override fun onScale(detector: ScaleGestureDetector): Boolean {
            if(liveDisplay) {
                liveGeometry()?.let { g -> livePose=livePose.scale(detector.scaleFactor,
                    (detector.focusX-g.centerX())/g.width(),(detector.focusY-g.centerY())/g.height(),liveFilmWidth(g),liveFilmHeight(g)) }
                return true
            }
            val old=zoom;zoom=(zoom*detector.scaleFactor).coerceIn(1f,5f)
            dx=(dx+width/2f-detector.focusX)*(zoom/old)-(width/2f-detector.focusX)
            dy=(dy+height/2f-detector.focusY)*(zoom/old)-(height/2f-detector.focusY)
            transform();detail();return true
        }
    })
    private val taps=GestureDetector(context,object: GestureDetector.SimpleOnGestureListener() {
        override fun onDown(e: MotionEvent)=true
        override fun onDoubleTap(e: MotionEvent): Boolean {
            if(liveDisplay) {
                liveGeometry()?.let { g -> livePose=if(livePose.zoom>1f) LiveSheetPose() else livePose.scale(2.5f,
                    (e.x-g.centerX())/g.width(),(e.y-g.centerY())/g.height(),liveFilmWidth(g),liveFilmHeight(g)) }
                return true
            }
            zoom=if(zoom>1f) 1f else 2.5f;dx=(width/2f-e.x)*(zoom-1f);dy=(height/2f-e.y)*(zoom-1f);transform();detail();return true
        }
        override fun onScroll(e1: MotionEvent?,e2: MotionEvent,distanceX: Float,distanceY: Float): Boolean {
            if(liveDisplay) {
                if(!scale.isInProgress && e2.pointerCount==1) liveGeometry()?.let { g ->
                    livePose=livePose.move(-distanceX/g.width(),-distanceY/g.height(),liveFilmWidth(g),liveFilmHeight(g))
                }
                return true
            }
            return true // Non-live panning and edge handoff share one touch path below.
        }
    })
    init {
        scaleType=ScaleType.MATRIX;setBackgroundColor(android.graphics.Color.rgb(16,16,16))
        setOnTouchListener { _,event ->
            if(liveDisplay) {
                if(event.actionMasked==MotionEvent.ACTION_DOWN) { pinched=false;parent?.requestDisallowInterceptTouchEvent(true) }
                if(event.pointerCount>1) pinched=true
                scale.onTouchEvent(event);taps.onTouchEvent(event)
                if(event.actionMasked==MotionEvent.ACTION_UP || event.actionMasked==MotionEvent.ACTION_CANCEL) parent?.requestDisallowInterceptTouchEvent(false)
                return@setOnTouchListener true
            }
            if(isTurning) return@setOnTouchListener true
            if(event.actionMasked == MotionEvent.ACTION_DOWN) {
                cancelTurn();pinched=false;startX=event.x;startY=event.y;lastX=event.x;lastY=event.y
                if(zoom>1f) parent?.requestDisallowInterceptTouchEvent(true)
                velocity?.recycle();velocity=android.view.VelocityTracker.obtain()
            }
            velocity?.addMovement(event)
            if(event.pointerCount>1) { pinched=true;parent?.requestDisallowInterceptTouchEvent(true) }
            scale.onTouchEvent(event);taps.onTouchEvent(event)
            if(!pinched && !scale.isInProgress && event.actionMasked==MotionEvent.ACTION_MOVE) {
                val moveX=event.x-lastX;val moveY=event.y-lastY
                val horizontal=kotlin.math.abs(event.x-startX)>kotlin.math.abs(event.y-startY)*1.2f
                val slop=android.view.ViewConfiguration.get(context).scaledTouchSlop
                if(paging) pageDistance+=moveX
                else if(zoom>1f) {
                    val bounds=panBounds()
                    val pan=PhotoDisplayPolicy.pan(dx,dy,moveX,moveY,bounds.first,bounds.second)
                    dx=pan.x;dy=pan.y;transform()
                    edgeDistance=if(pan.overflowX==0f) 0f else edgeDistance+pan.overflowX
                    if(horizontal && kotlin.math.abs(edgeDistance)>slop) { paging=true;pageDistance=edgeDistance }
                } else {
                    pageDistance=event.x-startX
                    if(horizontal && kotlin.math.abs(pageDistance)>slop) paging=true
                }
                if(paging) {
                    parent?.requestDisallowInterceptTouchEvent(true)
                    val exists=if(pageDistance>0) hasPrevious else hasNext
                    offset=pageDistance.coerceIn(-width.toFloat(),width.toFloat())*(if(exists) 1f else .22f);invalidate()
                }
            }
            lastX=event.x;lastY=event.y
            if(event.actionMasked==MotionEvent.ACTION_UP || event.actionMasked==MotionEvent.ACTION_CANCEL) {
                parent?.requestDisallowInterceptTouchEvent(false)
                velocity?.computeCurrentVelocity(1000)
                val speed=velocity?.xVelocity ?: 0f
                val direction=if(offset>0) 1 else -1
                val exists=if(direction>0) hasPrevious else hasNext
                val commit=event.actionMasked==MotionEvent.ACTION_UP && paging && !pinched && exists &&
                    PhotoDisplayPolicy.commitPage(offset,speed,width,zoom>1f,android.view.ViewConfiguration.get(context).scaledTouchSlop.toFloat())
                if(paging) settle(if(commit) direction else 0)
                val wasPaging=paging;paging=false;velocity?.recycle();velocity=null
                if(!wasPaging) upgradeWhenIdle()
            }
            true
        }
    }
    override fun setImageDrawable(drawable: Drawable?) {
        (drawable as? android.graphics.drawable.BitmapDrawable)?.let { wrapper ->
            wrapper.isFilterBitmap=true
            // ImageView may supply a BitmapDrawable with no bitmap when clearing a preview.
            wrapper.bitmap?.takeUnless { it.isRecycled }?.setHasMipMap(!liveDisplay)
        }
        cancelTurn();super.setImageDrawable(drawable);zoom=1f;dx=0f;dy=0f;transform()
    }
    override fun onSizeChanged(w: Int,h: Int,oldw: Int,oldh: Int) { super.onSizeChanged(w,h,oldw,oldh);transform() }
    private fun liveGeometry(): RectF? {
        val d=drawable ?: return null
        if(width<=0 || height<=0 || d.intrinsicWidth<=0 || d.intrinsicHeight<=0) return null
        val fit=minOf(width.toFloat()/d.intrinsicWidth,height.toFloat()/d.intrinsicHeight)
        val w=d.intrinsicWidth*fit;val h=d.intrinsicHeight*fit
        return RectF((width-w)/2,(height-h)/2,(width+w)/2,(height+h)/2)
    }
    private fun liveFilmWidth(g: RectF)=minOf(.86f,.86f*g.height()*filmAspect/g.width())
    private fun liveFilmHeight(g: RectF)=minOf(.86f,.86f*g.width()/filmAspect/g.height())
    private fun panBounds(): Pair<Float,Float> {
        val d=drawable ?: return 0f to 0f
        if(width<=0 || height<=0 || d.intrinsicWidth<=0 || d.intrinsicHeight<=0) return 0f to 0f
        val shape=com.filmfoundry.platform.OutputOrientation.size(d.intrinsicWidth,d.intrinsicHeight,orientationDegrees)
        val s=minOf(width.toFloat()/shape.first,height.toFloat()/shape.second)*zoom
        return maxOf(0f,(shape.first*s-width)/2f) to maxOf(0f,(shape.second*s-height)/2f)
    }
    private fun transform() {
        val d=drawable ?: return
        if(width<=0 || height<=0 || d.intrinsicWidth<=0 || d.intrinsicHeight<=0) return
        val shape=com.filmfoundry.platform.OutputOrientation.size(d.intrinsicWidth,d.intrinsicHeight,orientationDegrees)
        val fit=minOf(width.toFloat()/shape.first,height.toFloat()/shape.second)
        val s=fit*zoom
        val boundX=maxOf(0f,(shape.first*s-width)/2f);val boundY=maxOf(0f,(shape.second*s-height)/2f)
        dx=dx.coerceIn(-boundX,boundX);dy=dy.coerceIn(-boundY,boundY)
        imageMatrix=Matrix().apply { postTranslate(-d.intrinsicWidth/2f,-d.intrinsicHeight/2f);postRotate(orientationDegrees.toFloat());postScale(s,s);postTranslate(width/2f+dx,height/2f+dy) }
    }
}
