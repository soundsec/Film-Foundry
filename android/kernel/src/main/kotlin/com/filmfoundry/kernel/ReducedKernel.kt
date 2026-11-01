package com.filmfoundry.kernel

import com.filmfoundry.interchange.FloatNd

class KernelNotReady(operation: String) : UnsupportedOperationException(
    "mobile reduced kernel is not implemented; $operation cannot fabricate a filter result.",
)

class KernelException(message: String) : IllegalArgumentException(message)

class RgbVolume(
    val width: Int,
    val height: Int,
    val channels: Int = 3,
    val linearRgb: FloatArray = FloatArray(width * height * channels),
) {
    init {
        if (width <= 0 || height <= 0) {
            throw KernelException("RGB volume dimensions must be positive.")
        }
        if (channels != 3) {
            throw KernelException("RGB volume must have 3 channels.")
        }
        val expected = Math.multiplyExact(Math.multiplyExact(width, height), channels)
        if (linearRgb.size != expected) {
            throw KernelException("linear RGB size ${linearRgb.size} does not match $expected.")
        }
    }
}

class LatentState(
    val arrays: Map<String, FloatNd>,
    val metadata: Map<String, Any?>,
)

class OpticalMaster(
    val opticalDensityRgb: FloatNd,
    val metadata: Map<String, Any?>,
    val clearBaseOpticalDensityRgb: FloatArray? = null,
    val spectralComponentAmounts: FloatNd? = null,
    val spectralPostProcessDensityRgb: FloatNd? = null,
)

class ScanObservation(
    val outputRgb: FloatNd,
    val metadata: Map<String, Any?>,
)

interface ReducedKernel {
    fun expose(linearRgb: RgbVolume, recipe: Map<String, Any?>): LatentState
    fun develop(
        latent: LatentState,
        recipe: Map<String, Any?>,
        consumeLatent: Boolean = false,
    ): OpticalMaster
    fun observe(optical: OpticalMaster, recipe: Map<String, Any?>): ScanObservation
}

/** Optional task-owned readout preparation. Samples are immutable; every observation owns its output. */
interface PreparedObservation {
    val retainedBytes: Long
    fun matches(recipe: Map<String,Any?>): Boolean
    fun observe(recipe: Map<String,Any?>): ScanObservation
}
interface ObservationPreparingKernel {
    fun prepareObservation(optical: OpticalMaster,recipe: Map<String,Any?>,checkCancel: ()->Unit={}): PreparedObservation
}

/** Row execution with full-frame provenance and explicit spatial source binding. No platform objects. */
interface RowReducedKernel : ReducedKernel {
    fun exposeRows(linearRgb: RgbVolume, recipe: Map<String, Any?>, fullHeight: Int): LatentState
    fun developRows(latent: LatentState, recipe: Map<String, Any?>, fullHeight: Int): OpticalMaster

    /** Task-local setup. Implementations may retain parameters, never pixel or material arrays. */
    fun prepareExposure(recipe: Map<String, Any?>, width: Int, fullHeight: Int): PreparedRowExposure =
        PreparedRowExposure { exposeRows(it, recipe, fullHeight) }

    /** First rows describe the source; an implementation must not retain their arrays. */
    fun prepareDevelopment(recipe: Map<String, Any?>, firstRows: LatentState, fullHeight: Int): PreparedRowDevelopment =
        PreparedRowDevelopment { developRows(it, recipe, fullHeight) }
}

/** Platform-free linear data access. Implementations may be file/decoder backed outside the kernel. */
interface LinearRgbRows {
    val width: Int
    val height: Int
    fun readRows(start: Int,count: Int): RgbVolume
    fun checkActive() {}
}

/** A task binding owns bounded spatial buffers. Prepared parameter objects remain pixel-free. */
interface BoundRowExposure: AutoCloseable {
    fun exposeRows(start: Int,count: Int): LatentState
    override fun close() {}
}

fun interface PreparedRowExposure {
    fun exposeRows(linearRgb: RgbVolume): LatentState
    fun bindSource(source: LinearRgbRows): BoundRowExposure = object: BoundRowExposure {
        override fun exposeRows(start: Int,count: Int)=exposeRows(source.readRows(start,count))
    }
}

fun interface PreparedRowDevelopment {
    fun developRows(latent: LatentState): OpticalMaster
    /** Absolute material row; spatial implementations must not depend on call order. */
    fun developRowsAt(latent: LatentState, startRow: Int): OpticalMaster = developRows(latent)
    fun bindSource(source: LatentRows): BoundRowDevelopment = object: BoundRowDevelopment {
        override fun developRows(start: Int,count: Int)=developRowsAt(source.readRows(start,count),start)
    }
}

/** Full latent source access; the binding never owns or mutates the source. */
interface LatentRows {
    val width: Int
    val height: Int
    fun readRows(start: Int,count: Int): LatentState
    fun checkActive() {}
    /** Scratch reads per pass. Pixel visit order does not depend on this length. */
    val readRowsPerPass: Int
        get() = kotlin.math.max(1, kotlin.math.min(height, 16384 / width))
}

interface BoundRowDevelopment: AutoCloseable {
    /** Independent row computations; the caller must serialize a shared source reader. */
    val concurrentRows: Boolean get()=false
    fun developRows(start: Int,count: Int): OpticalMaster
    override fun close() {}
}

class NotReadyKernel : ReducedKernel {
    override fun expose(linearRgb: RgbVolume, recipe: Map<String, Any?>): LatentState {
        throw KernelNotReady("expose")
    }

    override fun develop(
        latent: LatentState,
        recipe: Map<String, Any?>,
        consumeLatent: Boolean,
    ): OpticalMaster {
        throw KernelNotReady("develop")
    }

    override fun observe(optical: OpticalMaster, recipe: Map<String, Any?>): ScanObservation {
        throw KernelNotReady("observe")
    }
}
