package com.filmfoundry.app.ui

import com.filmfoundry.interchange.RecipeJson
import com.filmfoundry.workflow.LightTableSettings
import com.filmfoundry.workflow.ScannerInterpretation

/** Style adjustments only; film interpretation and output size are independent. */
object ScanStyleChanges {
    enum class Kind { BRIGHTNESS,CONTRAST,COLOR,CURVES,LIGHT,TONE,DEVICE }
    fun between(baseline: Map<String,Any?>,current: Map<String,Any?>): Set<Kind> {
        fun section(recipe: Map<String,Any?>,key: String)=recipe[key] as? Map<*,*>
        val defaults=LightTableSettings().toRecipe()+ScannerInterpretation().toRecipe()+mapOf("scan_saturation" to 1.0)
        fun field(recipe: Map<String,Any?>,key: String)=section(recipe,"scanner")?.get(key) ?: recipe[key] ?: defaults[key]
        fun same(a: Any?,b: Any?)=if(a is Number && b is Number) a.toFloat()==b.toFloat() else RecipeJson.equivalent(a,b)
        val changes=linkedSetOf<Kind>()
        if(!same(section(baseline,"look")?.get("print_exposure_ev") ?: 0,section(current,"look")?.get("print_exposure_ev") ?: 0)) changes+=Kind.BRIGHTNESS
        if(!same(section(baseline,"look")?.get("print_contrast") ?: 1,section(current,"look")?.get("print_contrast") ?: 1)) changes+=Kind.CONTRAST
        val keys=(section(baseline,"scanner")?.keys.orEmpty()+section(current,"scanner")?.keys.orEmpty()+defaults.keys).filterIsInstance<String>().toSet()
        for(key in keys) {
            if(key=="scan_grid_long_edge" || key in StageOverrideValues.interpretationKeys || same(field(baseline,key),field(current,key))) continue
            changes+=when(key) {
                "scan_saturation"->Kind.COLOR
                "tone_curve","color_curve_rgb"->Kind.CURVES
                "transmission_light_ev","transmission_light_temperature_k","scanner_light_color"->Kind.LIGHT
                "scan_normalize","scan_normalize_strength","scan_normalize_mode","scan_black_percentile","scan_white_percentile"->Kind.TONE
                else->Kind.DEVICE
            }
        }
        return changes
    }
}
