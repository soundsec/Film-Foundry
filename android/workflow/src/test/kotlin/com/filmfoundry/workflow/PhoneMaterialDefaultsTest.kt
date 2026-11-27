package com.filmfoundry.workflow

import org.junit.jupiter.api.Assertions.*
import org.junit.jupiter.api.Test

class PhoneMaterialDefaultsTest {
    @Test fun reverseLoadingSeparatesSupportFromProcessedMask() {
        val clear=listOf(0.0,0.0,0.0);val tint=listOf(.14,.055,.025);val mask=listOf(.25,.65,.90)
        val primaries=com.filmfoundry.kernel.RgbVolume(3,1,linearRgb=floatArrayOf(1f,0f,0f,0f,1f,0f,0f,0f,1f))
        fun transmit(process:String,polarity:String,total:List<Double>,floor:List<Double>): FloatArray {
            val film=mapOf("color_process" to process,"image_polarity" to polarity,
                "film_base_density_rgb" to total,"clear_support_density_rgb" to floor)
            val recipe=mapOf("film" to film,"exposure" to mapOf("film_side" to "base","rear_optics" to mapOf("mirror" to "none")))
            return com.filmfoundry.kernel.RearExposure.from(recipe,3,1)!!.transmitBlock(primaries).linearRgb
        }
        for((process,polarity) in listOf("color" to "negative","color" to "positive","monochrome" to "negative")) {
            val ref=transmit(process,polarity,clear,clear)
            val masked=process=="color" && polarity=="negative"
            if(masked) {
                assertArrayEquals(ref,transmit(process,polarity,mask,clear),0f)
                assertArrayEquals(ref,transmit(process,polarity,clear,tint),0f)
            }
            val got=transmit(process,polarity,if(masked) mask else tint,if(masked) tint else clear)
            for(i in got.indices) assertEquals(ref[i]*Math.pow(10.0,-tint[i/3]).toFloat(),got[i],1e-6f)
        }
    }
    @Test fun rearWedgeAndVerticalFocusAreContinuousAcrossRowTiles() {
        val optics=mapOf("f_number" to 1.0,"thickness_um" to 300.0,"index" to 1.5,
            "profile" to "harman_red_125","mirror" to "vertical","focus_shift_um" to 0.0)
        val recipe=mapOf("film" to mapOf("color_process" to "color"), "enable_mtf" to false, "enable_halation" to false,
            "exposure" to mapOf("film_side" to "base","rear_optics" to optics))
        val width=512;val height=31
        val pixels=FloatArray(width*height*3) { i -> if(i/(3*width) in 3..8 && i/3%width < 80) .18f else 0f }
        val source=object: com.filmfoundry.kernel.LinearRgbRows {
            override val width=512; override val height=31
            override fun readRows(start:Int,count:Int)=com.filmfoundry.kernel.RgbVolume(width,count,linearRgb=
                pixels.copyOfRange(start*width*3,(start+count)*width*3))
        }
        val cpu=com.filmfoundry.kernel.PhoneReducedKernel()
        val frame=cpu.expose(source.readRows(0,height),recipe)
        assertEquals(optics+("auto_refocus" to false),frame.metadata["rear_optics"])
        val whole=frame.arrays.getValue("developability").values
        assertTrue(whole[24*width*3] > whole[4*width*3])
        fun withOpt(o:Map<String,Any>)=cpu.expose(source.readRows(0,height),recipe+("exposure" to
            mapOf("film_side" to "base","rear_optics" to o))).arrays.getValue("developability").values
        val refocused=withOpt(optics+("auto_refocus" to true))
        assertArrayEquals(withOpt(optics+("focus_shift_um" to 200.0)),refocused,0f)
        assertFalse(refocused.contentEquals(whole))
        cpu.prepareExposure(recipe,width,height).bindSource(source).use { bound ->
            for(start in 0 until height step 5) {
                val count=minOf(5,height-start)
                assertArrayEquals(whole.copyOfRange(start*width*3,(start+count)*width*3),
                    bound.exposeRows(start,count).arrays.getValue("developability").values,0f)
            }
        }
    }
    @Test fun baseFirstRollFreezesItsExposureAndLeavesTheSourceAlone() {
        val capture=PhoneMaterialDefaults.previewCaptureSettings()
        val film=mapOf("spatial_reference_short_edge_mm" to 24.0)
        val front=PhoneMaterialDefaults.exposureRecipe(film,capture)
        val rear=PhoneMaterialDefaults.exposureRecipe(film,capture+("film_side" to "base"))
        val scene=com.filmfoundry.kernel.RgbVolume(8,8,linearRgb=FloatArray(8*8*3){.18f})
        val cpu=com.filmfoundry.kernel.PhoneReducedKernel()
        val normal=cpu.expose(scene,front)
        val base=cpu.expose(scene,rear)
        assertNull(normal.metadata["film_side"])
        assertEquals("base",base.metadata["film_side"])
        assertTrue(scene.linearRgb.all { it==.18f })
        val a=normal.arrays.getValue("developability").values
        val b=base.arrays.getValue("developability").values
        assertTrue(b[0]/a[0] > b[1]/a[1] && b[1]/a[1] > b[2]/a[2])
        val frozen=cpu.develop(base,front).opticalDensityRgb.values
        assertArrayEquals(frozen,cpu.develop(base,rear).opticalDensityRgb.values,0f)
    }
    @Test fun captureDurationUsesSingleRecordedWindowAndKeepsOldRolls() {
        val key=com.filmfoundry.interchange.PhoneFormationProfile.OPTION
        val modern=mapOf(key to com.filmfoundry.interchange.PhoneFormationProfile.LIGHTWEIGHT)
        val capture=mapOf("frame_count" to 1,"exposure_time_ns" to 250_000_000L)
        assertEquals(.25,(PhoneMaterialDefaults.withCaptureDuration(modern,capture)["exposure"] as Map<*,*>)["duration_seconds"])
        assertEquals(modern,PhoneMaterialDefaults.withCaptureDuration(modern,capture+("frame_count" to 2)))
        val old=mapOf(key to com.filmfoundry.interchange.PhoneFormationProfile.PREVIOUS_LIGHTWEIGHT)
        assertEquals(old,PhoneMaterialDefaults.withCaptureDuration(old,capture))
        val explicit=modern+("exposure" to mapOf("duration_seconds" to 2.0))
        assertEquals(explicit,PhoneMaterialDefaults.withCaptureDuration(explicit,capture))
    }
    @Test fun formationBudgetIsExplicitAndOldRollsHaveNoNewDefault() {
        val key=com.filmfoundry.interchange.PhoneFormationProfile.OPTION
        val setting=com.filmfoundry.interchange.PhoneFormationProfile.ROLL_SETTING
        val fast=com.filmfoundry.interchange.PhoneFormationProfile.LIGHTWEIGHT
        assertFalse(PhoneMaterialDefaults.exposureRecipe(emptyMap(),emptyMap()).containsKey(key))
        assertEquals(fast,PhoneMaterialDefaults.exposureRecipe(emptyMap(),mapOf(setting to fast))[key])
        assertThrows(IllegalArgumentException::class.java) { PhoneMaterialDefaults.exposureRecipe(emptyMap(),mapOf(setting to "future")) }
    }
    @Test fun naturalKeepsStockStrengthsAndOtherLevelsAdjustThemWithoutChangingTheSource() {
        val stock=mapOf("grain_strength" to .045,"silver_grain_strength" to .06,"emulsion_mtf_strength" to .3,"halation_strength" to .2,
            "name" to "fixture","density_max" to listOf(2.0,1.9,2.1))
        val natural=PhoneMaterialDefaults.withLevels(stock,2,1,1,1.0)
        stock.forEach { (key,value)->assertEquals(value,natural[key]) }
        val fine=PhoneMaterialDefaults.withLevels(stock,1,1,1,1.0)
        val strong=PhoneMaterialDefaults.withLevels(stock,3,2,2,1.0)
        assertEquals(.0216,fine["grain_strength"] as Double,1e-12)
        assertEquals(.0288,fine["silver_grain_strength"] as Double,1e-12)
        assertEquals(.09,strong["grain_strength"] as Double,1e-12)
        assertEquals(.12,strong["silver_grain_strength"] as Double,1e-12)
        assertTrue(strong["emulsion_mtf_strength"] as Double>.3);assertEquals(.5,strong["halation_strength"])
        assertEquals(.045,stock["grain_strength"]);assertEquals(.06,stock["silver_grain_strength"])
    }
    @Test fun offDisablesSilverAsWellAsDyeGrainAndDefaultNaturalKeepsCurrentLoadingValues() {
        val off=PhoneMaterialDefaults.withLevels(mapOf("grain_strength" to .045,"silver_grain_strength" to .06),0,0,0,1.0)
        for(key in listOf("grain_strength","silver_grain_strength","emulsion_mtf_strength","halation_strength")) assertEquals(0.0,off[key])
        val normal=PhoneMaterialDefaults.withLevels(emptyMap(),2,1,1,1.0)
        assertEquals(.025,normal["grain_strength"]);assertEquals(.22,normal["emulsion_mtf_strength"]);assertEquals(.12,normal["halation_strength"])
        val limits=PhoneMaterialDefaults.withLevels(mapOf("grain_strength" to .2,"silver_grain_strength" to .4,"emulsion_mtf_strength" to .8,"halation_strength" to 1.8),3,2,2,1.0)
        assertEquals(.25,limits["grain_strength"]);assertEquals(.5,limits["silver_grain_strength"]);assertEquals(1.0,limits["emulsion_mtf_strength"]);assertEquals(2.0,limits["halation_strength"])
    }
}
