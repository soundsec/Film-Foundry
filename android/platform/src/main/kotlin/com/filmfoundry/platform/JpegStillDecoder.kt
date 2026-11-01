package com.filmfoundry.platform

fun interface JpegStillDecoder {
    fun decodeToEncodedRgb(jpeg: ByteArray): EncodedRgbCapture
}
