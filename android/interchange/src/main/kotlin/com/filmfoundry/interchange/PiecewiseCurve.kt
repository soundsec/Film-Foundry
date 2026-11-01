package com.filmfoundry.interchange

/** Shared point contract. Editing returns new values, so cancelling an editor cannot mutate a recipe. */
data class CurvePoint(val x: Double,val y: Double)
class PiecewiseCurve private constructor(val points: List<CurvePoint>) {
    fun payload(): List<List<Double>> = points.map { listOf(it.x,it.y) }
    fun move(index: Int,x: Double,y: Double): PiecewiseCurve {
        require(index in points.indices && x.isFinite() && y.isFinite())
        val position=if(index == 0 || index == points.lastIndex) points[index].x else
            x.coerceIn(points[index-1].x+1e-5,points[index+1].x-1e-5)
        return from(points.mapIndexed { i,p -> if(i == index) CurvePoint(position,y) else p })
    }
    fun insert(x: Double,y: Double): PiecewiseCurve {
        require(x.isFinite() && y.isFinite() && x > points.first().x && x < points.last().x)
        require(points.none { kotlin.math.abs(it.x-x) < 1e-5 }) { "A point already occupies this input value" }
        return from((points+CurvePoint(x,y)).sortedBy { it.x })
    }
    fun remove(index: Int): PiecewiseCurve {
        require(index in 1 until points.lastIndex) { "Keep the two end points" }
        return from(points.filterIndexed { i,_->i != index })
    }
    companion object {
        fun identity()=from(listOf(CurvePoint(0.0,0.0),CurvePoint(1.0,1.0)))
        fun from(points: List<CurvePoint>): PiecewiseCurve {
            require(points.size in 2..4096 && points.all { it.x.isFinite() && it.y.isFinite() && it.x in 0.0..1.0 }) { "Curve needs finite points with input in 0–1" }
            require(points.zipWithNext().all { (a,b)->a.x < b.x }) { "Curve inputs must strictly increase" }
            return PiecewiseCurve(points.toList())
        }
        fun parse(raw: Any?,sort: Boolean=false): PiecewiseCurve {
            val entries=raw as? List<*> ?: error("Curve must contain points")
            val points=entries.map { item ->
                val pair=item as? List<*> ?: error("Curve points must be pairs");require(pair.size == 2)
                CurvePoint((pair[0] as? Number)?.toDouble() ?: error("Invalid curve input"),(pair[1] as? Number)?.toDouble() ?: error("Invalid curve output"))
            }
            return from(if(sort) points.sortedBy { it.x } else points)
        }
    }
}
