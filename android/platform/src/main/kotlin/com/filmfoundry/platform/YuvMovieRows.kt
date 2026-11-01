package com.filmfoundry.platform

import java.nio.ByteBuffer
import kotlin.math.pow

data class MovieYuvPlane(val buffer: ByteBuffer,val rowStride: Int,val pixelStride: Int)

/** SDR BT.709 I/O conversion on requested rows; not a material/film formula. */
class YuvMovieRows(private val sourceWidth: Int,private val sourceHeight: Int,private val left: Int,private val top: Int,
    private val y: MovieYuvPlane,private val u: MovieYuvPlane,private val v: MovieYuvPlane,
    private val clockwise: Int,private val fullRange: Boolean) {
    val width=if(clockwise in setOf(90,270)) sourceHeight else sourceWidth
    val height=if(clockwise in setOf(90,270)) sourceWidth else sourceHeight
    init { require(sourceWidth > 0 && sourceHeight > 0 && left >= 0 && top >= 0 && clockwise in setOf(0,90,180,270)) }
    fun readRows(start: Int,count: Int): FloatArray {
        require(start >= 0 && count > 0 && start.toLong()+count <= height)
        val output=FloatArray(Math.multiplyExact(Math.multiplyExact(width,count),3))
        fun sample(plane: MovieYuvPlane,x: Int,row: Int): Float {
            require(plane.rowStride > 0 && plane.pixelStride > 0)
            val index=plane.buffer.position().toLong()+row.toLong()*plane.rowStride+x.toLong()*plane.pixelStride
            if(index < 0 || index >= plane.buffer.limit()) throw CaptureRejected("Movie YUV plane is truncated.")
            return (plane.buffer.get(index.toInt()).toInt() and 255).toFloat()
        }
        fun linear(value: Float): Float {
            val encoded=value.coerceIn(0f,1f)
            return if(encoded < .081f) encoded/4.5f else ((encoded+.099f)/1.099f).pow(1f/.45f)
        }
        for(row in 0 until count) for(col in 0 until width) {
            val vertical=start+row
            val sx: Int;val sy: Int
            when(clockwise) {
                90 -> { sx=vertical;sy=sourceHeight-1-col }
                180 -> { sx=sourceWidth-1-col;sy=sourceHeight-1-vertical }
                270 -> { sx=sourceWidth-1-vertical;sy=col }
                else -> { sx=col;sy=vertical }
            }
            val x=left+sx;val yy=top+sy
            val luma=if(fullRange) sample(y,x,yy)/255f else (sample(y,x,yy)-16f)/219f
            val cb=(sample(u,x/2,yy/2)-128f)/(if(fullRange) 255f else 224f)
            val cr=(sample(v,x/2,yy/2)-128f)/(if(fullRange) 255f else 224f)
            val at=(row*width+col)*3
            output[at]=linear(luma+1.5748f*cr)
            output[at+1]=linear(luma-.187324f*cb-.468124f*cr)
            output[at+2]=linear(luma+1.8556f*cb)
        }
        return output
    }
}
