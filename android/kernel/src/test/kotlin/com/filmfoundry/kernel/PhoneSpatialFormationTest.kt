package com.filmfoundry.kernel

import com.filmfoundry.interchange.*
import org.junit.jupiter.api.Assertions.*
import org.junit.jupiter.api.Test
import kotlin.math.*

class PhoneSpatialFormationTest {
    private val kernel=PhoneReducedKernel()
    private val w=97;private val h=71
    private fun source(values: FloatArray): LinearRgbRows=object: LinearRgbRows {
        override val width=w;override val height=h
        override fun readRows(start: Int,count: Int)=RgbVolume(w,count,linearRgb=values.copyOfRange(start*w*3,(start+count)*w*3))
    }
    private fun recipe()=mapOf<String,Any?>("film" to mapOf("emulsion_mtf_strength" to .35,"emulsion_blur_radius" to .025,
        "halation_strength" to .6,"halation_core_radius" to .03,"halation_outer_radius" to .08,
        "grain_strength" to .03,"grain_scales" to listOf(.01,.025,.06),"grain_scale_weights" to listOf(.58,.3,.12)),
        "frame_identity" to mapOf("roll_id" to "roll","frame_index" to 1))
    private fun pixels()=FloatArray(w*h*3) { i -> if(i/3 == (h/2)*w+w/2) 16f else if((i/3)%w>w/2) .4f else .02f }
    private fun slice(latent: LatentState,start: Int,count: Int)=LatentState(latent.arrays.mapValues { (_,a)->
        FloatNd(intArrayOf(count,w,a.shape[2]),a.values.copyOfRange(start*w*a.shape[2],(start+count)*w*a.shape[2])) },latent.metadata)

    @Test fun disabledEffectsAndZeroMtfRadiusKeepIdentityAndInvalidProfilesFailAtPreparation() {
        val rgb=RgbVolume(w,h,linearRgb=pixels())
        val base=kernel.expose(rgb,emptyMap())
        val off=recipe()+mapOf("enable_mtf" to false,"enable_halation" to false,"enable_grain" to false)
        val latent=kernel.expose(rgb,off)
        for(key in base.arrays.keys) assertArrayEquals(base.arrays.getValue(key).values,latent.arrays.getValue(key).values,0f)
        assertArrayEquals(kernel.develop(base,emptyMap()).opticalDensityRgb.values,kernel.develop(latent,off).opticalDensityRgb.values,0f)
        val zero=kernel.expose(rgb,mapOf("film" to mapOf("emulsion_mtf_strength" to 1.0,"emulsion_blur_radius" to 0.0)))
        for(key in base.arrays.keys) assertArrayEquals(base.arrays.getValue(key).values,zero.arrays.getValue(key).values,0f)
        assertThrows(IllegalArgumentException::class.java) { kernel.prepareExposure(mapOf("film" to mapOf("grain_scales" to listOf(.01),"grain_scale_weights" to listOf(-1))),w,h) }
        assertThrows(IllegalArgumentException::class.java) { kernel.prepareExposure(mapOf("film" to mapOf("emulsion_mtf_strength" to 1.0,"emulsion_blur_radius" to .05)),10000,10000) }
    }

    @Test fun spatialSourceIsRowOrderIndependentAndNoInputIsChanged() {
        val rgb=pixels();val before=rgb.copyOf();val recipe=recipe()
        val whole=kernel.expose(RgbVolume(w,h,linearRgb=rgb),recipe)
        for(step in listOf(1,9,h)) {
            val prepared=kernel.prepareExposure(recipe,w,h)
            val joined=whole.arrays.mapValues { FloatArray(it.value.values.size) }
            prepared.bindSource(source(rgb)).use { task ->
                for(start in (0 until h step step).reversed()) {
                    val n=min(step,h-start);val block=task.exposeRows(start,n)
                    assertEquals(whole.metadata,block.metadata)
                    for((key,array) in block.arrays) array.values.copyInto(joined.getValue(key),start*w*array.shape[2])
                }
            }
            for((key,array) in whole.arrays) assertArrayEquals(array.values,joined.getValue(key),0f)
        }
        assertArrayEquals(before,rgb,0f)
    }

