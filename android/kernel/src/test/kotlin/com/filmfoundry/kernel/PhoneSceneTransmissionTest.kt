package com.filmfoundry.kernel

import com.filmfoundry.interchange.FloatNd
import org.junit.jupiter.api.Assertions.*
import org.junit.jupiter.api.Test
import java.awt.image.BufferedImage
import java.io.File
import javax.imageio.ImageIO

class PhoneSceneTransmissionTest {
    private fun rows(w: Int,h: Int,pixels: FloatArray)=object: LinearRgbRows {
        override val width=w;override val height=h
        override fun readRows(start: Int,count: Int)=RgbVolume(w,count,linearRgb=pixels.copyOfRange(start*w*3,(start+count)*w*3))
    }
    @Test fun cachedTransformsAndBulkArgbKeepReferencePixelsAcrossMovementResizeAndCancelledPreparation() {
        val density=FloatArray(7*5*3) { (it%29)*.07f };val before=density.copyOf()
        val film=PhoneSceneTransmission(OpticalMaster(FloatNd(intArrayOf(5,7,3),density),emptyMap()))
        val poses=listOf(floatArrayOf(1f,0f,0f),floatArrayOf(2.75f,.3f,-.2f),floatArrayOf(5f,-1.1f,.6f),floatArrayOf(1f,0f,0f))
        for((w,h) in listOf(61 to 37,37 to 61,61 to 37)) {
            val source=FloatArray(w*h*3) { (it%101)*.013f };val original=source.copyOf();val input=rows(w,h,source)
            for(pose in poses) {
                val (z,x,y)=pose
                val reference=film.renderReference(input,z,x,y)
                assertArrayEquals(reference,film.render(input,z,x,y).outputRgb.values,0f)
                val packed=IntArray(w*h);film.renderArgb(input,packed,z,x,y)
                for(i in packed.indices) {
                    fun c(k: Int)=(reference[i*3+k]*255).toInt()
                    assertEquals(0xff000000.toInt() or (c(0) shl 16) or (c(1) shl 8) or c(2),packed[i])
                }
                val retry=IntArray(w*h);film.renderArgb(input,retry,z,x,y);assertArrayEquals(packed,retry)
            }
            val cancelled=object: LinearRgbRows by input { override fun checkActive() { throw InterruptedException("cancel geometry") } }
            assertThrows(InterruptedException::class.java) { film.renderArgb(cancelled,IntArray(w*h),1.5f) }
            assertArrayEquals(film.renderReference(input),film.render(input).outputRgb.values,0f)
            assertArrayEquals(original,source,0f)
        }
        assertArrayEquals(before,density,0f)
        if(System.getenv("FILM_FOUNDRY_SCENE_BENCH")=="1") {
            val source=rows(640,480,FloatArray(640*480*3) { (it%97)*.01f });val out=IntArray(640*480)
            repeat(2) { film.renderReference(source);film.renderArgb(source,out) }
            fun measured(block: ()->Unit)=List(3) { val t=System.nanoTime();block();(System.nanoTime()-t)/1e6 }.sorted()[1]
            println("scene_cpu_component_reference_ms="+measured { film.renderReference(source) }+" bulk_argb_ms="+measured { film.renderArgb(source,out) })
        }
    }
    @Test fun fixedFilmMultipliesSpatialCameraLightAndLeavesTheSurroundingsAndSourcesUntouched() {
        val density=FloatArray(12) { (it/3)*.2f+(it%3)*.1f };val original=density.copyOf()
        val film=PhoneSceneTransmission(OpticalMaster(FloatNd(intArrayOf(2,2,3),density),mapOf("image_polarity" to "negative")))
        val scene=FloatArray(12*8*3) { .05f+(it/3%12)*.045f };val before=scene.copyOf()
        val first=film.render(rows(12,8,scene));val expected=scene.copyOf();PhoneObserve.encodeRows(expected,emptyMap(),true)
        assertArrayEquals(expected.copyOfRange(0,3),first.outputRgb.values.copyOfRange(0,3),0f)
        val middle=(4*12+6)*3
        assertTrue(first.outputRgb.values[middle]<expected[middle]);assertTrue(first.outputRgb.values[middle+2]<first.outputRgb.values[middle])
        val moved=scene.copyOf().also { for(c in 0..2) it[middle+c]*=.4f }
        val second=film.render(rows(12,8,moved))
        for(i in scene.indices) if(i !in middle..middle+2) assertEquals(first.outputRgb.values[i],second.outputRgb.values[i])
        assertTrue(second.outputRgb.values[middle]<first.outputRgb.values[middle])
        assertEquals("unchanged",(second.metadata["scene_transmission"] as Map<*,*>)["polarity"])
        assertArrayEquals(original,density,0f);assertArrayEquals(before,scene,0f)
        assertTrue(film.render(rows(12,8,FloatArray(scene.size))).outputRgb.values.all { it.isFinite() })
        System.getenv("FILM_FOUNDRY_SCENE_REVIEW")?.let { path ->
            val w=240;val h=160
            val large=FloatArray(w*h*3) { i -> val x=i/3%w;val y=i/3/w
                when(i%3) { 0->.04f+x.toFloat()/w*.7f;1->.05f+y.toFloat()/h*.6f;else->if((x/30+y/30)%2==0) .65f else .08f } }
            val encoded=large.copyOf();PhoneObserve.encodeRows(encoded,emptyMap(),true)
            val frames=listOf(encoded,film.render(rows(w,h,large)).outputRgb.values)
            val image=BufferedImage(w*2,h,BufferedImage.TYPE_INT_RGB)
            for((n,p) in frames.withIndex()) for(y in 0 until h) for(x in 0 until w) {
                val i=(y*w+x)*3;fun c(k: Int)=(p[i+k]*255+.5f).toInt().coerceIn(0,255)
                image.setRGB(n*w+x,y,(c(0) shl 16) or (c(1) shl 8) or c(2))
            }
            File(path).parentFile.mkdirs();ImageIO.write(image,"png",File(path))
        }
    }
}
