package com.filmfoundry.app.session

import com.filmfoundry.platform.NativeCaptureSettings

/** Temporary product restriction, applied on every camera request, including rebinding. */
object CaptureAutomationPolicy {
    fun apply(settings: NativeCaptureSettings)=settings.copy(focusDiopters=null,whiteBalance=1,colorTemperatureK=null,lockAwb=false)
}
