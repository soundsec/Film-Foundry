package com.filmfoundry.kernel

import kotlin.math.abs

/** The same six-sided emitters used by the desktop date back, in a 51 x 75 cell. */
internal object DateSegmentPlate {
    private data class Segment(val horizontal: Boolean, val a: Double, val b: Double, val c: Double, val half: Double) {
        fun contains(x: Double, y: Double): Boolean {
            val u = if (horizontal) x else y
            val d = abs((if (horizontal) y else x) - c)
            return d <= half && u >= a+d && u <= b-d
        }
    }

    private val widePlate by lazy { readPlate("/date_segments.txt") }
    private val narrowPlate by lazy { readPlate("/date_segments_narrow.txt") }
    private fun readPlate(resource: String): Map<Char, Segment> {
        return checkNotNull(javaClass.getResourceAsStream(resource)).bufferedReader().useLines { lines ->
            lines.filterNot { it.startsWith("#") }.associate { line ->
                val v = line.split(' ')
                v[0].single() to Segment(v[1]=="h", v[2].toDouble(), v[3].toDouble(), v[4].toDouble(), v[5].toDouble())
            }
        }
    }
    private val enabled = listOf("abcdef","bc","abdeg","abcdg","bcfg","acdfg","acdefg","abc","abcdefg","abcdfg")

    fun stamp(ch: Char, width: Int, height: Int, narrow: Boolean=false): ByteArray {
        val paths = if (narrow) narrowPlate else widePlate
        val segments = (if (ch=='-') "g" else enabled[ch-'0']).map(paths::getValue)
        val pixels = ByteArray(width*height)
        for (y in 0 until height) for (x in 0 until width) {
            var count=0
            for (sy in 0..3) for (sx in 0..3) {
                val gx=(x+(sx+.5)/4)*51/width
                val gy=(y+(sy+.5)/4)*75/height
                if (segments.any { it.contains(gx,gy) }) count++
            }
            pixels[y*width+x]=count.toByte()
        }
        return pixels
    }
}
