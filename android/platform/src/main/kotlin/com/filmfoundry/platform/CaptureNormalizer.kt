package com.filmfoundry.platform

import com.filmfoundry.interchange.DataKernel
import kotlin.math.pow

interface CaptureNormalizer {
    fun normalize(source: CaptureSource): WorkingExposure
    /** Ownership is explicitly transferred; compatibility implementations may still copy. */
    fun normalizeOwned(source: CaptureSource): WorkingExposure = normalize(source)
}

/**
 * Capture ingest, not formation.
 *
 * Display-encoded stills become the desktop-equivalent linear working space.
 * DNG uses an explicit native-sample demosaic/calibration model. Preview frames are
 * never a still. Multi-frame stacks add in linear, never blend encodings.
 */
class DefaultCaptureNormalizer : CaptureNormalizer {
    override fun normalizeOwned(source: CaptureSource): WorkingExposure = when(source) {
        is EncodedRgbCapture -> normalizeEncoded(source,consumeSource=true)
        is LinearRgbCapture -> normalizeLinear(source,consumeSource=true)
        else -> normalize(source)
    }

    override fun normalize(source: CaptureSource): WorkingExposure {
        return when (source) {
            is PreviewCapture -> throw CaptureRejected(
                "preview YUV/JPEG is not a still capture and is not a latent.",
            )
            is DngCapture -> DngRawDecoder.decode(source)
            is EncodedRgbCapture -> normalizeEncoded(source)
            is LinearRgbCapture -> normalizeLinear(source)
            is ExposureStackCapture -> normalizeStack(source)
        }
    }

    private fun normalizeEncoded(source: EncodedRgbCapture, consumeSource: Boolean = false): WorkingExposure {
        val hdrGainmap = when {
            source.kind == CaptureKind.ULTRA_HDR_GAINMAP && source.hdrGainMap != null -> "applied"
            source.kind == CaptureKind.ULTRA_HDR_GAINMAP && source.gainMap != null ->
                "present_not_applied"
            source.kind == CaptureKind.ULTRA_HDR_GAINMAP -> "absent"
            else -> "absent"
        }
        val linear = if(consumeSource) source.encodedRgb.also { rgb ->
            for(i in rgb.indices) rgb[i] = TransferFunctions.srgbToLinear(rgb[i])
        } else TransferFunctions.srgbToLinear(source.encodedRgb)
        source.hdrGainMap?.applyRowsInPlace(linear, source.width, 0, source.height)
        return WorkingExposure(
            width = source.width,
            height = source.height,
            linearRgb = linear,
            record = CaptureRecord(
                kind = source.kind,
                ispProcessed = source.ispProcessed,
                workingSpace = DataKernel.CAPTURE_WORKING_SPACE,
                sourceEncoding = if (source.hdrGainMap != null) {
                    "ultra_hdr_gainmap_full_linear_srgb_v1"
                } else if (source.kind == CaptureKind.ULTRA_HDR_GAINMAP) {
                    "ultra_hdr_sdr_base_iec61966_2_1"
                } else {
                    "iec61966_2_1_srgb"
                },
                bitDepth = source.bitDepth,
                hdrGainmap = hdrGainmap,
                sceneReferredClaim = false,
                orientationDegrees = source.orientationDegrees,
                exposureTimeNs = source.exposureTimeNs,
                iso = source.iso,
                frameCount = 1,
            ),
        )
    }

    private fun normalizeLinear(source: LinearRgbCapture, consumeSource: Boolean = false): WorkingExposure {
        if (source.claimedSceneReferred && source.ispProcessed) {
            throw CaptureRejected("ISP-processed frames cannot claim scene-referred irradiance.")
        }
        return WorkingExposure(
            width = source.width,
            height = source.height,
            linearRgb = if(consumeSource) source.linearRgb else source.linearRgb.copyOf(),
            record = CaptureRecord(
                kind = CaptureKind.LINEAR_WORKING_RGB,
                ispProcessed = source.ispProcessed,
                workingSpace = DataKernel.CAPTURE_WORKING_SPACE,
                sourceEncoding = "linear_float32",
                bitDepth = source.bitDepth,
                hdrGainmap = "absent",
                sceneReferredClaim = source.claimedSceneReferred,
                orientationDegrees = source.orientationDegrees,
                exposureTimeNs = source.exposureTimeNs,
                iso = source.iso,
                frameCount = 1,
            ),
        )
    }

    private fun normalizeStack(source: ExposureStackCapture): WorkingExposure {
        var initial: WorkingExposure? = normalize(source.frames.first())
        val first = initial!!.record
        val width = initial.width
        val height = initial.height
        val stacked = FloatArray(initial.linearRgb.size)
        var ispProcessed = false
        var allSceneReferred = true
        var unappliedGainmap = false
        var appliedGainmap = false
        var bitDepth: Int? = null
        for (index in source.frames.indices) {
            val frame = if(index == 0) initial!!.also { initial=null } else normalize(source.frames[index])
            if(frame.width != width || frame.height != height)
                throw CaptureRejected("exposure stack frames must share width and height.")
            val weight = 2.0.pow(source.relativeEv[index].toDouble()).toFloat()
            val rgb = frame.linearRgb
            for (i in rgb.indices) {
                stacked[i] += rgb[i] * weight
            }
            ispProcessed = ispProcessed || frame.record.ispProcessed
            allSceneReferred = allSceneReferred && frame.record.sceneReferredClaim
            unappliedGainmap = unappliedGainmap || frame.record.hdrGainmap == "present_not_applied"
            appliedGainmap = appliedGainmap || frame.record.hdrGainmap == "applied"
            frame.record.bitDepth?.let { bitDepth=maxOf(bitDepth ?: it,it) }
        }
        val sceneReferred = allSceneReferred && !ispProcessed
        val hdrGainmap = if (unappliedGainmap) {
            "present_not_applied"
        } else if (appliedGainmap) {
            "applied"
        } else {
            "absent"
        }
        return WorkingExposure(
            width = width,
            height = height,
            linearRgb = stacked,
            record = CaptureRecord(
                kind = CaptureKind.EXPOSURE_STACK,
                ispProcessed = ispProcessed,
                workingSpace = DataKernel.CAPTURE_WORKING_SPACE,
                sourceEncoding = "linear_sum_relative_ev",
                bitDepth = bitDepth,
                hdrGainmap = hdrGainmap,
                sceneReferredClaim = sceneReferred,
                orientationDegrees = first.orientationDegrees,
                exposureTimeNs = first.exposureTimeNs,
                iso = first.iso,
                frameCount = source.frames.size,
            ),
        )
    }
}