    @Test fun mtfPreservesFlatFieldsAndSpreadsEdgesAndPointEnergy() {
        val recipe=mapOf("film" to mapOf("emulsion_mtf_strength" to 1.0,"emulsion_blur_radius" to .04))
        val optics=PhoneExposureOptics(recipe,w,h,1f)
        val flat=FloatArray(w*h*3) { .25f }
        optics.bind(source(flat)).use { assertArrayEquals(flat,it.readRows(0,h).linearRgb,0f) }
        val point=FloatArray(w*h*3);val center=((h/2)*w+w/2)*3
        for(c in 0..2) point[center+c]=1f
        val blurred=optics.bind(source(point)).use { it.readRows(0,h).linearRgb }
        assertTrue(blurred[center] in 0f..0.5f);assertTrue(blurred[center+3]>0f)
        for(c in 0..2) assertEquals(1.0,(c until blurred.size step 3).sumOf { blurred[it].toDouble() },1e-6)
        val edge=FloatArray(w*h*3) { if((it/3)%w>=w/2) 1f else 0f }
        val soft=optics.bind(source(edge)).use { it.readRows(0,h).linearRgb }
        assertTrue(soft[center-3] in .001f..0.999f);assertTrue(soft.all { it in 0f..1f })
    }

    @Test fun halationUsesHighlightsHasRedTailAndDoesNotFogUniformLight() {
        val recipe=mapOf("film" to mapOf("halation_strength" to 1.0,"halation_outer_radius" to .08,"halation_core_radius" to .025))
        val optics=PhoneExposureOptics(recipe,w,h,1f)
        for(v in listOf(.1f,2f)) {
            val flat=FloatArray(w*h*3) { v }
            val output=optics.bind(source(flat)).use { it.readRows(0,h).linearRgb }
            assertArrayEquals(flat,output,0f)
        }
        val point=FloatArray(w*h*3);val center=((h/2)*w+w/2)*3
        for(c in 0..2) point[center+c]=20f
        val output=optics.bind(source(point)).use { it.readRows(0,h).linearRgb }
        val tail=center+5*3
        assertTrue(output[tail]>output[tail+1] && output[tail+1]>output[tail+2] && output[tail+2]>0)
        assertTrue(output[center]>=20f)
        val below=FloatArray(w*h*3);for(c in 0..2)below[center+c]=.2f
        assertArrayEquals(below,optics.bind(source(below)).use { it.readRows(0,h).linearRgb },0f)
    }

