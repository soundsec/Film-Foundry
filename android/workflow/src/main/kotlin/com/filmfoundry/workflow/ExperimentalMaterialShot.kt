package com.filmfoundry.workflow

import com.filmfoundry.kernel.RgbVolume
import com.filmfoundry.kernel.materials.MaterialFamily
import com.filmfoundry.kernel.materials.PhoneMaterialKernel
import com.filmfoundry.kernel.materials.PhoneMaterialObserve

/** Pixels owned by the caller. The app does not need the kernel type. */
data class StillPixels(val width: Int, val height: Int, val values: FloatArray)
/** A finished single sheet. Peel-apart keeps the donor sheet until the user peels it off. */
data class MaterialPicture(val image: StillPixels, val peelSheet: StillPixels? = null)

/** Hidden single-shot materials. The code is an easter egg, not a recipe. */
object ExperimentalMaterialAccess {
    const val CODE = "FFC2:OPEN-MATERIALS"
    fun matches(text: String) = text.trim().replace(Regex("\\s+"), "").equals(CODE, ignoreCase = true)
    fun visible(developmentPath: Boolean, unlocked: Boolean) = developmentPath || unlocked
}

/** One still, one material, one observed image. It does not create a roll. */
object ExperimentalMaterialShot {
    const val BASIC_LONG_EDGE = 1280
    data class Choice(val id: String, val zh: String, val en: String, val family: MaterialFamily, val parameters: Map<String, Any?> = emptyMap())
    val choices = listOf(
        Choice("peel", "撕拉片", "Peel-apart", MaterialFamily.PEEL),
        Choice("instant", "拍立得", "Instant", MaterialFamily.INSTANT),
        Choice("silver", "银版", "Silver plate", MaterialFamily.SILVER),
        Choice("paper", "黑白相纸", "Black-and-white paper", MaterialFamily.PAPER),
        Choice("color-paper", "彩色相纸", "Color paper", MaterialFamily.PAPER, mapOf("kind" to "color")),
        Choice("wet", "湿版负片", "Wet-plate negative", MaterialFamily.WET),
        Choice("tintype", "锡版", "Tintype", MaterialFamily.WET, mapOf("support" to "tintype")),
        Choice("cyanotype", "蓝晒", "Cyanotype", MaterialFamily.CYANOTYPE),
        Choice("platinum", "铂钯", "Platinum", MaterialFamily.PLATINUM),
    )

    fun rotateClockwise(width: Int, height: Int, rgb: FloatArray, degrees: Int): StillPixels {
        require(rgb.size == width * height * 3)
        val turn = ((degrees % 360) + 360) % 360
        require(turn % 90 == 0) { "Rotation must be a quarter turn" }
        if (turn == 0) return StillPixels(width, height, rgb.copyOf())
        val outW = if (turn == 180) width else height
        val outH = if (turn == 180) height else width
        val out = FloatArray(rgb.size)
        for (y in 0 until height) for (x in 0 until width) {
            val dx: Int
            val dy: Int
            when (turn) {
                90 -> { dx = height - 1 - y; dy = x }
                180 -> { dx = width - 1 - x; dy = height - 1 - y }
                else -> { dx = y; dy = width - 1 - x }
            }
            val from = (y * width + x) * 3
            val to = (dy * outW + dx) * 3
            out[to] = rgb[from]; out[to + 1] = rgb[from + 1]; out[to + 2] = rgb[from + 2]
        }
        return StillPixels(outW, outH, out)
    }

    /** Box-filter the still down to the basic camera's long edge. Values stay in the caller's encoding. */
    fun fit(width: Int, height: Int, rgb: FloatArray, longEdge: Int = BASIC_LONG_EDGE): StillPixels {
        require(longEdge >= 2 && rgb.size == width * height * 3)
        val edge = maxOf(width, height)
        if (edge <= longEdge) return StillPixels(width, height, rgb.copyOf())
        val scale = edge.toDouble() / longEdge
        val outW = maxOf(1, (width / scale).toInt())
        val outH = maxOf(1, (height / scale).toInt())
        val out = FloatArray(outW * outH * 3)
        for (y in 0 until outH) for (x in 0 until outW) {
            val x0 = x * width / outW
            val x1 = maxOf(x0 + 1, (x + 1) * width / outW)
            val y0 = y * height / outH
            val y1 = maxOf(y0 + 1, (y + 1) * height / outH)
            var r = 0.0; var g = 0.0; var b = 0.0; var n = 0
            for (yy in y0 until y1) for (xx in x0 until x1) {
                val i = (yy * width + xx) * 3
                r += rgb[i]; g += rgb[i + 1]; b += rgb[i + 2]; n++
            }
            val o = (y * outW + x) * 3
            out[o] = (r / n).toFloat(); out[o + 1] = (g / n).toFloat(); out[o + 2] = (b / n).toFloat()
        }
        return StillPixels(outW, outH, out)
    }

    fun develop(image: StillPixels, choiceId: String, seed: Long = 1L): MaterialPicture {
        val choice = choices.firstOrNull { it.id == choiceId } ?: error("Unknown experimental material")
        val source = RgbVolume(image.width, image.height, linearRgb = image.values)
        var state = PhoneMaterialKernel.expose(choice.family, source, choice.parameters, groupId = "experimental-${choice.id}", seed = seed)
        for ((operation, parameters) in steps(choice)) state = PhoneMaterialKernel.process(state, operation, parameters)
        val bodies = PhoneMaterialKernel.finish(state).bodies
        val print = bodies.first { it.role != "donor_residue" && it.role != "silver_negative" }
        val cover = bodies.firstOrNull { it.role == "donor_residue" }
        fun pixels(body: com.filmfoundry.interchange.MaterialBody): StillPixels {
            val observed = PhoneMaterialObserve.observe(body)
            return StillPixels(observed.width, observed.height, observed.linearRgb)
        }
        return MaterialPicture(pixels(print), cover?.let(::pixels))
    }

    private fun steps(choice: Choice): List<Pair<String, Map<String, Any?>>> = when (choice.family) {
        MaterialFamily.PEEL -> listOf("spread" to emptyMap(), "advance" to mapOf("seconds" to 180.0, "light_exposure" to .3), "separate" to emptyMap())
        MaterialFamily.INSTANT -> listOf("eject" to emptyMap(), "advance" to mapOf("seconds" to 180.0, "light_exposure" to .3), "finish" to emptyMap())
        MaterialFamily.SILVER -> listOf("develop" to emptyMap(), "fix" to emptyMap(), "gild" to emptyMap(), "tarnish" to emptyMap())
        MaterialFamily.PAPER -> listOf("develop" to emptyMap(), (if (choice.parameters["kind"] == "color") "bleach_fix" else "fix") to emptyMap(), "wash" to emptyMap())
        MaterialFamily.WET -> listOf("develop" to emptyMap(), "fix" to emptyMap(), "wash" to emptyMap(), "dry" to mapOf("seconds" to 7200.0, "humidity" to 0.0), "varnish" to emptyMap())
        MaterialFamily.CYANOTYPE -> listOf("wash" to emptyMap(), "oxidize" to emptyMap())
        MaterialFamily.PLATINUM -> listOf("develop" to emptyMap(), "clear" to emptyMap(), "wash" to emptyMap())
    }
}
