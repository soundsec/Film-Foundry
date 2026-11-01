package com.filmfoundry.app.ui

/** One transient editor, with a revision so an old timeout cannot close a replacement. */
class FinderQuickSession(private val idleMs: Long=3200) {
    var key: String?=null; private set
    var revision=0L; private set
    private var deadline=0L
    private var dragging=false
    fun toggle(next: String,now: Long): Boolean {
        val open=key!=next;dismiss()
        if(open) { key=next;deadline=now+idleMs }
        return open
    }
    fun touch(now: Long,held: Boolean=false) { if(key!=null) { dragging=held;deadline=now+idleMs } }
    fun expire(expected: Long,now: Long): Boolean {
        if(expected!=revision || key==null || dragging || now<deadline) return false
        dismiss();return true
    }
    fun dismiss() { revision++;key=null;dragging=false }
}

/** Drag steps are measured from the gesture origin, preserving sub-step movement. */
class FinderDial(val lower: Int,val upper: Int,initial: Int) {
    init { require(lower<=upper) }
    var value=initial.coerceIn(lower,upper);private set
    private var origin=value
    fun start() { origin=value }
    fun drag(distanceUp: Float,pixelsPerStep: Float): Boolean {
        require(pixelsPerStep>0 && pixelsPerStep.isFinite())
        val next=(origin.toLong()+kotlin.math.round(distanceUp/pixelsPerStep).toLong()).coerceIn(lower.toLong(),upper.toLong()).toInt()
        return select(next)
    }
    fun select(next: Int): Boolean { val bounded=next.coerceIn(lower,upper);if(value==bounded) return false;value=bounded;return true }
}

/** One uninterrupted gesture in screen coordinates; perpendicular movement is ignored. */
class FinderDialGesture(private val dial: FinderDial,val horizontal: Boolean) {
    private var x=0f;private var y=0f;private var initial=dial.value
    var distance=0f;private set
    var fraction=0f;private set
    fun begin(screenX: Float,screenY: Float) { x=screenX;y=screenY;initial=dial.value;distance=0f;fraction=0f;dial.start() }
    fun move(screenX: Float,screenY: Float,pixelsPerStep: Float): Boolean {
        distance=if(horizontal) screenX-x else y-screenY
        val changed=dial.drag(distance,pixelsPerStep)
        val steps=(distance/pixelsPerStep).coerceIn((dial.lower.toLong()-initial).toFloat(),(dial.upper.toLong()-initial).toFloat())
        fraction=steps-(dial.value.toLong()-initial).toFloat()
        return changed
    }
    fun finish() { fraction=0f }
}

/** Confirm on the second UP: a press-and-slide on either touch must never reset. */
class FinderButtonTaps(private val timeoutMs: Long,private val tapSlop: Float,private val pairSlop: Float,val revealDelayMs: Long=180) {
    private data class Tap(val owner: Any,val up: Long,val x: Float,val y: Float)
    private data class Press(val owner: Any,val down: Long,val x: Float,val y: Float,val pair: Boolean,var moved: Boolean=false)
    private var previous: Tap?=null
    private var press: Press?=null
    fun down(owner: Any,time: Long,x: Float,y: Float) {
        val last=previous;previous=null
        val pair=last!=null && last.owner===owner && time-last.up in 0..timeoutMs && near(x,y,last.x,last.y,pairSlop)
        press=Press(owner,time,x,y,pair)
    }
    fun move(x: Float,y: Float) { press?.let { if(!near(x,y,it.x,it.y,tapSlop)) it.moved=true } }
    fun shouldReveal(time: Long,x: Float,y: Float,horizontal: Boolean): Boolean {
        val current=press ?: return false
        return time-current.down>=revealDelayMs || kotlin.math.abs(if(horizontal) x-current.x else y-current.y)>tapSlop
    }
    fun revealed() { press?.moved=true;previous=null }
    fun up(time: Long,x: Float,y: Float,cancelled: Boolean=false): Boolean {
        val current=press;press=null
        if(current==null || cancelled || current.moved || time-current.down !in 0..timeoutMs || !near(x,y,current.x,current.y,tapSlop)) { previous=null;return false }
        if(current.pair) return true
        previous=Tap(current.owner,time,x,y);return false
    }
    fun cancel() { previous=null;press=null }
    private fun near(x: Float,y: Float,ox: Float,oy: Float,limit: Float)=(x-ox)*(x-ox)+(y-oy)*(y-oy)<=limit*limit
}
