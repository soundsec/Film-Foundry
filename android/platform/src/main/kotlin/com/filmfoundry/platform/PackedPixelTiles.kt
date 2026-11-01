package com.filmfoundry.platform

/** Small encoded-pixel cache for gain sampling; no image-sized float copy. */
internal class PackedPixelTiles(private val width: Int,private val height: Int,
    private val read: (Int,Int,Int,Int,IntArray)->Unit) {
    private data class Tile(val x: Int,val y: Int,val width: Int,val values: IntArray)
    private val tiles=LinkedHashMap<Long,Tile>(4,.75f,true)
    fun pixel(x: Int,y: Int): Int {
        require(x in 0 until width && y in 0 until height)
        val left=x/64*64;val top=y/64*64;val key=(top.toLong() shl 32) or left.toLong()
        val tile=tiles[key] ?: run {
            if(tiles.size==4) tiles.remove(tiles.keys.first())
            val w=minOf(64,width-left);val h=minOf(64,height-top)
            Tile(left,top,w,IntArray(w*h).also { read(left,top,w,h,it) }).also { tiles[key]=it }
        }
        return tile.values[(y-tile.y)*tile.width+x-tile.x]
    }
}
