package com.filmfoundry.workflow

/**
 * Chooses how many rows one formation pass keeps in memory.
 * The picture size, formulas and archives stay the same; only the pass length changes.
 */
object PhoneComputeSchedule {
    const val MODEL="phone_compute_schedule_v1"
    const val MAX_PIXELS=262144
    /** Color upper bound: source RGB, five 3-layer pools, and one RGB result. Spectral cubes are separate. */
    const val COLOR_BYTES_PER_PIXEL=(3+5*3+3)*4

    fun blockPixels(width: Int,height: Int,budgetBytes: Long): Int {
        require(width>0 && height>0) { "frame dimensions must be positive" }
        if(width>MAX_PIXELS) return width
        val allowance=if(budgetBytes<=0L) 0L else budgetBytes/4
        val frameBytes=width.toLong()*height*COLOR_BYTES_PER_PIXEL
        if(allowance>=frameBytes) return width*height
        val affordable=if(allowance<=0L) 1 else (allowance/COLOR_BYTES_PER_PIXEL/width).toInt().coerceAtLeast(1)
        val rows=affordable.coerceAtMost(MAX_PIXELS/width).coerceIn(1,height)
        return rows*width
    }

    fun workingBytes(blockPixels: Int): Long {
        require(blockPixels>0)
        return blockPixels.toLong()*COLOR_BYTES_PER_PIXEL
    }
}
