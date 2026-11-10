package com.filmfoundry.kernel

import com.filmfoundry.interchange.*
import org.junit.jupiter.api.Assertions.*
import org.junit.jupiter.api.Test
import kotlin.math.*

class PhoneLightweightFormationTest {
    @Test fun bathParametersTriggerBasicIncidentsAndExplicitOffWins() {
        val r=recipe()+(PhoneFormationProfile.OPTION to PhoneFormationProfile.LIGHTWEIGHT)
        val conditions=mapOf("developer_exhaustion" to .8,"agitation" to .2,"process_steps" to listOf(
            mapOf("action" to "develop_color","temperature_c" to 40.0),mapOf("action" to "fix_halide","temperature_c" to 15.0)))
        val source=expose(r)
        fun effects(develop: Map<String,Any?>): Map<*,*> {
            val out=kernel.develop(source,r+("develop" to develop))
            return (out.metadata["film_process_model"] as Map<*,*>)["phone_material_effects"] as Map<*,*>
        }
        val triggered=effects(conditions)
        for(key in listOf("chemical_stain","uneven_development","reticulation_strength")) assertTrue((triggered[key] as Number).toFloat()>0f,key)
        val off=effects(conditions+mapOf("chemical_stain" to 0.0,"uneven_development" to 0.0,"reticulation_strength" to 0.0))
        for(key in listOf("chemical_stain","uneven_development","reticulation_strength")) assertEquals(0f,off[key])
    }
    private val kernel=PhoneReducedKernel()
    private val w=97; private val h=61
    private fun recipe(spatial: Boolean=false)=mapOf<String,Any?>(
        PhoneProcessModels.OPTION to PhoneProcessModels.CURRENT,
        PhoneMaterialPhenomena.DEVELOPMENT_OPTION to PhoneMaterialPhenomena.CURRENT,
        PhoneGrainModels.EXPOSURE_OPTION to PhoneGrainModels.LAYERED,
        "film" to mapOf("grain_strength" to .05,"grain_scales" to listOf(.003,.01,.02),"grain_scale_weights" to listOf(.7,.2,.1),
            "silver_grain_strength" to .12,"silver_grain_radius" to .018,
            "dir_material_response_matrix" to List(3) { a->List(3) { b->if(spatial && a!=b) .2 else 0.0 } }),
        "develop" to mapOf("program_key" to "color_negative_bleach_bypass","development_adjacency_strength" to if(spatial) .5 else 0.0,
            "chemical_stain" to .2,"uneven_development" to .2,"reticulation_strength" to .15),
        "frame_identity" to mapOf("roll_id" to "lightweight","frame_id" to "one","frame_index" to 1))
    private fun expose(r: Map<String,Any?>)=kernel.expose(RgbVolume(w,h,linearRgb=FloatArray(w*h*3) { .01f+(it%137)/140f }),r)
    private fun rows(a: LatentState,start: Int,count: Int)=LatentState(a.arrays.mapValues { (_,v)->
        FloatNd(intArrayOf(count,w,v.shape[2]),v.values.copyOfRange(start*w*v.shape[2],(start+count)*w*v.shape[2])) },a.metadata)

