package com.filmfoundry.kernel

/** Fixed exposure glyphs: platform fonts cannot change the replay of saved photographs. */
object PhoneImprintFont {
    const val MAX_CHARACTERS=24
    fun normalize(text: String)=text.map { when(it) {
        in '\uFF01'..'\uFF5E' -> (it.code-0xFEE0).toChar()
        '\u3000' -> ' '
        else -> it
    } }.joinToString("")
    fun valid(text: String)=text.length<=MAX_CHARACTERS && text.all { it in ' '..'~' }
    class Font(val width: Int,val height: Int,private val glyphs: Map<Char,IntArray>) {
        fun lit(ch: Char,x: Int,y: Int)=glyphs.getValue(ch)[y] and (1 shl (width-1-x)) != 0
    }
    private val legacy=Font(3,5,mapOf(
        48.toChar() to intArrayOf(7,5,5,5,7),
        49.toChar() to intArrayOf(2,6,2,2,7),
        50.toChar() to intArrayOf(7,1,7,4,7),
        51.toChar() to intArrayOf(7,1,7,1,7),
        52.toChar() to intArrayOf(5,5,7,1,1),
        53.toChar() to intArrayOf(7,4,7,1,7),
        54.toChar() to intArrayOf(7,4,7,5,7),
        55.toChar() to intArrayOf(7,1,2,2,2),
        56.toChar() to intArrayOf(7,5,7,5,7),
        57.toChar() to intArrayOf(7,5,7,1,7),
        45.toChar() to intArrayOf(0,0,7,0,0),
        58.toChar() to intArrayOf(0,2,0,2,0),
        47.toChar() to intArrayOf(1,1,2,4,4),
        46.toChar() to intArrayOf(0,0,0,0,2),
        32.toChar() to intArrayOf(0,0,0,0,0)
    ))
    private val ascii=Font(5,7,(' '..'~').associateWith { ch -> IntArray(7) { y ->
        (0..4).fold(0) { bits,x -> if(com.filmfoundry.interchange.ExposureGlyphs.lit(ch,x,y)) bits or (1 shl (4-x)) else bits }
    } })
    fun forText(text: String): Font {
        require(valid(text)) { "Use up to 24 English letters, digits, spaces or ASCII punctuation" }
        return if(text.all { it in "0123456789 -:/." }) legacy else ascii
    }
}
