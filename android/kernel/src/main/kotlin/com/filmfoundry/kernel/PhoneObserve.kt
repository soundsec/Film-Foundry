package com.filmfoundry.kernel

import com.filmfoundry.interchange.DataKernel
import com.filmfoundry.interchange.FloatNd
import com.filmfoundry.interchange.SpectralOptics
import com.filmfoundry.interchange.PhoneScanModels
import kotlin.math.ln
import kotlin.math.max
import kotlin.math.min
import kotlin.math.pow

/**
 * Reduced transmissive observation. Shared capture matches desktop order:
 * T = 10^{-D} through an illuminant, clip to scanner_raw. Viewer stops there.
 * Calibrated v3 scans sample at neutral lamp EV and apply signed exposure after
 * normalization; temperature/device response remain in capture. V1/v2 stay frozen.
 * Scanner then uses desktop field names for mask/invert/normalize/curve.
 * Never writes optical_density_rgb.
 */
internal object PhoneObserve {
    fun run(
        optical: OpticalMaster,
        recipe: Map<String, Any?>,
        fallbackBase: FloatArray,
        linearOnly: Boolean = false,
        preparedDevice: PhoneScannerCapture? = null,
        transmissionCompute: DensityTransmissionCompute?=null,
    ): ScanObservation {
        return capture(optical,recipe,fallbackBase,preparedDevice,transmissionCompute=transmissionCompute).render(recipe,linearOnly,false)
    }
    fun capture(optical: OpticalMaster,recipe: Map<String,Any?>,fallbackBase: FloatArray,
        preparedDevice: PhoneScannerCapture?=null,checkCancel: (()->Unit)?=null,transmissionCompute: DensityTransmissionCompute?=null,
        retainTransport: Boolean=false): Captured {
        require(recipe[PhoneScanModels.OPTION]==null || recipe[PhoneScanModels.OPTION] in listOf(PhoneScanModels.LEGACY,PhoneScanModels.RESULT_BASE,PhoneScanModels.CALIBRATED,PhoneScanModels.CURRENT)) { "Unknown scan interpretation implementation" }
        val mode = observeMode(recipe)
        val lightTable = mode == "viewer_light_table"
        val projection = mode == "viewer_projection"
        val viewer = lightTable || projection
        val scanner = scannerMap(recipe)
        val device=if(viewer) null else preparedDevice ?: PhoneScannerCapture(scanner,recipe)
        val deviceActive=device?.active==true
        val presented = if (lightTable) OpticalGrid.Sized(optical, null) else present(optical, scanner, recipe)
        val working = presented.master
        val density = working.opticalDensityRgb
        if (density.shape.size != 3 || density.shape[2] != 3) {
            throw KernelException("optical_density_rgb shape must be HxWx3.")
        }
        val height = density.shape[0]
        val width = density.shape[1]
        val pixels = height * width
        val spectral = field(scanner, recipe, SpectralOptics.OBSERVER_KEY)?.let { PhoneSpectralCapture(working, it) }
        if (spectral != null && viewer && recipe["viewer_illuminant"] == "scene_camera")
            throw KernelException("spectral observation needs an explicit illuminant spectrum; scene RGB preview is insufficient.")
        val ev = numberOf(field(scanner, recipe, "transmission_light_ev"), 0f)
        val kelvin = numberOf(field(scanner, recipe, "transmission_light_temperature_k"), 5500f)
        val light = captureLight(recipe,viewer,spectral!=null,ev,kelvin)
        val sceneImage = if (lightTable && recipe["viewer_illuminant"]?.toString() == "scene_camera") {
            sceneImage(recipe)
        } else {
            null
        }
        if (sceneImage != null && (
                sceneImage.shape.size != 3 ||
                    sceneImage.shape[0] != height ||
                    sceneImage.shape[1] != width ||
                    sceneImage.shape[2] != 3
                )
        ) {
            throw KernelException("scene_illuminant_rgb_image must match optical HxWx3.")
        }
        val gain = if (sceneImage != null) {
            2.0.pow(ev.toDouble()).toFloat()
        } else {
            1f
        }
        // Interactive reuse keeps unilluminated transmission, not already clipped raw.
        // Spatial camera-light frames retain the existing per-frame capture path.
        val transport=retainTransport && sceneImage==null
        val base = working.clearBaseOpticalDensityRgb ?: fallbackBase
        val removeBaseDefault=if(recipe[PhoneScanModels.OPTION]==PhoneScanModels.LEGACY) true else PhoneScanDefaults.removeBase(positive(working),base)
        val baseRaw = FloatArray(3) { channel ->
            val transmittance = spectral?.baseTransmission(channel) ?: transmittanceOf(base[channel])
            if(transport) return@FloatArray transmittance
            val signal=transmittance*light[channel]
            if(deviceActive) signal else signal.coerceIn(1e-6f,1f)
        }
        if(!transport) device?.apply(baseRaw)
        // Spectral acceleration evaluates every wavelength with the same component spectra.
        // Unavailable/failed hardware returns null and keeps the original CPU integral.
        val transmissions=if(spectral==null) transmissionCompute?.transmit(density.values) else spectral.accelerated(transmissionCompute)
        if(transmissions!=null) require(transmissions.size==density.values.size && transmissions.all { it>=0f && it<=1.00001f })
        val raw = FloatArray(pixels * 3)
        val densityValues=density.values
        val sceneValues=sceneImage?.values
        for (i in 0 until pixels) {
            if(checkCancel!=null && i%width==0) checkCancel()
            for (channel in 0 until 3) {
                val transmittance = transmissions?.get(i*3+channel) ?: spectral?.transmission(i, channel) ?: transmittanceOf(densityValues[i * 3 + channel])
                if(transport) { raw[i*3+channel]=transmittance;continue }
                val local = if (sceneValues != null) {
                    max(sceneValues[i * 3 + channel] * gain, 1e-6f)
                } else {
                    light[channel]
                }
                val signal=transmittance*local
                raw[i*3+channel]=if(deviceActive) signal else signal.coerceIn(1e-6f,1f)
            }
        }
        if(!transport) device?.apply(raw)
        return Captured(samplingKey(recipe,transport),mode,width,height,raw,baseRaw,positive(working),removeBaseDefault,
            com.filmfoundry.interchange.FilmRebateGeometry.gateRows(working.metadata,height),
            gridRecord(optical,working,presented.sampler),
            if(spectral==null) emptyMap() else mapOf("optical_capture" to mapOf("model" to PhoneSpectralCapture.MODEL,
                "observer_profile_id" to spectral.profileId,"normalization" to "open_gate_per_channel_v1")),
            if(deviceActive) device!!.record() else null,device?.curveActive==true,transport,if(transport && deviceActive) device else null)
    }
    private fun samplingKey(recipe: Map<String,Any?>,transport: Boolean=false): Map<String,Any?> {
        val scanner=scannerMap(recipe)
        val keys=listOf("scan_grid_long_edge","transmission_light_ev","transmission_light_temperature_k","scanner_light_color",
            "scanner_response_matrix","device_response_rgb",SpectralOptics.OBSERVER_KEY)
        val fixed=if(transport) keys-filterLightKeys else keys
        return fixed.associateWith { freezeKey(field(scanner,recipe,it)) }+mapOf(PhoneScanModels.OPTION to recipe[PhoneScanModels.OPTION],"observe_mode" to observeMode(recipe),
            "viewer_illuminant" to recipe["viewer_illuminant"],"scene_illuminant_rgb" to freezeKey(recipe["scene_illuminant_rgb"]),
            "scene_illuminant_rgb_image" to freezeKey(recipe["scene_illuminant_rgb_image"]),"film" to freezeKey(recipe["film"]))
    }
    private val filterLightKeys=setOf("transmission_light_ev","transmission_light_temperature_k","scanner_light_color")
    private fun freezeKey(value: Any?): Any? = when(value) {
        is Map<*,*> -> value.entries.associate { it.key to freezeKey(it.value) }
        is List<*> -> value.map(::freezeKey)
        is FloatNd -> listOf(value.shape.toList(),value.values.toList())
        else -> value
    }
    class Captured(private val key: Map<String,Any?>,private val mode: String,private val width: Int,private val height: Int,
        private val samples: FloatArray,private val baseRaw: FloatArray,private val isPositive: Boolean,private val removeBaseDefault: Boolean,private val imageRows: IntRange,
        private val grid: Map<String,Any?>,private val spectralRecord: Map<String,Any?>,
        private val deviceRecord: Map<String,Any?>?,private val deviceCurve: Boolean,
        private val transport: Boolean=false,private val device: PhoneScannerCapture?=null): PreparedObservation {
        override val retainedBytes get()=samples.size.toLong()*4+baseRaw.size*4
        override fun matches(recipe: Map<String,Any?>)=key==samplingKey(recipe,transport)
        override fun observe(recipe: Map<String,Any?>): ScanObservation {
            require(matches(recipe)) { "Readout sampling parameters changed" }
            return render(recipe,false,true)
        }
        fun render(recipe: Map<String,Any?>,linearOnly: Boolean,copySamples: Boolean): ScanObservation {
        val raw=if(copySamples) samples.copyOf() else samples
        val scanner=scannerMap(recipe);val pixels=width*height
        val lightTable=mode=="viewer_light_table";val projection=mode=="viewer_projection";val viewer=lightTable || projection
        val litBase=if(transport) baseRaw.copyOf() else baseRaw
        if(transport) {
            val ev=numberOf(field(scanner,recipe,"transmission_light_ev"),0f)
            val kelvin=numberOf(field(scanner,recipe,"transmission_light_temperature_k"),5500f)
            val light=captureLight(recipe,viewer,spectralRecord.isNotEmpty(),ev,kelvin)
            val active=device?.active==true
            for(c in 0..2) { val signal=litBase[c]*light[c];litBase[c]=if(active) signal else signal.coerceIn(1e-6f,1f) }
            device?.apply(litBase)
            for(i in raw.indices) { val signal=raw[i]*light[i%3];raw[i]=if(active) signal else signal.coerceIn(1e-6f,1f) }
            device?.apply(raw)
        }
        if (!viewer) {
            interpretScanner(raw, litBase, pixels, scanner, recipe,isPositive,removeBaseDefault,!linearOnly,imageRows.first*width,imageRows.count()*width)
        } else if (projection && !isPositive) {
            projectNegative(raw, litBase, pixels)
        }
        val curve = if (viewer) null else parseCurve(field(scanner, recipe, "color_curve_rgb"))
        val tone = if (viewer) null else parseToneCurve(field(scanner, recipe, "tone_curve"))
        if(!linearOnly) encodeRows(raw,recipe,viewer,invertTransmission=booleanOf(field(scanner,recipe,"invert_transmission"),!isPositive))
        return ScanObservation(
            outputRgb = FloatNd(intArrayOf(height, width, 3), raw),
            metadata = observationMetadata(mode, lightTable, projection, isPositive, removeBaseDefault, scanner, recipe, curve != null, tone != null && tone.any { kotlin.math.abs(it.first - it.second) > 1e-7f }) +
                (if(deviceRecord!=null) mapOf("scanner_capture_response" to deviceRecord,"scanner_color_response" to mapOf(
                    "model" to "simple_channel_adjust_v1","curve" to if(curve!=null) "color_curve_rgb" else "omitted_v0",
                    "tone" to if(tone!=null && tone.any { kotlin.math.abs(it.first-it.second)>1e-7f }) "piecewise_output_luma_v1" else "none",
                    "device" to if(deviceCurve) "piecewise_scanner_raw_rgb_v1" else "omitted_v0")) else emptyMap()) + grid + spectralRecord,
        )
        }
    }

