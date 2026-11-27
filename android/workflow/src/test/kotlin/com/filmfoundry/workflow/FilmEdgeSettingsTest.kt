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
    @Test fun customPlateFreezesSlotsAndEtchesOnlyTheMarkedSupport() {
        val w=480;val h=240;val kernel=PhoneReducedKernel()
        val plate=mapOf("font" to "sans","top" to listOf(listOf("barcode",4,64)),
            "bottom" to listOf(listOf("number",2,10),listOf("text",18,85),listOf("direction",88,98)))
        val border=FilmEdgeSettings.create("135",true,"FF 400",patternSettings=mapOf(
            "factory_composition" to plate,"factory_barcode" to "code128c","factory_batch" to 123))
        val film=mapOf("clear_support_density_rgb" to listOf(.03,.04,.05),"film_base_density_rgb" to listOf(.2,.4,.55))
        val r=mapOf("film" to film,"film_border" to border,"frame_identity" to mapOf("frame_index" to 12))
        val gate=kernel.develop(kernel.expose(RgbVolume(w,h,linearRgb=FloatArray(w*h*3){.25f}),r),r)
        val plan=FormedFilmBorder.prepare(kernel,r,w,h)!!
        val code=plan.record()["barcode"] as Map<*,*>
        assertEquals(listOf("top"),code["generated_sides"]);assertEquals("01230012",code["payload"])
        val original=plan.surround(gate).opticalDensityRgb.values
        val roundedPlate=plate+("font" to "rounded")
        val roundedBorder=FilmEdgeSettings.create("135",true,"FF 400",patternSettings=border+("factory_composition" to roundedPlate))
        val rounded=FormedFilmBorder.prepare(kernel,r+("film_border" to roundedBorder),w,h)!!.surround(gate).opticalDensityRgb.values
        assertFalse(original.contentEquals(rounded))
        val gateStart=plan.layout.rebate*w*3
        assertArrayEquals(gate.opticalDensityRgb.values,rounded.copyOfRange(gateStart,gateStart+gate.opticalDensityRgb.values.size),0f)
        val etched=FormedFilmBorder.prepare(kernel,r+("film_border" to (border+("factory_mark_type" to "laser_etched"))),w,h)!!
            .surround(gate).opticalDensityRgb.values
        assertTrue(etched.indices.step(3).any { etched[it]==.03f && etched[it+1]==.04f && original[it]!=etched[it] })
        val start=plan.layout.rebate*w*3
        assertArrayEquals(gate.opticalDensityRgb.values,etched.copyOfRange(start,start+gate.opticalDensityRgb.values.size),0f)
        val file=System.getenv("FILM_FOUNDRY_REVIEW_OUTPUT")?.let { java.io.File(it,"phone-edge.png") }
        if(file!=null) {
            val image=java.awt.image.BufferedImage(w,plan.layout.rebate,java.awt.image.BufferedImage.TYPE_INT_RGB)
            for(y in 0 until image.height) for(x in 0 until w) {
                val v=(255*Math.pow(10.0,-original[(y*w+x)*3+1].toDouble())).toInt().coerceIn(0,255)
                image.setRGB(x,y,v*0x010101)
            }
            javax.imageio.ImageIO.write(image,"png",file)
        }
        val f=com.filmfoundry.kernel.PhoneImprintFont
        val dot=f.forText("8","dot_matrix");val segment=f.forText("8","seven_segment")
        assertEquals(5,dot.width);assertEquals(7,segment.width)
        assertTrue(segment.lit('8',6,1));assertFalse(segment.lit('5',6,1));assertTrue(dot.lit('8',0,1))
    }

    @Test fun loadedFilmStraddlesFrameBoundariesAndUsesEachSourceFrameCode() {
        val w=480;val h=240;val kernel=PhoneReducedKernel()
        val film=mapOf("name" to "FF 400", "film_base_density_rgb" to listOf(.3,.2,.1))
        fun border(slot: Int, offset: Double): FormedFilmBorder {
            val config=FilmEdgeSettings.create("135",true,"FF 400",patternSettings=mapOf(
                "factory_barcode" to "code128c","factory_batch" to 123,"loading_offset" to offset))
            return FormedFilmBorder.prepare(kernel,mapOf("film_border" to config,"film" to film,
                "frame_identity" to mapOf("roll_id" to "integration","frame_index" to slot)),w,h)!!
        }
        val gate=kernel.develop(kernel.expose(RgbVolume(w,h,linearRgb=FloatArray(w*h*3){.18f}),mapOf("film" to film)),mapOf("film" to film))
        val offset=-.25;val start=w/4
        val base=border(7,0.0).surround(gate).opticalDensityRgb.values
        val next=border(8,0.0).surround(gate).opticalDensityRgb.values
        val plan=border(7,offset);val actual=plan.surround(gate).opticalDensityRgb.values
        assertEquals("01230007",(plan.record()["barcode"] as Map<*,*>)["payload"])
        assertFalse(base.contentEquals(next))
        for(y in 0 until plan.layout.height) {
            if(y in plan.layout.rebate until plan.layout.rebate+h) continue
            val row=y*w*3
            assertArrayEquals(base.copyOfRange(row+start*3,row+w*3),actual.copyOfRange(row,row+(w-start)*3),0f)
            assertArrayEquals(next.copyOfRange(row,row+start*3),actual.copyOfRange(row+(w-start)*3,row+w*3),0f)
        }
        val plate=com.filmfoundry.interchange.RegisteredFilmPlate("FF 400",123,7,"blocks","outline_pentagon",false,null,null,false,false)
        val tracks=plate.generatedCode!!
        assertTrue((0 until 128).any { tracks.ink(it,0)!=tracks.ink(it,6) })
        assertTrue((0 until 12).all { y -> (394 until 403).none { plate.ink(it,y,false,480,12) } })
        assertTrue((0 until 12).any { y -> (404 until 476).any { plate.ink(it,y,false,480,12) } })
        val narrow=FilmEdgeSettings.create("110",true,"FF")
        val single=FormedFilmBorder.prepare(kernel,mapOf("film_border" to narrow),w,h)!!
        assertEquals("bottom",narrow["sprocket_rows"]);assertEquals(1,single.layout.holesPerRow)
        System.getenv("FILM_FOUNDRY_REVIEW_OUTPUT")?.let { dir ->
            val strip=java.awt.image.BufferedImage(w*2,plan.layout.rebate,java.awt.image.BufferedImage.TYPE_BYTE_GRAY)
            for ((j,slot) in listOf(7,8).withIndex()) {
                val view=border(slot,offset)
                val density=view.strip(view.layout.rebate+h,view.layout.rebate,gate).opticalDensityRgb.values
                for (y in 0 until strip.height) for (x in 0 until w) strip.raster.setSample(j*w+x,y,0,
                    (255*Math.pow(10.0,-density[(y*w+x)*3+1].toDouble())).toInt().coerceIn(0,255))
            }
            javax.imageio.ImageIO.write(strip,"png",java.io.File(dir,"phone-code.png"))
        }
    }

    @Test fun lampEvAndColourReachOpticalMarksAlongsideCameraLight() {
        val kernel=PhoneReducedKernel()
        val w=160;val h=100
        val base=mapOf("film" to mapOf("name" to "FOUNDRY","grain_strength" to 0.0))
        val gate=kernel.develop(kernel.expose(RgbVolume(w,h,linearRgb=FloatArray(w*h*3){.22f}),base),base)
        val camera=mapOf("exposure_conditions" to mapOf("model" to PHONE_CAPTURE_CONDITIONS_MODEL,"camera" to mapOf("leak" to .6)))
        fun strip(method: String,ev: Double,rgb: List<Double>): FloatArray {
            val config=FilmEdgeSettings.create("135",true,"FOUNDRY",patternSettings=mapOf(
                "factory_mark_type" to method,"factory_exposure_ev" to ev,"factory_light_color_rgb" to rgb))
            val plan=FormedFilmBorder.prepare(kernel,base+("film_border" to config),w,h,camera)!!
            assertEquals(ev,plan.record()["factory_exposure_ev"])
            assertEquals(rgb,plan.record()["factory_light_color_rgb"])
            return plan.strip(0,plan.layout.rebate,gate).opticalDensityRgb.values
        }
        val white=listOf(1.0,1.0,1.0);val red=listOf(1.0,.04,.01)
        for(method in listOf("hard","laser")) {
            val normal=strip(method,0.0,white)
            val dim=strip(method,-2.0,white)
            assertTrue(normal.indices.any { kotlin.math.abs(normal[it]-dim[it])>.001f })
            val coloured=strip(method,0.0,red)
            assertTrue(normal.indices.any { kotlin.math.abs(normal[it]-coloured[it])>.001f })
        }
        assertArrayEquals(strip("laser_etched",-2.0,red),strip("laser_etched",2.0,white),0f)
    }

    @Test fun customTextIsFrozenInTheNewRollConfigAndTheFactoryFrameNumberIsAutomatic() {
        val custom=FilmEdgeSettings.create("half",true,"STOCK","  my roll  ")
        assertEquals("MY ROLL",custom["stock_label"]);assertEquals("half_frame",custom["format_key"])
        val plan=FormedFilmBorder.prepare(PhoneReducedKernel(),mapOf("film_border" to custom,"frame_identity" to mapOf("frame_index" to 12)),240,160)!!
        assertEquals("MY ROLL 12",plan.record()["label"])
        val next=FilmEdgeSettings.create("6x6",true,"STOCK",marks=false)
        assertEquals("120",next["format_key"]);assertEquals("STOCK",next["stock_label"]);assertEquals(false,next["factory_marks"])
        assertEquals("MY ROLL",custom["stock_label"])
        for (invalid in listOf("不支持的字","ROLL-26","ß","FF\tLAB")) {
            assertThrows(IllegalArgumentException::class.java) { FilmEdgeSettings.customLabel(invalid) }
        }
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
        val border=FilmEdgeSettings.create("120",true,"TEST",patternSettings=mapOf("factory_pattern_top" to pattern)) +
            ("strip_layout" to "frame_layout_v1") // archived full-strip mirror
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
