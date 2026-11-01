package com.filmfoundry.kernel

import com.filmfoundry.interchange.*
import org.junit.jupiter.api.Assertions.*
import org.junit.jupiter.api.Test
import kotlin.math.abs

class PhoneCrossProcessResultTest {
    private val k=PhoneReducedKernel()
    private val rgb=RgbVolume(5,1,linearRgb=floatArrayOf(.0001f,.0001f,.0001f,.02f,.02f,.02f,.18f,.18f,.18f,1f,1f,1f,32f,32f,32f))
    private fun recipe(positive: Boolean,key: String,bleach: Double=0.0): Map<String,Any?> = mapOf(
        PhoneProcessModels.OPTION to PhoneProcessModels.CURRENT,PhoneOpticalDensityModels.OPTION to PhoneOpticalDensityModels.CURRENT,
        "film" to mapOf("image_polarity" to if(positive) "positive" else "negative","color_process" to "color_negative",
            "film_base_density_rgb" to if(positive) listOf(.04,.04,.04) else listOf(.72,.44,.19),
            "clear_support_density_rgb" to listOf(.04,.04,.04),"density_max" to listOf(2.4,2.4,2.4),
            "dye_absorption_matrix" to List(3) { y -> List(3) { x -> if(x==y) 1.0 else 0.0 } }),
        "develop" to mapOf("program_key" to key,"mask_bleach_completion" to bleach),"scanner" to mapOf("scan_normalize" to false))
    @Test fun colorStocksAcrossFourProcessesKeepTheActualPolarityCarrierAndAutomaticScan() {
        for(stockPositive in listOf(false,true)) for(key in listOf("color_negative","color_reversal","bw_negative","bw_reversal")) {
            val r=recipe(stockPositive,key);val latent=k.expose(rgb,r);val before=latent.arrays.mapValues { it.value.values.copyOf() }
            val master=k.develop(latent,r);val isPositive=key.endsWith("reversal")
            assertEquals(if(isPositive) "positive" else "negative",master.metadata["image_polarity"],"$stockPositive/$key")
            val bw=key.startsWith("bw_")
            assertEquals(if(bw) "bw_silver" else "process_dye",master.metadata["color_system"])
            assertEquals(listOf(if(bw) "metallic_silver" else "dye"),master.metadata["formed_image_components"])
            assertTrue(if(isPositive) master.opticalDensityRgb.values[0]>master.opticalDensityRgb.values[12] else master.opticalDensityRgb.values[0]<master.opticalDensityRgb.values[12])
            if(bw) for(i in 0..4) {
                val b=master.clearBaseOpticalDensityRgb!!;val d=master.opticalDensityRgb.values
                assertEquals(d[i*3]-b[0],d[i*3+1]-b[1],1e-6f);assertEquals(d[i*3]-b[0],d[i*3+2]-b[2],1e-6f)
            }
            val frozen=master.opticalDensityRgb.values.copyOf();val scan=k.observe(master,r)
            assertEquals(!isPositive,scan.metadata["invert_transmission"])
            assertEquals(!isPositive || !stockPositive,scan.metadata["remove_base_mask"])
            assertTrue(scan.outputRgb.values[0]<scan.outputRgb.values[12],"Scan must preserve scene brightness ordering")
            assertArrayEquals(frozen,master.opticalDensityRgb.values,0f)
            before.forEach { (name,values)->assertArrayEquals(values,latent.arrays.getValue(name).values,0f) }
        }
        val r=recipe(false,"color_reversal",1.0);val bleached=k.develop(k.expose(rgb,r),r)
        assertEquals(false,k.observe(bleached,r).metadata["remove_base_mask"],"Cleared positive support must not inherit an orange-mask default")
    }
    @Test fun baseRemovalAndInversionAreIndependentAndLegacyJobsRetainTheOldMath() {
        val r=recipe(false,"color_negative");val m=k.develop(k.expose(rgb,r),r)
        fun observe(remove: Boolean,invert: Boolean,legacy: Boolean=false)=k.observe(m,r+
            ("scanner" to mapOf("remove_base_mask" to remove,"invert_transmission" to invert,"scan_normalize" to false))+
            if(legacy) mapOf(PhoneScanModels.OPTION to PhoneScanModels.LEGACY) else emptyMap())
        for(invert in listOf(false,true)) {
            val a=observe(false,invert);val b=observe(true,invert)
            assertTrue(a.outputRgb.values.indices.any { abs(a.outputRgb.values[it]-b.outputRgb.values[it])>1e-4f })
            assertEquals(false,a.metadata["remove_base_mask"]);assertEquals(invert,a.metadata["invert_transmission"])
        }
        assertArrayEquals(observe(false,true,true).outputRgb.values,observe(true,true,true).outputRgb.values,1e-6f)
    }
    @Test fun fixingBeforeDevelopmentProducesNoInventedSilverOrDyeImage() {
        val r=recipe(false,"color_negative");val d=(r["develop"] as Map<String,Any?>)+mapOf("program_key" to "custom",
            "process_steps" to (listOf(mapOf("action" to "fix_halide"))+ProcessSteps.template("color_negative")))
        val m=k.develop(k.expose(rgb,r),r+("develop" to d))
        assertEquals("no_developed_image",m.metadata["image_formation_state"])
        assertEquals(emptyList<String>(),m.metadata["formed_image_components"])
        for(i in 1..4) for(c in 0..2) assertEquals(m.opticalDensityRgb.values[c],m.opticalDensityRgb.values[i*3+c],0f)
    }
}
