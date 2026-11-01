package com.filmfoundry.kernel

/** Parameter-only prediction using the same ordered operators as development. Not image processing. */
data class PhoneProcessInspection(val program: String,val colorMaterial: Boolean,val positive: Boolean,
    val components: List<String>,val variableImage: Boolean,val steps: List<Map<String,Any?>> = emptyList())

fun inspectPhoneProcess(film: Map<String,Any?>,develop: Map<String,Any?>): PhoneProcessInspection {
    val color=film["color_process"]?.toString()?.lowercase() !in listOf("bw","black_white","monochrome")
    val positive=film["image_polarity"]?.toString()?.lowercase() in listOf("positive","slide","reversal")
    val program=PhoneProcessProgram.from(develop,color,positive,film,true,true,true)!!
    val absorption=Array(3) { y -> FloatArray(3) { x ->
        val declared=(film["dye_absorption_matrix"] as? List<*>)?.get(y) as? List<*>
        val base=(declared?.get(x) as? Number)?.toFloat() ?: PhoneReducedKernel.DEFAULT_ABSORPTION[y][x]
        val scale=((film["density_max"] as? List<*>)?.get(x) as? Number)?.toFloat() ?: 1f
        base*scale
    } }
    val retained=FloatArray(3) { ((film["retained_halide_density_rgb"] as? List<*>)?.get(it) as? Number)?.toFloat() ?: PhoneReducedKernel.DEFAULT_RETAINED[it] }
    return PhoneProcessInspection(program.key,color,program.positiveFor(color,absorption,retained),program.imageComponents(color),program.hasVariableImage(color),program.steps.map { it.toMap() })
}
