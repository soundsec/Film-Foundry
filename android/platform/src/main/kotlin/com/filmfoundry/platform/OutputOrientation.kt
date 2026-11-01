package com.filmfoundry.platform

/** Delivery geometry only; optical/material coordinates never change. */
object OutputOrientation {
    const val KEY="output_rotation_degrees"
    fun normalized(degrees: Int): Int { require(degrees%90==0);return (degrees%360+360)%360 }
    fun read(recipe: Map<String,Any?>): Int {
        val value=(recipe["scanner"] as? Map<*,*>)?.get(KEY) ?: return 0
        require(value is Number && value.toDouble()==value.toInt().toDouble()) { "Output rotation must be an integer quarter turn" }
        return normalized(value.toInt())
    }
    fun apply(recipe: Map<String,Any?>,degrees: Int): Map<String,Any?> {
        val scanner=(recipe["scanner"] as? Map<*,*>)?.entries?.associate { it.key.toString() to it.value }.orEmpty().toMutableMap()
        val angle=normalized(degrees)
        if(angle==0) scanner.remove(KEY) else scanner[KEY]=angle
        return recipe+("scanner" to scanner)
    }
    fun size(width: Int,height: Int,degrees: Int)=if(normalized(degrees)%180==0) width to height else height to width
}
