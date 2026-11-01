package com.filmfoundry.platform

import com.filmfoundry.kernel.SpectralTransmissionPlan
import java.util.concurrent.CancellationException
import kotlin.math.abs

/** Bounded synthetic diagnostics. Uses production guards/transfers; never opens a media store.
 * Timing selection is disabled ONLY here so a slower GPU can be measured, not mislabelled CPU.
 */
class HardwareComputeSelfTest(private val factory: ()->SrgbRowCompute={ GlesSrgbRowCompute() }) {
    data class Measurement(val operation: String,val pixels: Int,val cpuMs: Double,val gpuMs: Double?,
        val maxAbsoluteError: Double?,val detail: String) {
        val recommendGpu get()=gpuMs!=null && gpuMs<cpuMs*.85
        fun values(): Map<String,Any?> = linkedMapOf("operation" to operation,"pixels" to pixels,
            "cpu_ms" to cpuMs,"gpu_ms" to gpuMs,"max_absolute_error" to maxAbsoluteError,
            "recommend_gpu" to recommendGpu,"detail" to detail)
    }
    data class Report(val initializationMs: Double,val measurements: List<Measurement>) {
        fun values(): Map<String,Any?> = linkedMapOf("schema" to "hardware_compute_self_test_v1",
            "initialization_ms" to initializationMs,"warmups" to 2,"samples" to 7,
            "transfer_included" to true,"measurements" to measurements.map { it.values() })
    }
    fun run(cancelled: ()->Boolean={false},progress: (String)->Unit={}): Report {
        val started=System.nanoTime()
        fun checkActive() {
            if(cancelled() || Thread.currentThread().isInterrupted) throw CancellationException("Self-test cancelled")
            check(System.nanoTime()-started<60_000_000_000L) { "Self-test exceeded 60 seconds" }
        }
        checkActive()
        var initializationNs=0L;var backend=""
        val results=mutableListOf<Measurement>()
        DeferredPixelCompute(true,{
            val start=System.nanoTime()
            try { factory() } finally { initializationNs+=System.nanoTime()-start }
        },{backend=it},selectByTiming=false).use { gpu ->
            DeferredPixelCompute(false).use { cpu ->
                fun measure(name: String,pixels: Int,absolute: Float,relative: Float,
                    calculate: (DeferredPixelCompute)->FloatArray) {
                    checkActive();progress(name)
                    val expected=calculate(cpu)
                    val cpuTimes=mutableListOf<Long>();val gpuTimes=mutableListOf<Long>()
                    var error=0.0;var failure: String?=null
                    fun cpuSample(timed: Boolean) {
                        checkActive();val start=System.nanoTime();calculate(cpu)
                        if(timed) cpuTimes+=System.nanoTime()-start
                    }
                    fun gpuSample(timed: Boolean) {
                        checkActive();if(failure!=null) return
                        val start=System.nanoTime();val actual=calculate(gpu);val elapsed=System.nanoTime()-start
                        if(!backend.startsWith("gles31_")) { failure=backend;return }
                        if(actual.size!=expected.size) { failure="GPU output size mismatch";return }
                        for(i in actual.indices) {
                            val delta=abs(actual[i]-expected[i])
                            if(!actual[i].isFinite() || delta>absolute+abs(expected[i])*relative) {
                                failure="GPU output differs from CPU";return
                            }
                            error=maxOf(error,delta.toDouble())
                        }
                        if(timed) gpuTimes+=elapsed
                    }
                    repeat(2) { cpuSample(false);gpuSample(false) }
                    // Alternate order to reduce systematic warmup/thermal bias; report medians.
                    repeat(7) { if(it%2==0) { cpuSample(true);gpuSample(true) } else { gpuSample(true);cpuSample(true) } }
                    fun median(times: List<Long>)=times.sorted()[times.size/2]/1_000_000.0
                    results+=Measurement(name,pixels,median(cpuTimes),
                        if(failure==null) median(gpuTimes) else null,if(failure==null) error else null,
                        failure ?: backend)
                }
                val pixels=DeferredPixelCompute.MAX_GPU_COMPONENTS/3
                // Real JPEG bytes exercise the existing exact CPU lookup table too.
                val jpeg=FloatArray(pixels*3) { (it%256)/255f }
                measure("jpeg_srgb",pixels,2e-6f,2e-5f) { it.linearize(jpeg.copyOf()) }
                // The row scanner normally supplies up to 16384 pixels; also show the backend's full batch.
                for(batch in listOf(16384,pixels)) {
                    val density=FloatArray(batch*3) { (it%251)*.025f-.1f }
                    measure("rgb_transmission",batch,1e-8f,2e-5f) { it.transmit(density) }
                }
                val spectralPixels=DeferredPixelCompute.MAX_SPECTRAL_PIXELS
                val amounts=FloatArray(spectralPixels*9) { (it%37)*.06f }
                val post=FloatArray(spectralPixels*3) { (it%13)*.04f-.2f }
                val waves=31
                val plan=SpectralTransmissionPlan(waves,DoubleArray(waves*9) { .03+(it%17)*.025 },
                    DoubleArray(waves*3) { 1.0/waves },DoubleArray(waves*3) { 1.0/3 })
                measure("spectral_31",spectralPixels,1e-8f,5e-5f) { it.transmitSpectral(amounts,post,plan) ?: plan.reference(amounts,post) }
                val outputInput=FloatArray(16384*3) { (it%127)*.012f }
                val curve=listOf(listOf(0.0,.01),listOf(.3,.2),listOf(.7,.82),listOf(1.0,1.0))
                val observation=com.filmfoundry.kernel.PhoneRowObservation(mapOf("scanner" to mapOf(
                    "print_mapping_mode" to "sigmoid","color_curve_rgb" to List(3){curve},"tone_curve" to curve),
                    "look" to mapOf("print_exposure_ev" to .2,"print_contrast" to 1.1)))
                observation.linear(com.filmfoundry.kernel.OpticalMaster(com.filmfoundry.interchange.FloatNd(intArrayOf(1,1,3),floatArrayOf(.1f,.2f,.3f)),emptyMap()))
                val outputPlan=observation.outputPlan(outputInput.size/3) { it(outputInput) }
                measure("scan_output",outputInput.size/3,2e-6f,2e-5f) { it.encodeOutput(outputInput,outputPlan) ?: outputPlan.reference(outputInput) }
            }
        }
        checkActive()
        return Report(initializationNs/1_000_000.0,results)
    }
}
