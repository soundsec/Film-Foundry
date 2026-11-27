package com.filmfoundry.interchange

import kotlin.math.roundToInt

/** One view onto continuous film. Each entering piece retains its original frame index. */
class FilmStripWindow(val frame: Int, val width: Int, val offset: Double) {
    init { require(width > 0 && offset.isFinite() && offset in -0.5..0.5) }
    val shift = (width * offset).roundToInt()
    val adjacent = (frame + if (shift > 0) -1 else 1).coerceIn(0, 9999)
    fun fromAdjacent(x: Int) = x - shift !in 0 until width
    fun localX(x: Int): Int = Math.floorMod(x - shift, width)
}

/** The gate keeps the exposure number; the factory plate repeats every eight perforations. */
class FilmFactoryWindow(gate: FilmStripWindow, val halfFrame: Boolean) {
    val part=if(halfFrame) Math.floorMod(gate.frame-1,2) else 0
    val origin=part*gate.width
    val plate=if(halfFrame) FilmStripWindow((gate.frame+1)/2,2*gate.width,gate.offset/2) else gate
}

/** Same Code 128 C symbol widths and batch/frame namespace as contracts/edge_barcode.py. */
object FilmEdgeCode {
    private val widths = """
212222 222122 222221 121223 121322 131222 122213 122312 132212 221213
221312 231212 112232 122132 122231 113222 123122 123221 223211 221132
221231 213212 223112 312131 311222 321122 321221 312212 322112 322211
212123 212321 232121 111323 131123 131321 112313 132113 132311 211313
231113 231311 112133 112331 132131 113123 113321 133121 313121 211331
231131 213113 213311 213131 311123 311321 331121 312113 312311 332111
314111 221411 431111 111224 111422 121124 121421 141122 141221 112214
112412 122114 122411 142112 142211 241211 221114 413111 241112 134111
111242 121142 121241 114212 124112 124211 411212 421112 421211 212141
214121 412121 111143 111341 131141 114113 114311 411113 411311 113141
114131 311141 411131 211412 211214 211232
    """.trimIndent().trim().split(Regex("\\s+"))

    fun payload(batch: Int, frame: Int): String {
        require(batch in 0..4095 && frame in 0..9999)
        return batch.toString().padStart(4,'0') + frame.toString().padStart(4,'0')
    }

    fun pattern(batch: Int, frame: Int): EdgeMarkPattern {
        val pairs = payload(batch, frame).chunked(2).map(String::toInt)
        val checksum = (105 + pairs.mapIndexed { index, pair -> (index + 1) * pair }.sum()) % 103
        val row = BooleanArray(99) // 10 quiet + 6 eleven-module symbols + 13 stop + 10 quiet
        var x = 10
        for (value in listOf(105) + pairs + checksum + 106) {
            val symbol = if (value == 106) "2331112" else widths[value]
            for ((i, width) in symbol.withIndex()) repeat(width.digitToInt()) { row[x++] = i % 2 == 0 }
        }
        return EdgeMarkPattern.fromCells(row.size,9,BooleanArray(row.size*9) { row[it % row.size] })
    }
}

