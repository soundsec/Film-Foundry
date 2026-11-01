package com.filmfoundry.platform

import kotlin.math.exp
import kotlin.math.ln
import kotlin.math.pow

/** The same reconstruction formula for reference arrays and bounded encoded-pixel sampling. */
interface HdrGainSource {
    val width: Int
    val height: Int
    val ratioMin: FloatArray
    val ratioMax: FloatArray
    val gamma: FloatArray
    val epsilonSdr: FloatArray
    val epsilonHdr: FloatArray
    fun sample(x: Int,y: Int,channel: Int): Float
    fun applyRowsInPlace(linearRgb: FloatArray,frameWidth: Int,startRow: Int,frameHeight: Int) =
        applyWindowRowsInPlace(linearRgb,frameWidth,frameHeight,0,startRow,frameWidth)
    fun applyWindowRowsInPlace(linearRgb: FloatArray,frameWidth: Int,frameHeight: Int,left: Int,startRow: Int,windowWidth: Int) {
        require(frameWidth>0 && frameHeight>0 && startRow>=0)
        require(windowWidth>0 && left>=0 && left.toLong()+windowWidth<=frameWidth)
        val stride=Math.multiplyExact(windowWidth,3);require(linearRgb.size%stride==0)
        val rows=linearRgb.size/stride;require(startRow.toLong()+rows<=frameHeight)
        val lo=DoubleArray(3){ln(ratioMin[it].toDouble())}
        val span=DoubleArray(3){ln(ratioMax[it].toDouble())-lo[it]}
        for(y in 0 until rows) {
            val fy=(((startRow+y+.5)*height/frameHeight)-.5).coerceIn(0.0,(height-1).toDouble())
            val y0=fy.toInt();val y1=minOf(y0+1,height-1);val ty=fy-y0
            for(x in 0 until windowWidth) {
                val fx=(((left+x+.5)*width/frameWidth)-.5).coerceIn(0.0,(width-1).toDouble())
                val x0=fx.toInt();val x1=minOf(x0+1,width-1);val tx=fx-x0
                for(c in 0..2) {
                    val top=sample(x0,y0,c)*(1-tx)+sample(x1,y0,c)*tx
                    val bottom=sample(x0,y1,c)*(1-tx)+sample(x1,y1,c)*tx
                    val g=top*(1-ty)+bottom*ty
                    val index=(y*windowWidth+x)*3+c;val base=linearRgb[index]
                    if(!base.isFinite()) throw CaptureRejected("HDR base contains a nonfinite value.")
                    val value=(base+epsilonSdr[c])*exp(lo[c]+span[c]*g.pow(gamma[c].toDouble()))-epsilonHdr[c]
                    if(!value.isFinite() || value>Float.MAX_VALUE || value< -Float.MAX_VALUE)
                        throw CaptureRejected("HDR reconstruction exceeds finite float32 range.")
                    linearRgb[index]=value.toFloat()
                }
            }
        }
    }
}

internal fun validateHdrParameters(source: HdrGainSource) {
    for((name,values) in listOf("ratioMin" to source.ratioMin,"ratioMax" to source.ratioMax,"gamma" to source.gamma,
        "epsilonSdr" to source.epsilonSdr,"epsilonHdr" to source.epsilonHdr)) {
        if(values.size!=3 || values.any { !it.isFinite() || it<0f || (name !in listOf("epsilonSdr","epsilonHdr") && it==0f) })
            throw CaptureRejected("HDR $name must contain three finite valid values.")
    }
    if((0..2).any { source.ratioMax[it]<source.ratioMin[it] }) throw CaptureRejected("HDR ratioMax is below ratioMin.")
}

internal class SampledHdrGain(
    encodedWidth: Int,encodedHeight: Int,exif: Int,private val alphaOnly: Boolean,
    override val ratioMin: FloatArray,override val ratioMax: FloatArray,override val gamma: FloatArray,
    override val epsilonSdr: FloatArray,override val epsilonHdr: FloatArray,
    private val pixels: PackedPixelTiles,
): HdrGainSource {
    private val orientation=CapturePixelOrientation(encodedWidth,encodedHeight,exif)
    override val width get()=orientation.width
    override val height get()=orientation.height
    init { validateHdrParameters(this) }
    override fun sample(x: Int,y: Int,channel: Int): Float {
        val color=pixels.pixel(orientation.x(x,y),orientation.y(x,y))
        return ((color ushr (if(alphaOnly) 24 else 16-channel*8)) and 255)/255f
    }
}