    internal fun encodeRows(raw: FloatArray,recipe: Map<String,Any?>,viewer: Boolean=false,invertTransmission: Boolean?=null) {
        encodeRows(raw,recipe,viewer,true,invertTransmission)
    }
    /** Developer fallback retains the preceding three separate pointwise passes. */
    internal fun encodeRowsReference(raw: FloatArray,recipe: Map<String,Any?>,viewer: Boolean=false,invertTransmission: Boolean?=null) {
        encodeRows(raw,recipe,viewer,false,invertTransmission)
    }
    private fun encodeRows(raw: FloatArray,recipe: Map<String,Any?>,viewer: Boolean,fused: Boolean,invertTransmission: Boolean?) {
        encodeRows(raw,outputEncoding(recipe,viewer,invertTransmission),fused)
    }
    internal fun outputEncoding(recipe: Map<String,Any?>,viewer: Boolean,invertTransmission: Boolean?): ScanOutputPlan.Encoding {
        val scanner=scannerMap(recipe)
        val curve=if(viewer) null else parseCurve(field(scanner,recipe,"color_curve_rgb"))
        val tone=if(viewer) null else parseToneCurve(field(scanner,recipe,"tone_curve"))
        val look=recipe["look"] as? Map<*,*>
        val lampEv=if(calibratedExposure(recipe,viewer)) numberOf(field(scanner,recipe,"transmission_light_ev"),0f) else 0f
        val invert=invertTransmission ?: booleanOf(field(scanner,recipe,"invert_transmission"),true)
        val effectiveEv=numberOf(look?.get("print_exposure_ev"),0f)+(if(invert) -lampEv else lampEv)
        val printGain=if(viewer) 1f else 2.0.pow(effectiveEv.toDouble()).toFloat()
        val lampColor=if(rangeFirstModel(recipe) && !viewer) {
            val cct=colorTemperatureRgb(numberOf(field(scanner,recipe,"transmission_light_temperature_k"),5500f))
            val neutral=colorTemperatureRgb(5500f)
            FloatArray(3) { c -> if(invert) neutral[c]/cct[c].coerceAtLeast(.02f) else cct[c]/neutral[c] }
        } else null
        val contrast=if(viewer) 1f else numberOf(look?.get("print_contrast"),1f).coerceAtLeast(.01f)
        require(contrast.isFinite()) { "look.print_contrast must be finite" }
        return ScanOutputPlan.Encoding(printGain,lampColor?.toList(),contrast,curve?.toList(),tone,if(viewer) null else ColorMixer.parse(field(scanner,recipe,"color_mixer")))
    }
    internal fun encodeRows(raw: FloatArray,encoding: ScanOutputPlan.Encoding,fused: Boolean=true) {
        val pixels=raw.size/3
        val printGain=encoding.printGain;val lampColor=encoding.lampValues;val contrast=encoding.contrast
        val curve=encoding.curveValues;val tone=encoding.tone
        if(fused) {
            for(i in raw.indices) {
                val linear=(if(printGain!=1f) raw[i]*printGain else raw[i])*(lampColor?.get(i%3) ?: 1f)
                // Reduced scan contrast, centred on linear half-white. Curves stay independent.
                val contrasted=if(contrast!=1f) (linear-.5f)*contrast+.5f else linear
                var value=encodeSrgb(contrasted.coerceAtLeast(0f))
                if(curve!=null) value=applyCurve(value,curve[i%3])
                raw[i]=value.coerceIn(0f,1f)
            }
        } else {
        if(printGain != 1f) for(i in raw.indices) raw[i]*=printGain
        if(lampColor!=null) for(i in raw.indices) raw[i]*=lampColor[i%3]
        if(contrast!=1f) for(i in raw.indices) raw[i]=(raw[i]-.5f)*contrast+.5f
        for (i in raw.indices) raw[i] = encodeSrgb(raw[i].coerceAtLeast(0f))
        for (i in raw.indices) {
            var value = raw[i]
            if (curve != null) value = applyCurve(value, curve[i % 3])
            raw[i] = value.coerceIn(0f, 1f)
        }
        }
        if (tone != null && tone.any { kotlin.math.abs(it.first - it.second) > 1e-7f }) {
            for (pixel in 0 until pixels) {
                val i = pixel * 3
                val brightness = raw[i] * 0.2126f + raw[i + 1] * 0.7152f + raw[i + 2] * 0.0722f
                val target = applyCurve(brightness, tone)
                for (channel in 0 until 3) {
                    raw[i + channel] = (if (brightness <= 1e-8f) target else raw[i + channel] * (target / brightness)).coerceIn(0f, 1f)
                }
            }
        }
        encoding.mixer?.apply(raw)
    }

