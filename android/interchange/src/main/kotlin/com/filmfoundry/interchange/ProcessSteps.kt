package com.filmfoundry.interchange

/** Shared process vocabulary; formulas and runtime stay independent on desktop and phone. */
object ProcessSteps {
    val actions=listOf("develop_silver","develop_color","activate_remaining_halide","bleach_silver","fix_halide","remove_silver","destroy_dye","bleach_mask_dye","remove_auxiliary")
    val programs=listOf("auto","bw_negative","color_negative","color_negative_bleach_bypass","bw_reversal","color_reversal","custom")
    val developers=listOf("standard","fine_grain","compensating","high_acutance","high_contrast","push","exhausted","monobath")
    val fixers=listOf("standard","rapid","hardening","monobath")
    fun validateMaterial(material: Map<String,Any?>) {
        for(key in listOf("cross_process_silver_development","cross_process_activation","cross_process_silver_bleach","cross_process_halide_fixing","cross_process_silver_removal","cross_process_dye_stability","cross_process_auxiliary_removal","experimental_mask_bleach_susceptibility","experimental_mask_bleach_dye_damage","auxiliary_layer_amount")) material[key]?.let {
            require(it is Number && it.toDouble().isFinite() && it.toDouble() in 0.0..1.0) { "invalid $key" }
        }
        material["cross_process_dye_coupling"]?.let { require(it is Number && it.toDouble().isFinite() && it.toDouble() in 0.0..2.0) }
        material["cross_process_layer_balance"]?.let { raw -> require(raw is List<*> && raw.size==3 && raw.all { it is Number && it.toDouble().isFinite() && it.toDouble() in 0.0..2.0 }) }
    }
    private val ranges=mapOf("strength" to (0.0..1.0),"time_min" to (.1..120.0),"temperature_c" to (0.0..60.0),"concentration" to (.05..4.0),"agitation" to (0.0..3.5),"agitation_time_s" to (0.0..7200.0),
        "push_stops" to (-6.0..6.0),"developer_exhaustion" to (0.0..1.0),"fixer_exhaustion" to (0.0..1.0),"compensation" to (0.0..1.0),"silver_retention" to (0.0..1.0),"dye_damage_strength" to (0.0..1.0))
    fun validateConditions(value: Map<String,Any?>) {
        value["mask_bleach_completion"]?.let { require(it is Number && it.toDouble().isFinite() && it.toDouble() in 0.0..1.0) { "mask_bleach_completion must be in 0–1" } }
        for((key,range) in ranges) value[key]?.let { raw -> require(raw is Number && raw.toDouble().isFinite() && raw.toDouble() in range) { "$key must be in $range" } }
        for(key in listOf("developer_type","fixer_type")) value[key]?.let { require(it is String && it.isNotBlank() && it.length<=80 && it.none(Char::isISOControl)) { "invalid $key" } }
        val seconds=(value["agitation_time_s"] as? Number)?.toDouble()
        val minutes=(value["time_min"] as? Number)?.toDouble()
        require(seconds==null || minutes==null || seconds<=minutes*60.0) { "agitation time exceeds bath duration" }
    }
    fun read(raw: Any?): List<Map<String,Any?>>? {
        if(raw==null) return null
        val rows=raw as? List<*> ?: error("process_steps must be a list")
        require(rows.size in 1..32) { "use 1–32 process steps" }
        return rows.map { row ->
            val map=(row as? Map<*,*>)?.entries?.associate { it.key.toString() to it.value } ?: error("process step must be an object")
            require(map.keys.all { it in ranges || it in listOf("action","label","developer_type","fixer_type") }) { "unknown process step field" }
            require(map["action"] in actions) { "unknown process action" }
            map["label"]?.let { require(it is String && it.length<=80 && it.none(Char::isISOControl)) }
            validateConditions(map);map
        }
    }
    fun validate(recipe: Map<String,Any?>) {
        validateConditions(recipe)
        recipe["program_key"]?.let { require(it in programs) { "unknown program_key" } }
        val steps=read(recipe["process_steps"])
        require(recipe["program_key"]!="custom" || steps!=null) { "custom program needs process_steps" }
        steps?.forEach { validateConditions(recipe+it) }
    }
    fun template(key: String): List<Map<String,Any?>> {
        val names=when(key) {
            "bw_negative"->listOf("develop_silver","fix_halide","remove_auxiliary")
            "color_negative","color_negative_bleach_bypass"->listOf("develop_color","bleach_silver","fix_halide","remove_auxiliary")
            "bw_reversal"->listOf("develop_silver","remove_silver","activate_remaining_halide","develop_silver","fix_halide","remove_auxiliary")
            "color_reversal"->listOf("develop_silver","activate_remaining_halide","develop_color","bleach_silver","fix_halide","remove_auxiliary")
            else->error("choose a concrete process program")
        }
        return names.map { action->mapOf("action" to action,"strength" to if(key=="color_negative_bleach_bypass" && action=="bleach_silver") 0.0 else 1.0) }
    }
}
