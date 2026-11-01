package com.filmfoundry.platform

/** Bounded packed-pixel scratch; the returned encoded RGB still owns a full frame. */
internal object ArgbRowConverter {
    fun convert(width: Int, height: Int,
        readRows: (pixels: IntArray, start: Int, rows: Int) -> Unit): FloatArray {
        require(width > 0 && height > 0)
        val rgb = FloatArray(Math.multiplyExact(Math.multiplyExact(width, height), 3))
        val blockRows = minOf(height, maxOf(1, 16384 / width))
        val pixels = IntArray(Math.multiplyExact(width, blockRows))
        var start = 0
        while (start < height) {
            val rows = minOf(blockRows, height - start)
            readRows(pixels, start, rows)
            var dst = start * width * 3
            for (i in 0 until rows * width) {
                val color = pixels[i]
                rgb[dst++] = ((color ushr 16) and 255) / 255f
                rgb[dst++] = ((color ushr 8) and 255) / 255f
                rgb[dst++] = (color and 255) / 255f
            }
            start += rows
        }
        return rgb
    }
}
