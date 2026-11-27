package com.filmfoundry.interchange

import kotlin.math.roundToInt

data class EdgeSlot(val kind: String, val start: Double, val end: Double)

class EdgeComposition(val font: String, val top: List<EdgeSlot>, val bottom: List<EdgeSlot>) {
    fun record(): Map<String, Any> = mapOf("font" to font,
        "top" to top.map { listOf(it.kind, it.start, it.end) },
        "bottom" to bottom.map { listOf(it.kind, it.start, it.end) })

    companion object {
        val kinds = listOf("text", "number", "direction", "barcode")
        val initial = EdgeComposition("grid", listOf(EdgeSlot("text", 4.0, 76.0)),
            listOf(EdgeSlot("barcode", 4.0, 68.0), EdgeSlot("number", 78.0, 89.0), EdgeSlot("direction", 92.0, 99.0)))

        fun parse(value: Any?): EdgeComposition? {
            if (value == null) return null
            require(value is Map<*, *> && value.keys == setOf("font", "top", "bottom"))
            val font = value["font"]
            require(font == "grid" || font == "sans" || font == "rounded")
            fun slots(side: String): List<EdgeSlot> {
                val items = value[side]
                require(items is List<*> && items.size <= kinds.size)
                val row = items.map { item ->
                    require(item is List<*> && item.size == 3 && item[0] in kinds)
                    val start = (item[1] as? Number)?.toDouble()
                    val end = (item[2] as? Number)?.toDouble()
                    require(start != null && end != null && start >= 0 && start < end && end <= 100)
                    EdgeSlot(item[0].toString(), start, end)
                }.sortedBy { it.start }
                require(row.distinctBy { it.kind }.size == row.size && row.zipWithNext().all { (a,b) -> a.end <= b.start })
                return row
            }
            return EdgeComposition(font.toString(), slots("top"), slots("bottom"))
        }
    }
}

internal class SlotRaster(val w: Int, val h: Int, private val thinFace: Boolean=false, val ink: (Int, Int) -> Boolean) {
    fun fit(width: Int, height: Int): SlotRaster {
        if (!thinFace || height >= h) return this
        val pixels = BooleanArray(width * height)
        for (y in 0 until height) for (x in 0 until width) {
            val x0 = x*w/width
            val y0 = y*h/height
            val x1 = maxOf(x0+1,(x+1)*w/width)
            val y1 = maxOf(y0+1,(y+1)*h/height)
            var lit = 0
            for (sy in y0 until y1) for (sx in x0 until x1) if (ink(sx,sy)) lit++
            pixels[y*width+x] = lit*4 >= (x1-x0)*(y1-y0)
        }
        return SlotRaster(width,height) { x,y -> pixels[y*width+x] }
    }
    companion object {
        private class Glyph(val width: Int, val height: Int, val bits: ByteArray) {
            fun ink(x: Int,y: Int): Boolean {
                val rowBytes=(width+7)/8
                return bits[y*rowBytes+x/8].toInt() and (128 ushr (x%8)) != 0
            }
        }
        private val sans by lazy { readGlyphs("/edge_sans.txt") }
        private val rounded by lazy { readGlyphs("/edge_rounded.txt") }
        private fun readGlyphs(resource: String): Map<Char, Glyph> {
            return checkNotNull(SlotRaster::class.java.getResourceAsStream(resource)).bufferedReader().useLines { lines ->
                lines.filterNot { it.startsWith("#") }.associate { line ->
                    val (code,w,h,hex)=line.split(' ')
                    code.toInt().toChar() to Glyph(w.toInt(),h.toInt(),ByteArray(hex.length/2) { i ->
                        hex.substring(i*2,i*2+2).toInt(16).toByte()
                    })
                }
            }
        }
        fun pattern(p: EdgeMarkPattern) = SlotRaster(p.width, p.height, ink=p::ink)
        fun text(label: String, font: String): SlotRaster {
            if (font == "sans" || font == "rounded") {
                val face=if (font == "rounded") rounded else sans
                val gap=if (font == "rounded") 2 else 0
                val glyphs=label.map { face[it] ?: face.getValue(' ') }
                val width=glyphs.sumOf { it.width }+gap*(glyphs.size-1)
                val height=glyphs.first().height
                val bits=BooleanArray(width*height)
                var offset=0
                for(g in glyphs) {
                    for(y in 0 until height) for(x in 0 until g.width) bits[y*width+offset+x]=g.ink(x,y)
                    offset+=g.width+gap
                }
                return SlotRaster(width,height,thinFace=font=="sans") { x,y -> bits[y*width+x] }
            }
            return SlotRaster(label.length * 6 - 1, 7) { x,y -> x % 6 < 5 && ExposureGlyphs.lit(label[x / 6], x % 6, y) }
        }
    }
}

/** Fit once per preview size. Formation samples this band for every strip pixel. */
internal class ComposedBand(slots: List<EdgeSlot>, val flip: Boolean, resolve: (String) -> SlotRaster?) {
    private val items = slots.mapNotNull { slot -> resolve(slot.kind)?.let { slot to it } }
    private class Stamp(val x: Int, val y: Int, val w: Int, val h: Int, val mask: SlotRaster)
    private var stamps = emptyList<Stamp>()
    private var lastWidth = 0
    private var lastHeight = 0

    fun ink(x: Int, y: Int, width: Int, height: Int): Boolean {
        if (width != lastWidth || height != lastHeight) {
            stamps = items.mapNotNull { (slot, mask) ->
                val left = (width * slot.start / 100).roundToInt()
                val span = (width * slot.end / 100).roundToInt() - left
                if (span <= 0) return@mapNotNull null
                val scale = minOf(span.toDouble()/mask.w, height.toDouble()/mask.h)
                val w = if (slot.kind == "barcode") span else maxOf(1,(scale*mask.w).roundToInt())
                val h = if (slot.kind == "barcode") height else maxOf(1,(scale*mask.h).roundToInt())
                Stamp(left, (height-h)/2, w, h, mask.fit(w,h))
            }
            lastWidth = width
            lastHeight = height
        }
        for (i in stamps.indices) {
            val s = stamps[i]
            if (x !in s.x until s.x+s.w || y !in s.y until s.y+s.h) continue
            val gx = (x-s.x)*s.mask.w/s.w
            val gy = (y-s.y)*s.mask.h/s.h
            return if (flip) s.mask.ink(s.mask.w-1-gx,s.mask.h-1-gy) else s.mask.ink(gx,gy)
        }
        return false
    }
}
