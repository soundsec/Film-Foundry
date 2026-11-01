package com.filmfoundry.kernel

/** The actual readout geometry, shared by the observer and allocation admission. */
data class PhoneObservationGrid(val width: Int,val height: Int) {
    companion object {
        fun resolve(width: Int,height: Int,longEdge: Any?): PhoneObservationGrid {
            require(width > 0 && height > 0)
            if(longEdge == null) return PhoneObservationGrid(width,height)
            val number=longEdge as? Number ?: throw KernelException("scan_grid_long_edge must be an integer.")
            val edge=number.toInt()
            if(number.toDouble() != edge.toDouble() || edge !in 2..8192) throw KernelException("scan_grid_long_edge must be an integer in 2..8192.")
            val sourceEdge=maxOf(width,height)
            if(edge == sourceEdge) return PhoneObservationGrid(width,height)
            val scale=edge.toFloat()/sourceEdge
            return PhoneObservationGrid(maxOf(2,kotlin.math.round(width*scale).toInt()),maxOf(2,kotlin.math.round(height*scale).toInt()))
        }
    }
}
