package com.filmfoundry.platform

import org.junit.jupiter.api.Assertions.*
import org.junit.jupiter.api.Test

class BoundedSourceSamplesTest {
    @Test fun tiledGainSamplesMatchTheReferenceExactlyForAllExifDirectionsAndAlphaMaps() {
        val w=137;val h=91;var reads=0;var largest=0
        fun pixel(x: Int,y: Int)=((x*3+y*7)%256 shl 24) or ((x+y*11)%256 shl 16) or ((x*7+y)%256 shl 8) or ((x*17+y*3)%256)
        for(exif in 1..8) for(alpha in listOf(false,true)) {
            val cache=PackedPixelTiles(w,h) { left,top,width,height,pixels ->
                reads++;largest=maxOf(largest,pixels.size)
                for(y in 0 until height) for(x in 0 until width) pixels[y*width+x]=pixel(left+x,top+y)
            }
            val low=floatArrayOf(.5f,.8f,1f);val high=floatArrayOf(2f,5f,8f);val gamma=floatArrayOf(.8f,1f,1.4f)
            val epsS=floatArrayOf(.01f,.02f,.03f);val epsH=floatArrayOf(.02f,.01f,.03f)
            val raw=FloatArray(w*h*3) { i -> ((pixel(i/3%w,i/3/w) ushr (if(alpha) 24 else 16-(i%3)*8)) and 255)/255f }
            val reference=CapturePixelOrientation(w,h,exif).orientGain(HdrGainMap(w,h,raw,low,high,gamma,epsS,epsH))
            val sampled=SampledHdrGain(w,h,exif,alpha,low,high,gamma,epsS,epsH,cache)
            assertEquals(reference.width,sampled.width);assertEquals(reference.height,sampled.height)
            val input=FloatArray(31*17*3){(it%29)/17f};val actual=input.copyOf();val expected=input.copyOf()
            reference.applyWindowRowsInPlace(expected,43,29,3,2,31)
            sampled.applyWindowRowsInPlace(actual,43,29,3,2,31)
            assertArrayEquals(expected,actual,0f,"EXIF $exif alpha=$alpha")
            assertArrayEquals(FloatArray(31*17*3){(it%29)/17f},input,0f)
        }
        assertTrue(reads>4);assertTrue(largest<=64*64)
    }
    @Test fun nativeDecodeStripesCoverRequestedPixelsAndStayBoundedInEitherDirection() {
        for(exif in 1..8) {
            val o=CapturePixelOrientation(12000,9000,exif)
            val left=7;val top=128;val right=o.width-9;val bottom=top+1
            val xs=listOf(o.x(left,top),o.x(right,top),o.x(left,bottom),o.x(right,bottom))
            val ys=listOf(o.y(left,top),o.y(right,top),o.y(left,bottom),o.y(right,bottom))
            val request=PixelRectangle(xs.min(),ys.min(),xs.max()-xs.min()+1,ys.max()-ys.min()+1)
            val stripe=cachedPixelStripe(request,12000,9000)
            assertTrue(stripe.contains(request));assertTrue(stripe.width.toLong()*stripe.height<=2*65536)
        }
        var reads=0
        val cache=PackedPixelTiles(600,600) { _,_,_,_,pixels -> reads++;pixels.fill(reads) }
        assertEquals(cache.pixel(1,1),cache.pixel(2,1));assertEquals(1,reads)
        for(x in 1..5) cache.pixel(x*64,1)
        cache.pixel(1,1);assertEquals(7,reads) // LRU evicts; it never retains the whole source.
    }
}
