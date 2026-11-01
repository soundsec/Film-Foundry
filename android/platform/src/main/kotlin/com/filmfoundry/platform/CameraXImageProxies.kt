package com.filmfoundry.platform

import android.graphics.Bitmap
import android.graphics.BitmapFactory
import android.graphics.ImageFormat
import androidx.camera.core.ImageProxy
import java.nio.ByteBuffer

/**
 * Copy a CameraX [ImageProxy] into the JVM packet the capture bridge already
 * understands. Kernel never sees androidx types.
 */
object CameraXImageProxies {
    fun from(
        image: ImageProxy,
        useCase: CameraXUseCase,
        jpegDecoder: JpegStillDecoder = AndroidJpegDecoder,
        viewerPlanes: ViewerPlaneScratch?=null,
    ): CameraXImageProxy {
        val format = when (image.format) {
            ImageFormat.JPEG -> CameraXBufferFormat.JPEG
            ImageFormat.JPEG_R -> CameraXBufferFormat.JPEG
            ImageFormat.YUV_420_888 -> CameraXBufferFormat.YUV_420_888
            ImageFormat.RAW_SENSOR -> CameraXBufferFormat.RAW_SENSOR
            ImageFormat.FLEX_RGBA_8888 -> CameraXBufferFormat.RGBA_8888
            else -> throw CaptureRejected("unsupported CameraX image format ${image.format}.")
        }
        if (format == CameraXBufferFormat.RAW_SENSOR) {
            throw CaptureNotReady(
                "RAW ImageProxy is not a DNG file; CameraX DNG save is not wired.",
            )
        }
        val jpegBytes = if (format == CameraXBufferFormat.JPEG) planeBytes(image, 0) else null
        val decodedJpeg = jpegBytes?.let { jpegDecoder.decodeToEncodedRgb(it) }
        val encoded = when (format) {
            CameraXBufferFormat.JPEG -> decodedJpeg!!.encodedRgb
            CameraXBufferFormat.RGBA_8888 -> rgbaToEncoded(image)
            else -> null
        }
        val yuv = if (format == CameraXBufferFormat.YUV_420_888) yuv420(image,viewerPlanes) else null
        return CameraXImageProxy(
            useCase = useCase,
            format = format,
            width = decodedJpeg?.width ?: image.width,
            height = decodedJpeg?.height ?: image.height,
            rotationDegrees = image.imageInfo.rotationDegrees,
            encodedRgb = encoded,
            jpegBytes = jpegBytes,
            yuv = yuv,
            hdrGainMap = decodedJpeg?.hdrGainMap,
        )
    }

    private fun yuv420(image: ImageProxy,scratch: ViewerPlaneScratch?): Yuv420888 {
        val yPlane = image.planes[0]
        val uPlane = image.planes[1]
        val vPlane = image.planes[2]
        return Yuv420888(
            width = image.width,
            height = image.height,
            y = scratch?.copy(0,yPlane.buffer) ?: copyBuffer(yPlane.buffer),
            u = scratch?.copy(1,uPlane.buffer) ?: copyBuffer(uPlane.buffer),
            v = scratch?.copy(2,vPlane.buffer) ?: copyBuffer(vPlane.buffer),
            yRowStride = yPlane.rowStride,
            uRowStride = uPlane.rowStride,
            vRowStride = vPlane.rowStride,
            uPixelStride = uPlane.pixelStride,
            vPixelStride = vPlane.pixelStride,
        )
    }

    private fun rgbaToEncoded(image: ImageProxy): FloatArray {
        val bytes = copyBuffer(image.planes[0].buffer)
        val rgb = FloatArray(image.width * image.height * 3)
        var src = 0
        var dst = 0
        while (dst < rgb.size && src + 3 < bytes.size) {
            rgb[dst] = (bytes[src].toInt() and 0xFF) / 255f
            rgb[dst + 1] = (bytes[src + 1].toInt() and 0xFF) / 255f
            rgb[dst + 2] = (bytes[src + 2].toInt() and 0xFF) / 255f
            dst += 3
            src += 4
        }
        return rgb
    }

    private fun planeBytes(image: ImageProxy, index: Int): ByteArray {
        return copyBuffer(image.planes[index].buffer)
    }

    private fun copyBuffer(buffer: ByteBuffer): ByteArray {
        val duplicate = buffer.duplicate()
        val bytes = ByteArray(duplicate.remaining())
        duplicate.get(bytes)
        return bytes
    }
}

object AndroidJpegDecoder : JpegStillDecoder {
    override fun decodeToEncodedRgb(jpeg: ByteArray): EncodedRgbCapture {
        val options = BitmapFactory.Options().apply {
            inPreferredConfig = Bitmap.Config.ARGB_8888
            inScaled = false
        }
        val bitmap = BitmapFactory.decodeByteArray(jpeg, 0, jpeg.size, options)
            ?: throw CaptureRejected("JPEG still could not be decoded.")
        try {
            val width = bitmap.width
            val height = bitmap.height
            val rgb = ArgbRowConverter.convert(width, height) { pixels, start, rows ->
                bitmap.getPixels(pixels, 0, width, 0, start, width, rows)
            }
            val gainmap = AndroidHdrGainMaps.read(bitmap)
            return EncodedRgbCapture(width, height, rgb,
                kind = if (gainmap == null) CaptureKind.DISPLAY_REFERRED_SRGB else CaptureKind.ULTRA_HDR_GAINMAP,
                hdrGainMap = gainmap)
        } finally {
            bitmap.recycle()
        }
    }
}
