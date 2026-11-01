package com.filmfoundry.interchange

/** Hand-drawn stencil, not a barcode. Same FFEM1 row-major bit format as desktop. */
class EdgeMarkPattern(val width: Int,val height: Int,bits: ByteArray) {
    private val data=bits.copyOf()
    init {
        require(width in 1..128 && height in 1..32) { "Edge pattern size must be within 128 x 32 cells" }
        val count=width*height
        require(data.size==(count+7)/8 && (count%8==0 || (data.last().toInt() and ((1 shl (8-count%8))-1))==0)) { "Invalid edge pattern bits" }
    }
    fun ink(x: Int,y: Int): Boolean {
        if(x !in 0 until width || y !in 0 until height) return false
        val bit=y*width+x
        return (data[bit/8].toInt() and (1 shl (7-bit%8)))!=0
    }
    fun encode()="FFEM1:${width}x$height:"+data.joinToString("") { "%02X".format(java.util.Locale.ROOT,it.toInt() and 255) }
    companion object {
        fun decode(code: String): EdgeMarkPattern {
            val value=code.trim();require(value.length<=1045) { "Edge pattern code is too large" }
            val m=Regex("FFEM1:([1-9][0-9]{0,2})x([1-9][0-9]?):([0-9a-fA-F]+)").matchEntire(value)
                ?: throw IllegalArgumentException("Expected an FFEM1 edge pattern code")
            val hex=m.groupValues[3];require(hex.length%2==0) { "Invalid edge pattern data" }
            return EdgeMarkPattern(m.groupValues[1].toInt(),m.groupValues[2].toInt(),ByteArray(hex.length/2) { hex.substring(it*2,it*2+2).toInt(16).toByte() })
        }
        fun normalize(code: String)=if(code.isBlank()) "" else decode(code).encode()
        fun fromCells(width: Int,height: Int,cells: BooleanArray): EdgeMarkPattern {
            require(width in 1..128 && height in 1..32 && cells.size==width*height)
            val bits=ByteArray((cells.size+7)/8)
            cells.forEachIndexed { i,on -> if(on) bits[i/8]=(bits[i/8].toInt() or (1 shl (7-i%8))).toByte() }
            return EdgeMarkPattern(width,height,bits)
        }
    }
}
