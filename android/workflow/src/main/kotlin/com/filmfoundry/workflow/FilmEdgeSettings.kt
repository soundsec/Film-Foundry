package com.filmfoundry.workflow

import com.filmfoundry.interchange.StillFrameFormat
import com.filmfoundry.interchange.EdgeMarkPattern
import java.util.Locale

/** Loading owns this snapshot. Empty custom text uses the material label, plus the frame number. */
object FilmEdgeSettings {
    fun customLabel(text: String): String {
        val value=text.trim().uppercase(Locale.ROOT)
        require(value.length<=32 && value.all { it in 'A'..'Z' || it in '0'..'9' || it==' ' || it=='-' }) {
            "Factory edge text supports up to 32 letters, digits, spaces or hyphens"
        }
        return value
    }
    fun patterns(values: Map<String,Any?>): Map<String,Any?> {
        val type=values["factory_mark_type"]?.toString() ?: "hard";require(type in listOf("hard","laser"))
        val spread=(values["factory_mark_spread"] as? Number)?.toDouble() ?: 1.0;require(spread.isFinite() && spread in 0.0..3.0)
        return mapOf(
        "factory_pattern_top" to EdgeMarkPattern.normalize(values["factory_pattern_top"]?.toString().orEmpty()),
        "factory_pattern_bottom" to EdgeMarkPattern.normalize(values["factory_pattern_bottom"]?.toString().orEmpty()),
        "factory_pattern_linked" to (values["factory_pattern_linked"]!=false),
        "factory_flip_top" to (values["factory_flip_top"]==true),"factory_flip_bottom" to (values["factory_flip_bottom"]==true),
        "factory_mark_type" to type,"factory_mark_spread" to spread)
    }
    fun create(format: String,enabled: Boolean,materialLabel: String,custom: String="",marks: Boolean=true,patternSettings: Map<String,Any?> = emptyMap()): Map<String,Any?> = frozenRecipe(mapOf(
        "enabled" to enabled,"format_key" to when { StillFrameFormat.is120(format)->"120";format=="half"->"half_frame";else->format },
        "sprocket_rows" to "both","factory_marks" to marks,"stock_label" to customLabel(custom).ifBlank { materialLabel })+patterns(patternSettings))
}
