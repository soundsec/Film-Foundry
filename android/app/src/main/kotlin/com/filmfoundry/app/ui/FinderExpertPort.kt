package com.filmfoundry.app.ui

import com.filmfoundry.platform.NativeCaptureSettings

/** UI-facing native-camera port. No medium, processing recipe or synthetic meter value. */
interface FinderExpertPort {
    enum class Access { READY, FULL_EDITOR, NOT_CONNECTED, DEVICE_UNSUPPORTED, NOT_READY }
    enum class Control { ISO, SHUTTER, FOCUS, METER }
    data class State(val iso: Int?=null,val shutterNs: Long?=null,val focusDiopters: Float?=null,
        val isoAccess: Access=Access.NOT_READY,val shutterAccess: Access=Access.NOT_READY,
        val focusAccess: Access=Access.NOT_READY,val meterAccess: Access=Access.NOT_CONNECTED,
        val measuredExposureEv: Double?=null,val focusLimit: Float?=null)
    fun snapshot(): State
    fun openEditor(control: Control)
    // Future direct wheels can be connected without changing the finder or material contract.
    val setFocus: ((Float?)->Unit)? get()=null
    val setManualExposure: ((Int,Long)->Unit)? get()=null
    val applyNative: ((NativeCaptureSettings)->Unit)? get()=null
    /** Null returns exposure to automatic. A value is a temporary shutter for this shoot. */
    val setShutter: ((Long?)->Unit)? get()=null
}
