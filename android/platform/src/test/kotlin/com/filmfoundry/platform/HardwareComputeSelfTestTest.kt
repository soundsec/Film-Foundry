package com.filmfoundry.platform

import com.filmfoundry.kernel.DensityTransmissionCompute
import com.filmfoundry.kernel.SpectralTransmissionPlan
import org.junit.jupiter.api.Assertions.*
import org.junit.jupiter.api.Test
import java.util.concurrent.CancellationException

class HardwareComputeSelfTestTest {
    private open class Reference: SrgbRowCompute {
        var closed=false
        override fun linearize(encoded: FloatArray)=TransferFunctions.srgbToLinear(encoded)
        override fun transmit(density: FloatArray)=DensityTransmissionCompute.reference(density)
        override fun transmitSpectral(amounts: FloatArray,post: FloatArray?,plan: SpectralTransmissionPlan)=plan.reference(amounts,post)
        override fun encodeOutput(linear: FloatArray,plan: com.filmfoundry.kernel.ScanOutputPlan)=plan.reference(linear)
        override fun close() { closed=true }
    }
    @Test fun fallbackIsNeverReportedAsGpuTiming() {
        val report=HardwareComputeSelfTest { error("Device has no compute backend") }.run()
        assertEquals(5,report.measurements.size)
        assertTrue(report.measurements.all { it.gpuMs==null && !it.recommendGpu && it.detail.contains("CPU fallback") })
    }
    @Test fun detectsIncorrectLargeOutputEvenAfterSmallQualificationPasses() {
        val backend=object: Reference() {
            override fun transmit(density: FloatArray)=super.transmit(density).also { if(density.size>1000) it[300]=.55f }
        }
        val report=HardwareComputeSelfTest {backend}.run()
        assertNotNull(report.measurements.first().gpuMs)
        assertNull(report.measurements[1].gpuMs)
        assertEquals("GPU output differs from CPU",report.measurements[1].detail)
        assertNull(report.measurements[2].gpuMs)
        assertNotNull(report.measurements.last().gpuMs)
        assertTrue(backend.closed)
    }
    @Test fun cancellationReleasesOwnedBackendAndDoesNotPublishPartialResult() {
        val backend=Reference();var cancelled=false
        assertThrows(CancellationException::class.java) {
            HardwareComputeSelfTest {backend}.run({cancelled}) { if(it=="rgb_transmission") cancelled=true }
        }
        assertTrue(backend.closed)
        assertThrows(CancellationException::class.java) { HardwareComputeSelfTest {error("Must not initialize")}.run({true}) }
    }
}