    private fun interpretScanner(
        raw: FloatArray,
        baseRaw: FloatArray,
        pixels: Int,
        scanner: Map<String, Any?>?,
        recipe: Map<String, Any?>,
        positive: Boolean,
        removeBaseDefault: Boolean,
        normalize: Boolean = true,
        sampleStart: Int=0,samplePixels: Int=pixels,
    ) {
        val removeMask = booleanOf(field(scanner, recipe, "remove_base_mask"), removeBaseDefault)
        val invert = booleanOf(field(scanner, recipe, "invert_transmission"), !positive)
        val mapping=field(scanner,recipe,"print_mapping_mode")
        val sigmoid=mapping=="sigmoid"
        val rangeFirst=rangeFirstNegative(recipe,invert)
        val printlike=mapping=="printlike" || sigmoid
        val saturation = numberOf(field(scanner, recipe, "scan_saturation"), 1f)
        val legacy=recipe[PhoneScanModels.OPTION]==PhoneScanModels.LEGACY
        for (i in 0 until pixels) {
            var r = raw[i * 3]
            var g = raw[i * 3 + 1]
            var b = raw[i * 3 + 2]
            if (removeMask) {
                r = (r / baseRaw[0]).coerceAtLeast(1e-6f)
                g = (g / baseRaw[1]).coerceAtLeast(1e-6f)
                b = (b / baseRaw[2]).coerceAtLeast(1e-6f)
            }
            if (invert) {
                if (removeMask) {
                    r = (1f / r) * PhoneReducedKernel.OBSERVE_GAIN
                    g = (1f / g) * PhoneReducedKernel.OBSERVE_GAIN
                    b = (1f / b) * PhoneReducedKernel.OBSERVE_GAIN
                } else {
                    r = ((if(legacy) baseRaw[0] else 1f) / r) * PhoneReducedKernel.OBSERVE_GAIN
                    g = ((if(legacy) baseRaw[1] else 1f) / g) * PhoneReducedKernel.OBSERVE_GAIN
                    b = ((if(legacy) baseRaw[2] else 1f) / b) * PhoneReducedKernel.OBSERVE_GAIN
                }
                // Phone print-like proxy: clear film maps to zero image signal, not .18 grey.
                // Legacy recipes without this explicit mapping retain the previous interpretation.
                if(printlike) { r-=PhoneReducedKernel.OBSERVE_GAIN;g-=PhoneReducedKernel.OBSERVE_GAIN;b-=PhoneReducedKernel.OBSERVE_GAIN }
            }
            r = max(r, 0f)
            g = max(g, 0f)
            b = max(b, 0f)
            // Reciprocal negative readings are unbounded; the declared print
            // shoulder makes low/no normalization usable. Positive transmission stays direct.
            if(sigmoid && invert && !rangeFirst) { r/=1f+r;g/=1f+g;b/=1f+b }
            val luma = 0.2126f * r + 0.7152f * g + 0.0722f * b
            raw[i * 3] = luma + (r - luma) * saturation
            raw[i * 3 + 1] = luma + (g - luma) * saturation
            raw[i * 3 + 2] = luma + (b - luma) * saturation
        }
        if (normalize && booleanOf(field(scanner, recipe, "scan_normalize"), true)) {
            normalize(
                raw,
                pixels,
                numberOf(field(scanner, recipe, "scan_normalize_strength"), 0.45f).coerceIn(0f, 1f),
                field(scanner, recipe, "scan_normalize_mode")?.toString() ?: "luma",
                numberOf(field(scanner, recipe, "scan_black_percentile"), 0.3f),
                numberOf(field(scanner, recipe, "scan_white_percentile"), 99.7f),sampleStart,samplePixels,rangeFirst,
            )
        } else if(normalize && rangeFirst) boundNegative(raw)
    }