    @Test fun lightweightSkipsPreparationReadsAndIsExactAcrossReverseBlocksAndArchive() {
        val r=recipe(true)+(PhoneFormationProfile.OPTION to PhoneFormationProfile.LIGHTWEIGHT)
        val latent=expose(r);val before=latent.arrays.mapValues { it.value.values.copyOf() }
        val whole=kernel.develop(latent,r);var reads=0
        val source=object: LatentRows {
            override val width=w;override val height=h
            override fun readRows(start: Int,count: Int): LatentState { reads++;return rows(latent,start,count) }
        }
        kernel.prepareDevelopment(r,rows(latent,0,1),h).bindSource(source).use { bound ->
            assertEquals(0,reads,"Lightweight preparation must not replay chemistry over the source")
            for(start in (0 until h step 7).reversed()) {
                val count=min(7,h-start);val block=bound.developRows(start,count)
                assertArrayEquals(whole.opticalDensityRgb.values.copyOfRange(start*w*3,(start+count)*w*3),block.opticalDensityRgb.values,0f)
                assertEquals(whole.metadata,block.metadata)
            }
        }
        before.forEach { (key,v)->assertArrayEquals(v,latent.arrays.getValue(key).values,0f) }
        val codec=NpzMediumArchive();val saved=codec.readLatent(codec.writeLatent(LatentArchive(latent.arrays,latent.metadata)))
        val resumed=kernel.develop(LatentState(saved.arrays,saved.metadata),r+(PhoneFormationProfile.OPTION to PhoneFormationProfile.DETAILED))
        assertArrayEquals(whole.opticalDensityRgb.values,resumed.opticalDensityRgb.values,0f)
        assertEquals(PhoneFormationProfile.LIGHTWEIGHT,resumed.metadata[PhoneFormationProfile.OPTION])
        val process=whole.metadata["film_process_model"] as Map<*,*>
        assertNotNull(process["formation_budget"])
        assertEquals(false,(process["independent_silver_grain"] as Map<*,*>)["whole_frame_conservation"])
    }

    @Test fun explicitDetailedRetainsOldOutputAndLightweightCannotChangeChemicalInventoryOrScanning() {
        val r=recipe();val latent=expose(r)
        val old=kernel.develop(latent,r)
        val detailed=kernel.develop(latent,r+(PhoneFormationProfile.OPTION to PhoneFormationProfile.DETAILED))
        assertArrayEquals(old.opticalDensityRgb.values,detailed.opticalDensityRgb.values,0f)
        val original=rows(latent,0,h);val reduced=rows(latent,0,h)
        kernel.develop(original,r,true)
        val fast=r+(PhoneFormationProfile.OPTION to PhoneFormationProfile.LIGHTWEIGHT)
        val formed=kernel.develop(reduced,fast,true)
        original.arrays.forEach { (key,v)->assertArrayEquals(v.values,reduced.arrays.getValue(key).values,0f) }
        assertFalse(old.opticalDensityRgb.values.contentEquals(formed.opticalDensityRgb.values))
        val before=formed.opticalDensityRgb.values.copyOf()
        assertArrayEquals(kernel.observe(formed,r).outputRgb.values,kernel.observe(formed,fast).outputRgb.values,0f)
        assertArrayEquals(before,formed.opticalDensityRgb.values,0f)
    }

    @Test fun sharedGrainRetainsDensityResponseMaterialControlsAndNeutralLayerTexture() {
        val r=recipe();val metadata=expose(r).metadata
        val effects=PhoneFormationEffects(r,metadata,w,h,true)
        fun energy(density: Float): Double {
            var sum=0.0
            for(y in 0 until h) for(x in 0 until w) {
                val cache=PhoneFormationEffects.GrainSamples()
                val f=effects.grainFactor(x,y,0,density,cache)
                assertEquals(f,effects.grainFactor(x,y,2,density,cache),0f)
                sum+=(f-1f).toDouble().pow(2)
            }
            return sqrt(sum/(w*h))
        }
        val middle=energy(.48f)
        assertTrue(middle>energy(.02f)*3,"Grain must respond to formed density rather than become a uniform noise overlay")
        assertTrue(middle>energy(.98f)*3)
        val noGrain=PhoneFormationEffects(r,metadata+(PhoneGrainProfile.KEY to ((metadata[PhoneGrainProfile.KEY] as Map<*,*>)+("grain_strength" to 0f))),w,h,true)
        assertEquals(1f,noGrain.grainFactor(12,31,0,.48f))
        val silver=PhoneSilverGrain(r,metadata,w,h,true)
        assertEquals(0f,silver.raw(0f,12,31))
        assertTrue(abs(silver.raw(.1f,12,31)-.1f)!=abs(silver.raw(.8f,12,31)-.8f))
        assertTrue(effects.hasOverlay);assertEquals(.15f,effects.record()["reticulation_strength"])
    }
}
