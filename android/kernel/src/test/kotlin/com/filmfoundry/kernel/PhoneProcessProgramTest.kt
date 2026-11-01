package com.filmfoundry.kernel

import com.filmfoundry.interchange.*
import org.junit.jupiter.api.Assertions.*
import org.junit.jupiter.api.Test

class PhoneProcessProgramTest {
    private val kernel=PhoneReducedKernel()
    private fun recipe(key: String,extra: Map<String,Any?> = emptyMap())=mapOf("develop" to (mapOf("program_key" to key)+extra),"scanner" to mapOf("scan_normalize" to false))
    private fun source()=kernel.expose(RgbVolume(3,1,linearRgb=floatArrayOf(.001f,.001f,.001f,.18f,.18f,.18f,8f,8f,8f)),emptyMap())
    @Test fun orderedColorAndBwReversalProducePositiveTransmissionAndDefaultScanDoesNotInvert() {
        for(color in listOf(true,false)) {
            val latent=if(color) source() else kernel.expose(RgbVolume(3,1,linearRgb=floatArrayOf(.001f,.001f,.001f,.18f,.18f,.18f,8f,8f,8f)),mapOf("film" to mapOf("color_process" to "bw")))
            val recipe=recipe(if(color) "color_reversal" else "bw_reversal")
            val optical=kernel.develop(latent,recipe);val before=optical.opticalDensityRgb.values.copyOf()
            assertEquals("positive",optical.metadata["image_polarity"])
            assertTrue(before[0]>before[6],"bright exposure must give less positive density")
            for(mode in listOf("scanner","viewer_light_table","viewer_projection")) {
                val result=kernel.observe(optical,recipe+("observe_mode" to mode))
                assertTrue(result.outputRgb.values[0]<result.outputRgb.values[6]);assertArrayEquals(before,optical.opticalDensityRgb.values,0f)
            }
            val codec=NpzMediumArchive();val saved=codec.readDevelopedOptical(codec.writeDevelopedOptical(DevelopedOpticalArchive(optical.opticalDensityRgb,optical.metadata,optical.clearBaseOpticalDensityRgb)))
            assertEquals("positive",saved.metadata["image_polarity"])
            val declared=kernel.expose(RgbVolume(3,1,linearRgb=floatArrayOf(.001f,.001f,.001f,.18f,.18f,.18f,8f,8f,8f)),mapOf("film" to mapOf("image_polarity" to "positive","color_process" to if(color) "color_negative" else "bw")))
            assertEquals("positive",kernel.develop(declared,recipe("auto")).metadata["image_polarity"])
        }
    }
    @Test fun operatorOrderKineticsAndRemainingSilverAreMaterialChangesNotScannerChanges() {
        fun develop(steps: List<Map<String,Any?>>)=kernel.develop(source(),recipe("custom",mapOf("process_steps" to steps)))
        val standard=ProcessSteps.template("color_negative")
        val fixedFirst=listOf(mapOf<String,Any?>("action" to "fix_halide"))+standard
        val normal=develop(standard);val wrong=develop(fixedFirst)
        assertFalse(normal.opticalDensityRgb.values.contentEquals(wrong.opticalDensityRgb.values))
        val bypass=develop(standard.filter { it["action"]!="bleach_silver" })
        assertTrue(bypass.opticalDensityRgb.values[3]>normal.opticalDensityRgb.values[3])
        val cold=kernel.develop(source(),recipe("color_negative",mapOf("temperature_c" to 10.0)))
        val hot=kernel.develop(source(),recipe("color_negative",mapOf("temperature_c" to 30.0)))
        assertFalse(cold.opticalDensityRgb.values.contentEquals(hot.opticalDensityRgb.values))
        assertTrue(PhoneProcessProgram.completion(mapOf("agitation_time_s" to 0.0))<PhoneProcessProgram.completion(mapOf("agitation_time_s" to 480.0)))
        assertTrue(PhoneProcessProgram.completion(mapOf("agitation" to .2))<PhoneProcessProgram.completion(mapOf("agitation" to 2.0)))
        assertThrows(IllegalArgumentException::class.java) { kernel.develop(source(),recipe("custom",mapOf("process_steps" to emptyList<Any>()))) }
        assertThrows(IllegalArgumentException::class.java) { kernel.develop(source(),recipe("color_negative",mapOf("time_min" to 1,"agitation_time_s" to 61))) }
        val broken=ProcessSteps.template("color_reversal").map { if(it["action"]=="activate_remaining_halide") it+("strength" to 0.0) else it }
        assertEquals("negative",develop(broken).metadata["image_polarity"],"a disabled reversal bath cannot force a positive tag")
    }
    @Test fun perLayerPoolConservationRowsAndConsumptionAgreeWithFrozenRecipe() {
        val steps=listOf(mapOf<String,Any?>("action" to "develop_color","strength" to .7),mapOf("action" to "bleach_silver","strength" to .4))
        val program=PhoneProcessProgram.from(mapOf("program_key" to "custom","process_steps" to steps),true,false)!!
        val p=PhoneProcessProgram.Pools(1f,.5f,0f,1f,0f,0f);program.apply(p,true,1f)
        assertEquals(1f,p.halide+p.silver+p.bleached,1e-6f);assertEquals(1f,p.coupler+p.dye,1e-6f)
        val recipe=recipe("color_reversal");val rgb=RgbVolume(5,4,linearRgb=FloatArray(60) { (it%17)/8f })
        val latent=kernel.expose(rgb,emptyMap());val before=latent.arrays.mapValues { it.value.values.copyOf() }
        val expected=kernel.develop(latent,recipe,false)
        before.forEach { (key,value)->assertArrayEquals(value,latent.arrays.getValue(key).values,0f) }
        val copied=LatentState(latent.arrays.mapValues { FloatNd(it.value.shape,it.value.values.copyOf()) },latent.metadata)
        assertArrayEquals(expected.opticalDensityRgb.values,kernel.develop(copied,recipe,true).opticalDensityRgb.values,0f)
        fun row(start: Int)=LatentState(latent.arrays.mapValues { FloatNd(intArrayOf(1,5,it.value.shape[2]),it.value.values.copyOfRange(start*5*it.value.shape[2],(start+1)*5*it.value.shape[2])) },latent.metadata)
        val prepared=kernel.prepareDevelopment(recipe,row(0),4)
        for(y in 3 downTo 0) assertArrayEquals(expected.opticalDensityRgb.values.copyOfRange(y*15,(y+1)*15),prepared.developRowsAt(row(y),y).opticalDensityRgb.values,0f)
    }
}
