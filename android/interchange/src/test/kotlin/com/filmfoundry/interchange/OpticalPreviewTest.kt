package com.filmfoundry.interchange

import org.junit.jupiter.api.Assertions.*
import org.junit.jupiter.api.Test

class OpticalPreviewTest {
    private val codec = NpzMediumArchive()
    private fun source(): DevelopedOpticalArchive {
        val original = javaClass.getResourceAsStream("/phone_optical_python_sample.npz")!!.use { codec.readDevelopedOptical(it) }
        val profile = mapOf("model" to "normalized_pool_absorbance_v1","profile_id" to "synthetic_preview",
            "wavelengths_nm" to listOf(400,700),"component_absorbance" to List(9){listOf(0.1,0.2)},
            "component_names" to SpectralOptics.components,"coefficient_unit" to "normalized_final_pool_v1")
        val metadata = original.metadata + (SpectralOptics.MEDIUM_KEY to mapOf("profile" to profile,
            "base_component_amounts" to List(9){0.0},"post_process_model" to SpectralOptics.POST_PROCESS_MODEL))
        return DevelopedOpticalArchive(FloatNd(intArrayOf(137,71,3),FloatArray(137*71*3){(it%193)/193f}),metadata,
            floatArrayOf(.2f,.1f,.05f),FloatNd(intArrayOf(137,71,9),FloatArray(137*71*9){it/1000f}),
            FloatNd(intArrayOf(137,71,3),FloatArray(137*71*3){(it%19-9)/100f}))
    }
    @Test fun allOpticalFieldsAverageAcrossChunksAndPartialCellsAndKeepProvenance() {
        val original=source(); val bytes=codec.writeDevelopedOptical(original); val before=bytes.copyOf()
        val preview=readOpticalPreview(bytes.inputStream(),50)
        assertEquals(3,preview.stride); assertEquals(71,preview.sourceWidth); assertEquals(137,preview.sourceHeight)
        assertEquals(codec.readDevelopedOptical(bytes).metadata,preview.archive.metadata)
        assertArrayEquals(original.clearBaseOpticalDensityRgb,preview.archive.clearBaseOpticalDensityRgb)
        val pairs=listOf(original.opticalDensityRgb to preview.archive.opticalDensityRgb,
            original.spectralComponentAmounts!! to preview.archive.spectralComponentAmounts!!,
            original.spectralPostProcessDensityRgb!! to preview.archive.spectralPostProcessDensityRgb!!)
        for((full,small) in pairs) {
            val c=full.shape[2]
            assertArrayEquals(intArrayOf(46,24,c),small.shape)
            for(y in 0 until 46) for(x in 0 until 24) for(k in 0 until c) {
                var sum=0.0;var n=0
                for(yy in y*3 until minOf(137,y*3+3)) for(xx in x*3 until minOf(71,x*3+3)) { sum+=full.values[(yy*71+xx)*c+k];n++ }
                assertEquals((sum/n).toFloat().toRawBits(),small.values[(y*24+x)*c+k].toRawBits())
            }
        }
        assertEquals("box_mean_optical_preview_v3",preview.sampler)
        val native=readOpticalPreview(bytes.inputStream(),2048)
        assertArrayEquals(original.opticalDensityRgb.values,native.archive.opticalDensityRgb.values,0f)
        assertArrayEquals(original.spectralComponentAmounts!!.values,native.archive.spectralComponentAmounts!!.values,0f)
        assertArrayEquals(original.spectralPostProcessDensityRgb!!.values,native.archive.spectralPostProcessDensityRgb!!.values,0f)
        assertArrayEquals(before,bytes)
    }
    @Test fun unselectedBadPixelsAndCancellationAreStillRejected() {
        val original=source()
        val entries=codec.opticalEntries(original).toMutableMap()
        original.opticalDensityRgb.values[3]=-1f // This pixel is outside the selected grid.
        assertThrows(IllegalArgumentException::class.java) { readOpticalPreview(NpzFile.write(entries).inputStream(),50) }
        val valid=codec.writeDevelopedOptical(source())
        assertThrows(IllegalStateException::class.java) { readOpticalPreview(valid.inputStream(),50) { error("cancelled") } }
    }
}
