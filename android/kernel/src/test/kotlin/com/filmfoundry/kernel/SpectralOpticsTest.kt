package com.filmfoundry.kernel

import com.filmfoundry.interchange.*
import org.junit.jupiter.api.Assertions.*
import org.junit.jupiter.api.Test
import java.nio.file.Files
import java.nio.file.Path
import kotlin.math.pow

class SpectralOpticsTest {
    private val kernel = PhoneReducedKernel()
    private val codec = NpzMediumArchive()
    private fun recipe(): Map<String, Any?> = RecipeShareCodec.decode(
        javaClass.getResourceAsStream("/spectral_optics_sample.ffc2")!!.bufferedReader().use { it.readText() })
    private fun material(recipe: Map<String, Any?>): Map<String, Any?> =
        SpectralOptics.objectValue(SpectralOptics.objectValue(recipe["film"])[SpectralOptics.MATERIAL_KEY])
    private fun observer(recipe: Map<String, Any?>): Map<String, Any?> =
        SpectralOptics.objectValue(SpectralOptics.objectValue(recipe["scanner"])[SpectralOptics.OBSERVER_KEY])

    @Test
    fun sharedDefinitionsFreezeAndArchiveAndObserveWithoutChangingMedium() {
        val recipe = recipe()
        val latent = kernel.expose(RgbVolume(3,2,linearRgb=FloatArray(18) { 0.4f }),recipe)
        val latentBytes = codec.writeLatent(LatentArchive(latent.arrays,latent.metadata))
        val read = codec.readLatent(latentBytes)
        val before = read.arrays.getValue("halide").values.copyOf()
        val optical = kernel.develop(LatentState(read.arrays,read.metadata),recipe + ("film" to emptyMap<String,Any?>()))
        assertArrayEquals(before,read.arrays.getValue("halide").values,0f)
        val amounts = optical.spectralComponentAmounts!!
        assertArrayEquals(intArrayOf(2,3,9),amounts.shape)
        val bytes = codec.writeDevelopedOptical(DevelopedOpticalArchive(optical.opticalDensityRgb,optical.metadata,
            optical.clearBaseOpticalDensityRgb,amounts))
        val archived = codec.readDevelopedOptical(bytes)
        val frozen = OpticalMaster(archived.opticalDensityRgb,archived.metadata,archived.clearBaseOpticalDensityRgb,archived.spectralComponentAmounts)
        val original = frozen.spectralComponentAmounts!!.values.copyOf()
        val originalRgb = frozen.opticalDensityRgb.values.copyOf()
        val observed = kernel.observe(frozen,recipe + ("observe_mode" to "viewer_light_table"))
        assertTrue(observed.outputRgb.values.all { it.isFinite() })
        assertEquals(PhoneSpectralCapture.MODEL,(observed.metadata["optical_capture"] as Map<*,*>)["model"])
        assertArrayEquals(original,frozen.spectralComponentAmounts.values,0f)
        assertArrayEquals(originalRgb,frozen.opticalDensityRgb.values,0f)
        System.getenv("FILM_FOUNDRY_INTEROP_OUTPUT")?.let {
            val dir=Path.of(it);Files.createDirectories(dir)
            Files.write(dir.resolve("phone_pool_spectrum.darkroom_latent.npz"),latentBytes)
            Files.write(dir.resolve("phone_pool_spectrum.darkroom_negative.npz"),bytes)
        }
    }

    @Test
    fun flatAbsorbanceHasAnalyticTransmissionAndObserverIgnoresRgbMaster() {
        val recipe=recipe()
        val base=material(recipe)
        val flat = base + ("component_absorbance" to List(9) { k -> List(4) { if(k==0) 0.6 else 0.0 } })
        val amounts=FloatNd(intArrayOf(1,1,9),floatArrayOf(0.5f,0f,0f,0f,0f,0f,1f,0f,0f))
        val metadata=mapOf<String,Any?>(SpectralOptics.MEDIUM_KEY to mapOf("profile" to flat,
            "base_component_amounts" to listOf(0.0,0.0,0.0,0.0,0.0,0.0,1.0,0.0,0.0)))
        val optical=OpticalMaster(FloatNd(intArrayOf(1,1,3),floatArrayOf(9f,9f,9f)),metadata,spectralComponentAmounts=amounts)
        val capture=PhoneSpectralCapture(optical,observer(recipe))
        for(c in 0..2) {
            assertEquals(10.0.pow(-0.3).toFloat(),capture.transmission(0,c),1e-7f)
            assertEquals(1f,capture.baseTransmission(c),0f)
        }
    }

