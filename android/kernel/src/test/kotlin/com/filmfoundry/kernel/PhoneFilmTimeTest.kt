package com.filmfoundry.kernel

import com.filmfoundry.interchange.*
import org.junit.jupiter.api.Assertions.*
import org.junit.jupiter.api.Test

class PhoneFilmTimeTest {
    private val kernel=PhoneReducedKernel()
    private val response=mapOf("model" to "film_time_response_v2")
    private fun history(kind: String)=mapOf("model" to "film_${kind}_v1","segments" to listOf(
        if(kind=="residual_light")mapOf("duration_hours" to 10.0,"relative_illuminance" to 1.0,"light_rgb" to listOf(1.0,1.0,1.0))
        else if(kind=="storage_history")mapOf("duration_days" to 3650.0,"temperature_c" to 20.0,"relative_humidity" to .5)
        else mapOf("duration_hours" to 2400.0,"temperature_c" to 20.0,"relative_humidity" to .5)))
    private fun recipe(film: Map<String,Any?> = emptyMap())=mapOf<String,Any?>(
        PhoneFormationProfile.OPTION to PhoneFormationProfile.LIGHTWEIGHT,
        PhoneProcessModels.OPTION to PhoneProcessModels.CURRENT,
        "film" to (mapOf("time_response" to response,"grain_strength" to 0.0)+film),
        "develop" to mapOf("program_key" to "custom","process_steps" to listOf(mapOf("action" to "develop_silver","strength" to .5))))
    private val rgb=RgbVolume(3,2,linearRgb=FloatArray(18) {if(it<3)0f else .8f})
    @Test fun storageAndReciprocityApplyBeforeCurveAndFreezeOnCapture() {
        val r=recipe(mapOf("storage_history" to history("storage_history"),"reciprocity_response" to mapOf("model" to "film_reciprocity_response_v1")))+
            ("exposure" to mapOf("duration_seconds" to 32.0))
        val aged=kernel.expose(rgb,r);val plain=kernel.expose(rgb,recipe())
        val a=aged.arrays.getValue("developability").values;val b=plain.arrays.getValue("developability").values
        assertTrue(a[0]>b[0]);assertTrue(a[3]<b[3]);assertNotNull(aged.metadata["phone_exposure_time"])
        val old=r+(PhoneFormationProfile.OPTION to PhoneFormationProfile.PREVIOUS_LIGHTWEIGHT)
        assertArrayEquals(b,kernel.expose(rgb,old).arrays.getValue("developability").values,0f)
        val saved=NpzMediumArchive().let {it.readLatent(it.writeLatent(LatentArchive(aged.arrays,aged.metadata)))}
        assertEquals(PhoneFormationProfile.LIGHTWEIGHT,saved.metadata[PhoneFormationProfile.OPTION])
        val out=kernel.develop(LatentState(saved.arrays,saved.metadata),recipe(mapOf("time_response" to null)))
        assertEquals(PhoneFormationProfile.LIGHTWEIGHT,out.metadata[PhoneFormationProfile.OPTION])
    }
    @Test fun waitingAndResidualLightRespectInventoryRowsAndReadOnlyScan() {
        val r=recipe();val latent=kernel.expose(rgb,r);val before=latent.arrays.mapValues {it.value.values.copyOf()}
        val d=(r["develop"] as Map<*,*>).entries.associate {it.key.toString() to it.value}
        val timed=r+("develop" to (d+mapOf("latent_wait" to history("latent_wait"),"post_process_light" to history("residual_light"))))
        val whole=kernel.develop(latent,timed)
        assertFalse(whole.opticalDensityRgb.values.contentEquals(kernel.develop(latent,r).opticalDensityRgb.values))
        for(y in 0..1) {
            val row=LatentState(latent.arrays.mapValues {(_,a)->val stride=3*a.shape[2];FloatNd(intArrayOf(1,3,a.shape[2]),a.values.copyOfRange(y*stride,(y+1)*stride))},latent.metadata)
            assertArrayEquals(whole.opticalDensityRgb.values.copyOfRange(y*9,(y+1)*9),kernel.prepareDevelopment(timed,row,2).developRowsAt(row,y).opticalDensityRgb.values,0f)
        }
        before.forEach {(k,v)->assertArrayEquals(v,latent.arrays.getValue(k).values,0f)}
        val pools=PhoneProcessProgram.Pools(1f,.8f,0f,1f,0f,.4f)
        PhoneFilmTime(mapOf("time_response" to response),emptyMap(),mapOf("post_process_light" to history("residual_light")),1).finish(pools,0)
        assertEquals(1.4f,pools.halide+pools.bleached+pools.silver,1e-6f);assertEquals(0f,pools.dye)
        val density=whole.opticalDensityRgb.values.copyOf();kernel.observe(whole,timed);assertArrayEquals(density,whole.opticalDensityRgb.values,0f)
    }
    @Test fun oldFrozenFastProfileDoesNotAdoptNewTimeAndBadClockFails() {
        val r=recipe()+(PhoneFormationProfile.OPTION to PhoneFormationProfile.PREVIOUS_LIGHTWEIGHT)
        val latent=kernel.expose(rgb,r)
        val timed=recipe()+("develop" to mapOf("program_key" to "bw_negative","latent_wait" to history("latent_wait")))
        val out=kernel.develop(latent,timed)
        assertEquals(PhoneFormationProfile.PREVIOUS_LIGHTWEIGHT,out.metadata[PhoneFormationProfile.OPTION])
        assertNull((out.metadata["film_process_model"] as Map<*,*>)["phone_film_time"])
        assertThrows(IllegalArgumentException::class.java) {kernel.expose(rgb,recipe()+("exposure" to mapOf("duration_seconds" to -1.0)))}
    }
}
