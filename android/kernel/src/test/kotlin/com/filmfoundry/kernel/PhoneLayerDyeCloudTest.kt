package com.filmfoundry.kernel

import com.filmfoundry.interchange.*
import org.junit.jupiter.api.Assertions.*
import org.junit.jupiter.api.Test
import kotlin.math.*

class PhoneLayerDyeCloudTest {
    private val w=211;private val h=139
    private fun recipe(model: String=PhoneGrainModels.LAYERED)=mapOf<String,Any?>(PhoneGrainModels.EXPOSURE_OPTION to model,
        "film" to mapOf("grain_strength" to .025,"grain_scales" to listOf(.0012,.0035,.010),"grain_scale_weights" to listOf(.70,.22,.08)),
        "frame_identity" to mapOf("roll_id" to "layer-grain","frame_id" to "one","frame_index" to 1))

    @Test fun rowCachesMatchTheUncachedFormulaInAnyOrderAndAreOwnedByTheExecution() {
        val model=PhoneLayerDyeCloud(w,2250f,1f,listOf(.0012f,.0035f,.01f),listOf(.7f,.22f,.08f),17,.48f,.22f)
        val a=model.samples();val b=model.samples();val reference=model.samples(false)
        for(y in listOf(0,1,138,3,55,56,55)) for(x in (w-1 downTo 0 step 3)) for(c in 2 downTo 0) {
            val expected=model.factor(reference,x,y,c,.48f,.025f)
            assertEquals(expected.toRawBits(),model.factor(a,x,y,c,.48f,.025f).toRawBits())
            assertEquals(expected.toRawBits(),model.factor(b,x,y,c,.48f,.025f).toRawBits())
        }
    }
    @Test fun fineTextureHasLayerColorWithoutUniformFogOrOldLargeClumpEdges() {
        fun effects(model: String)=recipe(model).let { PhoneFormationEffects(it,mapOf(PhoneGrainProfile.KEY to PhoneGrainProfile.fromRecipe(it),"frame_identity" to it["frame_identity"]),w,h) }
        val next=effects(PhoneGrainModels.LAYERED);val old=effects(PhoneGrainModels.CURRENT)
        val a=PhoneFormationEffects.GrainSamples();val b=PhoneFormationEffects.GrainSamples()
        var sum=0.0;var chroma=0.0;var variance=0.0;var previousVariance=0.0;var toe=0.0
        for(y in 0 until h) for(x in 0 until w) {
            val p=next.grainFactor(x,y,0,.48f,a)-1;val q=next.grainFactor(x,y,1,.48f,a)-1
            sum+=p;variance+=p*p;chroma+=(p-q).pow(2);toe+=(next.grainFactor(x,y,0,.01f,a)-1).pow(2)
            previousVariance+=(old.grainFactor(x,y,0,.48f,b)-1).pow(2)
        }
        val n=w*h
        assertTrue(abs(sum/n)<.003)
        assertTrue(chroma>variance*.2,"Layers must not share a monochrome screen")
        assertTrue(variance>toe*2)
        assertTrue(variance<previousVariance,"Natural texture must avoid the old strong clumps")
        assertEquals("layer_density_coarse_cloud_v1",next.record()["dye_cloud"])
    }
    @Test fun profileArchiveKeepsLayerGrainFrozenAndScanningNeverReappliesIt() {
        val k=PhoneReducedKernel();val r=recipe();val latent=k.expose(RgbVolume(w,h,linearRgb=FloatArray(w*h*3) { .3f }),r)
        val io=NpzMediumArchive();val saved=io.readLatent(io.writeLatent(LatentArchive(latent.arrays,latent.metadata)))
        val master=k.develop(LatentState(saved.arrays,saved.metadata),r)
        val changed=k.develop(latent,r+("film" to mapOf("grain_strength" to .2)))
        assertArrayEquals(master.opticalDensityRgb.values,changed.opticalDensityRgb.values,0f)
        val original=master.opticalDensityRgb.values.copyOf();k.observe(master,r);k.observe(master,r)
        assertArrayEquals(original,master.opticalDensityRgb.values,0f)
        fun slice(start: Int,count: Int)=LatentState(latent.arrays.mapValues { (_,a)->
            FloatNd(intArrayOf(count,w,a.shape[2]),a.values.copyOfRange(start*w*a.shape[2],(start+count)*w*a.shape[2])) },latent.metadata)
        val prepared=k.prepareDevelopment(r,slice(0,1),h)
        for(start in (0 until h step 17).reversed()) {
            val count=min(17,h-start)
            assertArrayEquals(original.copyOfRange(start*w*3,(start+count)*w*3),prepared.developRowsAt(slice(start,count),start).opticalDensityRgb.values,0f)
        }
        val bw=r+("film" to ((r["film"] as Map<String,Any?>)+mapOf("color_process" to "bw","film_base_density_rgb" to listOf(.04,.04,.04),"retained_halide_density_rgb" to listOf(0.0,0.0,0.0))))
        val mono=k.develop(k.expose(RgbVolume(w,h,linearRgb=FloatArray(w*h*3) { .3f }),bw),bw)
        for(i in mono.opticalDensityRgb.values.indices step 3) {
            assertEquals(mono.opticalDensityRgb.values[i],mono.opticalDensityRgb.values[i+1],0f)
            assertEquals(mono.opticalDensityRgb.values[i],mono.opticalDensityRgb.values[i+2],0f)
        }
    }
}
