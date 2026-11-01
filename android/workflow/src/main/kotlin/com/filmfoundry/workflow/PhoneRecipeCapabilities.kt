package com.filmfoundry.workflow

/** Known cross-frontend capability differences. Reporting preserves every recipe field. */
object PhoneRecipeCapabilities {
    enum class Stage { CAPTURE, DEVELOP, SCAN }
    enum class Status { IMPLEMENTED, REDUCED, PRESERVED, UNSUPPORTED }
    data class Entry(val path: String,val status: Status)
    data class Report(val entries: List<Entry>) {
        val warnings get()=entries.filter { it.status==Status.PRESERVED || it.status==Status.UNSUPPORTED }
    }
    fun inspect(recipe: Map<String,Any?>,stage: Stage): Report {
        fun section(key: String)=(recipe[key] as? Map<*,*>)?.entries?.associate { it.key.toString() to it.value }.orEmpty()
        val film=section("film");val develop=section("develop");val scanner=recipe+section("scanner")
        val entries=mutableListOf<Entry>()
        fun add(path: String,status: Status) { if(entries.none { it.path==path }) entries+=Entry(path,status) }
        fun nonzero(value: Any?): Boolean=when(value) {
            is Number -> value.toDouble()!=0.0
            is List<*> -> value.any(::nonzero)
            else -> false
        }
        fun differs(value: Any?,neutral: Double): Boolean=when(value) {
            is Number -> value.toDouble()!=neutral
            is List<*> -> value.any { differs(it,neutral) }
            else -> false
        }
        fun matrixActive(value: Any?): Boolean {
            val rows=value as? List<*> ?: return value!=null
            return rows.size!=3 || rows.indices.any { y ->
                val row=rows[y] as? List<*>
                row==null || row.size!=3 || row.indices.any { x -> (row[x] as? Number)?.toDouble()!=if(x==y) 1.0 else 0.0 }
            }
        }
        if(stage!=Stage.SCAN) {
            add("film.material_response",Status.REDUCED)
            if((recipe["film_border"] as? Map<*,*>)?.get("enabled")==true) add("film_border",Status.REDUCED)
            if(film["spectral_exposure_profile"]!=null) add("film.spectral_exposure_profile",Status.REDUCED)
            if(nonzero(film["dir_material_response_matrix"])) add("film.dir_material_response_matrix",Status.REDUCED)
            for(key in listOf("light_piping_strength","silver_grain_strength","emulsion_highlight_spread_strength"))
                if(nonzero(film[key])) add("film.$key",Status.REDUCED)
            if(recipe["enable_halation"]!=false && nonzero(film["halation_strength"])) add("film.halation_strength",
                if(film["halation_return_model"] in listOf(null,"compatibility_rgb","layer_selective")) Status.REDUCED else Status.UNSUPPORTED)
            if(stage==Stage.DEVELOP) {
                add("develop.process_steps",Status.REDUCED)
                if(nonzero(develop["mask_bleach_completion"])) add("develop.mask_bleach_completion",Status.REDUCED)
                if(nonzero(develop["development_adjacency_strength"])) add("develop.development_adjacency_strength",Status.REDUCED)
            }
        } else {
            add("scanner.transmission",Status.REDUCED)
            for(key in listOf("scanner_response_matrix","device_response_rgb","color_curve_rgb","tone_curve"))
                if(scanner[key]!=null || recipe[key]!=null) add("scanner.$key",Status.IMPLEMENTED)
            for(key in listOf("negative_channel_matrix")) if(matrixActive(scanner[key])) add("scanner.$key",Status.PRESERVED)
            for(key in listOf("negative_channel_gamma","print_gamma","print_color_bias","highlight_color_bias"))
                if(differs(scanner[key],1.0)) add("scanner.$key",Status.PRESERVED)
            for(key in listOf("print_color_shift","positive_scan_color_control_strength","projection_white_softness","projection_black_adaptation"))
                if(nonzero(scanner[key])) add("scanner.$key",Status.PRESERVED)
        }
        return Report(entries.toList())
    }
}
