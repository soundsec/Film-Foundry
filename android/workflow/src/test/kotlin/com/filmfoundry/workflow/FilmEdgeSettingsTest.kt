package com.filmfoundry.workflow

import com.filmfoundry.kernel.FormedFilmBorder
import com.filmfoundry.kernel.PhoneReducedKernel
import com.filmfoundry.kernel.RgbVolume
import com.filmfoundry.interchange.EdgeMarkPattern
import com.filmfoundry.interchange.RecipeShareCodec
import com.filmfoundry.interchange.RecipeJson
import org.junit.jupiter.api.Assertions.*
import org.junit.jupiter.api.Test

class FilmEdgeSettingsTest {
    @Test fun customTextIsFrozenInTheNewRollConfigAndTheFactoryFrameNumberIsAutomatic() {
        val custom=FilmEdgeSettings.create("half",true,"STOCK","  my roll-26  ")
        assertEquals("MY ROLL-26",custom["stock_label"]);assertEquals("half_frame",custom["format_key"])
        val plan=FormedFilmBorder.prepare(PhoneReducedKernel(),mapOf("film_border" to custom,"frame_identity" to mapOf("frame_index" to 12)),240,160)!!
        assertEquals("MY ROLL-26 12",plan.record()["label"])
        val next=FilmEdgeSettings.create("6x6",true,"STOCK",marks=false)
        assertEquals("120",next["format_key"]);assertEquals("STOCK",next["stock_label"]);assertEquals(false,next["factory_marks"])
        assertEquals("MY ROLL-26",custom["stock_label"])
        assertThrows(IllegalArgumentException::class.java) { FilmEdgeSettings.customLabel("不支持的字") }
    }

    @Test fun stencilCodesHaveSharedBitOrderBoundedSizeAndFrozenRecipeSettings() {
        val code="FFEM1:3x2:A8";val p=EdgeMarkPattern.decode(code.lowercase().replace("ffem1","FFEM1"))
        assertEquals(code,p.encode());assertTrue(p.ink(0,0));assertFalse(p.ink(1,0));assertTrue(p.ink(1,1))
        for(invalid in listOf("FFEM1:3x2:A9","FFEM1:129x1:00","FFEM1:3x2:A","invalid"))
            assertThrows(IllegalArgumentException::class.java) { EdgeMarkPattern.decode(invalid) }
        val input=mutableMapOf<String,Any?>("factory_pattern_top" to code,"factory_pattern_bottom" to "FFEM1:3x2:54","factory_pattern_linked" to false,"factory_flip_top" to true)
        val saved=FilmEdgeSettings.create("135",true,"TEST",patternSettings=input)
        input["factory_pattern_top"]=""
        assertEquals(code,saved["factory_pattern_top"])
        assertTrue(RecipeJson.equivalent(saved,RecipeShareCodec.decode(RecipeShareCodec.encode(mapOf("film_border" to saved)))["film_border"]))
    }