    @Test fun grainProfileIsFrozenChangesSizeAndHasMidtoneResponse() {
        val recipe=recipe()+("frame_identity" to mapOf("roll_id" to "roll","frame_index" to 1L))
        val rgb=FloatArray(w*h*3) { .35f };val latent=kernel.expose(RgbVolume(w,h,linearRgb=rgb),recipe)
        val expected=kernel.develop(latent,recipe)
        val codec=NpzMediumArchive();val saved=codec.readLatent(codec.writeLatent(LatentArchive(latent.arrays,latent.metadata)))
        assertArrayEquals(expected.opticalDensityRgb.values,kernel.develop(LatentState(saved.arrays,saved.metadata),recipe).opticalDensityRgb.values,0f)
        val modified=recipe+("film" to mapOf("grain_strength" to .2,"grain_scales" to listOf(.08,.09,.1)))
        assertArrayEquals(expected.opticalDensityRgb.values,kernel.develop(latent,modified).opticalDensityRgb.values,0f)
        val process=expected.metadata["film_process_model"] as Map<*,*>
        assertEquals(PhoneGrainProfile.MODEL,((process["execution_implementation"] as Map<*,*>)["component_models"] as Map<*,*>)["grain"])
        val small=PhoneFormationEffects(recipe,latent.metadata,w,h)
        val profile=(latent.metadata[PhoneGrainProfile.KEY] as Map<*,*>).entries.associate { it.key.toString() to it.value }
        val large=PhoneFormationEffects(recipe,latent.metadata+(PhoneGrainProfile.KEY to (profile+("size_multiplier" to 2.0))),w,h)
        var fineEdge=0.0;var largeEdge=0.0;var mid=0.0;var dark=0.0
        for(y in 0 until h) for(x in 1 until w) {
            val a=small.grainFactor(x,y,0,.48f);val b=large.grainFactor(x,y,0,.48f)
            fineEdge+=abs(a-small.grainFactor(x-1,y,0,.48f));largeEdge+=abs(b-large.grainFactor(x-1,y,0,.48f))
            mid+=(a-1.0).pow(2);dark+=(small.grainFactor(x,y,0,.01f)-1.0).pow(2)
        }
        assertTrue(largeEdge<fineEdge);assertTrue(mid>dark)
        val bound=kernel.prepareDevelopment(recipe,slice(latent,0,1),h)
        for(start in 0 until h step 11) {
            val n=min(11,h-start);val actual=bound.developRowsAt(slice(latent,start,n),start)
            assertArrayEquals(expected.opticalDensityRgb.values.copyOfRange(start*w*3,(start+n)*w*3),actual.opticalDensityRgb.values,0f)
        }
    }

    @Test fun frozenOpticsMetadataSurvivesSpectralDevelopmentAndObservingDoesNotReapplyIt() {
        val original=RecipeShareCodec.decode(javaClass.getResourceAsStream("/spectral_optics_sample.ffc2")!!.bufferedReader().readText())
        val film=(original["film"] as Map<*,*>).entries.associate { it.key.toString() to it.value }+ (recipe()["film"] as Map<String,Any?>)
        val recipe=original+mapOf("film" to film,"enable_mtf" to true,"enable_halation" to true,"enable_grain" to true)
        val latent=kernel.expose(RgbVolume(w,h,linearRgb=pixels()),recipe)
        val codec=NpzMediumArchive();val saved=codec.readLatent(codec.writeLatent(LatentArchive(latent.arrays,latent.metadata)))
        val optical=kernel.develop(LatentState(saved.arrays,saved.metadata),recipe)
        val before=optical.opticalDensityRgb.values.copyOf();val components=optical.spectralComponentAmounts!!.values.copyOf()
        val changed=recipe+("film" to (film+mapOf("emulsion_mtf_strength" to 0.0,"halation_strength" to 0.0)))
        assertArrayEquals(before,kernel.develop(LatentState(saved.arrays,saved.metadata),changed).opticalDensityRgb.values,0f)
        kernel.observe(optical,recipe);kernel.observe(optical,recipe)
        assertArrayEquals(before,optical.opticalDensityRgb.values,0f);assertArrayEquals(components,optical.spectralComponentAmounts!!.values,0f)
        val process=optical.metadata["film_process_model"] as Map<*,*>
        assertEquals("phone_exposure_optics_v1",(process["exposure_optics"] as Map<*,*>)["model"])
    }