    internal fun normalize(
        linear: FloatArray,
        pixels: Int,
        strength: Float,
        mode: String,
        blackPercentile: Float,
        whitePercentile: Float,
        sampleStart: Int=0,samplePixels: Int=pixels,
        boundedInput: Boolean=false,
    ) {
        if (strength <= 0f || pixels <= 0) { if(boundedInput) boundNegative(linear);return }
        val measured = ScanNormalization.measure(linear, samplePixels, strength, mode, blackPercentile, whitePercentile,sampleStart)
        // Only application is partitioned. Every block uses the same whole-frame measurements.
        val blockPixels = 262144
        for (start in 0 until pixels step blockPixels) measured.apply(linear, start, min(blockPixels, pixels - start),boundedInput)
    }

    /** Developer fallback retaining the previous full-sort execution, never a user quality mode. */
    internal fun normalizeReference(
        linear: FloatArray,
        pixels: Int,
        strength: Float,
        mode: String,
        blackPercentile: Float,
        whitePercentile: Float,
    ) {
        if (strength <= 0f || pixels <= 0) {
            return
        }
        if (mode == "rgb") {
            // Private scratch only: never sort the optical master or capture in place.
            val samples = FloatArray(pixels)
            for (channel in 0 until 3) {
                for (i in 0 until pixels) samples[i] = linear[i * 3 + channel]
                samples.sort()
                val lo = sortedPercentile(samples, blackPercentile)
                val hi = sortedPercentile(samples, whitePercentile)
                val span = hi - lo
                if (span <= 1e-6f) continue
                for (i in 0 until pixels) {
                    val src = linear[i * 3 + channel]
                    val stretched = ((src - lo) / span).coerceIn(0f, 1f)
                    linear[i * 3 + channel] = src * (1f - strength) + stretched * strength
                }
            }
            return
        }
        val luma = FloatArray(pixels) { i ->
            0.2126f * linear[i * 3] + 0.7152f * linear[i * 3 + 1] + 0.0722f * linear[i * 3 + 2]
        }
        luma.sort()
        val lo = sortedPercentile(luma, blackPercentile)
        val hi = sortedPercentile(luma, whitePercentile)
        val span = hi - lo
        if (span <= 1e-6f) {
            return
        }
        for (i in 0 until pixels) {
            // Recompute from untouched RGB rather than retaining a second full-frame array.
            val y = 0.2126f * linear[i * 3] + 0.7152f * linear[i * 3 + 1] + 0.0722f * linear[i * 3 + 2]
            val stretched = ((y - lo) / span).coerceIn(0f, 1f)
            val gain = if (y > 1e-6f) {
                (y * (1f - strength) + stretched * strength) / y
            } else {
                0f
            }
            for (channel in 0 until 3) {
                linear[i * 3 + channel] = max(linear[i * 3 + channel] * gain, 0f)
            }
        }
    }