    @Test fun customStencilFlipsOnlyItsOwnEdgeAndKeepsTheGateAndObservationReadOnly() {
        val w=120;val h=80;val kernel=PhoneReducedKernel()
        val pattern="FFEM1:3x2:A8"
        val border=FilmEdgeSettings.create("120",true,"TEST",patternSettings=mapOf("factory_pattern_top" to pattern))
        val recipe=mapOf<String,Any?>("film_border" to border,"frame_identity" to mapOf("frame_index" to 1))
        val main=kernel.develop(kernel.expose(RgbVolume(w,h,linearRgb=FloatArray(w*h*3){.15f}),recipe),recipe)
        val plan=FormedFilmBorder.prepare(kernel,recipe,w,h)!!;val a=plan.surround(main)
        val flipped=FormedFilmBorder.prepare(kernel,recipe+("film_border" to (border+("factory_flip_top" to true))),w,h)!!.surround(main)
        val start=plan.layout.rebate-kotlin.math.ceil(plan.layout.textBottom*plan.layout.rebate).toInt()
        val end=plan.layout.rebate-(plan.layout.textTop*plan.layout.rebate).toInt()
        for(y in start until end) for(x in 0 until w) for(c in 0..2)
            assertEquals(a.opticalDensityRgb.values[((start+end-1-y)*w+w-1-x)*3+c],flipped.opticalDensityRgb.values[(y*w+x)*3+c],0f)
        val gateStart=plan.layout.rebate*w*3;val gateEnd=gateStart+w*h*3
        assertArrayEquals(main.opticalDensityRgb.values,a.opticalDensityRgb.values.copyOfRange(gateStart,gateEnd),0f)
        assertArrayEquals(a.opticalDensityRgb.values.copyOfRange(gateEnd,a.opticalDensityRgb.values.size),flipped.opticalDensityRgb.values.copyOfRange(gateEnd,flipped.opticalDensityRgb.values.size),0f)
        val before=a.opticalDensityRgb.values.copyOf();kernel.observe(a,recipe);assertArrayEquals(before,a.opticalDensityRgb.values,0f)
        assertEquals("TEST 01",plan.record()["label"])
        val next=FormedFilmBorder.prepare(kernel,recipe+("frame_identity" to mapOf("frame_index" to 2)),w,h)!!
        assertEquals("TEST 02",next.record()["label"]);assertEquals(pattern,next.record()["factory_pattern_top"])
        val independent=FormedFilmBorder.prepare(kernel,recipe+("film_border" to (border+mapOf("factory_pattern_linked" to false,"factory_pattern_bottom" to "FFEM1:3x2:54"))),w,h)!!
        assertEquals("FFEM1:3x2:54",independent.record()["factory_pattern_bottom"])
    }

    @Test fun laserMarkResponseDependsOnTheStockBeforeObservation() {
        val w=180;val h=120;val k=PhoneReducedKernel()
        val pattern=EdgeMarkPattern.fromCells(32,8,BooleanArray(256) { it%32 in 8..10 && it/32 in 2..5 }).encode()
        val border=FilmEdgeSettings.create("135",true,"TEST",patternSettings=mapOf("factory_pattern_top" to pattern,"factory_mark_type" to "laser"))
        val recipe=mapOf<String,Any?>("film_border" to border,"film" to mapOf("halation_strength" to 0.0,"emulsion_mtf_strength" to 0.0))
        val main=k.develop(k.expose(RgbVolume(w,h,linearRgb=FloatArray(w*h*3){.15f}),recipe),recipe)
        val clean=FormedFilmBorder.prepare(k,recipe,w,h)!!;val a=clean.surround(main)
        val changed=FormedFilmBorder.prepare(k,recipe+("film" to mapOf("halation_strength" to .9,"halation_outer_radius" to .025,"emulsion_mtf_strength" to .8)),w,h)!!
        val b=changed.surround(main);val start=clean.layout.rebate*w*3
        assertFalse(a.opticalDensityRgb.values.contentEquals(b.opticalDensityRgb.values))
        assertArrayEquals(main.opticalDensityRgb.values,b.opticalDensityRgb.values.copyOfRange(start,start+w*h*3),0f)
        assertEquals("material_exposure_table_v1",changed.record()["factory_response"])
        val original=b.opticalDensityRgb.values.copyOf();k.observe(b,recipe);assertArrayEquals(original,b.opticalDensityRgb.values,0f)
        // A quantized 8x8 table could only produce 64 red-density values, leaving
        // visible contour bands around the lettering. Interpolation must fill them.
        val redLevels=(0 until start step 3).map { kotlin.math.round(original[it]*100000).toInt() }.toSet()
        assertTrue(redLevels.size>64,"Factory halo must not use a 64-color density palette")
        val zero=FormedFilmBorder.prepare(k,recipe+("film_border" to (border+("factory_mark_spread" to 0.0))),w,h)!!.surround(main)
        val hard=FormedFilmBorder.prepare(k,recipe+("film_border" to (border+("factory_mark_type" to "hard"))),w,h)!!.surround(main)
        assertArrayEquals(hard.opticalDensityRgb.values,zero.opticalDensityRgb.values,0f)
    }
}
