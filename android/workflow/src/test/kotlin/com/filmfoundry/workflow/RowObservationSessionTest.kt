package com.filmfoundry.workflow

import com.filmfoundry.interchange.*
import com.filmfoundry.kernel.*
import java.io.*
import java.nio.file.*
import kotlinx.coroutines.runBlocking
import org.junit.jupiter.api.Assertions.*
import org.junit.jupiter.api.Test
import org.junit.jupiter.api.io.TempDir

class RowObservationSessionTest {
    @Test fun optionalTransmissionPortMatchesCpuRowsAndLeavesTheOpticalArchiveUntouched() {
        val kernel=PhoneReducedKernel();val w=23;val h=17
        val master=kernel.develop(kernel.expose(RgbVolume(w,h,linearRgb=FloatArray(w*h*3) { (it%71)/60f }),emptyMap()),emptyMap())
        val file=root.resolve("compute.npz").toFile();val codec=NpzMediumArchive()
        file.writeBytes(codec.writeDevelopedOptical(DevelopedOpticalArchive(master.opticalDensityRgb,master.metadata,master.clearBaseOpticalDensityRgb)))
        val before=file.readBytes();var calls=0
        val compute=object: DensityTransmissionCompute {
            override fun transmit(density: FloatArray): FloatArray { calls++;return DensityTransmissionCompute.reference(density) }
        }
        fun scan(port: DensityTransmissionCompute?): FloatArray {
            var values=FloatArray(0)
            RowObservationSession.scan(io,"compute.npz",emptyMap(),object: RowObservationSession.Output {
                override fun begin(width: Int,height: Int) { values=FloatArray(width*height*3) }
                override fun rows(start: Int,count: Int,rgb: FloatArray) { rgb.copyInto(values,start*w*3) }
            },transmissionCompute=port)
            return values
        }
        assertArrayEquals(scan(null),scan(compute),0f);assertTrue(calls>0);assertArrayEquals(before,file.readBytes())
    }
    @Test fun spectralCrossDevelopedPositiveIsObservedThroughTheSameRowPath() {
        val material=mapOf("model" to "normalized_pool_absorbance_v1","profile_id" to "scan_rows",
            "coefficient_unit" to "normalized_final_pool_v1","component_names" to SpectralOptics.components,
            "wavelengths_nm" to listOf(400,550,700),"component_absorbance" to List(9){k->List(3){n->.02+k*.01+n*.03}})
        val observer=mapOf("model" to "spectral_transmission_observer_v1","profile_id" to "row_observer",
            "working_space" to DataKernel.CAPTURE_WORKING_SPACE,"normalization" to "open_gate_per_channel_v1",
            "wavelengths_nm" to listOf(400,550,700),"illuminant" to listOf(1,1,1),
            "response_rgb" to List(3){listOf(1,1,1)})
        val recipe=mapOf("film" to mapOf(SpectralOptics.MATERIAL_KEY to material),"develop" to mapOf("chemistry" to "cross","chemical_stain" to .2),
            PhoneMaterialPhenomena.DEVELOPMENT_OPTION to PhoneMaterialPhenomena.CURRENT)
        val kernel=PhoneReducedKernel();val original=kernel.develop(kernel.expose(RgbVolume(127,149,linearRgb=FloatArray(127*149*3){(it%19)/17f}),recipe),recipe)
        assertEquals("positive",original.metadata["image_polarity"])
        val archive=DevelopedOpticalArchive(original.opticalDensityRgb,original.metadata,original.clearBaseOpticalDensityRgb,original.spectralComponentAmounts,original.spectralPostProcessDensityRgb)
        root.resolve("spectral.npz").toFile().writeBytes(NpzMediumArchive().writeDevelopedOptical(archive))
        val scan=mapOf("scanner" to mapOf(SpectralOptics.OBSERVER_KEY to observer,"scan_grid_long_edge" to 6,"transmission_light_ev" to -1.0,
            "scanner_response_matrix" to listOf(listOf(.8,.1,.1),listOf(.2,.7,.1),listOf(.1,.1,.8)),
            "device_response_rgb" to List(3) { listOf(listOf(0.0,0.0),listOf(.4,.3),listOf(1.0,1.0)) }))
        for(settings in listOf(scan,scan+("scanner" to (scan.getValue("scanner")-"scan_grid_long_edge")))) {
            val expected=kernel.observe(original,settings)
            var output=FloatArray(0);var outWidth=0
            val metadata=RowObservationSession.scan(io,"spectral.npz",settings,object: RowObservationSession.Output {
                override fun begin(width: Int,height: Int) { output=FloatArray(width*height*3);outWidth=width }
                override fun rows(start: Int,count: Int,rgb: FloatArray) { rgb.copyInto(output,start*outWidth*3) }
            },transmissionCompute=object: DensityTransmissionCompute {
                override fun transmit(density: FloatArray): FloatArray=error("RGB compute cannot replace spectral integration")
            })
            assertArrayEquals(expected.outputRgb.values,output,0f)
            assertEquals(expected.metadata,metadata)
            assertTrue(io.scratchRoot.listFiles().orEmpty().isEmpty())
        }
    }
    @TempDir lateinit var root: Path
    private val io get()=object: RowMediumIo {
        override val scratchRoot=root.resolve("scratch").toFile()
        override fun <T> read(path: String,reader: (InputStream)->T)=root.resolve(path).toFile().inputStream().use(reader)
        override fun write(path: String,beforeCommit: ()->Unit,writer: (OutputStream)->Unit)=error("Observation must be read-only")
        override fun sameLocation(a: String,b: String)=a==b
    }
    @Test fun exactWholeFrameStatisticsResamplingCurvesAndPolarityMatchTheExistingKernel() = runBlocking {
        val kernel=PhoneReducedKernel();val w=31;val h=23
        val latent=kernel.expose(RgbVolume(w,h,linearRgb=FloatArray(w*h*3) { ((it*13)%179)/81f }),emptyMap())
        val original=kernel.develop(latent,emptyMap())
        val codec=NpzMediumArchive()
        val source=DevelopedOpticalArchive(original.opticalDensityRgb,original.metadata,original.clearBaseOpticalDensityRgb)
        val bytes=codec.writeDevelopedOptical(source);root.resolve("optical.npz").toFile().writeBytes(bytes)
        val curves=listOf(listOf(0.0,0.03),listOf(.4,.55),listOf(1.0,1.0))
        val cases=listOf(
            emptyMap(),
            mapOf("scanner" to mapOf("print_mapping_mode" to "sigmoid","transmission_light_ev" to -1.0,"scan_normalize_strength" to 1.0)),
            mapOf("scanner" to mapOf("print_mapping_mode" to "sigmoid","transmission_light_ev" to 1.0,"scan_normalize_strength" to .45)),
            mapOf("scanner" to mapOf("transmission_light_ev" to -1.0,"invert_transmission" to false,"remove_base_mask" to true)),
            mapOf("scanner" to mapOf("scanner_response_matrix" to listOf(listOf(.7,.2,.1),listOf(.1,.8,.1),listOf(.2,.1,.7)),
                "device_response_rgb" to List(3) { curves },"scan_normalize_mode" to "rgb","scan_grid_long_edge" to 17,"tone_curve" to curves)),
            mapOf("scanner" to mapOf("scan_normalize_mode" to "rgb","scan_grid_long_edge" to 17,"tone_curve" to curves,"color_curve_rgb" to List(3){curves},"print_mapping_mode" to "printlike")),
            mapOf("scanner" to mapOf("scan_normalize" to false,"scan_grid_long_edge" to 62)),
            mapOf("scanner" to mapOf("scan_grid_long_edge" to 2,"scan_saturation" to 2.1)),
            mapOf("observe_mode" to "viewer_projection","scanner" to mapOf("scan_grid_long_edge" to 11)),
            mapOf("observe_mode" to "viewer_light_table","scanner" to mapOf("transmission_light_ev" to 1.0,"transmission_light_temperature_k" to 4200,
                "scan_invert" to true,"remove_base_mask" to true,"scan_normalize_strength" to 1.0,"tone_curve" to curves,"color_curve_rgb" to List(3){curves})),
            mapOf("scanner" to mapOf("scan_grid_long_edge" to 31,"remove_base_mask" to false,"scan_black_percentile" to 0,"scan_white_percentile" to 100)))
        for(recipe in cases) {
            val expected=kernel.observe(original,recipe)
            var width=0;var actual=FloatArray(0);var next=0
            val metadata=RowObservationSession.scan(io,"optical.npz",recipe,object: RowObservationSession.Output {
                override fun begin(w: Int,h: Int) { width=w;actual=FloatArray(w*h*3) }
                override fun rows(start: Int,count: Int,rgb: FloatArray) { assertEquals(next,start);next+=count;rgb.copyInto(actual,start*width*3) }
            })
            assertArrayEquals(expected.outputRgb.values,actual,0f,"Recipe $recipe")
            assertEquals(expected.metadata,metadata)
            assertTrue(io.scratchRoot.listFiles().orEmpty().isEmpty())
        }
        assertArrayEquals(bytes,root.resolve("optical.npz").toFile().readBytes())
        Unit
    }
    @Test fun sourceRowsRemainBoundedAndCancellationOrBudgetFailureDoesNotPublish() {
        val w=16;val h=12;var reads=0
        val density=FloatArray(w*h*3) { it/100f }
        val whole=OpticalMaster(FloatNd(intArrayOf(h,w,3),density),mapOf("image_polarity" to "negative","compatible_interpreters" to listOf("negative_scan")))
        for(edge in listOf(4,11,32)) {
            val grid=PhoneObservationGrid.resolve(w,h,edge);val target=grid.width to grid.height
            val actual=PhoneRowObservation.sampleRows(w,h,target.first,target.second,0,target.second) { y ->
                reads++;OpticalMaster(FloatNd(intArrayOf(1,w,3),density.copyOfRange(y*w*3,(y+1)*w*3)),emptyMap())
            }
            val recipe=mapOf("scanner" to mapOf("scan_grid_long_edge" to edge,"scan_normalize" to false))
            val expected=PhoneReducedKernel().observe(whole,recipe).outputRgb.values
            val math=PhoneRowObservation(recipe);val linear=math.linear(actual).outputRgb.values;math.encode(linear)
            assertArrayEquals(expected,linear,0f)
        }
        assertTrue(reads>0)
        val bytes=NpzMediumArchive().writeDevelopedOptical(DevelopedOpticalArchive(whole.opticalDensityRgb,whole.metadata,floatArrayOf(0f,0f,0f)))
        root.resolve("optical.npz").toFile().writeBytes(bytes)
        val output=object: RowObservationSession.Output { override fun begin(width: Int,height: Int)=error("Must reject before output allocation");override fun rows(start: Int,count: Int,rgb: FloatArray)=error("no output") }
        assertThrows(WorkflowException::class.java) { RowObservationSession.scan(io,"optical.npz",emptyMap(),output,memoryBudgetBytes=1) }
        assertTrue(io.scratchRoot.listFiles().orEmpty().isEmpty())
        val control=WorkflowControl(cancelRequested={ true })
        assertThrows(WorkflowException::class.java) { RowObservationSession.scan(io,"optical.npz",emptyMap(),output,control) }
        var cancelAfterRows=false;var rowsDelivered=0
        assertThrows(WorkflowException::class.java) { RowObservationSession.scan(io,"optical.npz",emptyMap(),object: RowObservationSession.Output {
            override fun begin(width: Int,height: Int)=Unit
            override fun rows(start: Int,count: Int,rgb: FloatArray) { rowsDelivered+=count;cancelAfterRows=true }
        },WorkflowControl(cancelRequested={cancelAfterRows})) }
        assertTrue(rowsDelivered>0);assertTrue(io.scratchRoot.listFiles().orEmpty().isEmpty())
        assertArrayEquals(bytes,root.resolve("optical.npz").toFile().readBytes())
    }
}
