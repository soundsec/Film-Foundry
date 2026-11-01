package com.filmfoundry.kernel

import com.filmfoundry.interchange.*
import org.junit.jupiter.api.Assertions.*
import org.junit.jupiter.api.Test
import kotlin.math.*

class PhoneSilverGrainTest {
    private val kernel=PhoneReducedKernel();private val w=71;private val h=47
    private fun recipe(steps: Map<String,Any?> = mapOf("program_key" to "bw_negative"),color: Boolean=false)=mapOf(
        PhoneProcessModels.OPTION to PhoneProcessModels.CURRENT,PhoneMaterialPhenomena.DEVELOPMENT_OPTION to PhoneMaterialPhenomena.CURRENT,
        "film" to mapOf("color_process" to if(color) "color_negative" else "bw","grain_strength" to 0.0,"silver_grain_strength" to .12,"silver_grain_radius" to .018,"silver_grain_clump_mix" to .3),
        "develop" to steps,"frame_identity" to mapOf("roll_id" to "silver-test","frame_id" to "one","frame_index" to 1))
    private fun latent(r: Map<String,Any?>)=kernel.expose(RgbVolume(w,h,linearRgb=FloatArray(w*h*3) { .03f+it%79*.016f }),r)
    private fun row(a: LatentState,start: Int,count: Int)=LatentState(a.arrays.mapValues { (_,v)->
        FloatNd(intArrayOf(count,w,v.shape[2]),v.values.copyOfRange(start*w*v.shape[2],(start+count)*w*v.shape[2])) },a.metadata)
    @Test fun silverGrainIsNeutralConservesFullFrameSilverAndCannotRewriteChemicalPools() {
        val r=recipe();val a=latent(r);val before=a.arrays.mapValues { it.value.values.copyOf() }
        val base=kernel.develop(a,r-PhoneMaterialPhenomena.DEVELOPMENT_OPTION);val out=kernel.develop(a,r)
        assertFalse(base.opticalDensityRgb.values.contentEquals(out.opticalDensityRgb.values))
        var baseTotal=0.0;var grainTotal=0.0
        for(i in 0 until w*h) {
            val delta=out.opticalDensityRgb.values[i*3]-base.opticalDensityRgb.values[i*3]
            for(c in 1..2) assertEquals(delta,out.opticalDensityRgb.values[i*3+c]-base.opticalDensityRgb.values[i*3+c],2e-7f)
            baseTotal+=base.opticalDensityRgb.values[i*3]-base.clearBaseOpticalDensityRgb!![0]
            grainTotal+=out.opticalDensityRgb.values[i*3]-out.clearBaseOpticalDensityRgb!![0]
        }
        assertEquals(baseTotal,grainTotal,baseTotal*2e-6)
        before.forEach { (key,v)->assertArrayEquals(v,a.arrays.getValue(key).values,0f) }
        val consumed=row(a,0,h);val oldConsumed=row(a,0,h)
        kernel.develop(consumed,r,true);kernel.develop(oldConsumed,r-PhoneMaterialPhenomena.DEVELOPMENT_OPTION,true)
        consumed.arrays.forEach { (key,v)->assertArrayEquals(oldConsumed.arrays.getValue(key).values,v.values,0f) }
        assertArrayEquals(base.clearBaseOpticalDensityRgb,out.clearBaseOpticalDensityRgb,0f)
    }
    @Test fun fullBleachingSkipsSilverPreparationAndLeavesDyeAndObservationUnchanged() {
        val r=recipe(mapOf("program_key" to "color_negative"),true);val a=latent(r)
        var reads=0;val source=object: LatentRows {
            override val width=w;override val height=h
            override fun readRows(start: Int,count: Int): LatentState { reads++;return row(a,start,count) }
        }
        val prepared=kernel.prepareDevelopment(r,row(a,0,1),h);val bound=prepared.bindSource(source)
        assertEquals(0,reads)
        val out=bound.use { it.developRows(0,h) };assertEquals(1,reads)
        val base=kernel.develop(a,r-PhoneMaterialPhenomena.DEVELOPMENT_OPTION)
        assertArrayEquals(base.opticalDensityRgb.values,out.opticalDensityRgb.values,0f)
        val record=(out.metadata["film_process_model"] as Map<*,*>)["independent_silver_grain"] as Map<*,*>
        assertEquals(false,record["applied"]);assertEquals("no_retained_silver",record["reason"])
        val before=out.opticalDensityRgb.values.copyOf();kernel.observe(out,r);assertArrayEquals(before,out.opticalDensityRgb.values,0f)
    }
    @Test fun silverWithAdjacencyAndDirIsExactAcrossReverseRowsAndArchiveRoundtrip() {
        val initial=recipe(mapOf("program_key" to "color_negative_bleach_bypass","development_adjacency_strength" to .6),true)
        val film=(initial["film"] as Map<String,Any?>)+("dir_material_response_matrix" to listOf(listOf(0.0,.3,0.0),listOf(0.0,0.0,0.0),listOf(0.0,0.0,0.0)))
        val r=initial+("film" to film);val a=latent(r);val whole=kernel.develop(a,r)
        val source=object: LatentRows {
            override val width=w;override val height=h
            override fun readRows(start: Int,count: Int)=row(a,start,count)
        }
        kernel.prepareDevelopment(r,row(a,0,1),h).bindSource(source).use { bound ->
            for(start in (0 until h step 5).reversed()) {
                val count=min(5,h-start);val out=bound.developRows(start,count)
                assertEquals(whole.metadata,out.metadata)
                assertArrayEquals(whole.opticalDensityRgb.values.copyOfRange(start*w*3,(start+count)*w*3),out.opticalDensityRgb.values,0f)
            }
        }
        val codec=NpzMediumArchive();val saved=codec.readLatent(codec.writeLatent(LatentArchive(a.arrays,a.metadata)))
        assertArrayEquals(whole.opticalDensityRgb.values,kernel.develop(LatentState(saved.arrays,saved.metadata),r).opticalDensityRgb.values,0f)
    }
    @Test fun spectralSilverAmountIsConservedAndDyeIsSeparate() {
        val r0=recipe(mapOf("program_key" to "color_negative_bleach_bypass"),true)
        val material=mapOf("model" to "normalized_pool_absorbance_v1","profile_id" to "silver_fixture","coefficient_unit" to "normalized_final_pool_v1",
            "component_names" to SpectralOptics.components,"wavelengths_nm" to listOf(400,550,700),"component_absorbance" to List(9) { k->List(3) { .05+k*.03 } })
        val r=r0+("film" to ((r0["film"] as Map<String,Any?>)+(SpectralOptics.MATERIAL_KEY to material)))
        val a=latent(r);val base=kernel.develop(a,r-PhoneMaterialPhenomena.DEVELOPMENT_OPTION);val grain=kernel.develop(a,r)
        val b=base.spectralComponentAmounts!!.values;val g=grain.spectralComponentAmounts!!.values
        for(i in g.indices) if(i%9!=3) assertEquals(b[i],g[i],0f)
        val before=(3 until b.size step 9).sumOf { b[it].toDouble() };val after=(3 until g.size step 9).sumOf { g[it].toDouble() }
        assertEquals(before,after,before*2e-6)
        assertFalse((3 until b.size step 9).all { b[it]==g[it] })
    }
    @Test fun noRetainedSilverAndExplicitOffCannotCreateGrainAndInvalidParametersFail() {
        val r=recipe(mapOf("process_steps" to listOf(mapOf("action" to "fix_halide"))));val a=latent(r)
        assertArrayEquals(kernel.develop(a,r-PhoneMaterialPhenomena.DEVELOPMENT_OPTION).opticalDensityRgb.values,kernel.develop(a,r).opticalDensityRgb.values,0f)
        val active=recipe();val b=latent(active)
        assertArrayEquals(kernel.develop(b,active-PhoneMaterialPhenomena.DEVELOPMENT_OPTION).opticalDensityRgb.values,kernel.develop(b,active+("enable_grain" to false)).opticalDensityRgb.values,0f)
        assertThrows(IllegalArgumentException::class.java) { kernel.develop(b,active+("film" to mapOf("silver_grain_radius" to -1))) }
    }
    @Test fun normalizationCancellationDoesNotConsumeBorrowedSources() {
        val r=recipe();val a=latent(r);val before=a.arrays.mapValues { it.value.values.copyOf() };var checks=0
        val source=object: LatentRows {
            override val width=w;override val height=h
            override fun readRows(start: Int,count: Int)=a.also { require(start==0 && count==h) }
            override fun checkActive() { if(++checks==5) error("cancelled") }
        }
        assertThrows(IllegalStateException::class.java) { kernel.prepareDevelopment(r,row(a,0,1),h).bindSource(source) }
        before.forEach { (key,v)->assertArrayEquals(v,a.arrays.getValue(key).values,0f) }
    }
}
