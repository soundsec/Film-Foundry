package com.filmfoundry.platform

import android.content.Context
import android.graphics.ImageFormat
import android.hardware.camera2.CameraCharacteristics
import android.hardware.camera2.CameraManager
import android.util.Range

/** Supported still sizes and frame-rate ranges. These are device capabilities, not a chosen film grid. */
object CameraOutputCatalog {
    fun jpegSizes(context: Context,lens: NativeCameraCatalog.Lens?=null,format: Int=ImageFormat.JPEG): List<Pair<Int, Int>> {
        val characteristics = lens?.characteristics ?: backCamera(context) ?: return emptyList()
        val map = characteristics.get(CameraCharacteristics.SCALER_STREAM_CONFIGURATION_MAP) ?: return emptyList()
        return map.getOutputSizes(format).orEmpty()
            .map { it.width to it.height }
            .filter { it.first > 0 && it.second > 0 }
            .distinct()
            .sortedByDescending { it.first.toLong() * it.second }
    }

    fun fpsRanges(context: Context,lens: NativeCameraCatalog.Lens?=null): List<Pair<Int, Int>> {
        val characteristics = lens?.characteristics ?: backCamera(context) ?: return emptyList()
        val ranges = characteristics.get(CameraCharacteristics.CONTROL_AE_AVAILABLE_TARGET_FPS_RANGES) ?: return emptyList()
        return ranges.map { it.lower to it.upper }.distinct()
    }

    private fun backCamera(context: Context): CameraCharacteristics? {
        val manager = context.getSystemService(Context.CAMERA_SERVICE) as CameraManager
        val id = manager.cameraIdList.firstOrNull { camera ->
            manager.getCameraCharacteristics(camera).get(CameraCharacteristics.LENS_FACING) == CameraCharacteristics.LENS_FACING_BACK
        } ?: manager.cameraIdList.firstOrNull() ?: return null
        return manager.getCameraCharacteristics(id)
    }
}
