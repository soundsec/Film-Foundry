package com.filmfoundry.platform

import java.io.File

data class StoredStillInfo(val width: Int,val height: Int,val record: CaptureRecord,val extra: Map<String,Any?> = emptyMap())

/** Capture into an owned file without first decoding a full FP32 RGB image. */
interface StoredStillCamera {
    fun sourceFileName(): String = "source.jpg"
    fun saveStill(target: File): StoredStillInfo
    fun plannedOrientationDegrees(): Int? = null
    fun plannedDimensions(): Pair<Int,Int>? = null
}
