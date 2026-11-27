package com.filmfoundry.interchange

/** Material strip order, measured outward from the image: perforations, then factory marks. */
object FilmRebateGeometry {
    fun inkTransmission(config: Map<*,*>): List<Double> {
        val rgb=config["factory_ink_transmission_rgb"] ?: return listOf(.18,.18,.18)
        require(rgb is List<*> && rgb.size==3 && rgb.all { it is Number && it.toDouble() in 0.0..1.0 })
        return rgb.map { (it as Number).toDouble() }
    }
    const val KEY="formed_film_rebate"
    const val MODEL="formed_rebate_reference_v1"
    data class Layout(val width: Int,val imageHeight: Int,val rebate: Int,val holesPerRow: Int,val single110: Boolean = false, val typeSpan: Double? = null, val vertical: Boolean = false) {
        val height get()=imageHeight+2*rebate
        val outputWidth get()=if(vertical) height else width
        val outputHeight get()=if(vertical) width else height
        val holeTop get()=.12
        val holeBottom get()=.62
        val textTop get()=if(typeSpan!=null) (if(holesPerRow==0) .48 else .84)-typeSpan/2 else if(holesPerRow==0) .12 else .72
        val textBottom get()=if(typeSpan!=null) (if(holesPerRow==0) .48 else .84)+typeSpan/2 else if(holesPerRow==0) .84 else .96
        /** Unshifted, normalized apertures for a readout of the stored film. */
        fun windows(rows: String): List<List<Double>> = buildList {
            for (side in listOf("top", "bottom")) {
                if (rows != "both" && rows != side) continue
                val top = if (side == "top") rebate*(1-holeBottom) else height-rebate*(1-holeTop)
                val bottom = top + rebate*(holeBottom-holeTop)
                for (i in 0 until holesPerRow) {
                    val centre = if (single110) .94 else (i+.5)/holesPerRow
                    val half = if (single110) .0375 else .24/holesPerRow
                    add(if(vertical) listOf(1-bottom/height,centre-half,1-top/height,centre+half)
                        else listOf(centre-half,top/height,centre+half,bottom/height))
                }
            }
        }
        /** y is distance outward from the gate, shared by both edges. */
        fun hole(x: Int,y: Int): Boolean {
            if(holesPerRow==0) return false
            val pitch=width.toDouble()/holesPerRow
            val halfWidth=if(single110) width*.0375 else pitch*.24;val halfHeight=rebate*(holeBottom-holeTop)*.5
            val radius=minOf(halfWidth,halfHeight)*.4
            val dx=maxOf(0.0,kotlin.math.abs((x+.5)%pitch-pitch*(if(single110) .94 else .5))-(halfWidth-radius))
            val dy=maxOf(0.0,kotlin.math.abs(y+.5-rebate*(holeTop+holeBottom)*.5)-(halfHeight-radius))
            return dx*dx+dy*dy<=radius*radius
        }
    }
    fun layout(format: String,width: Int,height: Int,single110: Boolean=false): Layout {
        require(width>0 && height>0)
        val holes=when(format) { "135"->8;"half","half_frame"->4;"110"->if(single110) 1 else 6;"120","6x4.5","6x6","6x7","6x9"->0;else->error("Unsupported rebate format") }
        return Layout(width,height,maxOf(1,Math.rint(height*if(holes==0) .075 else .23).toInt()),holes,format=="110" && single110)
    }
    fun forStock(config: Map<*,*>, sourceWidth: Int, sourceHeight: Int, formed: Boolean=false): Layout {
        val mode = config["formation_mode"] ?: "attached"
        require(mode in listOf("attached","native","sprocket_panorama"))
        val pano = mode == "sprocket_panorama"
        val format = config["format_key"]?.toString() ?: "135"
        require(!pano || format=="135") { "Exposure through perforations requires full-frame 135 stock in a wide gate" }
        val vertical=when(config["transport_axis"]) {
            "vertical" -> true
            "short_edge" -> sourceWidth>sourceHeight
            "format" -> if(format in listOf("half","half_frame","6x4.5")) sourceWidth>sourceHeight else sourceHeight>sourceWidth
            null,"horizontal" -> false
            else -> error("Unknown film transport axis")
        }
        val width=if(vertical) sourceHeight else sourceWidth
        val height=if(vertical) sourceWidth else sourceHeight
        val count = (config["sprocket_count"] as? Number)?.toInt()
        val normal = layout(format,width,height,count==1)
        val ratio = (config["rebate_height_ratio"] as? Number)?.toDouble() ?: if (normal.holesPerRow==0) .075 else .23
        require(ratio in .02.. .8)
        val whole = formed || pano
        val rebate = if (whole) {
            require(height>=3)
            Math.rint(height*ratio/(1+2*ratio)).toInt().coerceIn(1,(height-1)/2)
        } else maxOf(1,Math.rint(height*ratio).toInt())
        val gate = if (whole) height-2*rebate else height
        val holes = if (normal.holesPerRow==0) 0 else count ?: if (pano) maxOf(1,Math.rint(width*35.0/(height*4.75)).toInt()) else normal.holesPerRow
        val gateMm = when(format) { "110"->13.0; "120","6x4.5","6x6","6x7","6x9"->56.0; else->24.0 }
        val span = if (config["lettering_size"]=="format_mm")
            minOf(if(holes==0) .96 else .32,1.25*(if(pano) height/35.0 else gate/gateMm)/rebate)
        else null
        return normal.copy(imageHeight=gate,rebate=rebate,holesPerRow=holes,typeSpan=span,vertical=vertical)
    }
    /** Readout normalization uses only the image gate, excluding clear holes and factory marks. */
    fun gateRows(metadata: Map<String,Any?>,height: Int, columns: Boolean=false): IntRange {
        val record=metadata[KEY] as? Map<*,*> ?: return 0 until height
        require(record["model"]==MODEL)
        if((record["transport_axis"]=="vertical")!=columns) return 0 until height
        val original=(record["image_height"] as Number).toInt()
        val rebate=(record["rebate_rows"] as Number).toInt()
        require(original>0 && rebate>0)
        val total=original.toDouble()+2*rebate
        val start=kotlin.math.ceil(height*rebate/total).toInt().coerceIn(0,height-1)
        val end=kotlin.math.floor(height*(rebate+original)/total).toInt().coerceIn(start+1,height)
        return start until end
    }
}
