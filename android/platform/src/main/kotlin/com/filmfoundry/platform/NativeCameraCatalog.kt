package com.filmfoundry.platform

import android.annotation.SuppressLint
import android.hardware.camera2.CameraCharacteristics as C
import android.graphics.ImageFormat
import androidx.camera.camera2.interop.Camera2CameraInfo
import androidx.camera.core.CameraInfo
import androidx.camera.core.CameraSelector
import androidx.camera.core.ImageCapture
import androidx.camera.lifecycle.ProcessCameraProvider
import kotlin.math.sqrt

/** Enumerated CameraX endpoints, never guessed IDs or a manufacturer's advertised lens list. */
@SuppressLint("UnsafeOptInUsageError")
object NativeCameraCatalog {
    data class Snapshot(val lenses: List<Lens>,val defaultKey: String?)
    fun load(context: android.content.Context,done: (Result<Snapshot>)->Unit) {
        val future=ProcessCameraProvider.getInstance(context)
        val handler=android.os.Handler(android.os.Looper.getMainLooper())
        future.addListener({ done(runCatching {
            val provider=future.get();val lenses=read(provider)
            val defaultId=runCatching { Camera2CameraInfo.from(CameraSelector.DEFAULT_BACK_CAMERA.filter(provider.availableCameraInfos).first()).cameraId }.getOrNull()
            Snapshot(lenses,lenses.firstOrNull { it.cameraId==defaultId && it.physicalId==null }?.key)
        }) },java.util.concurrent.Executor { handler.post(it) })
    }
    data class Lens(val cameraId: String,val physicalId: String?,val info: CameraInfo,val characteristics: android.hardware.camera2.CameraCharacteristics,
        val front: Boolean,val logical: Boolean,val equivalentMm: Double?) {
        val key get()="$cameraId|${physicalId.orEmpty()}"
        fun label(zh: Boolean): String {
            val side=if(front) (if(zh) "前摄" else "Front") else (if(zh) "后摄" else "Rear")
            val mode=if(logical && physicalId==null) (if(zh) "自动镜头" else "Auto lenses") else equivalentMm?.let { String.format(java.util.Locale.ROOT,if(zh) "约 %.0fmm" else "~%.0fmm",it) } ?: (if(zh) "镜头" else "Lens")
            return "$side · $mode · ${physicalId ?: cameraId}"
        }
        fun selector(): CameraSelector=CameraSelector.Builder().addCameraFilter { infos -> infos.filter { Camera2CameraInfo.from(it).cameraId==cameraId } }
            .apply { physicalId?.let(::setPhysicalCameraId) }.build()
        fun sizes(format: Int=ImageFormat.JPEG): List<Pair<Int,Int>> = characteristics.get(C.SCALER_STREAM_CONFIGURATION_MAP)?.getOutputSizes(format).orEmpty()
            .map { it.width to it.height }.filter { it.first>0 && it.second>0 }.distinct().sortedByDescending { it.first.toLong()*it.second }
        fun fps(): List<Pair<Int,Int>> = characteristics.get(C.CONTROL_AE_AVAILABLE_TARGET_FPS_RANGES).orEmpty().map { it.lower to it.upper }.distinct()
        val raw get()=(characteristics.get(C.REQUEST_AVAILABLE_CAPABILITIES) ?: intArrayOf()).contains(C.REQUEST_AVAILABLE_CAPABILITIES_RAW)
        val rawCapture get()=physicalId==null && raw && runCatching {
            ImageCapture.getImageCaptureCapabilities(info).supportedOutputFormats.contains(ImageCapture.OUTPUT_FORMAT_RAW)
        }.getOrDefault(false)
        val ultraHdr get()=physicalId==null && android.os.Build.VERSION.SDK_INT>=34 && runCatching {
            ImageCapture.getImageCaptureCapabilities(info).supportedOutputFormats.contains(ImageCapture.OUTPUT_FORMAT_JPEG_ULTRA_HDR)
        }.getOrDefault(false)
        fun metadata(): Map<String,Any?> = mapOf("model" to "phone_native_camera_selection_v1","camera_id" to cameraId,
            "requested_physical_id" to physicalId,"logical_auto" to (logical && physicalId==null),"front" to front,"reference_equivalent_mm" to equivalentMm)
    }
    fun read(provider: ProcessCameraProvider): List<Lens> {
        val result=mutableListOf<Lens>()
        for(info in provider.availableCameraInfos) {
            val parent=runCatching { Camera2CameraInfo.from(info).cameraId }.getOrNull() ?: continue
            val physicals=runCatching { info.physicalCameraInfos.mapNotNull { physical -> runCatching { physical to Camera2CameraInfo.from(physical).cameraId }.getOrNull() } }.getOrDefault(emptyList())
            val entries=listOf<Pair<CameraInfo,String?>>(info to null)+physicals
            for((entry,physical) in entries) runCatching {
                val chars=Camera2CameraInfo.extractCameraCharacteristics(entry)
                val facing=chars.get(C.LENS_FACING)
                if(facing !in listOf(C.LENS_FACING_BACK,C.LENS_FACING_FRONT)) return@runCatching
                val focal=chars.get(C.LENS_INFO_AVAILABLE_FOCAL_LENGTHS)?.minOrNull()?.toDouble()
                val size=chars.get(C.SENSOR_INFO_PHYSICAL_SIZE)
                val equivalent=if(focal!=null && size!=null) referenceEquivalent(focal,size.width.toDouble(),size.height.toDouble()) else null
                val logical=(chars.get(C.REQUEST_AVAILABLE_CAPABILITIES) ?: intArrayOf()).contains(C.REQUEST_AVAILABLE_CAPABILITIES_LOGICAL_MULTI_CAMERA)
                result+=Lens(parent,physical,info,chars,facing==C.LENS_FACING_FRONT,logical,equivalent)
            }
        }
        return result.distinctBy { it.key }.sortedWith(compareBy<Lens> { it.front }.thenBy { it.physicalId!=null }.thenBy { it.equivalentMm ?: 0.0 })
    }
    /** Full-sensor diagonal reference only; not a claim about the final crop, EIS or fusion. */
    fun referenceEquivalent(focalMm: Double,widthMm: Double,heightMm: Double): Double? {
        if(!focalMm.isFinite() || !widthMm.isFinite() || !heightMm.isFinite() || focalMm<=0 || widthMm<=0 || heightMm<=0) return null
        return focalMm*sqrt(36.0*36+24.0*24)/sqrt(widthMm*widthMm+heightMm*heightMm)
    }
}
