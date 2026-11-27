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
    @Test fun bathDoseDevelopsFogAndChangesContrastOnTheSameLatent() {
        val wedge=floatArrayOf(.016f,.18f,1f)
        val frame=RgbVolume(3,1,linearRgb=FloatArray(9) { wedge[it/3] })
        for(profile in listOf(PhoneFormationProfile.STOCK_LIGHTWEIGHT,PhoneFormationProfile.LIGHTWEIGHT,
            PhoneFormationProfile.PREVIOUS_STOCK_DETAILED,PhoneFormationProfile.STOCK_DETAILED)) {
            val base=recipe(emptyMap(),emptyMap())+(PhoneFormationProfile.OPTION to profile)
            val latent=kernel.expose(frame,base)
            fun optical(minutes: Int)=kernel.develop(latent,base+("develop" to
                mapOf("program_key" to "color_negative","time_min" to minutes))).opticalDensityRgb.values
            val normal=optical(8);val long=optical(16)
            if(PhoneFormationProfile.hasBathDose(profile)) {
                assertTrue(long[0]<normal[0],"longer development increases shadow separation")
                assertTrue(long[6]>=normal[6],"bright exposure must not lose density from an unpivoted gamma")
            } else assertArrayEquals(normal,long,0f)
        }
        fun program(susceptibility: Double)=PhoneProcessProgram.from(mapOf("program_key" to "color_reversal",
            "time_min" to 16,"compensation" to .4),true,true,mapOf("chemical_fog_susceptibility" to susceptibility),true,true,true,true)!!
        val short=PhoneProcessProgram.from(mapOf("program_key" to "color_negative","time_min" to 4),
            true,false,interactive=true,doseResponse=true)!!.record()["effective_fractions"] as List<*>
        assertTrue((short[0] as Float) < 1f);assertEquals(listOf(1f,1f,1f),short.drop(1))
        val retained=PhoneProcessProgram.from(mapOf("program_key" to "color_negative","silver_bleach_completion" to 0.0),
            true,false,interactive=true,doseResponse=true)!!
        assertTrue("metallic_silver" in retained.imageComponents(true))
        assertFalse("metallic_silver" in PhoneProcessProgram.from(mapOf("program_key" to "color_negative"),
            true,false,interactive=true,doseResponse=true)!!.imageComponents(true))
        val record=program(1.0).record()
        assertEquals(1f,(record["effective_gamma"] as List<*>)[2])
        assertEquals(0f,(record["effective_compensation"] as List<*>)[2])
        fun firstFog(s: Double): Float {
            val projection=PhoneProcessProgram.Projection()
            program(s).apply(PhoneProcessProgram.Pools(1f,0f,0f,1f,0f,0f),true,1f,projection=projection)
            return projection.firstSilver
        }
        assertEquals(0f,firstFog(0.0));assertTrue(firstFog(1.0)>0f)
    }

    @Test fun stockCurveReproducesDesktopLatentWedgeOnNewRolls() {
        val stock = mapOf("hd_gamma" to List(3) { .72 }, "density_min" to List(3) { .08 },
            "density_max" to List(3) { 1.88 }, "log_exposure_toe" to List(3) { -2.2 },
            "log_exposure_shoulder" to List(3) { .5 }, "hd_toe_width" to .17, "hd_shoulder_width" to .24,
            "layer_sensitivity_matrix" to List(3) { y -> List(3) { x -> if (x == y) 1.0 else 0.0 } })
        val wedge = floatArrayOf(0f, .016f, .18f, 1f, 2f)
        val frame = RgbVolume(wedge.size, 1, linearRgb=FloatArray(wedge.size * 3) { wedge[it / 3] })
        val common = recipe(emptyMap(), stock)
        // Clear Modern Negative, measured at the desktop material.expose() boundary.
        val reference = floatArrayOf(0f, .16767663f, .58158672f, .86873454f, .96564215f)
        for (profile in listOf(PhoneFormationProfile.LIGHTWEIGHT, PhoneFormationProfile.STOCK_DETAILED)) {
            val latent = kernel.expose(frame, common + (PhoneFormationProfile.OPTION to profile))
            assertEquals("phone_stock_hd_v1", latent.metadata["phone_layer_response"])
            val actual = latent.arrays.getValue("developability").values
            for (i in actual.indices) assertEquals(reference[i / 3], actual[i], 1e-5f)
        }
    }
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