    private fun calibratedExposure(recipe: Map<String,Any?>,viewer: Boolean)=!viewer &&
        (recipe[PhoneScanModels.OPTION]==null || recipe[PhoneScanModels.OPTION] in listOf(PhoneScanModels.CURRENT,PhoneScanModels.CALIBRATED))

    private fun rangeFirstModel(recipe: Map<String,Any?>)=recipe[PhoneScanModels.OPTION]==null || recipe[PhoneScanModels.OPTION]==PhoneScanModels.CURRENT
    internal fun rangeFirstNegative(recipe: Map<String,Any?>,invert: Boolean)=rangeFirstModel(recipe) && invert && field(scannerMap(recipe),recipe,"print_mapping_mode")=="sigmoid"
    internal fun boundNegative(raw: FloatArray) { for(i in raw.indices) raw[i]=ScanNormalization.bounded(raw[i]) }

    private fun captureLight(recipe: Map<String,Any?>,viewer: Boolean,spectral: Boolean,ev: Float,kelvin: Float): FloatArray {
        // The calibrated phone scanner measures its base/device/normalization at
        // neutral lamp exposure. The signed user EV is applied once after calibration.
        // Frozen v1/v2 retain illuminated capture and the preceding cancellation behavior.
        val samplingEv=if(calibratedExposure(recipe,viewer)) 0f else ev
        if(!spectral) return illuminantRgb(recipe,viewer,samplingEv,if(!viewer && rangeFirstModel(recipe)) 5500f else kelvin)
        val residual=float3(field(scannerMap(recipe),recipe,"scanner_light_color")) ?: floatArrayOf(1f,1f,1f)
        val brightness=2.0.pow(samplingEv.toDouble()).toFloat()
        return FloatArray(3) { residual[it]*brightness }
    }