/** Factory lettering and code stay in 4–82%; the lower frame mark has its own 84–99% slot. */
class RegisteredFilmPlate(private val name: String, batch: Int, val frame: Int, val codeStyle: String,
    marker: String, leftward: Boolean, private val top: EdgeMarkPattern?, private val bottom: EdgeMarkPattern?,
    private val flipTop: Boolean, private val flipBottom: Boolean, seed: Int=0, composition: EdgeComposition?=null) {
    val generatedCode = if(composition==null && bottom!=null) null else when(codeStyle) {
        "code128c" -> FilmEdgeCode.pattern(batch,frame)
        "blocks" -> tracks(batch,frame,seed)
        else -> null
    }
    val generatedSides = if(generatedCode==null) emptyList() else if(composition==null) listOf("bottom")
        else listOf("top" to composition.top, "bottom" to composition.bottom).filter { (_,row) -> row.any { it.kind=="barcode" } }.map { it.first }
    private val upperBand: ComposedBand?
    private val lowerBand: ComposedBand?
    private val number=frame.toString()
    private val frameMark: EdgeMarkPattern
    init {
        val symbol=when(marker) {
            "triangle" -> listOf("1000000","1110000","1111100","1111111","1111100","1110000","1000000")
            "outline_pentagon" -> listOf("1111000","1000100","1000010","1000001","1000010","1000100","1111000")
            "none" -> emptyList()
            else -> listOf("0001000","0000100","0000010","1111111","0000010","0000100","0001000")
        }
        fun band(slots: List<EdgeSlot>, stencil: EdgeMarkPattern?, flip: Boolean, font: String) =
            ComposedBand(slots,flip) { kind ->
                when(kind) {
                    "barcode" -> generatedCode?.let { SlotRaster.pattern(it) }
                    "direction" -> if(symbol.isEmpty()) null else SlotRaster(7,7) { x,y -> symbol[y][if(leftward) 6-x else x]=='1' }
                    "text" -> stencil?.let { SlotRaster.pattern(it) } ?: SlotRaster.text(name,font)
                    else -> SlotRaster.text(number,font)
                }
            }
        upperBand=composition?.let { band(it.top,top,flipTop,it.font) }
        lowerBand=composition?.let { band(it.bottom,bottom,flipBottom,it.font) }
        val digits=number.length*6-1
        val symbolWidth=if(symbol.isEmpty()) 0 else 9
        val width=digits+symbolWidth
        frameMark=EdgeMarkPattern.fromCells(width,7,BooleanArray(width*7) { i ->
            val x=i%width;val y=i/width;val nx=if(leftward) x-symbolWidth else x
            val sx=if(leftward) 6-x else x-digits-2
            (nx in 0 until digits && nx%6<5 && ExposureGlyphs.lit(number[nx/6],nx%6,y)) ||
                (symbol.isNotEmpty() && sx in 0..6 && symbol[y][sx]=='1')
        })
    }

    fun ink(x: Int,y: Int,isTop: Boolean,width: Int,height: Int): Boolean {
        if(y !in 0 until height || x !in 0 until width) return false
        (if(isTop) upperBand else lowerBand)?.let { return it.ink(x,y,width,height) }
        val left=(width*.04).roundToInt()
        val right=(width*.82).roundToInt()
        if(x in left until right) {
            val pattern=if(isTop) top else bottom ?: generatedCode
            val flip=if(isTop) flipTop else flipBottom
            val columns=pattern?.width ?: (name.length*6-1)
            val rows=pattern?.height ?: 7
            val wanted=if(pattern!=null && pattern===generatedCode) (.64*width).roundToInt()
                else (height.toDouble()*columns/rows).roundToInt()
            val span=minOf(right-left,maxOf(1,wanted))
            val col=x-left
            if(col>=span) return false
            val gx=if(flip) columns-1-col*columns/span else col*columns/span
            val gy=if(flip) rows-1-y*rows/height else y*rows/height
            return pattern?.ink(gx,gy) ?: (gx%6<5 && ExposureGlyphs.lit(name[gx/6],gx%6,gy))
        }
        if(isTop) return false
        val end=(width*.99).roundToInt()
        val span=minOf(end-(width*.84).roundToInt(),maxOf(1,height*frameMark.width/frameMark.height))
        if(x !in end-span until end) return false
        return frameMark.ink((x-end+span)*frameMark.width/span,y*frameMark.height/height)
    }

    private fun tracks(batch: Int,frame: Int,seed: Int): EdgeMarkPattern {
        var state=0x9E3779B9.toInt()+seed*1664525+batch*1013904223+frame*69069
        val data=IntArray(24)
        for(i in data.indices) {
            state=1664525*state+1013904223
            val bit=(state ushr 16) and 1
            data[i]=if(i>=2 && data[i-1]==bit && data[i-2]==bit) 1-bit else bit
        }
        val cells=BooleanArray(128*12) { index ->
            val x=index%128;val k=(x-8)/2
            if(x !in 8 until 120) false
            else if(index/128<6) when(k) {
                in 0..3 -> "1101"[k]=='1'
                in 4..51 -> k%2==0
                else -> "1011"[k-52]=='1'
            } else when(k) {
                in 0..3 -> "1011"[k]=='1'
                in 4..51 -> data[(k-4)/2]==1
                else -> "1101"[k-52]=='1'
            }
        }
        return EdgeMarkPattern.fromCells(128,12,cells)
    }
}
