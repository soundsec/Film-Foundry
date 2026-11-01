package com.filmfoundry.platform

/** Explicit native requests. Null ISO/shutter/focus leaves the corresponding native automation on. */
data class NativeCaptureSettings(val flash: String="off",val torch: Boolean=false,val lockAe: Boolean=false,
    val lockAwb: Boolean=false,val whiteBalance: Int=1,val iso: Int?=null,val exposureNs: Long?=null,
    val focusDiopters: Float?=null,val format: String="jpeg",val colorTemperatureK: Int?=null) {
    init {
        require(flash in listOf("off","auto","on") && format in listOf("jpeg","ultra_hdr","raw"))
        require((iso==null)==(exposureNs==null))
        require(iso==null || iso>0);require(exposureNs==null || exposureNs>0)
        require(focusDiopters==null || focusDiopters.isFinite() && focusDiopters>=0f)
        require(colorTemperatureK==null || colorTemperatureK in 2_000..10_000)
        require(!(iso!=null && (lockAe || flash!="off"))) { "Manual exposure cannot use AE lock or automatic flash" }
        require(!(torch && flash!="off")) { "Choose torch or photo flash" }
        require(colorTemperatureK==null || !lockAwb) { "Color temperature replaces AWB lock" }
    }
    fun metadata(): Map<String,Any?> = mapOf("model" to "phone_native_capture_requests_v1","flash" to flash,"torch" to torch,
        "ae_lock" to lockAe,"awb_lock" to lockAwb,"awb_mode" to whiteBalance,"color_temperature_k" to colorTemperatureK,"iso" to iso,
        "exposure_time_ns" to exposureNs,"focus_diopters" to focusDiopters,"format" to format)
    fun illuminationChoices(available: Boolean): List<String> = if(!available) listOf("off")
        else listOf("off")+(if(iso==null) listOf("auto","on") else emptyList())+"torch"
    fun withIllumination(mode: String): NativeCaptureSettings {
        require(mode in listOf("off","auto","on","torch"))
        return copy(flash=if(mode=="torch") "off" else mode,torch=mode=="torch")
    }
}

/** Relative RGGB gains for a requested color temperature. Green stays 1. This is the request, not a measured white point. */
internal fun colorTemperatureGains(kelvin: Int): FloatArray {
    val temperature=(kelvin.coerceIn(2_000,10_000)/100.0)
    val red=if(temperature<=66) 255.0 else 329.698727446*Math.pow(temperature-60,-0.1332047592)
    val green=if(temperature<=66) 99.4708025861*Math.log(temperature)-161.1195681661 else 255.0
    val blue=when {
        temperature>=66 -> 255.0
        temperature<=19 -> 0.0
        else -> 138.5177312231*Math.log(temperature-10)-305.0447927307
    }
    val greenScale=green.coerceIn(1.0,255.0)
    fun channel(value: Double)=(value.coerceIn(0.0,255.0)/greenScale).toFloat().coerceAtLeast(0.02f)
    return floatArrayOf(channel(red),1f,1f,channel(blue))
}