    @Test fun optionalSpectralPortPreservesTheCpuIntegralAndReadOnlyComponents() {
        val recipe=recipe()
        val formed=kernel.develop(kernel.expose(RgbVolume(3,1,linearRgb=FloatArray(9) { .1f+it*.07f }),recipe),recipe)
        val definition=SpectralOptics.objectValue(formed.metadata[SpectralOptics.MEDIUM_KEY])+ ("post_process_model" to SpectralOptics.POST_PROCESS_MODEL)
        val master=OpticalMaster(formed.opticalDensityRgb,formed.metadata+(SpectralOptics.MEDIUM_KEY to definition),formed.clearBaseOpticalDensityRgb,
            formed.spectralComponentAmounts,FloatNd(intArrayOf(1,3,3),floatArrayOf(-20f,-20f,-20f,.2f,.3f,.4f,0f,0f,0f)))
        val before=master.spectralComponentAmounts!!.values.copyOf()
        val cpu=PhoneObserve.capture(master,recipe,floatArrayOf(0f,0f,0f)).render(recipe,false,false)
        var used=false
        val port=object: DensityTransmissionCompute {
            override fun transmit(density: FloatArray): FloatArray=error("Spectral master cannot use RGB approximation")
            override fun transmitSpectral(amounts: FloatArray,post: FloatArray?,plan: SpectralTransmissionPlan): FloatArray {
                used=true;return plan.reference(amounts,post)
            }
        }
        val accelerated=PhoneObserve.capture(master,recipe,floatArrayOf(0f,0f,0f),transmissionCompute=port).render(recipe,false,false)
        assertTrue(used);assertArrayEquals(cpu.outputRgb.values,accelerated.outputRgb.values,0f)
        assertEquals(cpu.metadata,accelerated.metadata)
        assertArrayEquals(before,master.spectralComponentAmounts.values,0f)
    }

    @Test fun reusedSpectralScratchMatchesUncachedWavelengthSumExactlyInArbitraryPixelOrder() {
        val recipe=recipe();val profile=material(recipe);val seen=SpectralOptics.observer(observer(recipe))
        val material=SpectralOptics.material(profile)
        val amounts=FloatNd(intArrayOf(1,3,9),FloatArray(27) { (it%11)*.07f })
        val base=List(9) { it*.015 }
        val metadata=mapOf<String,Any?>(SpectralOptics.MEDIUM_KEY to mapOf("profile" to profile,"base_component_amounts" to base))
        val capture=PhoneSpectralCapture(OpticalMaster(FloatNd(intArrayOf(1,3,3),FloatArray(9)),metadata,spectralComponentAmounts=amounts),observer(recipe))
        val q=DoubleArray(seen.wave.size)
        for(n in 0 until q.size-1) { val half=(seen.wave[n+1]-seen.wave[n])*.5;q[n]+=half;q[n+1]+=half }
        val weights=Array(3) { c -> val raw=DoubleArray(q.size) { n -> seen.light[n]*seen.response[c][n]*q[n] };val sum=raw.sum();DoubleArray(q.size) { raw[it]/sum } }
        fun expected(pixel: Int?,c: Int): Float {
            var sum=0.0
            for(n in seen.wave.indices) {
                var density=0.0
                for(k in 0..8) density+=(if(pixel==null) base[k] else amounts.values[pixel*9+k].toDouble())*material.absorbance[k][n]
                sum+=10.0.pow(-density.coerceAtMost(80.0))*weights[c][n]
            }
            return sum.toFloat()
        }
        for(pixel in listOf(2,0,1,2)) for(c in listOf(2,0,1)) {
            assertEquals(expected(pixel,c),capture.transmission(pixel,c),0f)
            assertEquals(expected(null,c),capture.baseTransmission(c),0f)
        }
    }

