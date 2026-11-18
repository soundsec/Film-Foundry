package com.filmfoundry.kernel.materials

import kotlin.math.*

/** Reduced bath access, paper hydration and the optical equations shared with
 * the desktop schemas this kernel emits. Structure fields are sampled here;
 * they are not the desktop OpenCV or millimetre grid. */
internal object PhoneMaterialBath {
    fun paperBase(family: MaterialFamily, material: Map<String, Any?>) =
        token(material, "paper_base", setOf("rc", "fiber"), if (family == MaterialFamily.PAPER) "rc" else "fiber")

    fun textureDefault(family: MaterialFamily) = when (family) {
        MaterialFamily.CYANOTYPE -> .45
        MaterialFamily.PLATINUM -> .3
        MaterialFamily.WET -> .15
        else -> .2
    }

    fun glossDefault(family: MaterialFamily) = if (family == MaterialFamily.PAPER) .35 else .15

    fun initialMoisture(family: MaterialFamily, material: Map<String, Any?>): Double {
        val humidity = if (family == MaterialFamily.PAPER) .5 else PhoneMaterialKernel.n(material, "humidity", .6, 0.0, 1.0)
        val bulk = if (paperBase(family, material) == "rc") .15 else 1.0
        return .12 * humidity * bulk
    }

    fun contact(parameters: Map<String, Any?>): BathContact {
        val region = token(parameters, "region", setOf("all", "spot"), "all")
        require(region == "all") { "Reduced materials do not apply local bath regions" }
        return BathContact(region,
            PhoneMaterialKernel.n(parameters, "position_x", .5, 0.0, 1.0),
            PhoneMaterialKernel.n(parameters, "position_y", .5, 0.0, 1.0),
            PhoneMaterialKernel.n(parameters, "radius", .25, .01, 2.0),
            PhoneMaterialKernel.n(parameters, "coverage", 1.0, 0.0, 1.0))
    }

    /** Time-integrated local water access. Moisture moves toward saturation; dose is the
     * integral used by washing and clearing, including a dry contact of zero. */
    fun hydrate(moisture: FloatArray, width: Int, height: Int, seconds: Double, temperature: Double,
        uptake: Double, rc: Boolean, bath: BathContact, check: () -> Unit = {}): FloatArray {
        val heat = 2.0.pow((temperature - 20) / 10)
        val tau = uptake * (if (rc) .35 else 1.0) / heat
        val dose = FloatArray(moisture.size)
        for (i in moisture.indices) {
            if (i and 4095 == 0) check()
            val wet = moisture[i].toDouble()
            val access = accessAt(i, width, height, bath)
            val gain = -expm1(-seconds * access / tau)
            moisture[i] = (wet + (1 - wet) * gain).toFloat()
            dose[i] = max(0.0, heat * (access * seconds - .75 * (1 - wet) * tau * gain)).toFloat()
        }
        return dose
    }

    fun dryPaper(moisture: FloatArray, seconds: Double, temperature: Double, drying: Double,
        airflow: Double, humidity: Double, rc: Boolean, check: () -> Unit = {}) {
        val heat = 2.0.pow((temperature - 20) / 10)
        val tau = drying * (if (rc) .2 else 1.0)
        val fraction = -expm1(-seconds / tau * airflow * heat)
        val target = .12 * humidity * (if (rc) .15 else 1.0)
        for (i in moisture.indices) {
            if (i and 4095 == 0) check()
            moisture[i] = (moisture[i] + (target - moisture[i]) * fraction).toFloat()
        }
    }

    fun dryWet(moisture: FloatArray, coating: FloatArray, seconds: Double, temperature: Double,
        drying: Double, humidity: Double, check: () -> Unit = {}) {
        val heat = 2.0.pow((temperature - 20) / 10)
        val rate = (1 - .9 * humidity) * heat / drying
        for (i in moisture.indices) {
            if (i and 4095 == 0) check()
            moisture[i] = (moisture[i] * exp(-rate * seconds * (2 - coating[i]))).toFloat()
        }
    }

