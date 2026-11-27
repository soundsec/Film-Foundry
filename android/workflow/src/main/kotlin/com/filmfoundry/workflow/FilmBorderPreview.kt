package com.filmfoundry.workflow

import kotlin.math.max

/** Same layout vocabulary as PC FilmBorderConfig; explicitly not a formed medium or scan overlay. */
object FilmBorderPreview {
    val formats=listOf("135","half_frame","120","110")
    val rows=listOf("none","top","bottom","both")
    data class Hole(val left: Double,val top: Double,val right: Double,val bottom: Double)
    data class TextBand(val top: Double,val bottom: Double,val fontSize: Double)
    data class Layout(val width: Int,val height: Int,val rebate: Int,val holes: List<Hole>,val text: String,val top: Boolean,
        val textBand: TextBand,val pipelineStatus: String="interface_only")
    fun layout(format: String,width: Int,height: Int,row: String="both",text: String="FILM FOUNDRY 01",top: Boolean=false,settings: Map<String,Any?> = emptyMap()): Layout {
        require(format in formats && row in rows && width>0 && height>0 && text.length<=64)
        val geometry=com.filmfoundry.interchange.FilmRebateGeometry.forStock(settings+mapOf("format_key" to format,"sprocket_count" to if(format=="110" && settings["formation_mode"]!="sprocket_panorama") 1 else null),width,height)
        val rebate=geometry.rebate;val fullHeight=geometry.height
        val holes=mutableListOf<Hole>()
        if(geometry.holesPerRow>0) for(i in 0 until geometry.holesPerRow) {
            val x0=if(geometry.single110) width*.9025 else width*(i+.26)/geometry.holesPerRow
            val x1=if(geometry.single110) width*.9775 else width*(i+.74)/geometry.holesPerRow
            if(row=="top" || (row=="both" && !geometry.single110)) holes+=Hole(x0,rebate*(1-geometry.holeBottom),x1,rebate*(1-geometry.holeTop))
            if(row in listOf("bottom","both")) holes+=Hole(x0,fullHeight-rebate+rebate*geometry.holeTop,x1,fullHeight-rebate+rebate*geometry.holeBottom)
        }
        val a=if(top) rebate*(1-geometry.textBottom) else fullHeight-rebate+rebate*geometry.textTop
        val b=if(top) rebate*(1-geometry.textTop) else fullHeight-rebate+rebate*geometry.textBottom
        val band=TextBand(a,b,(b-a)*.70)
        return Layout(width,fullHeight,rebate,holes,text,top,band)
    }
}
