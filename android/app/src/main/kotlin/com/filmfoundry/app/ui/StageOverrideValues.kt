package com.filmfoundry.app.ui

/** Explicit UI choices only; automatic inversion defers to the actual final medium. */
internal object StageOverrideValues {
    val interpretationKeys=setOf("remove_base_mask","invert_transmission")
    /** Film interpretation defaults to final-medium policy, never to flags from a style preset. */
    fun interpretation(scanner: Map<String,Any?>,baseMode: Int,inversionMode: Int): Map<String,Any?> {
        require(baseMode in 0..2 && inversionMode in 0..2)
        val independent=inversion(base(emptyMap(),baseMode),inversionMode)
        return (scanner-interpretationKeys)+independent
    }
    fun bleach(source: Map<String,Any?>,enabled: Boolean?,strength: Double): Map<String,Any?> =
        if(enabled==null) source else source+("mask_bleach_completion" to if(enabled) strength else 0.0)
    fun inversion(source: Map<String,Any?>,mode: Int)=choice(source,mode,"invert_transmission")
    fun base(source: Map<String,Any?>,mode: Int)=choice(source,mode,"remove_base_mask")
    private fun choice(source: Map<String,Any?>,mode: Int,key: String): Map<String,Any?> = when(mode) {
        0->source-key
        1->source+(key to true)
        2->source+(key to false)
        3->source
        else->error("Invalid inversion choice")
    }
}
