package com.filmfoundry.kernel

import org.junit.jupiter.api.Assertions.*
import org.junit.jupiter.api.Test

class RowScanStatisticsTest {
    @Test fun boundedRadixStatisticsMatchExactInMemoryRanksIncludingSignedZeroTiesAndHdr() {
        val values=FloatArray(713*3) { i -> when(i%17) { 0->-0f;1->0f;2->1e20f;3->-2f;else->((i*31)%701)/37f } }
        for(mode in listOf("luma","rgb")) {
            val reference=values.copyOf();val actual=values.copyOf()
            ScanNormalization.measure(reference,713,.73f,mode,.3f,99.7f).apply(reference)
            var passes=0
            val measured=ScanNormalization.measureRows(713,.73f,mode,.3f,99.7f) { consume ->
                passes++;for(start in 0 until 713 step 19) consume(values.copyOfRange(start*3,minOf(start+19,713)*3))
            }
            measured.apply(actual)
            assertArrayEquals(reference,actual,0f);assertEquals(2,passes)
        }
    }
}
