package com.filmfoundry.kernel

import org.junit.jupiter.api.Assertions.*
import org.junit.jupiter.api.Test

class ColorMixerTest {
    private val settings=List(8) { if(it==0) listOf(30,-50,20) else listOf(0,0,0) }
    @Test fun selectedColorNeutralProtectionAndChunkEquivalence() {
        val input=floatArrayOf(1f,0f,0f,0f,1f,0f,.5f,.5f,.5f,0f,0f,0f,1f,1f,1f)
        val mix=ColorMixer.parse(settings)!!;val result=input.copyOf();mix.apply(result)
        assertArrayEquals(floatArrayOf(.775f,.55f,.325f),result.copyOfRange(0,3),1e-6f)
        assertArrayEquals(input.copyOfRange(3,input.size),result.copyOfRange(3,result.size),1e-6f)
        val chunked=input.copyOfRange(0,6).also(mix::apply)+input.copyOfRange(6,input.size).also(mix::apply)
        assertArrayEquals(result,chunked,0f)
        assertNull(ColorMixer.parse(List(8){listOf(0,0,0)}))
        assertThrows(IllegalArgumentException::class.java) { ColorMixer.parse(List(8){listOf(31,0,0)}) }
    }
    @Test fun outputEncodingAppliesOnceAndViewerBypasses() {
        val base=mapOf<String,Any?>("scanner" to mapOf("color_mixer" to settings))
        val input=floatArrayOf(.8f,.1f,.05f,.1f,.4f,.2f)
        val expected=input.copyOf();PhoneObserve.encodeRows(expected,emptyMap());ColorMixer.parse(settings)!!.apply(expected)
        val actual=input.copyOf();PhoneObserve.encodeRows(actual,base)
        assertArrayEquals(expected,actual,0f)
        val reference=input.copyOf();PhoneObserve.encodeRowsReference(reference,base)
        assertArrayEquals(actual,reference,1e-6f)
        val viewer=input.copyOf();PhoneObserve.encodeRows(viewer,base,true)
        val plain=input.copyOf();PhoneObserve.encodeRows(plain,emptyMap(),true)
        assertArrayEquals(plain,viewer,0f)
    }
    @Test fun sharedRecipeAndRowBackendKeepMixer() {
        val recipe=mapOf("scanner" to mapOf("color_mixer" to settings,"scan_normalize" to false))
        val decoded=com.filmfoundry.interchange.RecipeShareCodec.decode(com.filmfoundry.interchange.RecipeShareCodec.encode(recipe))
        val backend=object: DensityTransmissionCompute {
            override fun transmit(density: FloatArray)=DensityTransmissionCompute.reference(density)
            override fun encodeOutput(linear: FloatArray,plan: ScanOutputPlan)=plan.reference(linear)
        }
        val row=PhoneRowObservation(decoded,backend)
        val density=floatArrayOf(.1f,.2f,.3f)
        row.linear(OpticalMaster(com.filmfoundry.interchange.FloatNd(intArrayOf(1,1,3),density),emptyMap()))
        val plan=row.outputPlan(1) { error("Normalization is disabled") }
        val values=floatArrayOf(.8f,.1f,.05f)
        val expected=values.copyOf();row.encode(expected)
        row.output(values,plan)
        assertArrayEquals(expected,values,0f)
        assertArrayEquals(floatArrayOf(.1f,.2f,.3f),density,0f)
    }
}