    fun cyanPainter(width: Int, height: Int, seed: Long, material: Map<String, Any?>): (Int) -> CyanCoat {
        val texture = PhoneMaterialKernel.n(material, "texture", textureDefault(MaterialFamily.CYANOTYPE), 0.0, 1.0)
        val overlap = PhoneMaterialKernel.n(material, "brush_overlap", .35, 0.0, 1.0)
        val binding = PhoneMaterialKernel.n(material, "pigment_binding", .7, 0.0, 1.0)
        val direction = token(material, "brush_direction", setOf("down", "up", "left", "right"), "right")
        val reflectance = PhoneMaterialKernel.n(material, "paper_reflectance", .93, .01, 1.0)
        return { i ->
            val x = (i % width + .5) / width
            val y = (i / width + .5) / height
            val (along, across) = axes(x, y, direction)
            var brush = 0.0
            for (lane in 0 until 9) {
                val center = .02 + lane * (.96 / 8) + .04 * unit(PhoneMaterialKernel.noise(lane.toDouble(), 0.0, seed + 17))
                val widthLane = .08 * (1 + .25 * overlap)
                brush += exp(-((across - center) / widthLane).pow(4)) * .55
            }
            brush = brush.coerceIn(0.0, 1.0)
            val fibers = unit(PhoneMaterialKernel.noise(x * 6, y * 6, seed + 2602)).toFloat()
            var cover = brush * (1 - texture * .12 * fibers)
            val held = (binding * (.7 + .3 * fibers)).coerceIn(0.0, 1.0).toFloat()
            val paper = ((1 - texture * .16 * fibers).coerceIn(0.0, 1.0) * reflectance).toFloat()
            CyanCoat(cover.coerceIn(0.0, 1.0).toFloat(), paper, held, fibers)
        }
    }

    fun noblePainter(width: Int, height: Int, seed: Long, material: Map<String, Any?>): (Int) -> NobleCoat {
        val texture = PhoneMaterialKernel.n(material, "texture", textureDefault(MaterialFamily.PLATINUM), 0.0, 1.0)
        val sizing = PhoneMaterialKernel.n(material, "paper_sizing", .65, 0.0, 1.0)
        val method = token(material, "coating_method", setOf("rod", "brush"), "rod")
        val reflectance = PhoneMaterialKernel.n(material, "paper_reflectance", .93, .01, 1.0)
        return { i ->
            val x = (i % width + .5) / width
            val y = (i / width + .5) / height
            val horizontal = unit(PhoneMaterialKernel.noise(x * 12, y * 1.2, seed + 3601))
            val vertical = unit(PhoneMaterialKernel.noise(x * 1.2, y * 12, seed + 3602))
            val fibers = ((horizontal + vertical) / 2).toFloat()
            val absorb = ((1 - sizing) * (.45 + .55 * fibers) + sizing * .35).coerceIn(0.0, 1.0)
            var mottling = unit(PhoneMaterialKernel.noise(x * 2, y * 2, seed + 3603))
            if (method == "brush") mottling = .45 * mottling + .55 * unit(PhoneMaterialKernel.noise(x * 8, y * .4, seed + 3604))
            var cover = (1 - .2 * mottling) * (1 - .15 * absorb)
            cover *= 1 - texture * .12 * fibers
            val access = (1 - .55 * absorb).coerceIn(0.0, 1.0).toFloat()
            val paper = ((1 - texture * .13 * fibers).coerceIn(0.0, 1.0) * reflectance).toFloat()
            NobleCoat(cover.coerceIn(0.0, 1.0).toFloat(), paper, absorb.toFloat(), access, fibers)
        }
    }

