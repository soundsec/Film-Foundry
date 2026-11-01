package com.filmfoundry.workflow

import com.filmfoundry.interchange.*
import com.filmfoundry.kernel.OpticalMaster
import com.filmfoundry.kernel.PhoneReducedKernel
import kotlinx.coroutines.runBlocking
import org.junit.jupiter.api.Assertions.*
import org.junit.jupiter.api.Test

class OpticalPreviewSessionTest {
    @Test fun displayRelightsCachedTransportAndInvalidatesOnSourceAndClearWhileFormalScanBypassesTheCache() = runBlocking {
        val kernel=PhoneReducedKernel();val delegate=DefaultWorkflowFacade(kernel);var prepares=0;var full=0
        val facade=object: WorkflowFacade,ObservationPreparationFacade {
            override suspend fun run(request: WorkflowRequest): WorkflowResult { full++;return delegate.run(request) }
            override fun prepareObservation(optical: OpticalMaster,recipe: Map<String,Any?>,checkCancel: ()->Unit): com.filmfoundry.kernel.PreparedObservation {
                prepares++;return kernel.prepareObservation(optical,recipe,checkCancel)
            }
        }
        val archive=DevelopedOpticalArchive(FloatNd(intArrayOf(5,7,3),FloatArray(105){(it%19)/10f}),emptyMap(),floatArrayOf(.2f,.1f,.05f))
        val preview=OpticalPreview(archive,7,5,1)
        val session=OpticalPreviewSession(facade,retainPrepared=true)
        val initial=mapOf("scanner" to mapOf("scan_normalize" to true,"scan_normalize_strength" to 1.0))
        session.observe(preview,initial,false);assertEquals(1,prepares)
        val changed=initial+mapOf("scanner" to (initial["scanner"]!!+mapOf("scan_saturation" to 1.4)))
        val seen=session.observe(preview,changed,false)
        assertEquals(1,prepares);assertEquals(0,full)
        assertArrayEquals(OpticalPreviewSession().observe(preview,changed,false).rgb,seen.rgb,0f)
        val light=mapOf("scanner" to (changed["scanner"]!!+mapOf("transmission_light_ev" to .5)))
        val relit=session.observe(preview,light,false);assertEquals(1,prepares)
        assertArrayEquals(OpticalPreviewSession().observe(preview,light,false).rgb,relit.rgb,0f)
        session.clear();session.observe(preview,light,false);assertEquals(2,prepares)
        session.scan(archive,light);assertEquals(2,prepares);assertEquals(1,full)
        val another=preview.copy(archive=preview.archive.copy(opticalDensityRgb=FloatNd(preview.archive.opticalDensityRgb.shape,preview.archive.opticalDensityRgb.values.copyOf())))
        session.observe(another,light,false);assertEquals(3,prepares)
        Unit
    }
    @Test fun explicitMemoryAdmissionRejectsOversizedReadoutWithoutChangingFilmOrOutputSettings() = runBlocking {
        val metadata=mapOf<String,Any?>("medium_family" to "film","medium_process" to "negative","image_polarity" to "negative",
            "view_mode" to "transmissive","base_type" to "orange_mask","color_system" to "color_negative_dye","compatible_interpreters" to listOf("negative_scan"))
        val archive=DevelopedOpticalArchive(FloatNd(intArrayOf(4,6,3),FloatArray(72){.3f}),metadata,floatArrayOf(.2f,.1f,.05f))
        val before=archive.opticalDensityRgb.values.copyOf();val recipe=mapOf("scanner" to mapOf("scan_grid_long_edge" to 8192))
        val error=assertThrows(WorkflowException::class.java) { runBlocking {
            OpticalPreviewSession().scan(archive,recipe,memoryBudgetBytes=16L*1024*1024,outputExtraBytesPerPixel=4)
        } }
        assertEquals("invalid_request",error.code);assertArrayEquals(before,archive.opticalDensityRgb.values)
        assertEquals(8192,(recipe["scanner"] as Map<*,*>)["scan_grid_long_edge"])
        val small=OpticalPreviewSession().scan(archive,mapOf("scanner" to mapOf("scan_grid_long_edge" to 12)),memoryBudgetBytes=16L*1024*1024,outputExtraBytesPerPixel=4)
        assertEquals(12,small.width);assertEquals(8,small.height)
        Unit
    }
    @Test fun displayGridIsBoundedWhileSavedScanKeepsRequestedGridAndSourceDimensions() = runBlocking {
        val metadata=mapOf<String,Any?>("medium_family" to "film","medium_process" to "negative","image_polarity" to "negative",
            "view_mode" to "transmissive","base_type" to "orange_mask","color_system" to "color_negative_dye","compatible_interpreters" to listOf("negative_scan"))
        val archive=DevelopedOpticalArchive(FloatNd(intArrayOf(5,7,3),FloatArray(105){(it%19)/10f}),metadata,floatArrayOf(.2f,.1f,.05f))
        val original=archive.opticalDensityRgb.values.copyOf()
        val recipe=mapOf("scanner" to mapOf("scan_grid_long_edge" to 12))
        val preview=readOpticalPreview(NpzMediumArchive().writeDevelopedOptical(archive).inputStream(),4)
        val session=OpticalPreviewSession()
        for(mode in listOf("scanner","viewer_projection","viewer_light_table")) {
            val display=session.observe(preview,recipe,mode != "scanner",mode=mode)
            assertTrue(maxOf(display.width,display.height) <= 4)
            assertEquals(7,display.sourceWidth);assertEquals(5,display.sourceHeight)
        }
        val full=session.scan(archive,recipe)
        val expected=OpticalPreviewSession.outputDimensions(preview.sourceWidth,preview.sourceHeight,12)
        assertEquals(full.width,expected.width);assertEquals(full.height,expected.height)
        val native=OpticalPreviewSession.outputDimensions(preview.sourceWidth,preview.sourceHeight,null)
        assertEquals(7,native.width);assertEquals(5,native.height)
        assertEquals(12,full.width);assertEquals(7,full.sourceWidth);assertEquals(5,full.sourceHeight)
        assertEquals(12,((recipe["scanner"] as Map<*,*>)["scan_grid_long_edge"]))
        assertArrayEquals(original,archive.opticalDensityRgb.values)
        assertFalse(full.metadata.containsKey("display_proxy"))
        Unit
    }
    @Test fun displayDtoPreservesOrientationAndUsesTheExistingReadOnlyObserver() = runBlocking {
        val codec=NpzMediumArchive()
        val metadata=mapOf<String,Any?>("medium_family" to "film","medium_process" to "negative",
            "image_polarity" to "negative","view_mode" to "transmissive","base_type" to "orange_mask",
            "color_system" to "color_negative_dye","compatible_interpreters" to listOf("negative_scan"),
            "capture" to mapOf("orientation_degrees" to 90))
        val archive=DevelopedOpticalArchive(FloatNd(intArrayOf(5,7,3),FloatArray(105){(it%19)/10f}),metadata,
            floatArrayOf(.2f,.1f,.05f))
        val bytes=codec.writeDevelopedOptical(archive)
        val preview=readOpticalPreview(bytes.inputStream(),4)
        val original=preview.archive.opticalDensityRgb.values.copyOf()
        for(viewer in listOf(true,false)) {
            val result=OpticalPreviewSession().observe(preview,emptyMap(),viewer)
            val reference=PhoneReducedKernel().observe(OpticalMaster(preview.archive.opticalDensityRgb,preview.archive.metadata,
                preview.archive.clearBaseOpticalDensityRgb),mapOf("observe_mode" to if(viewer) "viewer_light_table" else "scanner"))
            assertArrayEquals(reference.outputRgb.values,result.rgb)
            assertArrayEquals(original,preview.archive.opticalDensityRgb.values)
            assertEquals(7,result.sourceWidth);assertEquals(5,result.sourceHeight)
            assertEquals(4,result.width);assertEquals(3,result.height)
            assertEquals("box_mean_optical_preview_v3",(result.metadata["display_proxy"] as Map<*,*>)["model"])
        }
    }
}
