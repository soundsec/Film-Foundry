package com.filmfoundry.platform

/** Optional row math backend. Receives frozen numeric plans, never platform/UI or medium ownership. */
interface SrgbRowCompute: AutoCloseable,com.filmfoundry.kernel.DensityTransmissionCompute {
    override fun transmit(density: FloatArray): FloatArray=throw UnsupportedOperationException("Density compute unavailable")
    fun linearize(encoded: FloatArray): FloatArray
    override fun transmitSpectral(amounts: FloatArray,post: FloatArray?,plan: com.filmfoundry.kernel.SpectralTransmissionPlan): FloatArray =
        throw UnsupportedOperationException("Spectral compute unavailable")
    override fun close()=Unit
}

/** CPU default; worker-owned hardware is qualified and abandoned on failure. */
class DeferredPixelCompute(private val hardwareRequested: Boolean=false,
    private val factory: ()->SrgbRowCompute={GlesSrgbRowCompute()},
    private val report: (String)->Unit={},private val selectByTiming: Boolean=true,
    private val clockNs: ()->Long=System::nanoTime): AutoCloseable,com.filmfoundry.kernel.DensityTransmissionCompute {
    private var attempted=false
    private var accelerator: SrgbRowCompute?=null
    private var fallback: String?=null
    private var transmissionQualified=false
    private var srgbQualified=false
    private var spectralPlan: com.filmfoundry.kernel.SpectralTransmissionPlan?=null
    private var outputPlan: com.filmfoundry.kernel.ScanOutputPlan?=null
    private var outputBlocks=0
    private var outputSupported=true
    private var transmissionBlocks=0
    private var operation="srgb"
    private val cpuPreferred=mutableSetOf<String>()
    private val measured=mutableSetOf<String>()
    private val timingDetails=mutableMapOf<String,String>()
    private var usedHardware=false
    private var lastPublished: String?=null
    companion object {
        const val MAX_GPU_COMPONENTS=3*32768
        const val MAX_SPECTRAL_PIXELS=8192
    }
    override fun encodeOutput(linear: FloatArray,plan: com.filmfoundry.kernel.ScanOutputPlan): FloatArray? {
        require(linear.size%3==0 && linear.all { it>=-Float.MAX_VALUE && it<=Float.MAX_VALUE }) { "Invalid linear scan rows" }
        operation="scan_output";usedHardware=false;prepare()
        val backend=accelerator ?: run { publish();return null }
        try {
            fun verify(expected: FloatArray,actual: FloatArray) {
                require(actual.size==expected.size && actual.indices.all {
                    actual[it]>=0f && actual[it]<=1f && kotlin.math.abs(actual[it]-expected[it])<=2e-6f+kotlin.math.abs(expected[it])*2e-5f
                }) { "GPU scan output differs from CPU" }
            }
            if(outputPlan!==plan) {
                val edges=floatArrayOf(-4f,-.1f,-0f,0f,1e-8f,1e-6f,.0031307f,.0031308f,.0031309f,.18f,.5f,1f,4f,64f)
                val probe=FloatArray(256*3) { index ->
                    if(index<edges.size*3) edges[index/3] else if(index<128*3) ((index*17)%257)/256f
                    else linear.getOrElse(index-128*3) { .2f }
                }
                val expected=plan.reference(probe)
                val actual=backend.encodeOutput(probe.copyOf(),plan) ?: run {
                    outputPlan=plan;outputBlocks=0;outputSupported=false;cpuPreferred+=operation;publish();return null
                }
                verify(expected,actual)
                outputPlan=plan;outputBlocks=0;outputSupported=true;measured.remove(operation);cpuPreferred.remove(operation)
            }
            outputBlocks++
            // A first block may be rebate/black pixels. Revisit a CPU decision on later real
            // rows, at bounded checkpoints, rather than extrapolating that block to the image.
            if(outputSupported && operation in cpuPreferred && (outputBlocks==16 || outputBlocks==64 || outputBlocks==256)) {
                cpuPreferred.remove(operation);measured.remove(operation)
            }
            if(operation in cpuPreferred) { publish();return null }
            fun accelerated(): FloatArray {
                val result=FloatArray(linear.size);var start=0
                while(start<linear.size) {
                    val end=minOf(start+MAX_GPU_COMPONENTS,linear.size)
                    val part=backend.encodeOutput(linear.copyOfRange(start,end),plan) ?: error("GPU scan output became unavailable")
                    require(part.size==end-start && part.all { it>=0f && it<=1f }) { "Invalid GPU scan output" }
                    part.copyInto(result,start);start=end
                }
                return result
            }
            val measure=selectByTiming && operation !in measured
            if(measure) {
                // Warm real-sized buffers on both sides; compare full first-block output as well.
                // Subsequent timing includes upload, dispatch, readback, copies and guards.
                verify(plan.reference(linear),accelerated())
            }
            val start=if(measure) clockNs() else 0L
            val result=accelerated()
            if(measure) preferCpu(clockNs()-start) { plan.reference(linear) }?.let { publish();return it }
            usedHardware=true;publish();return result
        } catch(error: Exception) { disable(error);return null }
    }
    override fun transmitSpectral(amounts: FloatArray,post: FloatArray?,plan: com.filmfoundry.kernel.SpectralTransmissionPlan): FloatArray? {
        require(amounts.size%9==0 && (post==null || post.size==amounts.size/3))
        require(amounts.all { it>=-Float.MAX_VALUE && it<=Float.MAX_VALUE } &&
            (post==null || post.all { it>=-Float.MAX_VALUE && it<=Float.MAX_VALUE }))
        operation="spectral_transmission";prepare()
        val backend=accelerator ?: return null
        try {
            val previous=spectralPlan
            if(previous==null || previous.wavelengths!=plan.wavelengths ||
                !previous.absorbance.contentEquals(plan.absorbance) || !previous.weights.contentEquals(plan.weights) || !previous.overlay.contentEquals(plan.overlay)) {
                // Qualify each coefficient set, including clear film, dense film, each layer,
                // negative/positive overlays and actual incoming amounts.
                val sample=FloatArray(16*9) { index ->
                    val p=index/9;val k=index%9
                    when { p==0 -> 0f;p<=9 -> if(k==p-1) 1f else 0f;p==10 -> 4f;p==11 -> .01f
                        else -> amounts.getOrElse((p-12)*9+k) { .2f } }
                }
                val overlay=FloatArray(16*3) { index -> when(index/3) { 0 -> -1f;10 -> 3f;11 -> .2f;else -> post?.getOrElse(index%maxOf(post.size,1)) { 0f } ?: 0f } }
                for(extra in listOf(null,overlay)) {
                    val expected=plan.reference(sample,extra);val actual=backend.transmitSpectral(sample,extra,plan)
                    require(actual.size==expected.size && actual.indices.all { actual[it].isFinite() && kotlin.math.abs(actual[it]-expected[it])<=1e-8f+kotlin.math.abs(expected[it])*5e-5f }) { "GPU spectral transmission differs from CPU" }
                }
                spectralPlan=plan
                measured.remove(operation);cpuPreferred.remove(operation)
            }
            if(operation in cpuPreferred) { usedHardware=false;publish();return null }
            val measure=selectByTiming && operation !in measured
            if(measure) plan.reference(amounts,post) // warm the CPU loop before choosing
            val startNs=if(measure) clockNs() else 0L
            val pixels=amounts.size/9;val result=FloatArray(pixels*3)
            var start=0
            while(start<pixels) {
                val end=minOf(start+MAX_SPECTRAL_PIXELS,pixels)
                val part=backend.transmitSpectral(amounts.copyOfRange(start*9,end*9),post?.copyOfRange(start*3,end*3),plan)
                require(part.size==(end-start)*3 && part.all { it>=0f && it<=1.00001f }) { "Invalid GPU spectral output" }
                part.copyInto(result,start*3);start=end
            }
            if(measure) preferCpu(clockNs()-startNs) { plan.reference(amounts,post) }?.let {
                usedHardware=false;publish();return it
            }
            usedHardware=true;publish();return result
        } catch(error: Exception) { disable(error);return null }
    }
    fun linearize(encoded: FloatArray): FloatArray {
        require(encoded.all { it>=-Float.MAX_VALUE && it<=Float.MAX_VALUE }) { "Nonfinite encoded source" }
        operation="srgb";prepare()
        accelerator?.let { candidate -> if(!srgbQualified) {
            try {
                val probe=floatArrayOf(-.1f,0f,.003f,.04044f,.04045f,.04046f,.18f,.5f,.9f,1f,1.1f)
                val expected=TransferFunctions.srgbToLinear(probe);val actual=candidate.linearize(probe)
                require(actual.size==expected.size && actual.indices.all { actual[it].isFinite() && kotlin.math.abs(actual[it]-expected[it])<=2e-6f+expected[it]*2e-5f }) { "GPU sRGB qualification differs from CPU" }
                srgbQualified=true
            } catch(error: Exception) { disable(error) }
        } }
        select(encoded,false)?.let { return it }
        usedHardware=false;publish()
        return TransferFunctions.srgbToLinearInPlace(encoded)
    }
    override fun transmit(density: FloatArray): FloatArray {
        require(density.all { it>=-Float.MAX_VALUE && it<=Float.MAX_VALUE }) { "Nonfinite optical density" }
        operation="density_transmission";prepare()
        transmissionBlocks++
        if(operation in cpuPreferred && (transmissionBlocks==16 || transmissionBlocks==64 || transmissionBlocks==256)) {
            // The same startup/first-block bias applies before scanner interpretation too.
            cpuPreferred.remove(operation);measured.remove(operation)
        }
        accelerator?.let { candidate -> if(!transmissionQualified) {
            transmissionQualified=true
            try {
                val probe=floatArrayOf(-.1f,0f,.04f,.5f,1f,2f,4f,6f,12f)
                val expected=com.filmfoundry.kernel.DensityTransmissionCompute.reference(probe)
                val actual=candidate.transmit(probe)
                require(actual.size==expected.size && actual.indices.all { actual[it].isFinite() && kotlin.math.abs(actual[it]-expected[it])<=1e-8f+expected[it]*2e-5f }) { "GPU density transmission differs from CPU" }
            } catch(error: Exception) { disable(error) }
        } }
        select(density,true)?.let { return it }
        usedHardware=false;publish()
        return com.filmfoundry.kernel.DensityTransmissionCompute.reference(density)
    }
    private fun prepare() {
        if(hardwareRequested && !attempted) {
            attempted=true
            try {
                // Each operation qualifies its own formula before consuming image rows.
                accelerator=factory()
            } catch(error: Exception) { disable(error) }
        }
    }
    private fun select(input: FloatArray,transmission: Boolean): FloatArray? {
        if(operation in cpuPreferred) return null
        val measure=selectByTiming && accelerator!=null && operation !in measured
        fun cpu()=if(transmission) com.filmfoundry.kernel.DensityTransmissionCompute.reference(input)
            else TransferFunctions.srgbToLinearInPlace(input.copyOf())
        if(measure) cpu()
        val start=if(measure) clockNs() else 0L
        val gpu=accelerated(input,transmission) ?: return null
        if(measure) {
            preferCpu(clockNs()-start,::cpu)?.let {
                usedHardware=false;publish();return it
            }
        }
        usedHardware=true;publish();return gpu
    }
    private fun preferCpu(gpuNs: Long,cpu: ()->FloatArray): FloatArray? {
        var best=Long.MAX_VALUE;var result=FloatArray(0)
        repeat(2) { val start=clockNs();result=cpu();best=minOf(best,clockNs()-start) }
        measured+=operation
        timingDetails[operation]="GPU %.3f ms / CPU %.3f ms".format(java.util.Locale.ROOT,gpuNs/1_000_000.0,best/1_000_000.0)
        // Include transfers and require a margin. Keep the CPU path when timing is noisy.
        return if(gpuNs.toDouble()>=best*.85) { cpuPreferred+=operation;result } else null
    }
    private fun accelerated(encoded: FloatArray,transmission: Boolean): FloatArray? {
        accelerator?.let { backend ->
            try {
                // A formation block can exceed one GPU buffer. Never reduce image size or fall
                // back merely because the caller supplied several rows. Commit only on success.
                val result=FloatArray(encoded.size)
                var start=0
                while(start<encoded.size) {
                    val end=minOf(start+MAX_GPU_COMPONENTS,encoded.size)
                    val input=encoded.copyOfRange(start,end)
                    val part=if(transmission) backend.transmit(input) else backend.linearize(input)
                    val minimum=if(transmission) 1e-6f else 0f
                    require(part.size==end-start && part.all { it>=minimum && it<=1.00001f })
                    part.copyInto(result,start);start=end
                }
                return result
            } catch(error: Exception) { disable(error) }
        }
        return null
    }
    private fun disable(error: Exception) {
        fallback=error.message ?: error.javaClass.simpleName
        runCatching { accelerator?.close() };accelerator=null
        usedHardware=false
        publish()
    }
    private fun publish() {
        val actual=if(usedHardware) "gles31_${operation}_rows_v1" else "cpu_${operation}_rows_v1"
        val message=actual+(fallback?.let { " · CPU fallback: $it" } ?: if(operation in cpuPreferred) " · CPU selected after transfer timing"+(timingDetails[operation]?.let { " ($it)" } ?: "") else "")
        if(lastPublished!=message) { lastPublished=message;runCatching { report(message) } }
    }
    override fun close() {
        try { accelerator?.close() } finally {
            accelerator=null
            publish()
        }
    }
}
