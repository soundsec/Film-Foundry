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
            val low = if (bottom) 100*unit else 8*unit
            val high = if (bottom) width-104*unit else height-66*unit
            val depth = if (bottom) boundary+pivot-8*unit else width-104*unit-boundary+pivot
            val radius = minOf(112*unit, (high-low)/1.92f, depth-10*unit).coerceAtLeast(unit)
            val inset = radius*.96f
            val anchor = if (bottom) anchorX else anchorY
            val center = if (low+inset <= high-inset) anchor.coerceIn(low+inset, high-inset) else (low+high)/2
            val x = if (bottom) center else boundary-pivot
            val y = if (bottom) boundary+pivot else center
            return FinderArcGeometry(x,y,radius,boundary,bottom)
        }
    }
}
