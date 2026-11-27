package com.filmfoundry.workflow

import com.filmfoundry.interchange.StillFrameFormat
import com.filmfoundry.interchange.EdgeMarkPattern
import java.util.Locale

/** Loading owns this snapshot. Empty custom text uses the material label, plus the frame number. */
object FilmEdgeSettings {
    fun customLabel(text: String): String {
        require(text.all { it in 'A'..'Z' || it in 'a'..'z' || it==' ' }) {
            "Factory edge text accepts English letters and spaces"
        }
        return text.trim(' ').uppercase(Locale.ROOT).also { require(it.length<=32) }
    }
    fun patterns(values: Map<String,Any?>): Map<String,Any?> {
        val type=values["factory_mark_type"]?.toString() ?: "hard";require(type in listOf("hard","laser","laser_etched","ink"))
        val spread=(values["factory_mark_spread"] as? Number)?.toDouble() ?: 1.0;require(spread.isFinite() && spread in 0.0..3.0)
        val offset=(values["loading_offset"] as? Number)?.toDouble() ?: 0.0
        require(offset.isFinite() && offset in -0.5..0.5)
        val glow=values["rebate_glow"] ?: 0.0
        require(glow is Number && glow.toDouble() in 0.0..1.0)
        val batch=(values["factory_batch"] as? Number)?.toDouble() ?: 0.0
        require(batch in 0.0..4095.0 && batch % 1.0 == 0.0)
        return mapOf(
        "formation_mode" to (values["formation_mode"] ?: "attached").also { require(it in listOf("attached","native","sprocket_panorama")) },
        "factory_repeat" to "stock_frame", "lettering_size" to "format_mm", "transport_axis" to "format",
        "loading_offset" to offset, "factory_batch" to batch.toInt(),
        "factory_barcode" to (values["factory_barcode"]?.toString()?.takeIf { it in setOf("code128c","blocks") } ?: "none"),
        "advance_marker_style" to (values["advance_marker_style"]?.toString()?.takeIf { it in setOf("arrow","triangle","outline_pentagon","none") } ?: "arrow"),
        "advance_direction" to (if(values["advance_direction"]=="left") "left" else "right"),
        "factory_layout" to "text_code","strip_layout" to "registered_strip_v2",
        "factory_pattern_top" to EdgeMarkPattern.normalize(values["factory_pattern_top"]?.toString().orEmpty()),
        "factory_pattern_bottom" to EdgeMarkPattern.normalize(values["factory_pattern_bottom"]?.toString().orEmpty()),
        "factory_pattern_linked" to (values["factory_pattern_linked"]!=false),
        "factory_flip_top" to (values["factory_flip_top"]==true),"factory_flip_bottom" to (values["factory_flip_bottom"]==true),
        "factory_composition" to com.filmfoundry.interchange.EdgeComposition.parse(values["factory_composition"])?.record(),
        "factory_mark_type" to type,"factory_mark_spread" to spread) + com.filmfoundry.interchange.FactoryLamp.read(values).record() +
            (if(glow.toDouble()>0) mapOf("rebate_glow" to glow.toDouble()) else emptyMap()) +
            (if(type=="ink" || "factory_ink_transmission_rgb" in values)
                mapOf("factory_ink_transmission_rgb" to com.filmfoundry.interchange.FilmRebateGeometry.inkTransmission(values)) else emptyMap())
    }
    fun create(format: String,enabled: Boolean,materialLabel: String,custom: String="",marks: Boolean=true,patternSettings: Map<String,Any?> = emptyMap()): Map<String,Any?> {
        val selection=patterns(patternSettings).toMutableMap()
        if(format!="135" && selection["formation_mode"]=="sprocket_panorama") selection["formation_mode"]="attached"
        return frozenRecipe(mapOf(
        "enabled" to enabled,"format_key" to when { StillFrameFormat.is120(format)->"120";format=="half"->"half_frame";else->format },
        "sprocket_rows" to (if(format=="110") "bottom" else "both"),"factory_marks" to marks,"stock_label" to customLabel(custom).ifBlank { materialLabel })+selection+(if(format=="110") mapOf("sprocket_count" to 1) else emptyMap())+if(format=="6x4.5") mapOf("transport_axis" to "short_edge") else emptyMap())
    }
}