    @Test fun archivedGrainVersionsKeepTheirHistoricalValuesAndReportTheirActualModel() {
        val identity=mapOf("roll_id" to "compat","frame_id" to "frame","frame_index" to 1L)
        val recipe=mapOf("film" to mapOf("grain_strength" to .04,"grain_scales" to listOf(.01,.025,.06),"grain_scale_weights" to listOf(.58,.3,.12)))
        val profile=PhoneGrainProfile.fromRecipe(recipe+(PhoneGrainModels.EXPOSURE_OPTION to PhoneGrainModels.PREVIOUS))!!
        val v1=PhoneFormationEffects(recipe,mapOf("frame_identity" to identity),47,31)
        val v2=PhoneFormationEffects(recipe,mapOf("frame_identity" to identity,PhoneGrainProfile.KEY to profile),47,31)
        // Historical v1/v2 formula reference samples; no image/file checksums.
        val points=listOf(Triple(3,4,0),Triple(13,9,2),Triple(29,18,1))
        val amounts=floatArrayOf(.48f,.1f,.75f)
        val old1=floatArrayOf(1.03774536f,1.00733531f,.84063894f)
        val old2=floatArrayOf(1.04000568f,.95203787f,1.00579989f)
        points.forEachIndexed { i,(x,y,layer) ->
            assertEquals(old1[i],v1.grainFactor(x,y,layer,amounts[i]),1e-7f)
            assertEquals(old2[i],v2.grainFactor(x,y,layer,amounts[i]),1e-7f)
        }
        assertEquals("phone_density_cloud_v1",v1.record()["grain"])
        assertEquals(PhoneGrainModels.PREVIOUS,v2.record()["grain"])
        val current=PhoneGrainProfile.fromRecipe(recipe)!!
        val v3=PhoneFormationEffects(recipe,mapOf("frame_identity" to identity,PhoneGrainProfile.KEY to current),47,31)
        assertEquals(PhoneGrainModels.CURRENT,v3.record()["grain"])
        assertNotEquals(v2.grainFactor(13,9,2,.1f),v3.grainFactor(13,9,2,.1f))
        val latent=kernel.expose(RgbVolume(w,h,linearRgb=pixels()),recipe+(PhoneGrainModels.EXPOSURE_OPTION to PhoneGrainModels.PREVIOUS))
        val whole=kernel.develop(latent,recipe);val bound=kernel.prepareDevelopment(recipe,slice(latent,0,1),h)
        for(start in (0 until h step 13).reversed()) {
            val n=min(13,h-start)
            assertArrayEquals(whole.opticalDensityRgb.values.copyOfRange(start*w*3,(start+n)*w*3),bound.developRowsAt(slice(latent,start,n),start).opticalDensityRgb.values,0f)
        }
    }

    @Test fun clumpGrainKeepsANeutralMeanAndPutsColorOnLargerScales() {
        fun effects(scale: Double): PhoneFormationEffects {
            val film=mapOf("grain_strength" to .08,"grain_scales" to listOf(scale),"grain_scale_weights" to listOf(1.0))
            val recipe=mapOf("film" to film,"frame_identity" to mapOf("roll_id" to "grain","frame_index" to 1L))
            val profile=PhoneGrainProfile.fromRecipe(recipe)!!
            return PhoneFormationEffects(recipe,mapOf(PhoneGrainProfile.KEY to profile,"frame_identity" to recipe["frame_identity"]),200,120)
        }
        val fine=effects(.001);val coarse=effects(.04)
        var mean=0.0;var fineGap=0.0;var coarseGap=0.0;var mid=0.0;var toe=0.0;val n=200*120
        for(y in 0 until 120) for(x in 0 until 200) {
            val a=fine.grainFactor(x,y,0,.48f);val b=fine.grainFactor(x,y,1,.48f)
            mean+=a;fineGap+=abs(a-b);coarseGap+=abs(coarse.grainFactor(x,y,0,.48f)-coarse.grainFactor(x,y,1,.48f))
            mid+=(a-1.0).pow(2);toe+=(fine.grainFactor(x,y,0,.01f)-1.0).pow(2)
            assertTrue(a in .58f..1.42f)
        }
        assertTrue(mean/n in .98..1.02) { "mean ${mean/n}" }
        assertTrue(coarseGap>fineGap*1.4) { "coarse $coarseGap fine $fineGap" }
        assertTrue(mid>toe) { "mid $mid toe $toe" }
        val previous=PhoneGrainProfile.fromRecipe(mapOf("film" to mapOf("grain_scales" to listOf(.01),"grain_scale_weights" to listOf(1.0))))!!
        PhoneFormationEffects(emptyMap(),mapOf(PhoneGrainProfile.KEY to (previous+("model" to PhoneGrainProfile.PREVIOUS_MODEL))),16,12).grainFactor(3,4,0,.4f)
    }
}
