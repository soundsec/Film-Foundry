package com.filmfoundry.interchange

/** Material strip order, measured outward from the image: perforations, then factory marks. */
object FilmRebateGeometry {
    const val KEY="formed_film_rebate"
    const val MODEL="formed_rebate_reference_v1"
    data class Layout(val width: Int,val imageHeight: Int,val rebate: Int,val holesPerRow: Int) {
        val height get()=imageHeight+2*rebate
        val holeTop get()=.12
        val holeBottom get()=.62
        val textTop get()=if(holesPerRow==0) .12 else .72
        val textBottom get()=if(holesPerRow==0) .84 else .96
        /** y is distance outward from the gate, shared by both edges. */
        fun hole(x: Int,y: Int): Boolean {
            if(holesPerRow==0) return false
            val pitch=width.toDouble()/holesPerRow
            val halfWidth=pitch*.24;val halfHeight=rebate*(holeBottom-holeTop)*.5
            val radius=minOf(halfWidth,halfHeight)*.4
            val dx=maxOf(0.0,kotlin.math.abs((x+.5)%pitch-pitch*.5)-(halfWidth-radius))
            val dy=maxOf(0.0,kotlin.math.abs(y+.5-rebate*(holeTop+holeBottom)*.5)-(halfHeight-radius))
            return dx*dx+dy*dy<=radius*radius
        }
    }
    fun layout(format: String,width: Int,height: Int): Layout {
        require(width>0 && height>0)
        val holes=when(format) { "135"->8;"half","half_frame"->4;"110"->6;"120","6x4.5","6x6","6x7","6x9"->0;else->error("Unsupported rebate format") }
        return Layout(width,height,maxOf(1,Math.rint(height*if(holes==0) .075 else .23).toInt()),holes)
    }
    /** Readout normalization uses only the image gate, excluding clear holes and factory marks. */
    fun gateRows(metadata: Map<String,Any?>,height: Int): IntRange {
        val record=metadata[KEY] as? Map<*,*> ?: return 0 until height
        require(record["model"]==MODEL)
        val original=(record["image_height"] as Number).toInt()
        val rebate=(record["rebate_rows"] as Number).toInt()
        require(original>0 && rebate>0)
        val total=original.toDouble()+2*rebate
        val start=kotlin.math.ceil(height*rebate/total).toInt().coerceIn(0,height-1)
        val end=kotlin.math.floor(height*(rebate+original)/total).toInt().coerceIn(start+1,height)
        return start until end
    }
}
