package com.filmfoundry.interchange

/** scanner.color_mixer: red/orange/yellow/green/aqua/blue/purple/magenta H/S/L. */
object ColorMixerSettings {
    fun normalize(value: Any?): List<List<Float>>? {
        if(value==null) return null
        val list=value as? List<*> ?: error("color_mixer requires eight H/S/L rows")
        require(list.size==8)
        val rows=list.map { item ->
            val row=item as? List<*> ?: error("color_mixer row requires three values")
            require(row.size==3)
            row.mapIndexed { c, item ->
                val v=(item as? Number)?.toFloat() ?: error("color_mixer values must be numeric")
                require(v.isFinite() && kotlin.math.abs(v)<=if(c==0) 30f else 100f);v
            }
        }
        return if(rows.all { it.all { v->v==0f } }) null else rows
    }
}
