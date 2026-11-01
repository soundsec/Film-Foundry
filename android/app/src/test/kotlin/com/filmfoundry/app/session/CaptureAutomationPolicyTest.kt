package com.filmfoundry.app.session

import com.filmfoundry.platform.NativeCaptureSettings
import org.junit.jupiter.api.Assertions.*
import org.junit.jupiter.api.Test

class CaptureAutomationPolicyTest {
    @Test fun oldManualFocusAndWhiteBalanceAreClearedWithoutLosingShutterRawOrFlashRequests() {
        val old=NativeCaptureSettings(iso=400,exposureNs=10_000_000,focusDiopters=2f,whiteBalance=5,lockAwb=true,format="raw",torch=true)
        val safe=CaptureAutomationPolicy.apply(old)
        assertNull(safe.focusDiopters);assertNull(safe.colorTemperatureK);assertEquals(1,safe.whiteBalance);assertFalse(safe.lockAwb)
        assertEquals(old.copy(focusDiopters=null,whiteBalance=1,lockAwb=false),safe)
        assertEquals(safe,CaptureAutomationPolicy.apply(safe));assertEquals(2f,old.focusDiopters)
        val temperature=NativeCaptureSettings(flash="auto",lockAe=true,colorTemperatureK=3200)
        assertEquals(temperature.copy(colorTemperatureK=null),CaptureAutomationPolicy.apply(temperature))
    }
}
