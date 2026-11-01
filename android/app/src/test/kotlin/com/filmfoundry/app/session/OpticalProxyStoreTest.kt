package com.filmfoundry.app.session

import com.filmfoundry.interchange.*
import java.io.File
import org.junit.jupiter.api.Assertions.*
import org.junit.jupiter.api.Test
import org.junit.jupiter.api.io.TempDir

class OpticalProxyStoreTest {
    @TempDir lateinit var root: File
    private fun source(): File {
        val codec=NpzMediumArchive()
        val template=File("../interchange/src/test/resources/phone_optical_python_sample.npz").inputStream().use { codec.readDevelopedOptical(it) }
        val profile=mapOf("model" to "normalized_pool_absorbance_v1","profile_id" to "synthetic_proxy",
            "wavelengths_nm" to listOf(400,700),"component_absorbance" to List(9) { listOf(.1,.2) },
            "component_names" to SpectralOptics.components,"coefficient_unit" to "normalized_final_pool_v1")
        val archive=template.copy(opticalDensityRgb=FloatNd(intArrayOf(71,97,3),FloatArray(71*97*3) { (it%193)/193f }),
            metadata=template.metadata+(SpectralOptics.MEDIUM_KEY to mapOf("profile" to profile,
                "base_component_amounts" to List(9) { 0.0 },"post_process_model" to SpectralOptics.POST_PROCESS_MODEL)),
            spectralComponentAmounts=FloatNd(intArrayOf(71,97,9),FloatArray(71*97*9) { it/1000f }),
            spectralPostProcessDensityRgb=FloatNd(intArrayOf(71,97,3),FloatArray(71*97*3) { (it%19-9)/100f }))
        return File(root,"film.npz").apply { writeBytes(codec.writeDevelopedOptical(archive)) }
    }
    @Test fun cacheSurvivesReopenButRequiresUnchangedExistingSourceAndKeepsOriginalProvenance() {
        val file=source();val bytes=file.readBytes();val modified=file.lastModified();var reads=0
        fun store()=OpticalProxyStore(root,File(root,"cache"),readSource={ input,edge,check -> reads++;readOpticalPreview(input,edge,check) })
        val first=store().get("frame","film.npz",32)
        val second=store().get("frame","film.npz",32)
        assertEquals(1,reads);assertEquals(97,second.sourceWidth);assertEquals(71,second.sourceHeight)
        assertEquals(first.stride,second.stride);assertEquals(first.sampler,second.sampler)
        assertArrayEquals(first.archive.opticalDensityRgb.values,second.archive.opticalDensityRgb.values,0f)
        assertArrayEquals(first.archive.spectralComponentAmounts!!.values,second.archive.spectralComponentAmounts!!.values,0f)
        assertArrayEquals(first.archive.spectralPostProcessDensityRgb!!.values,second.archive.spectralPostProcessDensityRgb!!.values,0f)
        assertEquals(first.archive.metadata,second.archive.metadata)
        assertArrayEquals(bytes,file.readBytes());assertEquals(modified,file.lastModified())
        store().get("legacy:frame","film.npz",32);assertEquals(2,reads)
        assertEquals(1,File(root,"cache/optical-proxies-v1").listFiles()!!.size)
        assertTrue(file.setLastModified(modified+2000))
        store().get("frame","film.npz",32);assertEquals(3,reads)
        assertTrue(file.delete())
        assertThrows(IllegalArgumentException::class.java) { store().get("frame","film.npz",32) }
        assertEquals(3,reads)
    }
    @Test fun corruptCacheFallsBackAndClearOrDeleteCannotResurrectAnInflightProxy() {
        source();val cache=File(root,"cache");var reads=0
        val store=OpticalProxyStore(root,cache,readSource={ input,edge,check -> reads++;readOpticalPreview(input,edge,check) })
        store.get("frame","film.npz",32)
        val directory=File(cache,"optical-proxies-v1")
        directory.listFiles()!!.single().writeText("invalid")
        store.get("frame","film.npz",32);assertEquals(2,reads)
        store.get("frame-extra","film.npz",32);store.dropResult("frame")
        assertEquals("frame-extra-32.ffpv",directory.listFiles()!!.single().name)
        store.clear();assertTrue(directory.listFiles()!!.isEmpty())
        lateinit var clearing: OpticalProxyStore
        clearing=OpticalProxyStore(root,cache,readSource={ input,edge,check ->
            readOpticalPreview(input,edge,check).also { clearing.clear() }
        })
        clearing.get("frame","film.npz",32);assertTrue(directory.listFiles()!!.isEmpty())
        assertThrows(IllegalStateException::class.java) { store.get("frame","film.npz",32) { error("cancelled") } }
        assertTrue(directory.listFiles()!!.isEmpty())
    }
}