    fun wetPainter(width: Int, height: Int, seed: Long, material: Map<String, Any?>): (Int) -> WetCoat {
        val texture = PhoneMaterialKernel.n(material, "texture", textureDefault(MaterialFamily.WET), 0.0, 1.0)
        val ridges = PhoneMaterialKernel.n(material, "flow_ridges", .25, 0.0, 1.0)
        val pooling = PhoneMaterialKernel.n(material, "edge_pooling", .2, 0.0, 1.0)
        val direction = token(material, "pour_direction", setOf("down", "up", "left", "right"), "down")
        return { i ->
            val x = (i % width + .5) / width
            val y = (i / width + .5) / height
            val (along, across) = axes(x, y, direction)
            var flow = 0.0
            for (band in 0 until 5) {
                val base = .1 + .8 * unit(PhoneMaterialKernel.noise(band.toDouble(), 5.0, seed + 1601))
                val bow = (unit(PhoneMaterialKernel.noise(band.toDouble(), 6.0, seed + 1602)) - .5) * .6
                val tilt = (unit(PhoneMaterialKernel.noise(band.toDouble(), 7.0, seed + 1603)) - .5) * .26
                val center = base + bow * (across - .5).pow(2) + tilt * (across - .5)
                val widthBand = .025 + .065 * unit(PhoneMaterialKernel.noise(band.toDouble(), 8.0, seed + 1607))
                flow += exp(-((along - center) / widthBand).pow(2)) * (.15 + .2 * unit(PhoneMaterialKernel.noise(band.toDouble(), 9.0, seed + 1608)))
            }
            flow = flow.coerceIn(0.0, 1.0)
            val thin = (1 - ridges * .8 * flow).coerceIn(0.0, 1.0)
            val pooled = pooling * exp(-(1 - along) / .055) * (1 - thin)
            val cover = thin + pooled
            val rough = (texture * (.15 + .85 * unit(PhoneMaterialKernel.noise(x * 9, y * 9, seed + 1611)))).coerceIn(0.0, 1.0)
            WetCoat(cover.coerceIn(0.0, 1.0).toFloat(), flow.toFloat(), rough.toFloat(), (1 - .12 * rough).coerceIn(0.0, 1.0).toFloat())
        }
    }

    /** Directional gloss factor. Illuminant and exposure are applied by the observer. */
    fun viewGloss(rough: Double, light: DoubleArray, eye: DoubleArray): Double {
        val hx = light[0] + eye[0]
        val hy = light[1] + eye[1]
        val hz = light[2] + eye[2]
        val length = sqrt(hx * hx + hy * hy + hz * hz)
        if (length < 1e-6) return 0.0
        val cosine = max(0.0, hz / length)
        val bounded = rough.coerceIn(0.0, 1.0)
        return cosine.pow(8 + 120 * (1 - bounded)) * max(0.0, light[2]) * max(0.0, eye[2])
    }

    fun dried(diffuse: Double, base: Double, wet: Double, surfaceGloss: Double, drydown: Double,
        texture: Double, rc: Boolean, light: DoubleArray, eye: DoubleArray): Double {
        val bulk = if (rc) .2 else 1.0
        val exponent = 1 + drydown * bulk * (1 - wet)
        val relative = if (base > 0) (diffuse / base).coerceIn(0.0, 1.0) else 0.0
        val image = base * relative.pow(exponent)
        val gloss = surfaceGloss + (1 - surfaceGloss) * .65 * wet
        val weight = .035 * gloss
        val rough = (.15 + .65 * (1 - gloss) + texture * .08).coerceIn(0.0, 1.0)
        return image * (1 - weight) + weight * viewGloss(rough, light, eye)
    }

    fun remit(paper: Double, absorption: Double, scatter: Double): Double {
        val k = max(absorption, 0.0) / max(scatter, 1e-4)
        return paper / (1 + k + sqrt(k * (k + 2)))
    }

    private fun accessAt(i: Int, width: Int, height: Int, bath: BathContact): Double {
        if (bath.region == "all" || bath.coverage == 0.0) return bath.coverage
        val scale = min(width, height).toDouble()
        val x = (i % width + .5 - bath.x * width) / scale
        val y = (i / width + .5 - bath.y * height) / scale
        val edge = ((bath.radius * bath.radius - x * x - y * y) / (.15 * bath.radius * bath.radius)).coerceIn(0.0, 1.0)
        return bath.coverage * edge * edge * (3 - 2 * edge)
    }

    private fun axes(x: Double, y: Double, direction: String) = when (direction) {
        "down" -> y to x
        "up" -> (1 - y) to x
        "right" -> x to y
        "left" -> (1 - x) to y
        else -> error("Invalid coating direction")
    }

    private fun unit(noise: Double) = ((noise + 1) * .5).coerceIn(0.0, 1.0)

    private fun token(map: Map<String, Any?>, key: String, allowed: Set<String>, default: String): String {
        val value = map[key] ?: return default
        require(value is String && value in allowed) { "Invalid $key" }
        return value
    }
}

internal data class BathContact(val region: String, val x: Double, val y: Double, val radius: Double, val coverage: Double)
internal data class CyanCoat(val coating: Float, val paper: Float, val binding: Float, val fibers: Float)
internal data class NobleCoat(val coating: Float, val paper: Float, val absorption: Float, val clearAccess: Float, val fibers: Float)
internal data class WetCoat(val coating: Float, val flow: Float, val roughness: Float, val surface: Float)
