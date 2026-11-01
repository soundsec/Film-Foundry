package com.filmfoundry.platform

import org.junit.jupiter.api.Assertions.*
import org.junit.jupiter.api.Test

class ProcessingMemoryTest {
    private val mib=1048576L
    @Test fun actualHeapAndNativeUsageBoundTheBudgetRatherThanAdvertisedLargeClass() {
        val m=ProcessingMemorySnapshot(512*mib,80*mib,32*mib,largeClassMiB=1024)
        assertEquals(368*mib,m.budgetBytes)
        m.requireAllocation(6000L*4000*8+16*mib,"24 MP decode")
        assertThrows(CaptureNotReady::class.java) { m.requireAllocation(8000L*6000*8+16*mib,"48 MP decode") }
    }
    @Test fun systemPressureAndUnknownSystemStateDoNotInventCapacity() {
        val m=ProcessingMemorySnapshot(512*mib,80*mib,32*mib,200*mib,100*mib)
        assertEquals(68*mib,m.budgetBytes)
        assertEquals(0L,m.copy(systemLowMemory=true).budgetBytes)
        assertEquals(0L,m.copy(heapUsed=600*mib).budgetBytes)
        assertTrue(m.copy(systemAvailable=null).budgetBytes>m.budgetBytes)
        assertThrows(CaptureNotReady::class.java) { m.copy(systemLowMemory=true).requireAllocation(1,"Decode") }
    }
}
