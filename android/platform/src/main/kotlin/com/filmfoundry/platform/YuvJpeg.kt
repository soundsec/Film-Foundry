package com.filmfoundry.platform

import android.graphics.ImageFormat
import android.graphics.Rect
import android.graphics.YuvImage
import androidx.camera.core.ImageProxy
import java.io.ByteArrayOutputStream

/** Small motion frames become JPEG bytes. The pixel grid is the film; no rotation is stored. */
internal object YuvJpeg {
    fun encode(image: ImageProxy, quality: Int = 80): ByteArray {
        require(image.format == ImageFormat.YUV_420_888) { "motion frames require YUV_420_888" }
        val width = image.width
        val height = image.height
        val nv21 = ByteArray(width * height * 3 / 2)
        val y = image.planes[0]
        val u = image.planes[1]
        val v = image.planes[2]
        copyLuma(y, nv21, width, height)
        var offset = width * height
        val uBuffer = u.buffer
        val vBuffer = v.buffer
        val uStart = uBuffer.position()
        val vStart = vBuffer.position()
        for (row in 0 until height / 2) {
            for (col in 0 until width / 2) {
                nv21[offset++] = vBuffer.get(vStart + row * v.rowStride + col * v.pixelStride)
                nv21[offset++] = uBuffer.get(uStart + row * u.rowStride + col * u.pixelStride)
            }
        }
        val output = ByteArrayOutputStream()
        check(YuvImage(nv21, ImageFormat.NV21, width, height, null).compressToJpeg(Rect(0, 0, width, height), quality, output)) {
            "JPEG encoding failed"
        }
        return output.toByteArray()
    }

    private fun copyLuma(plane: ImageProxy.PlaneProxy, dest: ByteArray, width: Int, height: Int) {
        val buffer = plane.buffer
        val start = buffer.position()
        var offset = 0
        for (row in 0 until height) {
            val rowStart = start + row * plane.rowStride
            if (plane.pixelStride == 1) {
                buffer.position(rowStart)
                buffer.get(dest, offset, width)
                offset += width
            } else for (col in 0 until width) dest[offset++] = buffer.get(rowStart + col * plane.pixelStride)
        }
    }
}
