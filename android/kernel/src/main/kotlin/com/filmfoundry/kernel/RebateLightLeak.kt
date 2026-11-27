package com.filmfoundry.kernel

import kotlin.math.sqrt

/** Incident camera light at the two gate boundaries, in the frozen capture coordinates. */
internal class RebateLightLeak private constructor(val peak: Float, private val edges: Array<FloatArray>) {
    fun level(x: Int, top: Boolean) = edges[if (top) 0 else 1][x]

    fun incident(x: Int,top: Boolean,channel: Int): Float {
        val t=level(x,top)/(LEVELS-1)
        return peak*t*t*PhoneCaptureConditions.LEAK_RGB[channel]
    }

    fun exposures(factory: FloatArray): FloatArray = FloatArray(LEVELS * factory.size) { i ->
        val t = (i / factory.size).toFloat() / (LEVELS-1)
        factory[i % factory.size] + peak*t*t*PhoneCaptureConditions.LEAK_RGB[i % 3]
    }

    companion object {
        const val LEVELS = 33

        fun fromCapture(width: Int, height: Int, capture: Map<*, *>?, vertical: Boolean=false): RebateLightLeak? {
            if (capture == null) return null
            val sources = (capture["stored_exposures"] as? List<*>)?.mapNotNull {
                (it as? Map<*, *>)?.get("capture") as? Map<*, *>
            } ?: listOf(capture)
            val settings = sources.mapNotNull { it["exposure_conditions"] as? Map<*, *> }
            val active = settings.filter { ((it["camera"] as? Map<*, *>)?.get("leak") as? Number)?.toFloat()?.let { v -> v>0 } == true }
            if (active.isEmpty()) return null
            val along = if(vertical) height else width
            val edges = Array(2) { FloatArray(along*3) }
            for (s in active) {
                // Only the leak generator applies outside the gate. Do not rasterize imprints or gate dirt.
                val camera = (s["camera"] as Map<*, *>).filterKeys { it in setOf("leak","leak_side","leak_shape","leak_components","seed") }
                val conditions = PhoneCaptureConditions(width,height,mapOf("model" to s["model"],"camera" to camera))
                if(vertical) {
                    conditions.addLeaks(edges[0],0,height,width-1,width)
                    conditions.addLeaks(edges[1],0,height,0,1)
                } else {
                    conditions.addLeaks(edges[0],0,1)
                    conditions.addLeaks(edges[1],height-1,1)
                }
            }
            val stock = settings.firstOrNull()?.get("film") as? Map<*, *>
            val transmission = PhoneFilmStorageConditions(mapOf("age" to stock?.get("age"))).incidentTransmission
            val red = Array(2) { side -> FloatArray(along) { x -> edges[side][3*x]*transmission } }
            val peak = maxOf(red[0].max(),red[1].max())
            if (peak<=0f) return null
            for (row in red) for (x in row.indices) row[x] = sqrt(row[x]/peak)*(LEVELS-1)
            return RebateLightLeak(peak,red)
        }
    }
}
