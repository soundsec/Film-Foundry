package com.filmfoundry.app.ui

import kotlin.math.hypot

/** Screen geometry only. The pivot is hidden behind the originating rail. */
data class FinderArcGeometry(val x: Float,val y: Float,val radius: Float,val boundary: Float,val bottom: Boolean) {
    val axis get()=if(bottom) -90f else 0f
    fun contains(px: Float,py: Float,extra: Float=0f): Boolean =
        (if(bottom) py<=boundary else px>=boundary) && hypot(px-x,py-y)<=radius+extra
    companion object {
        fun place(width: Float,height: Float,anchorX: Float,anchorY: Float,boundary: Float,bottom: Boolean,unit: Float): FinderArcGeometry {
            val pivot=12*unit
            val x=if(bottom) anchorX else boundary-pivot
            val y=if(bottom) boundary+pivot else anchorY
            val room=if(bottom) minOf(x-100*unit,width-104*unit-x) else minOf(y-8*unit,height-66*unit-y)
            val depth=if(bottom) y-8*unit else width-104*unit-x
            val radius=minOf(112*unit,room/.96f,depth-10*unit).coerceAtLeast(unit)
            return FinderArcGeometry(x,y,radius,boundary,bottom)
        }
    }
}
