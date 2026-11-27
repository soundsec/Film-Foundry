package com.filmfoundry.kernel

import com.filmfoundry.interchange.FilmRebateGeometry
import kotlin.math.*

/** Open air bypasses film interpretation. Coordinates are global even in a streamed scan. */
class ScanPerforations private constructor(private val width: Int, private val hardEdges: Boolean, private val windows: List<Window>) {
    private class Window(val x: Double, val y: Double, val halfW: Double, val halfH: Double, val radius: Double)

    fun show(rgb: FloatArray, start: Int, count: Int, lamp: FloatArray) {
        for (w in windows) {
            val left = max(0, floor(w.x-w.halfW).toInt())
            val right = min(width, ceil(w.x+w.halfW).toInt())
            val top = max(start, floor(w.y-w.halfH).toInt())
            val bottom = min(start+count, ceil(w.y+w.halfH).toInt())
            for (y in top until bottom) {
                val dy = abs(y+.5-w.y)-w.halfH+w.radius
                for (x in left until right) {
                    val dx = abs(x+.5-w.x)-w.halfW+w.radius
                    val distance = if (dx <= 0 || dy <= 0) max(dx,dy)-w.radius else hypot(dx,dy)-w.radius
                    val alpha = if (hardEdges) { if (distance <= 0) 1f else 0f }
                                else (.5-distance).coerceIn(0.0,1.0).toFloat()
                    if (alpha == 0f) continue
                    val i = ((y-start)*width+x)*3
                    for (c in 0..2) rgb[i+c] += (lamp[c]-rgb[i+c])*alpha
                }
            }
        }
    }

    companion object {
        fun from(metadata: Map<String,Any?>, width: Int, height: Int, rowDensity: (Int)->FloatArray): ScanPerforations? {
            val record = metadata[FilmRebateGeometry.KEY] as? Map<*,*> ?: return null
            val count = (record["holes_per_row"] as? Number)?.toInt() ?: return null
            if (count <= 0) return null
            val saved = record["perforation_windows_v1"] as? List<*>
            val loading = record["loading"] as? Map<*,*>
            val shift = if (saved == null) 0.0 else
                ((record["perforation_shift_frames"] ?: loading?.get("offset_frames")) as? Number)?.toDouble() ?: 0.0
            val bounds = if (saved != null) saved.map { item ->
                (item as List<*>).map { (it as Number).toDouble() }
            } else {
                // Older archives store no aperture positions or punched side.
                // Recover the open runs from two optical rows at the belt centres.
                val original = (record["image_height"] as Number).toDouble()
                val rebate = (record["rebate_rows"] as Number).toDouble()
                val fraction = rebate / (original+2*rebate)
                buildList {
                    for (top in listOf(.38*fraction, 1-.88*fraction)) {
                        val bottom = top+.5*fraction
                        val line = rowDensity(((top+bottom)*height/2).toInt().coerceIn(0,height-1))
                        val runs = mutableListOf<Pair<Int,Int>>()
                        var left = -1
                        for (x in 0..width) {
                            val air = x < width && (0..2).all { line[x*3+it] == 0f }
                            if (air && left < 0) left = x
                            if (!air && left >= 0) {
                                runs.add(left to x)
                                left = -1
                            }
                        }
                        if (runs.size >= 2 && runs.first().first == 0 && runs.last().second == width) {
                            val end = width + runs.removeAt(0).second
                            runs[runs.lastIndex] = runs.last().first to end
                        }
                        for ((begin,end) in runs) if (end-begin <= max(2.0,width*.60/count))
                            add(listOf(begin.toDouble()/width,top,end.toDouble()/width,bottom))
                    }
                }
            }
            val vertical = record["transport_axis"]=="vertical"
            val radius = if ("config" in record) .18 else .20
            val windows = buildList {
                for ((left,top,right,bottom) in bounds) for (wrap in -1..1) {
                    val dx=if(vertical) 0.0 else shift+wrap
                    val dy=if(vertical) shift+wrap else 0.0
                    val x0 = (left+dx)*width
                    val x1 = (right+dx)*width
                    if (x1<=0 || x0>=width) continue
                    val w=x1-x0
                    val h=(bottom-top)*height
                    add(Window((x0+x1)/2,((top+bottom)/2+dy)*height,w/2,h/2,min(w,h)*radius))
                }
            }
            val nativeHeight = (record["image_height"] as Number).toInt()+2*(record["rebate_rows"] as Number).toInt()
            return ScanPerforations(width,"config" !in record && nativeHeight == (if(vertical) width else height),windows)
        }
        fun lamp(recipe: Map<String,Any?>): FloatArray = PhoneObserve.openGateLamp(recipe)
    }
}