    @Test
    fun varyingAbsorbanceIntegratesTransmissionBeforeTheObserver() {
        val recipe=recipe()
        val profile=material(recipe)
        val wave=profile["wavelengths_nm"] as List<*>
        val absorbance=List(9) { k -> List(wave.size) { i -> if(k==0 && i%2==0) 2.0 else 0.0 } }
        val amounts=FloatNd(intArrayOf(1,1,9),floatArrayOf(0.5f,0f,0f,0f,0f,0f,1f,0f,0f))
        val metadata=mapOf<String,Any?>(SpectralOptics.MEDIUM_KEY to mapOf("profile" to profile + ("component_absorbance" to absorbance),
            "base_component_amounts" to listOf(0.0,0.0,0.0,0.0,0.0,0.0,1.0,0.0,0.0)))
        val capture=PhoneSpectralCapture(OpticalMaster(FloatNd(intArrayOf(1,1,3),floatArrayOf(9f,9f,9f)),metadata,spectralComponentAmounts=amounts),observer(recipe))
        val seen=SpectralOptics.observer(observer(recipe))
        val quadrature=DoubleArray(seen.wave.size)
        for(n in 0 until quadrature.size-1) { val half=(seen.wave[n+1]-seen.wave[n])*0.5;quadrature[n]+=half;quadrature[n+1]+=half }
        for(c in 0..2) {
            val row=DoubleArray(quadrature.size) { seen.light[it]*seen.response[c][it]*quadrature[it] }
            val sum=row.sum()
            var expected=0.0
            var mean=0.0
            for(n in row.indices) {
                val even=n%2==0
                expected += 10.0.pow(if(even) -1.0 else 0.0) * row[n] / sum
                if(even) mean += 2.0 * row[n] / sum
            }
            assertEquals(expected.toFloat(),capture.transmission(0,c),1e-6f)
            assertTrue(kotlin.math.abs(expected-10.0.pow(-0.5*mean)) > 1e-4)
        }
    }

    @Test
    fun malformedCapabilityFailsBeforeConsumingPoolsOrReadingRgbFallback() {
        val recipe=recipe()
        val latent=kernel.expose(RgbVolume(1,1,linearRgb=FloatArray(3) {0.4f}),recipe)
        val invalid=material(recipe) + ("model" to "unknown")
        val state=LatentState(latent.arrays,latent.metadata + (SpectralOptics.MATERIAL_KEY to invalid))
        val before=latent.arrays.getValue("halide").values.copyOf()
        assertThrows(IllegalArgumentException::class.java) { kernel.develop(state,recipe,true) }
        assertArrayEquals(before,latent.arrays.getValue("halide").values,0f)
        val optical=kernel.develop(latent,recipe)
        assertThrows(IllegalArgumentException::class.java) {
            codec.writeDevelopedOptical(DevelopedOpticalArchive(optical.opticalDensityRgb,optical.metadata)) }
        val old=OpticalMaster(optical.opticalDensityRgb,emptyMap())
        assertThrows(IllegalArgumentException::class.java) { kernel.observe(old,recipe) }
        assertThrows(IllegalArgumentException::class.java) { kernel.observe(optical,recipe + mapOf(
            "observe_mode" to "viewer_light_table","viewer_illuminant" to "scene_camera")) }
    }

    @Test
    fun desktopFinalComponentsAndLatentAreReadableByIndependentPhoneKernel() {
        val developed=codec.readDevelopedOptical(javaClass.getResourceAsStream("/spectral_optics_python.darkroom_negative.npz")!!.use { it.readBytes() })
        val frozen=OpticalMaster(developed.opticalDensityRgb,developed.metadata,developed.clearBaseOpticalDensityRgb,developed.spectralComponentAmounts)
        assertTrue(kernel.observe(frozen,recipe()).outputRgb.values.all { it.isFinite() })
        val latent=codec.readLatent(javaClass.getResourceAsStream("/spectral_optics_python.darkroom_latent.npz")!!.use { it.readBytes() })
        val reduced=kernel.develop(LatentState(latent.arrays,latent.metadata),recipe())
        assertNotNull(reduced.spectralComponentAmounts)
        assertTrue(kernel.observe(reduced,recipe()).outputRgb.values.all { it.isFinite() })
    }