    private fun illuminantRgb(
        recipe: Map<String, Any?>,
        viewer: Boolean,
        ev: Float,
        kelvin: Float,
    ): FloatArray {
        val kind = recipe["viewer_illuminant"]?.toString() ?: "synthetic_light_table"
        if (viewer && kind == "scene_camera") {
            val scene = float3(recipe["scene_illuminant_rgb"])
                ?: throw KernelException("scene_camera illuminant requires scene_illuminant_rgb")
            val gain = 2.0.pow(ev.toDouble()).toFloat()
            return FloatArray(3) { max(scene[it] * gain, 1e-6f) }
        }
        if (kind !in DataKernel.PHONE_VIEWER_ILLUMINANT) {
            throw KernelException("unknown viewer_illuminant: $kind")
        }
        val cct = colorTemperatureRgb(kelvin)
        val gain = 2.0.pow(ev.toDouble()).toFloat()
        return FloatArray(3) { max(cct[it] * gain, 1e-6f) }
    }

    private fun colorTemperatureRgb(temperatureK: Float): FloatArray {
        val temp = temperatureK.coerceIn(1000f, 40000f) / 100f
        val red: Float
        val green: Float
        val blue: Float
        if (temp <= 66f) {
            red = 255f
            green = (99.4708025861 * ln(max(temp, 1e-6f).toDouble()) - 161.1195681661).toFloat()
            blue = if (temp <= 19f) {
                0f
            } else {
                (138.5177312231 * ln(max(temp - 10f, 1e-6f).toDouble()) - 305.0447927307).toFloat()
            }
        } else {
            red = (329.698727446 * (temp - 60.0).pow(-0.1332047592)).toFloat()
            green = (288.1221695283 * (temp - 60.0).pow(-0.0755148492)).toFloat()
            blue = 255f
        }
        val r = red.coerceIn(0f, 255f)
        val g = green.coerceIn(0f, 255f)
        val b = blue.coerceIn(0f, 255f)
        val peak = max(r, max(g, b)).coerceAtLeast(1e-6f)
        return floatArrayOf(r / peak, g / peak, b / peak)
    }

    private fun observeMode(recipe: Map<String, Any?>): String {
        val mode = recipe["observe_mode"]?.toString() ?: "scanner"
        if (mode !in DataKernel.PHONE_OBSERVE_MODES) {
            throw KernelException("unknown observe_mode: $mode")
        }
        return mode
    }

    private fun present(optical: OpticalMaster, scanner: Map<String, Any?>?, recipe: Map<String, Any?>): OpticalGrid.Sized {
        val raw = field(scanner, recipe, "scan_grid_long_edge") ?: return OpticalGrid.Sized(optical, null)
        val shape = optical.opticalDensityRgb.shape
        val grid=PhoneObservationGrid.resolve(shape[1],shape[0],raw)
        return OpticalGrid.resize(optical,grid.width,grid.height)
    }

    private fun positive(optical: OpticalMaster): Boolean {
        val text = optical.metadata["image_polarity"]?.toString()?.trim()?.lowercase().orEmpty()
        return "positive" in text || text == "slide"
    }

