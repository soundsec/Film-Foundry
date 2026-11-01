package com.filmfoundry.kernel

import org.junit.jupiter.api.Assertions.*
import org.junit.jupiter.api.Test

class PhoneCaptureConditionsTest {
    @Test fun allPrintableAsciiGlyphsExistAndMixedTextSurvivesRowPartitioning() {
        for(ch in ' '..'~') {
            val font=PhoneImprintFont.forText("A$ch")
            val count=(0 until font.height).sumOf { y->(0 until font.width).count { x->font.lit(ch,x,y) } }
            assertEquals(ch!=' ',count>0,"Visible glyph for $ch")
        }
        val text="Film Aa-01 @50% #2! (OK)"
        val w=480;val h=320
        fun render(rows: Int,caption: String=text)=FloatArray(w*h*3).also { output->
            val conditions=PhoneCaptureConditions(w,h,mapOf("model" to PhoneCaptureConditions.MODEL,"camera" to mapOf(
                "imprint" to caption,"imprint_style" to "laser_dot","imprint_x" to 1.0,"imprint_y" to 1.0)))
            for(start in 0 until h step rows) {
                val count=minOf(rows,h-start);val block=FloatArray(w*count*3)
                conditions.apply(block,start,count);block.copyInto(output,start*w*3)
            }
        }
        val full=render(h);assertTrue(full.any { it>0f });assertArrayEquals(full,render(7))
        assertFalse(render(h,"Film").contentEquals(render(h,"FILM")))
        val old=PhoneImprintFont.forText("2026-11-01 12:34")
        assertEquals(3,old.width);assertEquals(5,old.height)
        assertTrue(old.lit('0',0,0));assertFalse(old.lit('0',1,1))
        assertThrows(IllegalArgumentException::class.java) { PhoneImprintFont.forText("中文") }
        assertThrows(IllegalArgumentException::class.java) { PhoneImprintFont.forText("line\nbreak") }
    }
    @Test fun printPlacementAndBrightnessStayBoundedAndPartitionIndependent() {
        val w=480;val h=320
        val base=mapOf<String,Any?>("imprint" to "2026-11-01 12:34","imprint_style" to "laser_dot")
        fun render(extra: Map<String,Any?>,rows: Int=h): FloatArray {
            val model=PhoneCaptureConditions(w,h,mapOf("model" to PhoneCaptureConditions.MODEL,"camera" to (base+extra)))
            return FloatArray(w*h*3).also { out->
                for(y in 0 until h step rows) { val n=minOf(rows,h-y);val block=FloatArray(w*n*3);model.apply(block,y,n);block.copyInto(out,y*w*3) }
            }
        }
        val old=render(emptyMap())
        assertArrayEquals(old,render(mapOf("imprint_x" to 0.0,"imprint_y" to 1.0,"imprint_brightness" to 1.0)))
        val center=mapOf("imprint_x" to .37,"imprint_y" to .62)
        val normal=render(center);val bright=render(center+mapOf("imprint_brightness" to 2.0))
        for(i in normal.indices) assertEquals(normal[i]*2,bright[i])
        assertArrayEquals(bright,render(center+mapOf("imprint_brightness" to 2.0),7))
        assertTrue(render(center+mapOf("imprint_brightness" to 0.0)).all { it==0f })
        for(x in listOf(0.0,1.0)) for(y in listOf(0.0,1.0)) {
            val pixels=render(mapOf("imprint_x" to x,"imprint_y" to y))
            assertEquals(old.count { it>0 },pixels.count { it>0 }) // No corner clips characters.
            val lit=pixels.indices.filter { pixels[it]>0 }
            assertTrue(lit.all { if(y==0.0) it/3/w<h/2 else it/3/w>h/2 })
            assertTrue(lit.all { if(x==0.0) it/3%w<w/2 else it/3%w>w/2 })
        }
        for(bad in listOf(Double.NaN,-.1,2.1)) assertThrows(IllegalArgumentException::class.java) { render(mapOf("imprint_brightness" to bad)) }
        assertThrows(IllegalArgumentException::class.java) { render(mapOf("imprint_x" to 1.1)) }
    }
    @Test fun combinedLeaksAddExposureAndKeepTheirOwnIdentityAcrossPartitions() {
        val w=160;val h=108
        val left=mapOf("shape" to "edge","side" to "left","strength" to .7)
        val cone=mapOf("shape" to "cone","side" to "bottom","strength" to .5,"seed" to 851L)
        fun render(parts: List<Map<String,Any>>,rows: Int=h)=FloatArray(w*h*3).also { out ->
            val model=PhoneCaptureConditions(w,h,mapOf("model" to PhoneCaptureConditions.MODEL,"camera" to mapOf("leak" to .8,"seed" to 17L,"leak_components" to parts)))
            for(y in 0 until h step rows) { val n=minOf(rows,h-y);val block=FloatArray(w*n*3);model.apply(block,y,n);block.copyInto(out,y*w*3) }
        }
        val a=render(listOf(left));val b=render(listOf(cone));val both=render(listOf(left,cone))
        for(i in both.indices) assertEquals(a[i]+b[i],both[i],1e-7f)
        assertArrayEquals(both,render(listOf(left,cone),7));assertTrue(both.sum()>a.sum())
        assertTrue(render(emptyList()).all { it==0f })
    }
    @Test fun oldReceiptsIgnoreNewCompositionAndPrintingOptions() {
        val camera=mapOf("seed" to 17L,"leak" to .8,"leak_shape" to "corner","imprint" to "12:34")
        fun render(model: String,extra: Map<String,Any> = emptyMap())=FloatArray(240*160*3).also {
            PhoneCaptureConditions(240,160,mapOf("model" to model,"camera" to (camera+extra))).apply(it,0,160)
        }
        val new=mapOf("leak_components" to listOf(mapOf("shape" to "cone","side" to "right")),"imprint_style" to "none","imprint_side" to "top")
        for(model in listOf(PhoneCaptureConditions.LEGACY_MODEL,PhoneCaptureConditions.PREVIOUS_MODEL)) assertArrayEquals(render(model),render(model,new))
        assertFalse(render(PhoneCaptureConditions.MODEL).contentEquals(render(PhoneCaptureConditions.MODEL,new)))
    }
    @Test fun printStylesAreExposureAndCanBeSwitchedOffWithoutMovingDirt() {
        fun render(style: String,side: String="bottom")=FloatArray(480*320*3).also {
            PhoneCaptureConditions(480,320,mapOf("model" to PhoneCaptureConditions.MODEL,"camera" to mapOf("imprint" to "2026-10-24","imprint_style" to style,"imprint_side" to side))).apply(it,0,320)
        }
        assertTrue(render("none").all { it==0f })
        val amber=render("optical_amber");val dots=render("laser_dot");val green=render("lab_edge")
        assertTrue(amber.any { it>0f });assertTrue(amber.maxOrNull()!!<.25f);assertFalse(amber.contentEquals(dots));assertFalse(amber.contentEquals(green))
        assertTrue(render("laser_dot","top").take(480*40*3).any { it>0f })
        assertTrue(dots.take(480*200*3).all { it==0f })
    }
    @Test fun cameraDefectsAreStableAcrossFramesAndRowPartitionsAndLiveBeforeFormation() {
        val w=240;val h=160
        val settings=mapOf("model" to PhoneCaptureConditions.MODEL,"camera" to mapOf(
            "seed" to 142L,"leak" to .7,"leak_shape" to "corner","leak_side" to "left",
            "dust" to true,"debris" to true,"imprint" to "2026-10-17"))
        val source=FloatArray(w*h*3){.2f};val full=source.copyOf()
        PhoneCaptureConditions(w,h,settings).apply(full,0,h)
        assertTrue(full.any { it < .2f });assertTrue(full.any { it > .2f })
        val blocks=FloatArray(source.size);val prepared=PhoneCaptureConditions(w,h,settings)
        for(start in 0 until h step 7) {
            val count=minOf(7,h-start);val rgb=source.copyOfRange(start*w*3,(start+count)*w*3)
            prepared.apply(rgb,start,count);rgb.copyInto(blocks,start*w*3)
        }
        assertArrayEquals(full,blocks)
        val another=source.copyOf();PhoneCaptureConditions(w,h,settings).apply(another,0,h)
        assertArrayEquals(full,another)
        val clear=source.copyOf();PhoneCaptureConditions(w,h,emptyMap()).apply(clear,0,h)
        assertArrayEquals(source,clear)
        // Printing is added exposure, even without a scene; it is not display RGB text.
        val darkness=FloatArray(w*h*3)
        PhoneCaptureConditions(w,h,mapOf("camera" to mapOf("imprint" to "12:24"))).apply(darkness,0,h)
        assertTrue(darkness.maxOrNull()!! > 1f)
    }
    @Test fun cameraLeakShapeIsIndependentOfFilmStorageState() {
        val rgb=FloatArray(120*80*3)
        fun leak(film: Double)=rgb.copyOf().also { PhoneCaptureConditions(120,80,
            mapOf("camera" to mapOf("leak" to .7,"seed" to 7,"leak_side" to "right"),"film" to mapOf("age" to film))).apply(it,0,80) }
        assertArrayEquals(leak(0.0),leak(1.0))
        val old=FloatArray(9){.6f};PhoneFilmStorageConditions(mapOf("age" to .5)).apply(old)
        assertEquals(.6f*(1f-.5f*.55f)+.5f*.018f,old[0])
        assertThrows(IllegalArgumentException::class.java) { PhoneCaptureConditions(2,2,mapOf("camera" to mapOf("imprint" to "汉字"))) }
    }
    @Test fun gateStreamsAreIndependentOfTogglesAndBodyIdentity() {
        val w=240;val h=160
        val base=mapOf<String,Any?>("seed" to 13L,"gate_seed" to 42L)
        fun render(camera: Map<String,Any?>,model: String=PhoneCaptureConditions.MODEL)=FloatArray(w*h*3){1f}.also {
            PhoneCaptureConditions(w,h,mapOf("model" to model,"camera" to camera)).apply(it,0,h)
        }
        val dust=render(base+mapOf("dust" to true));val debris=render(base+mapOf("debris" to true))
        val both=render(base+mapOf("dust" to true,"debris" to true))
        for(i in both.indices) assertEquals(dust[i]*debris[i],both[i],1e-7f)
        assertArrayEquals(both,render(base+mapOf("dust" to true,"debris" to true,"seed" to 99L)))
        assertFalse(both.contentEquals(render(base+mapOf("dust" to true,"debris" to true,"gate_seed" to 99L))))
        val leak=base+mapOf("leak" to .7,"leak_shape" to "corner")
        assertArrayEquals(render(leak),render(leak+mapOf("gate_seed" to 999L)))
        assertFalse(render(leak).contentEquals(render(leak+mapOf("seed" to 999L))))
        // Frozen v1 receipts keep the old stream: a new gate field never changes their replay.
        val old=base+mapOf("dust" to true,"debris" to true)
        assertArrayEquals(render(old,PhoneCaptureConditions.LEGACY_MODEL),
            render(old+mapOf("gate_seed" to 999L),PhoneCaptureConditions.LEGACY_MODEL))
    }
}
