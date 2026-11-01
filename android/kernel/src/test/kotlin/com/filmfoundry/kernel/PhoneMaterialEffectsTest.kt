package com.filmfoundry.kernel

import com.filmfoundry.interchange.*
import org.junit.jupiter.api.Assertions.*
import org.junit.jupiter.api.Test

class PhoneMaterialEffectsTest {
    private val kernel=PhoneReducedKernel()
    private val w=47;private val h=31
    private fun identity(frame: Int)=mapOf("roll_id" to "roll-a","frame_index" to frame)
    private fun recipe(grain: Double=.025,frame: Int=1)=mapOf<String,Any?>(
        "film" to mapOf("grain_strength" to grain),"frame_identity" to identity(frame),
        "develop" to mapOf("uneven_development" to .6,"chemical_stain" to .5,"reticulation_strength" to .4))
    private fun source(recipe: Map<String,Any?>)=kernel.expose(RgbVolume(w,h,linearRgb=FloatArray(w*h*3) { .3f }),recipe)
    private fun rows(source: LatentState,start: Int,count: Int)=LatentState(source.arrays.mapValues { (_,a)->
        FloatNd(intArrayOf(count,w,a.shape[2]),a.values.copyOfRange(start*w*a.shape[2],(start+count)*w*a.shape[2])) },source.metadata)

    @Test fun absoluteRowsMatchWholeFrameAndDoNotDependOnCallOrder() {
        val recipe=recipe();val latent=source(recipe)
        val before=latent.arrays.mapValues { it.value.values.copyOf() }
        val expected=kernel.develop(latent,recipe)
        for(step in listOf(1,7,20)) {
            val task=kernel.prepareDevelopment(recipe,rows(latent,0,1),h)
            val actual=FloatArray(w*h*3)
            for(start in (0 until h step step).reversed()) {
                val count=minOf(step,h-start)
                val block=task.developRowsAt(rows(latent,start,count),start)
                block.opticalDensityRgb.values.copyInto(actual,start*w*3)
                assertEquals(expected.metadata,block.metadata)
            }
            assertArrayEquals(expected.opticalDensityRgb.values,actual,0f)
        }
        before.forEach { (key,values)->assertArrayEquals(values,latent.arrays.getValue(key).values,0f) }
        assertEquals("phone_reduced_v0",((expected.metadata["film_process_model"] as Map<*,*>)["execution_implementation"] as Map<*,*>)["profile_id"])
    }

    @Test fun incidentsAreRollFixedAndGrainIsFrameSpecificAndFrozenInOptical() {
        val clean1=recipe(0.0,1);val clean2=recipe(0.0,2)
        val a=kernel.develop(source(clean1),clean1);val b=kernel.develop(source(clean2),clean2)
        assertArrayEquals(a.opticalDensityRgb.values,b.opticalDensityRgb.values,0f)
        val first=recipe(.04,1);val second=recipe(.04,2)
        val optical=kernel.develop(source(first),first)
        val other=kernel.develop(source(second),second)
        assertFalse(optical.opticalDensityRgb.values.contentEquals(other.opticalDensityRgb.values))
        val before=optical.opticalDensityRgb.values.copyOf()
        kernel.observe(optical,mapOf("scanner" to mapOf("scan_saturation" to 1.4)))
        kernel.observe(optical,mapOf("observe_mode" to "viewer_light_table"))
        assertArrayEquals(before,optical.opticalDensityRgb.values,0f)
        assertArrayEquals(a.clearBaseOpticalDensityRgb,optical.clearBaseOpticalDensityRgb,0f)
    }

    @Test fun spectralAmountsAndOverlaySurviveArchiveAndRows() {
        val raw=RecipeShareCodec.decode(javaClass.getResourceAsStream("/spectral_optics_sample.ffc2")!!.bufferedReader().readText())
        val film=(raw["film"] as Map<*,*>).entries.associate { it.key.toString() to it.value }+mapOf("grain_strength" to .025)
        val recipe=recipe()+("film" to film)
        val latent=source(recipe);val whole=kernel.develop(latent,recipe)
        val codec=NpzMediumArchive()
        val roundTrip=codec.readDevelopedOptical(codec.writeDevelopedOptical(DevelopedOpticalArchive(whole.opticalDensityRgb,
            whole.metadata,whole.clearBaseOpticalDensityRgb,whole.spectralComponentAmounts,whole.spectralPostProcessDensityRgb)))
        assertArrayEquals(whole.spectralPostProcessDensityRgb!!.values,roundTrip.spectralPostProcessDensityRgb!!.values,0f)
        val task=kernel.prepareDevelopment(recipe,rows(latent,0,1),h)
        for(start in 0 until h step 7) {
            val count=minOf(7,h-start);val block=task.developRowsAt(rows(latent,start,count),start)
            assertArrayEquals(whole.spectralComponentAmounts!!.values.copyOfRange(start*w*9,(start+count)*w*9),block.spectralComponentAmounts!!.values,0f)
            assertArrayEquals(whole.spectralPostProcessDensityRgb!!.values.copyOfRange(start*w*3,(start+count)*w*3),block.spectralPostProcessDensityRgb!!.values,0f)
        }
    }

    @Test fun newGateObstructionsAreIndependentAndRowStable() {
        fun settings(hair: Boolean,insect: Boolean)=mapOf("model" to PhoneCaptureConditions.MODEL,"camera" to mapOf("seed" to 9,"gate_seed" to 42,"hair" to hair,"insect" to insect))
        for(kind in listOf(true to false,false to true,true to true)) {
            val condition=PhoneCaptureConditions(301,199,settings(kind.first,kind.second))
            val whole=FloatArray(301*199*3) { 1f };condition.apply(whole,0,199)
            assertTrue(whole.any { it<1f })
            val joined=FloatArray(whole.size)
            for(start in 0 until 199 step 17) {
                val count=minOf(17,199-start);val row=FloatArray(301*count*3) { 1f }
                condition.apply(row,start,count);row.copyInto(joined,start*301*3)
            }
            assertArrayEquals(whole,joined,0f)
        }
    }

    @Test fun positiveDefaultDoesNotInvertButExplicitExpertChoiceStillWorks() {
        val optical=OpticalMaster(FloatNd(intArrayOf(1,2,3),floatArrayOf(.1f,.1f,.1f,.8f,.8f,.8f)),mapOf("image_polarity" to "positive"))
        val normal=kernel.observe(optical,mapOf("scanner" to mapOf("scan_normalize" to false)))
        val explicit=kernel.observe(optical,mapOf("scanner" to mapOf("scan_normalize" to false,"invert_transmission" to true)))
        assertTrue(normal.outputRgb.values[0] > normal.outputRgb.values[3])
        assertTrue(explicit.outputRgb.values[0] < explicit.outputRgb.values[3])
        assertEquals(false,normal.metadata["invert_transmission"])
    }
}
