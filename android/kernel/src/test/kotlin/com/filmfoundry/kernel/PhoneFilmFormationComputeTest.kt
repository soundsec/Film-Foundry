package com.filmfoundry.kernel

import com.filmfoundry.interchange.*
import org.junit.jupiter.api.Assertions.*
import org.junit.jupiter.api.Test

class PhoneFilmFormationComputeTest {
    @Test fun orderedBackendPreservesGrainTimeAndInputAcrossRowBoundaries() {
        var calls=0
        val accelerated=PhoneReducedKernel(object: FilmDevelopmentCompute {
            override fun develop(input: FloatArray,plan: FilmDevelopmentPlan): FloatArray {
                calls++
                val result=plan.reference(input)
                if(calls%5==1) System.getenv("FILM_FORMATION_SHADER_FIXTURES")?.let { path ->
                    java.io.File(path).mkdirs()
                    java.io.File(path,"program_${calls/5}.npz").outputStream().use { stream -> NpzFile.write(mapOf(
                        "input" to NpyValue.Float32(FloatNd(intArrayOf(input.size),input)),
                        "coefficients" to plan.coefficients().let { NpyValue.Float32(FloatNd(intArrayOf(it.size),it)) },
                        "expected" to NpyValue.Float32(FloatNd(intArrayOf(result.size),result))),stream) }
                }
                return result
            }
        })
        for(program in listOf("color_negative","color_reversal","color_negative_bleach_bypass","bw_negative","bw_reversal")) {
            val recipe=mapOf(PhoneFormationProfile.OPTION to PhoneFormationProfile.LIGHTWEIGHT,
                PhoneProcessModels.OPTION to PhoneProcessModels.CURRENT,
                PhoneMaterialPhenomena.DEVELOPMENT_OPTION to PhoneMaterialPhenomena.CURRENT,
                "film" to mapOf("color_process" to if(program.startsWith("bw")) "monochrome" else "color","grain_strength" to .08,
                    "time_response" to mapOf("model" to "film_time_response_v2")),
                "develop" to mapOf("program_key" to program,"developer_type" to "high_contrast","developer_exhaustion" to .4,"compensation" to .2,
                    "chemical_stain" to .2,"uneven_development" to .15,
                    "latent_wait" to mapOf("model" to "film_latent_wait_v1","segments" to listOf(mapOf("duration_hours" to 240.0,"temperature_c" to 28.0,"relative_humidity" to .6))),
                    "post_process_light" to mapOf("model" to "film_residual_light_v1","segments" to listOf(mapOf("duration_hours" to 3.0,"relative_illuminance" to .5,"light_rgb" to listOf(1.0,.8,.6))))))
            val cpu=PhoneReducedKernel();val latent=cpu.expose(RgbVolume(31,17,linearRgb=FloatArray(31*17*3){ (it%97)/110f }),recipe)
            val before=latent.arrays.mapValues { it.value.values.copyOf() };val expected=cpu.develop(latent,recipe)
            val source=object: LatentRows {
                override val width=31;override val height=17
                override fun readRows(start: Int,count: Int)=LatentState(latent.arrays.mapValues { (_,a)->
                    val stride=width*a.shape[2];FloatNd(intArrayOf(count,width,a.shape[2]),a.values.copyOfRange(start*stride,(start+count)*stride)) },latent.metadata)
            }
            accelerated.prepareDevelopment(recipe,source.readRows(0,1),17).bindSource(source).use { bound ->
                for(start in 0 until 17 step 4) {
                    val n=minOf(4,17-start);val actual=bound.developRows(start,n)
                    assertArrayEquals(expected.opticalDensityRgb.values.copyOfRange(start*31*3,(start+n)*31*3),actual.opticalDensityRgb.values,0f)
                }
            }
            before.forEach { (k,v)->assertArrayEquals(v,latent.arrays.getValue(k).values,0f) }
        }
        assertEquals(25,calls)
    }
    @Test fun monochromeSensitivityAppliesOnlyToNewProfile() {
        val source=RgbVolume(3,1,linearRgb=floatArrayOf(1f,0f,0f,0f,1f,0f,0f,0f,1f))
        val film=mapOf("color_process" to "monochrome","layer_sensitivity_matrix" to List(3){listOf(.02,.68,.30)})
        fun expose(profile: String)=PhoneReducedKernel().expose(source,mapOf("film" to film,PhoneFormationProfile.OPTION to profile))
        val current=expose(PhoneFormationProfile.LIGHTWEIGHT).arrays.getValue("developability").values
        assertTrue(current[0]<current[2] && current[2]<current[1])
        for(profile in listOf(PhoneFormationProfile.TIME_LIGHTWEIGHT,PhoneFormationProfile.PREVIOUS_LIGHTWEIGHT,PhoneFormationProfile.DETAILED)) {
            val old=expose(profile);val a=old.arrays.getValue("developability").values
            assertEquals(a[0],a[1]);assertEquals(a[0],a[2]);assertNull(old.metadata["phone_monochrome_sensitivity"])
        }
    }
}
