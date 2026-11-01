package com.filmfoundry.kernel

import com.filmfoundry.interchange.*
import org.junit.jupiter.api.Assertions.*
import org.junit.jupiter.api.Test

class PhoneLayerMaterialTest {
    private val kernel=PhoneReducedKernel();private val w=83;private val h=57
    private val identity=List(3) { y->List(3) { x->if(x==y) 1.0 else 0.0 } }
    private fun recipe(film: Map<String,Any?>)=mapOf("film" to (mapOf("layer_sensitivity_matrix" to identity)+film),
        PhoneMaterialPhenomena.EXPOSURE_OPTION to PhoneMaterialPhenomena.CURRENT)
    private fun expose(rgb: FloatArray,r: Map<String,Any?>)=kernel.expose(RgbVolume(w,h,linearRgb=rgb),r)
    private fun activation(a: LatentState)=a.arrays.getValue("developability").values
    @Test fun declaredEdgePipingAddsLayerExposureOnBlackAndIsIndependentOfRgbSensitivity() {
        val film=mapOf("light_piping_strength" to .3,"light_piping_edge_mode" to "left","light_piping_depth" to .06,
            "light_piping_layer_weights" to listOf(1,0,0),"layer_sensitivity_matrix" to List(3) { List(3) { 0.0 } })
        val rgb=FloatArray(w*h*3);val a=activation(expose(rgb,recipe(film)))
        assertTrue(a[0]>a[12*3]);assertTrue(a[12*3]>a[(w-1)*3]);assertEquals(0f,a[1],0f);assertEquals(0f,a[2],0f)
        assertEquals(a[0],a[((h-1)*w)*3],0f)
        val base=activation(expose(rgb,emptyMap()))
        assertArrayEquals(base,activation(expose(rgb,recipe(film)-PhoneMaterialPhenomena.EXPOSURE_OPTION)),0f)
        val off=recipe(film+("light_piping_edge_mode" to "none"))
        assertArrayEquals(base,activation(expose(rgb,off)),0f)
    }
    @Test fun selectiveHalationReturnsOnlyToDeclaredLayerAndDoesNotFogFlatFields() {
        val r=recipe(mapOf("halation_strength" to 1.0,"halation_return_model" to "layer_selective","halation_layer_return_weights" to listOf(0,1,0),
            "halation_core_radius" to .03,"halation_outer_radius" to .09,"halation_spread_scale_weights" to listOf(.1,.6,.3)))
        val rgb=FloatArray(w*h*3);val center=((h/2)*w+w/2)*3;for(c in 0..2) rgb[center+c]=20f
        val before=rgb.copyOf();val a=activation(expose(rgb,r));val tail=center+5*3
        assertEquals(0f,a[tail],0f);assertTrue(a[tail+1]>0f);assertEquals(0f,a[tail+2],0f)
        assertArrayEquals(before,rgb,0f)
        for(value in listOf(.1f,2f)) {
            val flat=FloatArray(w*h*3) { value }
            assertArrayEquals(activation(expose(flat,recipe(emptyMap()))),activation(expose(flat,r)),0f)
        }
        assertThrows(IllegalArgumentException::class.java) { expose(rgb,r-PhoneMaterialPhenomena.EXPOSURE_OPTION) }
    }
    @Test fun compactEmulsionSpreadPreservesLayerColorAndActsBeforeActivation() {
        val r=recipe(mapOf("emulsion_highlight_spread_strength" to 1.0,"emulsion_highlight_spread_radius" to .04,
            "emulsion_highlight_spread_layer_strengths" to listOf(1,1,1),"emulsion_highlight_spread_layer_radius_scales" to listOf(1,1,1)))
        val rgb=FloatArray(w*h*3);val center=((h/2)*w+w/2)*3;rgb[center]=8f
        val a=activation(expose(rgb,r));val tail=center+3*3
        assertTrue(a[tail]>0f);assertEquals(0f,a[tail+1],0f);assertEquals(0f,a[tail+2],0f)
        assertArrayEquals(activation(expose(rgb,recipe(emptyMap()))),activation(expose(rgb,r-PhoneMaterialPhenomena.EXPOSURE_OPTION)),0f)
    }
    @Test fun layerAdditionsAreExactAcrossReversePartitionsAndFrozenNpzAndCloseReleasesBinding() {
        val r=recipe(mapOf("light_piping_strength" to .15,"light_piping_edge_mode" to "long_edges","emulsion_mtf_strength" to .2,"emulsion_blur_radius" to .015,
            "halation_strength" to .6,"halation_return_model" to "layer_selective","emulsion_highlight_spread_strength" to .5))
        val rgb=FloatArray(w*h*3) { if(it/3==(h/2)*w+w/2) 14f else if(it%3==0) .02f else .4f }
        val before=rgb.copyOf();val whole=expose(rgb,r)
        val source=object: LinearRgbRows {
            override val width=w;override val height=h
            override fun readRows(start: Int,count: Int)=RgbVolume(w,count,linearRgb=rgb.copyOfRange(start*w*3,(start+count)*w*3))
        }
        val prepared=kernel.prepareExposure(r,w,h);val bound=prepared.bindSource(source)
        for(start in (0 until h step 7).reversed()) {
            val n=minOf(7,h-start);val actual=bound.exposeRows(start,n);assertEquals(whole.metadata,actual.metadata)
            whole.arrays.forEach { (key,v)->assertArrayEquals(v.values.copyOfRange(start*w*v.shape[2],(start+n)*w*v.shape[2]),actual.arrays.getValue(key).values,0f) }
        }
        bound.close();assertThrows(IllegalStateException::class.java) { bound.exposeRows(0,1) };assertArrayEquals(before,rgb,0f)
        val codec=NpzMediumArchive();val saved=codec.readLatent(codec.writeLatent(LatentArchive(whole.arrays,whole.metadata)))
        val optical=kernel.develop(LatentState(saved.arrays,saved.metadata),mapOf(PhoneProcessModels.OPTION to PhoneProcessModels.CURRENT))
        assertEquals(PhoneLayerExposure.MODEL,((optical.metadata["film_process_model"] as Map<*,*>)["layer_exposure"] as Map<*,*>)["model"])
    }
    @Test fun physicalReferenceAndHalfFrameOrientationDetermineDeclaredEdgesNotDevicePortrait() {
        val r=recipe(mapOf("light_piping_strength" to .2,"light_piping_edge_mode" to "long_edges"))
        val a=kernel.expose(RgbVolume(15,21),r);val values=activation(a)
        assertTrue(values[0]>values[(10*15+7)*3]);assertEquals(values[(10*15)*3],values[(10*15+14)*3],0f)
        assertEquals(values[0],values[(20*15)*3],0f)
    }
    @Test fun invalidLayerWeightsAndCancellationFailBeforeSourceChanges() {
        assertThrows(IllegalArgumentException::class.java) { kernel.prepareExposure(recipe(mapOf("halation_layer_return_weights" to listOf(-1,0,1))),w,h) }
        assertThrows(IllegalArgumentException::class.java) { kernel.prepareExposure(recipe(mapOf("light_piping_edge_mode" to "random")),w,h) }
        val rgb=FloatArray(w*h*3) { 2f };val before=rgb.copyOf();var checks=0
        val source=object: LinearRgbRows {
            override val width=w;override val height=h
            override fun checkActive() { if(++checks==5) error("cancelled") }
            override fun readRows(start: Int,count: Int)=RgbVolume(w,count,linearRgb=rgb.copyOfRange(start*w*3,(start+count)*w*3))
        }
        assertThrows(IllegalStateException::class.java) { kernel.prepareExposure(recipe(mapOf("emulsion_highlight_spread_strength" to .5)),w,h).bindSource(source) }
        assertArrayEquals(before,rgb,0f)
    }
    @Test fun smitsMappedHighlightSourceFeedsItsOwnLayerAndExtremeSupportFailsAtPreparation() {
        val profile=mapOf("model" to DataKernel.SMITS_EXPOSURE_MODEL_ID,"profile_id" to "synthetic_layer_spread","working_space" to DataKernel.CAPTURE_WORKING_SPACE,
            "wavelengths_nm" to listOf(400,700),"rgb_basis" to List(7) { i->List(2) { if(i==4) 1.0 else .0001 } },"layer_sensitivity" to List(3) { List(2) { 1.0 } },"layer_gain" to listOf(.01,.01,.01))
        val film=mapOf("spectral_exposure_profile" to profile,"layer_sensitivity_matrix" to List(3) { List(3) { 0.0 } },
            "emulsion_highlight_spread_strength" to 1.0,"emulsion_highlight_spread_radius" to .04,"emulsion_highlight_spread_layer_strengths" to listOf(1,0,0))
        val rgb=FloatArray(w*h*3);val center=((h/2)*w+w/2)*3;rgb[center]=8f
        val base=activation(expose(rgb,recipe(film)-PhoneMaterialPhenomena.EXPOSURE_OPTION));val out=activation(expose(rgb,recipe(film)))
        assertTrue(out[center+9]>base[center+9]);assertEquals(base[center+10],out[center+10],0f);assertEquals(base[center+11],out[center+11],0f)
        assertThrows(IllegalArgumentException::class.java) { kernel.prepareExposure(recipe(mapOf("halation_strength" to 1.0,"halation_return_model" to "layer_selective",
            "halation_outer_radius" to .25,"halation_spread_scale_weights" to listOf(0,0,1))),4000,3000) }
    }
}