    private fun projectNegative(raw: FloatArray, baseRaw: FloatArray, pixels: Int) {
        for (i in 0 until pixels) {
            raw[i * 3] = ((baseRaw[0] / raw[i * 3]) * PhoneReducedKernel.OBSERVE_GAIN).coerceAtLeast(0f)
            raw[i * 3 + 1] = ((baseRaw[1] / raw[i * 3 + 1]) * PhoneReducedKernel.OBSERVE_GAIN).coerceAtLeast(0f)
            raw[i * 3 + 2] = ((baseRaw[2] / raw[i * 3 + 2]) * PhoneReducedKernel.OBSERVE_GAIN).coerceAtLeast(0f)
        }
    }

    private fun gridRecord(source: OpticalMaster, presented: OpticalMaster, sampler: String?): Map<String, Any?> {
        if (sampler == null) return emptyMap()
        val before = source.opticalDensityRgb.shape
        val after = presented.opticalDensityRgb.shape
        return mapOf("observation_grid" to mapOf(
            "model" to "scan_grid_v1", "sampler" to sampler,
            "source_width" to before[1], "source_height" to before[0],
            "width" to after[1], "height" to after[0]))
    }

    private fun observationMetadata(
        mode: String,
        lightTable: Boolean,
        projection: Boolean,
        positive: Boolean,
        removeBaseDefault: Boolean,
        scanner: Map<String, Any?>?,
        recipe: Map<String, Any?>,
        curveApplied: Boolean,
        toneApplied: Boolean,
    ): Map<String, Any?> {
        val viewer = lightTable || projection
        val payload = linkedMapOf<String, Any?>(
            "observation_mode" to mode,
            "observation_model" to when {
                projection -> DataKernel.PHONE_PROJECTION_MODEL_ID
                lightTable -> DataKernel.PHONE_VIEWER_MODEL_ID
                else -> DataKernel.PHONE_SCANNER_MODEL_ID
            },
            "transmission_capture" to DataKernel.PHONE_TRANSMISSION_CAPTURE_ID,
            "negative_interpretation" to when {
                !viewer && field(scanner,recipe,"print_mapping_mode")=="sigmoid" -> "phone_bounded_negative_print_v1"
                !viewer && field(scanner,recipe,"print_mapping_mode")=="printlike" -> "phone_negative_print_v1"
                else -> "legacy_reciprocal_v0"
            },
            "remove_base_mask" to when {
                projection -> !positive
                viewer -> false
                else -> booleanOf(field(scanner, recipe, "remove_base_mask"), removeBaseDefault)
            },
            "invert_transmission" to when {
                projection -> !positive
                viewer -> false
                else -> booleanOf(field(scanner, recipe, "invert_transmission"), !positive)
            },
            "scanner_color_response" to mapOf(
                "model" to "simple_channel_adjust_v1",
                "curve" to if (curveApplied) "color_curve_rgb" else "omitted_v0",
                "tone" to if (toneApplied) "piecewise_output_luma_v1" else "none",
                "device" to "omitted_v0",
            ),
            "viewer_illuminant" to if (viewer) {
                recipe["viewer_illuminant"]?.toString() ?: "synthetic_light_table"
            } else {
                "synthetic_light_table"
            },
        )
        if(!viewer) {
            val ev=numberOf(field(scanner,recipe,"transmission_light_ev"),0f)
            val invert=booleanOf(field(scanner,recipe,"invert_transmission"),!positive)
            val calibrated=calibratedExposure(recipe,false)
            payload["scanner_light_exposure"]=mapOf(
                "model" to if(calibrated) "neutral_calibration_then_signed_ev_v1" else "illuminated_capture_ev_v1",
                "parameter" to "scanner.transmission_light_ev",
                "stage" to if(calibrated) "post_normalization" else "transmission_capture",
                "lamp_ev" to ev,"effective_ev" to if(calibrated && invert) -ev else ev,
                "calibration_ev" to if(calibrated) 0f else ev)
        }
        if(!viewer) payload["interpretation_defaults"]=mapOf("model" to (recipe[PhoneScanModels.OPTION] ?: PhoneScanModels.CURRENT),
            "remove_base_mask" to removeBaseDefault,"invert_transmission" to !positive,"basis" to "final_medium_polarity_and_base_v1")
        if(!viewer && rangeFirstModel(recipe)) payload["scanner_lamp_color"]=mapOf("model" to "relative_cct_rgb_calibration_v1",
            "temperature_k" to numberOf(field(scanner,recipe,"transmission_light_temperature_k"),5500f),
            "reference_k" to 5500,"stage" to "post_normalization","inverted" to booleanOf(field(scanner,recipe,"invert_transmission"),!positive),
            "spectral_illuminant" to "observer_profile_unchanged")
        if (viewer && recipe["viewer_illuminant"]?.toString() == "scene_camera") {
            payload["viewer_illuminant_reduction"] = recipe["viewer_illuminant_reduction"]
                ?: if (recipe[DataKernel.PHONE_SCENE_ILLUMINANT_IMAGE_KEY] != null) {
                    DataKernel.PHONE_VIEWER_ILLUMINANT_REDUCTION[1]
                } else {
                    DataKernel.PHONE_VIEWER_ILLUMINANT_REDUCTION[0]
                }
        }
        return payload
    }

