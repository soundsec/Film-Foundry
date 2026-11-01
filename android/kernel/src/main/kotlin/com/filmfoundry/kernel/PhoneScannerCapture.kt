package com.filmfoundry.kernel

import com.filmfoundry.interchange.PiecewiseCurve

/** Device sampling only: illuminated transmission -> response matrix -> clip -> raw curves. */
internal class PhoneScannerCapture(private val scanner: Map<String,Any?>?,private val recipe: Map<String,Any?>) {
    private fun field(key: String)=scanner?.get(key) ?: recipe[key]
    private val matrix: Array<FloatArray>?=field("scanner_response_matrix")?.let { raw ->
        val rows=raw as? List<*> ?: error("scanner_response_matrix must be 3x3")
        require(rows.size==3)
        Array(3) { y ->
            val row=rows[y] as? List<*> ?: error("scanner_response_matrix must be 3x3");require(row.size==3)
            FloatArray(3) { x -> (row[x] as? Number)?.toFloat()?.also { require(it.isFinite()) { "Nonfinite scanner response" } }
                ?: error("Invalid scanner response") }
        }.takeUnless { values -> (0..2).all { y -> (0..2).all { x -> values[y][x]==if(x==y) 1f else 0f } } }
    }
    private val curves=field("device_response_rgb")?.let { raw ->
        val channels=raw as? List<*> ?: error("device_response_rgb needs three channels");require(channels.size==3)
        Array(3) { c -> PiecewiseCurve.parse(channels[c],sort=true).points.map {
            require(it.x.toFloat().isFinite() && it.y.toFloat().isFinite()) { "Device curve exceeds Float range" }
            it.x.toFloat() to it.y.toFloat()
        } }.takeUnless { channelsParsed -> channelsParsed.all { points -> points.all { it.first==it.second } } }
    }
    val active get()=matrix!=null || curves!=null
    val curveActive get()=curves!=null
    fun apply(values: FloatArray) {
        if(!active) return
        require(values.size%3==0)
        for(i in values.indices step 3) {
            val r=values[i];val g=values[i+1];val b=values[i+2]
            for(c in 0..2) {
                var v=matrix?.let { it[c][0]*r+it[c][1]*g+it[c][2]*b } ?: values[i+c]
                require(!v.isNaN()) { "Invalid scanner response signal" }
                v=v.coerceIn(1e-6f,1f)
                curves?.get(c)?.let { points ->
                    v=when {
                        v<=points.first().first -> points.first().second
                        v>=points.last().first -> points.last().second
                        else -> {
                            val right=points.indexOfFirst { it.first>=v };val a=points[right-1];val z=points[right]
                            a.second+(v-a.first)/(z.first-a.first)*(z.second-a.second)
                        }
                    }.coerceIn(1e-6f,1f)
                }
                values[i+c]=v
            }
        }
    }
    fun record()=mapOf("model" to "phone_rgb_device_capture_v1",
        "matrix" to if(matrix!=null) "rgb_response_3x3_v1" else "identity",
        "device_curve" to if(curves!=null) "piecewise_scanner_raw_rgb_v1" else "none",
        "order" to listOf("illuminated_transmission","response_matrix","sensor_clip","device_curve"),
        "base_reference" to "same_device_response")
}