    @Test
    fun signedPostProcessOverlayIsPortableReadOnlyAndBaseIndependent() {
        val formed=kernel.develop(kernel.expose(RgbVolume(2,1,linearRgb=FloatArray(6) { .4f }),recipe()),recipe())
        val definition=SpectralOptics.objectValue(formed.metadata[SpectralOptics.MEDIUM_KEY]) +
            ("post_process_model" to SpectralOptics.POST_PROCESS_MODEL)
        val metadata=formed.metadata + (SpectralOptics.MEDIUM_KEY to definition)
        val overlay=FloatNd(intArrayOf(1,2,3),floatArrayOf(.2f,.2f,.2f,-20f,-20f,-20f))
        val archive=DevelopedOpticalArchive(formed.opticalDensityRgb,metadata,formed.clearBaseOpticalDensityRgb,
            formed.spectralComponentAmounts,overlay)
        val bytes=codec.writeDevelopedOptical(archive)
        val read=codec.readDevelopedOptical(bytes)
        assertArrayEquals(overlay.values,read.spectralPostProcessDensityRgb!!.values,0f)
        val frozen=OpticalMaster(read.opticalDensityRgb,read.metadata,read.clearBaseOpticalDensityRgb,
            read.spectralComponentAmounts,read.spectralPostProcessDensityRgb)
        val base=PhoneSpectralCapture(formed,observer(recipe()))
        val changed=PhoneSpectralCapture(frozen,observer(recipe()))
        for(c in 0..2) {
            assertEquals(base.transmission(0,c)*10.0.pow(-.2).toFloat(),changed.transmission(0,c),1e-7f)
            assertEquals(1f,changed.transmission(1,c),0f)
            assertEquals(base.baseTransmission(c),changed.baseTransmission(c),0f)
        }
        kernel.observe(frozen,recipe())
        assertArrayEquals(overlay.values,read.spectralPostProcessDensityRgb!!.values,0f)
        assertThrows(IllegalArgumentException::class.java) { codec.writeDevelopedOptical(archive.copy(spectralPostProcessDensityRgb=null)) }
        assertThrows(IllegalArgumentException::class.java) { codec.writeDevelopedOptical(archive.copy(metadata=formed.metadata)) }
        System.getenv("FILM_FOUNDRY_INTEROP_OUTPUT")?.let {
            Files.write(Path.of(it).resolve("phone_overlay.darkroom_negative.npz"),bytes)
        }
    }

    @Test
    fun desktopOverlayArchiveIsReadAndUsesTheSharedProxyBasis() {
        val path=System.getenv("FILM_FOUNDRY_OVERLAY_INPUT") ?: return
        val read=codec.readDevelopedOptical(Files.readAllBytes(Path.of(path)))
        assertNotNull(read.spectralPostProcessDensityRgb)
        val optical=OpticalMaster(read.opticalDensityRgb,read.metadata,read.clearBaseOpticalDensityRgb,
            read.spectralComponentAmounts,read.spectralPostProcessDensityRgb)
        val before=read.spectralPostProcessDensityRgb!!.values.copyOf()
        val capture=PhoneSpectralCapture(optical,observer(recipe()))
        assertTrue((0..2).all { capture.transmission(0,it).isFinite() })
        val directory=System.getenv("FILM_FOUNDRY_INTEROP_OUTPUT") ?: return
        Files.writeString(Path.of(directory).resolve("desktop_overlay_phone_capture.json"),
            (0..2).joinToString(prefix="[",postfix="]") { capture.transmission(0,it).toString() })
        assertArrayEquals(before,read.spectralPostProcessDensityRgb!!.values,0f)
    }
}