    private fun sceneImage(recipe: Map<String, Any?>): FloatNd? {
        val raw = recipe[DataKernel.PHONE_SCENE_ILLUMINANT_IMAGE_KEY] ?: return null
        return raw as? FloatNd
            ?: throw KernelException("scene_illuminant_rgb_image must be HxWx3.")
    }

    private fun parseToneCurve(raw: Any?): List<Pair<Float, Float>>? {
        if (raw == null) return null
        val points = raw as? List<*> ?: error("scanner.tone_curve must contain points")
        require(points.size >= 2) { "scanner.tone_curve needs at least two points" }
        val parsed = points.map { item ->
            val pair = item as? List<*> ?: error("scanner.tone_curve points must be pairs")
            require(pair.size == 2)
            val x = (pair[0] as? Number)?.toFloat() ?: error("Invalid tone x")
            val y = (pair[1] as? Number)?.toFloat() ?: error("Invalid tone y")
            require(x.isFinite() && y.isFinite() && x in 0f..1f)
            x to y
        }
        require(parsed.zipWithNext().all { (a, b) -> a.first < b.first }) { "Tone x must increase" }
        return parsed
    }

    private fun parseCurve(raw: Any?): Array<List<Pair<Float, Float>>>? {
        if(raw == null) return null
        val channels=raw as? List<*> ?: error("RGB curve must have three channels")
        require(channels.size == 3) { "RGB curve must have three channels" }
        return Array(3) { channel -> com.filmfoundry.interchange.PiecewiseCurve.parse(channels[channel],sort=true).points.map { it.x.toFloat() to it.y.toFloat() } }
    }

    private fun applyCurve(value: Float, points: List<Pair<Float, Float>>): Float {
        if (value <= points.first().first) {
            return points.first().second
        }
        if (value >= points.last().first) {
            return points.last().second
        }
        for (index in 0 until points.size - 1) {
            val left = points[index]
            val right = points[index + 1]
            if (value <= right.first) {
                val span = right.first - left.first
                val t = if (span <= 1e-6f) 0f else (value - left.first) / span
                return left.second + t * (right.second - left.second)
            }
        }
        return value
    }

    private fun sortedPercentile(sorted: FloatArray, percentile: Float): Float {
        if (sorted.isEmpty()) {
            return 0f
        }
        val t = (percentile.coerceIn(0f, 100f) / 100f) * (sorted.lastIndex)
        val lo = t.toInt().coerceIn(0, sorted.lastIndex)
        val hi = min(lo + 1, sorted.lastIndex)
        val frac = t - lo
        return sorted[lo] * (1f - frac) + sorted[hi] * frac
    }

    private fun transmittanceOf(density: Float): Float {
        return 10.0.pow((-max(density, 0f)).toDouble()).toFloat().coerceAtLeast(1e-6f)
    }

    internal fun encodeDisplaySample(linear: Float)=encodeSrgb(linear.coerceAtLeast(0f)).coerceIn(0f,1f)
    private fun encodeSrgb(linear: Float): Float {
        val u = linear.coerceIn(0f, 1f)
        return if (u <= 0.0031308f) {
            12.92f * u
        } else {
            (1.055 * u.toDouble().pow(1.0 / 2.4) - 0.055).toFloat()
        }
    }

    private fun scannerMap(recipe: Map<String, Any?>): Map<String, Any?>? {
        val raw = recipe["scanner"] as? Map<*, *> ?: return null
        return raw.entries.associate { it.key.toString() to it.value }
    }

    private fun field(scanner: Map<String, Any?>?, recipe: Map<String, Any?>, key: String): Any? {
        return scanner?.get(key) ?: recipe[key]
    }

    private fun numberOf(value: Any?, default: Float): Float {
        return when (value) {
            null -> default
            is Number -> value.toFloat()
            else -> default
        }
    }

    private fun booleanOf(value: Any?, default: Boolean): Boolean {
        return when (value) {
            null -> default
            is Boolean -> value
            else -> default
        }
    }

    private fun float3(value: Any?): FloatArray? {
        val list = value as? List<*> ?: return null
        if (list.size != 3) {
            return null
        }
        return FloatArray(3) { index ->
            val item = list[index]
            if (item is Number) max(item.toFloat(), 0f) else return null
        }
    }
}

