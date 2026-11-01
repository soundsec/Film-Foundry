package com.filmfoundry.kernel

import com.filmfoundry.interchange.*
import org.junit.jupiter.api.Assertions.*
import org.junit.jupiter.api.Test

class PhoneBathInteractionTest {
    private val kernel=PhoneReducedKernel()
    private val pixels=RgbVolume(31,19,linearRgb=FloatArray(31*19*3) { .001f+(it%27)*.13f })
    private val film=mapOf<String,Any?>("grain_strength" to .04,"grain_scales" to listOf(.004,.02,.06),"grain_scale_weights" to listOf(.7,.22,.08),
        "cross_process_layer_balance" to listOf(.7,1.15,.9),"cross_process_dye_coupling" to .8,"cross_process_dye_stability" to .85)
    private fun recipe(develop: Map<String,Any?>,stock: Map<String,Any?> = film)=mapOf("film" to stock,"develop" to (mapOf("program_key" to "auto")+develop),PhoneProcessModels.OPTION to PhoneProcessModels.CURRENT)
    @Test fun crossedStockAndDeveloperChangeMaterialPoolsAndGrainWhileObservationStaysReadOnly() {
        val latent=kernel.expose(pixels,mapOf("film" to film));val before=latent.arrays.mapValues { it.value.values.copyOf() }
        val native=kernel.develop(latent,recipe(emptyMap()));val cross=kernel.develop(latent,recipe(mapOf("chemistry" to "cross")))
        assertEquals("positive",cross.metadata["image_polarity"])
        assertFalse(native.opticalDensityRgb.values.contentEquals(cross.opticalDensityRgb.values))
        val neutral=kernel.develop(latent,recipe(mapOf("chemistry" to "cross"),film-"cross_process_layer_balance"-"cross_process_dye_coupling"-"cross_process_dye_stability"))
        assertFalse(neutral.opticalDensityRgb.values.contentEquals(cross.opticalDensityRgb.values))
        val fine=kernel.develop(latent,recipe(mapOf("developer_type" to "fine_grain")))
        val push=kernel.develop(latent,recipe(mapOf("developer_type" to "push","push_stops" to 1.0)))
        assertFalse(fine.opticalDensityRgb.values.contentEquals(push.opticalDensityRgb.values))
        val opticalBefore=cross.opticalDensityRgb.values.copyOf();repeat(2) { kernel.observe(cross,emptyMap()) }
        assertArrayEquals(opticalBefore,cross.opticalDensityRgb.values,0f)
        before.forEach { (key,value)->assertArrayEquals(value,latent.arrays.getValue(key).values,0f) }
        val sourcePositive=kernel.expose(pixels,mapOf("film" to (film+("image_polarity" to "positive"))))
        assertEquals("negative",kernel.develop(sourcePositive,recipe(mapOf("chemistry" to "cross"),film+("image_polarity" to "positive"))).metadata["image_polarity"])
    }
    @Test fun exhaustionFixingTemperatureJumpsAndExplicitCleanAreFrozenConditionsNotScannerEffects() {
        val latent=kernel.expose(pixels,mapOf("film" to film))
        val clean=kernel.develop(latent,recipe(emptyMap()))
        val stale=kernel.develop(latent,recipe(mapOf("developer_exhaustion" to .8,"fixer_exhaustion" to .8)))
        fun effects(master: OpticalMaster)=(master.metadata["film_process_model"] as Map<*,*>)["phone_material_effects"] as Map<*,*>
        assertTrue((effects(stale)["chemical_stain"] as Number).toDouble()>0)
        assertFalse(clean.opticalDensityRgb.values.contentEquals(stale.opticalDensityRgb.values))
        val steps=ProcessSteps.template("color_negative").mapIndexed { i,step->step+("temperature_c" to if(i==0) 40.0 else 10.0) }
        val shocked=kernel.develop(latent,recipe(mapOf("process_steps" to steps)))
        assertTrue((effects(shocked)["reticulation_strength"] as Number).toDouble()>0)
        val suppressed=kernel.develop(latent,recipe(mapOf("process_steps" to steps,"reticulation_strength" to 0.0,"chemical_stain" to 0.0,"uneven_development" to 0.0)))
        assertEquals(0.0,(effects(suppressed)["reticulation_strength"] as Number).toDouble())
        assertThrows(IllegalArgumentException::class.java) { kernel.develop(latent,recipe(emptyMap(),film+("cross_process_layer_balance" to listOf(1.0,-.1,1.0)))) }
    }
    @Test fun newLayerResponseAndInteractionStayVersionedAndAbsoluteRowsAgree() {
        val stock=film+mapOf("hd_gamma" to listOf(.8,.65,.5),"log_exposure_toe" to listOf(-2.0,-2.2,-2.5),"log_exposure_shoulder" to listOf(.6,.3,.1),"density_max" to listOf(2.0,1.8,1.5))
        val modern=recipe(mapOf("developer_type" to "compensating"),stock)
        val old=modern-PhoneProcessModels.OPTION
        val a=kernel.expose(pixels,modern);val b=kernel.expose(pixels,old)
        assertFalse(a.arrays.getValue("developability").values.contentEquals(b.arrays.getValue("developability").values))
        assertNull(b.metadata["phone_layer_response"])
        val whole=kernel.develop(a,modern)
        val components=((whole.metadata["film_process_model"] as Map<*,*>)["execution_implementation"] as Map<*,*>)["component_models"] as Map<*,*>
        assertEquals("phone_stock_logistic_v1",components["expose"])
        assertEquals(PhoneProcessModels.CURRENT,components["develop"])
        fun row(y: Int)=LatentState(a.arrays.mapValues { (_,v)->FloatNd(intArrayOf(1,31,v.shape[2]),v.values.copyOfRange(y*31*v.shape[2],(y+1)*31*v.shape[2])) },a.metadata)
        val prepared=kernel.prepareDevelopment(modern,row(0),19)
        for(y in 18 downTo 0) assertArrayEquals(whole.opticalDensityRgb.values.copyOfRange(y*93,(y+1)*93),prepared.developRowsAt(row(y),y).opticalDensityRgb.values,0f)
    }
}
