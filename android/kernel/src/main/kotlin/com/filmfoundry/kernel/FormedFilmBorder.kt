package com.filmfoundry.kernel

import com.filmfoundry.interchange.*

/** Formed optical strip from reference exposures of the same stock/baths.
 * Hard marks use two samples; laser-style marks use a bounded exposure table.
 * The stored optical and spectral arrays include the strip; holes have no material.
 */
class FormedFilmBorder private constructor(val layout: FilmRebateGeometry.Layout,private val samples: OpticalMaster,
    private val label: String,private val rows: String,private val marks: Boolean,
    private val topPattern: EdgeMarkPattern?,private val bottomPattern: EdgeMarkPattern?,
    private val linked: Boolean,private val flipTop: Boolean,private val flipBottom: Boolean,
    private val markType: String,private val markSpread: Double,private val exposure: FactoryMarkExposure?) {
    private val unit=maxOf(1,minOf(layout.width/maxOf(1,label.length*4+4),((layout.textBottom-layout.textTop)*layout.rebate/5).toInt()))
    private val textWidth=label.length*4*unit
    private val textLeft=maxOf(1,(layout.width-textWidth)/2)
    private val textOut=((layout.textTop+layout.textBottom)*layout.rebate/2-2.5*unit).toInt()
    private val bandStart=(layout.textTop*layout.rebate).toInt()
    private val bandEnd=kotlin.math.ceil(layout.textBottom*layout.rebate).toInt()
    private val topCode=topPattern?.encode().orEmpty()
    private val bottomCode=bottomPattern?.encode().orEmpty()
    private fun kind(x: Int,y: Int): Int {
        val top=y<layout.rebate
        val outward=if(top) layout.rebate-1-y else y-layout.rebate-layout.imageHeight
        if((rows=="both" || rows==if(top) "top" else "bottom") && layout.hole(x,outward)) return -1
        if(!marks) return 0
        return if(exposure==null) markKind(x,outward,top) else 0
    }
    private fun markKind(x: Int,outward: Int,top: Boolean): Int {
        val start=bandStart;val end=bandEnd
        val flip=if(top) flipTop else flipBottom
        val px=if(flip) layout.width-1-x else x
        val py=if(flip) start+end-1-outward else outward
        val pattern=if(top) topPattern else bottomPattern
        if(pattern!=null) {
            if(py !in start until end) return 0
            val sy=if(top) end-1-py else py-start
            val left=(layout.width*.04).toInt();val right=maxOf(left+1,(layout.width*.82).toInt())
            if(px in left until right && pattern.ink((px-left)*pattern.width/(right-left),sy*pattern.height/(end-start))) return 1
            // Keep the automatically advancing frame number outside the user stencil.
            val frame=label.substringAfterLast(' ');val u=maxOf(1,minOf(maxOf(1,(layout.width*.13).toInt())/maxOf(1,frame.length*4),(end-start)/5))
            val fx=(layout.width*.86).toInt();val fy=maxOf(0,(end-start-5*u)/2)
            if(px<fx || sy<fy) return 0
            val gx=(px-fx)/u;val gy=(sy-fy)/u
            return if(gy in 0..4 && gx/4<frame.length && gx%4<3 && glyphs[frame[gx/4]]?.get(gy*3+gx%4)=='1') 1 else 0
        }
        val gx=(px-textLeft)/unit;val gy=(py-textOut)/unit
        if(px<textLeft || py<textOut || gy !in 0..4 || gx<0) return 0
        val ch=gx/4;val column=gx%4
        return if(ch<label.length && column<3 && glyphs[label[ch]]?.get((if(top) 4-gy else gy)*3+column)=='1') 1 else 0
    }
    fun record(): Map<String,Any?> = mapOf("model" to FilmRebateGeometry.MODEL,"image_height" to layout.imageHeight,
        "rebate_rows" to layout.rebate,"holes_per_row" to layout.holesPerRow,"label" to label,
        "order" to "image_perforations_factory_marks","spatial_reference" to if(exposure==null) "uniform_two_sample_v1" else "interpolated_edge_footprint_v2",
        "factory_pattern_top" to topCode,"factory_pattern_bottom" to bottomCode,
        "factory_pattern_linked" to linked,"factory_flip_top" to flipTop,"factory_flip_bottom" to flipBottom,
        "factory_mark_type" to markType,"factory_mark_spread" to markSpread,
        "factory_response" to if(exposure==null) "uniform_two_sample_v1" else "material_exposure_table_v1")
    fun stamp(optical: OpticalMaster)=OpticalMaster(optical.opticalDensityRgb,optical.metadata+(FilmRebateGeometry.KEY to record()),
        optical.clearBaseOpticalDensityRgb,optical.spectralComponentAmounts,optical.spectralPostProcessDensityRgb)
    fun strip(start: Int,count: Int,reference: OpticalMaster): OpticalMaster {
        require(start>=0 && count>0 && start+count<=layout.height)
        require(start+count<=layout.rebate || start>=layout.rebate+layout.imageHeight)
        fun field(channels: Int,source: FloatNd?): FloatNd? {
            if(source==null) return null
            val values=FloatArray(count*layout.width*channels)
            for(pixel in 0 until count*layout.width) {
                val x=pixel%layout.width;val y=start+pixel/layout.width;val k=kind(x,y)
                if(k<0) continue
                if(exposure!=null && marks) {
                    val top=y<layout.rebate;val outward=if(top) layout.rebate-1-y else y-layout.rebate-layout.imageHeight
                    exposure.writePixel(x,outward,top,source.values,channels,values,pixel*channels)
                } else for(c in 0 until channels) values[pixel*channels+c]=source.values[k*channels+c]
            }
            return FloatNd(intArrayOf(count,layout.width,channels),values)
        }
        return stamp(OpticalMaster(field(3,samples.opticalDensityRgb)!!,reference.metadata,reference.clearBaseOpticalDensityRgb,
            field(9,samples.spectralComponentAmounts),reference.spectralPostProcessDensityRgb?.let { FloatNd(intArrayOf(count,layout.width,3),FloatArray(count*layout.width*3)) }))
    }
    fun surround(optical: OpticalMaster): OpticalMaster {
        fun field(channels: Int,main: FloatNd?,top: FloatNd?,bottom: FloatNd?): FloatNd? {
            if(main==null) return null
            val values=FloatArray(layout.height*layout.width*channels)
            top!!.values.copyInto(values);main.values.copyInto(values,layout.rebate*layout.width*channels)
            bottom!!.values.copyInto(values,(layout.rebate+layout.imageHeight)*layout.width*channels)
            return FloatNd(intArrayOf(layout.height,layout.width,channels),values)
        }
        val a=strip(0,layout.rebate,optical);val b=strip(layout.rebate+layout.imageHeight,layout.rebate,optical)
        return stamp(OpticalMaster(field(3,optical.opticalDensityRgb,a.opticalDensityRgb,b.opticalDensityRgb)!!,optical.metadata,
            optical.clearBaseOpticalDensityRgb,field(9,optical.spectralComponentAmounts,a.spectralComponentAmounts,b.spectralComponentAmounts),
            field(3,optical.spectralPostProcessDensityRgb,a.spectralPostProcessDensityRgb,b.spectralPostProcessDensityRgb)))
    }
    companion object {
        fun prepare(kernel: ReducedKernel,recipe: Map<String,Any?>,width: Int,height: Int): FormedFilmBorder? {
            val config=recipe["film_border"] as? Map<*,*> ?: return null
            if(config["enabled"]!=true) return null
            val film=(recipe["film"] as? Map<*,*>)?.entries?.associate { it.key.toString() to it.value }.orEmpty()
            val develop=(recipe["develop"] as? Map<*,*>)?.entries?.associate { it.key.toString() to it.value }.orEmpty()
            val sampleRecipe=(recipe-"film_border")+mapOf("enable_mtf" to false,"enable_halation" to false,"enable_grain" to false,
                "film" to (film+mapOf("grain_strength" to 0.0,"emulsion_mtf_strength" to 0.0,"halation_strength" to 0.0,
                    "emulsion_highlight_spread_strength" to 0.0,"light_piping_strength" to 0.0)),
                "develop" to (develop+mapOf("grain_strength" to 0.0,"uneven_development" to 0.0,"chemical_stain" to 0.0,"reticulation_strength" to 0.0,
                    "development_adjacency_strength" to 0.0,"dir_process_effectiveness" to 0.0)))
            val identity=recipe["frame_identity"] as? Map<*,*>
            val index=(identity?.get("frame_index") as? Number)?.toInt() ?: 1
            val name=(config["stock_label"] ?: film["name"] ?: "FILM FOUNDRY").toString().uppercase(java.util.Locale.ROOT)
                .map { if(it in glyphs || it==' ') it else ' ' }.joinToString("").trim().ifBlank { "FILM FOUNDRY" }.take(32)
            val label=name+" "+index.toString().padStart(2,'0')
            val rows=config["sprocket_rows"]?.toString() ?: "both";require(rows in listOf("none","both","top","bottom"))
            fun pattern(key: String)=config[key]?.toString()?.takeIf { it.isNotBlank() }?.let(EdgeMarkPattern::decode)
            val top=pattern("factory_pattern_top");val bottom=pattern("factory_pattern_bottom");val linked=config["factory_pattern_linked"]!=false
            val geometry=FilmRebateGeometry.layout(config["format_key"]?.toString() ?: "135",width,height)
            val type=config["factory_mark_type"]?.toString() ?: "hard";require(type in listOf("hard","laser"))
            val spread=(config["factory_mark_spread"] as? Number)?.toDouble() ?: 1.0;require(spread.isFinite() && spread in 0.0..3.0)
            val flipTop=config["factory_flip_top"]==true;val flipBottom=config["factory_flip_bottom"]==true
            fun formed(rgb: FloatArray)=kernel.develop(kernel.expose(RgbVolume(rgb.size/3,1,linearRgb=rgb),sampleRecipe),sampleRecipe)
            val optical=formed(floatArrayOf(0f,0f,0f,1.8f,1.8f,1.8f))
            val base=FormedFilmBorder(geometry,optical,label,rows,config["factory_marks"]!=false,top,if(linked) top else bottom,linked,flipTop,flipBottom,type,spread,null)
            if(type=="hard" || spread==0.0 || config["factory_marks"]==false) return base
            val exposure=FactoryMarkExposure(width,height,geometry.rebate,film,spread,recipe["enable_mtf"]!=false,recipe["enable_halation"]!=false,
                geometry.rebate*(geometry.textBottom-geometry.textTop)) { x,out,isTop -> base.markKind(x,out,isTop)>0 }
            return FormedFilmBorder(geometry,formed(exposure.sampleRgb),label,rows,true,top,if(linked) top else bottom,linked,flipTop,flipBottom,type,spread,exposure)
        }
        // Original compact 3×5 factory stencil; no external typeface or raster resource.
        private val glyphs=mapOf('0' to "111101101101111",'1' to "010110010010111",'2' to "111001111100111",'3' to "111001111001111",
            '4' to "101101111001001",'5' to "111100111001111",'6' to "111100111101111",'7' to "111001010010010",'8' to "111101111101111",'9' to "111101111001111",
            'A' to "010101111101101",'B' to "110101110101110",'C' to "111100100100111",'D' to "110101101101110",'E' to "111100110100111",'F' to "111100110100100",
            'G' to "111100101101111",'H' to "101101111101101",'I' to "111010010010111",'J' to "001001001101111",'K' to "101101110101101",'L' to "100100100100111",
            'M' to "101111111101101",'N' to "101111111111101",'O' to "111101101101111",'P' to "111101111100100",'Q' to "111101101111001",'R' to "110101110101101",
            'S' to "111100111001111",'T' to "111010010010010",'U' to "101101101101111",'V' to "101101101101010",'W' to "101101111111101",'X' to "101101010101101",'Y' to "101101010010010",'Z' to "111001010100111",'-' to "000000111000000")
    }
}
