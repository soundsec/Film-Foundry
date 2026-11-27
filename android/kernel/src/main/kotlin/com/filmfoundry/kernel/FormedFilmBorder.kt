package com.filmfoundry.kernel

import com.filmfoundry.interchange.*

/** Formed optical strip from reference exposures of the same stock/baths.
 * Hard marks use two samples; laser-style marks use a bounded exposure table.
 * The stored optical and spectral arrays include the strip; holes have no material.
 */
class FormedFilmBorder private constructor(val layout: FilmRebateGeometry.Layout,private val samples: OpticalMaster,
    private val name: String,private val window: FilmStripWindow,private val factory: FilmFactoryWindow,private val batch: Int,private val codeStyle: String,private val marker: String,private val leftward: Boolean,private val registered: Boolean,private val sequence: Int,private val composition: EdgeComposition?,private val rows: String,private val marks: Boolean,
    private val topPattern: EdgeMarkPattern?,private val bottomPattern: EdgeMarkPattern?,
    private val linked: Boolean,private val flipTop: Boolean,private val flipBottom: Boolean,
    private val markType: String,private val markSpread: Double,private val exposure: FactoryMarkExposure?,private val leak: RebateLightLeak?,private val lamp: FactoryLamp,
    private val glowStrength: Float,private val ink: List<Double>?) {
    private inner class Plate(index: Int) {
        val number = index.toString().padStart(2,'0')
        val label = "$name $number"
        val registeredPlate=if(registered && marks) RegisteredFilmPlate(name,batch,index,codeStyle,marker,leftward,
            topPattern,bottomPattern,flipTop,flipBottom,sequence,composition) else null
        val unit = maxOf(1,minOf(factory.plate.width/(label.length*4+4),((layout.textBottom-layout.textTop)*layout.rebate/5).toInt()))
        val textLeft = maxOf(1,(factory.plate.width-label.length*4*unit)/2)
        val textOut = ((layout.textTop+layout.textBottom)*layout.rebate/2-2.5*unit).toInt()
    }
    private val current = Plate(factory.plate.frame)
    private val adjacent = if (window.shift == 0) current else Plate(factory.plate.adjacent)
    private val bandStart=(layout.textTop*layout.rebate).toInt()
    private val bandEnd=kotlin.math.ceil(layout.textBottom*layout.rebate).toInt()
    private val topCode=topPattern?.encode().orEmpty()
    private val bottomCode=bottomPattern?.encode().orEmpty()
    private val glow by lazy {
        val beam = if (marks && markType=="laser" && markSpread>0)
            lamp.applyTo(floatArrayOf(1f,0f,0f))[0] else 0f
        RebateGlow(factory.plate.width,layout.rebate,markSpread,beam,
            { x,y,top -> (rows=="both" || rows==(if(top) "top" else "bottom")) && layout.hole(window.localX(x),y) },
            { x,y,top -> markKind(x,y,top)>0 })
    }
    private fun kind(x: Int,y: Int): Int {
        val top=y<layout.rebate
        val outward=if(top) layout.rebate-1-y else y-layout.rebate-layout.imageHeight
        if((rows=="both" || rows==if(top) "top" else "bottom") && layout.hole(window.localX(x),outward)) return -1
        if(!marks) return 0
        return if(exposure==null) markKind(factory.origin+x,outward,top) else 0
    }
    private fun markKind(x: Int,outward: Int,top: Boolean): Int {
        val start=bandStart;val end=bandEnd
        val flip=if(top) flipTop else flipBottom
        val plate=if(factory.plate.fromAdjacent(x)) adjacent else current
        val localX=factory.plate.localX(x)
        plate.registeredPlate?.let {
            val y=if(top) end-1-outward else outward-start
            return if(it.ink(localX,y,top,factory.plate.width,end-start)) 1 else 0
        }
        val px=if(flip) factory.plate.width-1-localX else localX
        val py=if(flip) start+end-1-outward else outward
        val pattern=if(top) topPattern else bottomPattern
        if(pattern!=null) {
            if(py !in start until end) return 0
            val sy=if(top) end-1-py else py-start
            val left=(factory.plate.width*.04).toInt();val right=maxOf(left+1,(factory.plate.width*.82).toInt())
            if(px in left until right && pattern.ink((px-left)*pattern.width/(right-left),sy*pattern.height/(end-start))) return 1
            // Keep the automatically advancing frame number outside the user stencil.
            val frame=plate.number;val u=maxOf(1,minOf(maxOf(1,(factory.plate.width*.13).toInt())/maxOf(1,frame.length*4),(end-start)/5))
            val fx=(factory.plate.width*.86).toInt();val fy=maxOf(0,(end-start-5*u)/2)
            if(px<fx || sy<fy) return 0
            val gx=(px-fx)/u;val gy=(sy-fy)/u
            return if(gy in 0..4 && gx/4<frame.length && gx%4<3 && glyphs[frame[gx/4]]?.get(gy*3+gx%4)=='1') 1 else 0
        }
        val textLeft=plate.textLeft;val textOut=plate.textOut;val unit=plate.unit
        val label=plate.label
        val gx=(px-textLeft)/unit;val gy=(py-textOut)/unit
        if(px<textLeft || py<textOut || gy !in 0..4 || gx<0) return 0
        val ch=gx/4;val column=gx%4
        return if(ch<label.length && column<3 && glyphs[label[ch]]?.get((if(top) 4-gy else gy)*3+column)=='1') 1 else 0
    }
    fun fullStripRecipe(recipe: Map<String,Any?>,gateWidth: Int,gateHeight: Int): Map<String,Any?> {
        val ratio=maxOf(layout.outputWidth,layout.outputHeight).toDouble()/maxOf(gateWidth,gateHeight)
        if(ratio==1.0) return recipe
        val geometry=PhoneMaterialGeometry.resolve(recipe,gateHeight,gateWidth) ?: return recipe
        val develop=(recipe["develop"] as? Map<*,*>).orEmpty().entries.associate { it.key.toString() to it.value }
        return recipe+("develop" to (develop+("frame_long_edge_mm" to geometry.longEdgeMm*ratio)))
    }

    /** Iterate only rebate pixels, returning the physical row block offset and strip coordinates. */
    private inline fun edgePixels(start: Int,count: Int,visit: (Int,Int,Int)->Unit) {
        for(row in 0 until count) {
            val y=start+row
            if(layout.vertical) {
                for(out in 0 until layout.rebate) {
                    visit(row*layout.outputWidth+out,y,layout.height-1-out)
                    visit((row+1)*layout.outputWidth-1-out,y,out)
                }
            } else if(y<layout.rebate || y>=layout.rebate+layout.imageHeight) {
                for(x in 0 until layout.width) visit(row*layout.width+x,x,y)
            }
        }
    }
    /** Factory light and physical holes on rows that are about to enter exposure. */
    fun exposeEdge(rgb: FloatArray, start: Int, count: Int, padded: Boolean, gain: Float) {
        val hard = lamp.applyTo(floatArrayOf(1.8f,1.8f,1.8f))
        val laser = exposure?.let { lamp.applyTo(it.sampleRgb) }
        val beam = FloatArray(3)
        edgePixels(start,count) { pixel,x,y ->
                val top = y < layout.rebate
                val outward = if(top) layout.rebate-1-y else y-layout.rebate-layout.imageHeight
                val at = pixel*3
                val k = kind(x,y)
                if(k<0) { rgb.fill(0f,at,at+3); return@edgePixels }
                if(glowStrength>0) {
                    val dose=glow.at(factory.origin+x,outward,top)*glowStrength/gain
                    for(c in 0..2) rgb[at+c]+=dose*RebateGlow.warmRgb[c]
                }
                if(padded && leak!=null) for(c in 0..2) rgb[at+c] += leak.incident(x,top,c)
                if(!marks || markType=="laser_etched" || ink!=null) return@edgePixels
                if(laser!=null) exposure!!.writePixel(factory.origin+x,outward,top,laser,3,beam,0)
                else if(k>0) hard.copyInto(beam) else return@edgePixels
                for(c in 0..2) rgb[at+c] += beam[c]/gain
        }
    }

    fun eachCut(start: Int,count: Int,visit: (Int,Boolean)->Unit) {
        edgePixels(start,count) { pixel,x,y ->
            val k=kind(x,y)
            if(k<0 || (k>0 && markType=="laser_etched")) visit(pixel,k<0)
        }
    }

    private fun printInk(optical: OpticalMaster,start: Int): OpticalMaster {
        if(ink==null || !marks) return optical
        val rgb=optical.opticalDensityRgb
        val pigment=ink.map { -kotlin.math.log10(maxOf(.000001,it)).toFloat() }
        val surface=optical.spectralPostProcessDensityRgb
        edgePixels(start,rgb.shape[0]) { pixel,x,y ->
            if(kind(x,y)>0) for(c in 0..2) {
                rgb.values[pixel*3+c]+=pigment[c]
                if(surface!=null) surface.values[pixel*3+c]+=pigment[c]
            }
        }
        return optical
    }

    fun finishNative(optical: OpticalMaster,start: Int,mode: String): OpticalMaster {
        eachCut(start,optical.opticalDensityRgb.shape[0]) { i,air ->
            for(c in 0..2) {
                optical.opticalDensityRgb.values[i*3+c]=if(air) 0f else samples.opticalDensityRgb.values[3+c]
                optical.spectralPostProcessDensityRgb?.values?.set(i*3+c,0f)
            }
            optical.spectralComponentAmounts?.let { pools -> for(c in 0..8)
                if(air || (c!=6 && c!=8)) pools.values[i*9+c]=0f }
        }
        val geometry=record()+mapOf("formation_mode" to mode,"spatial_reference" to "full_strip_v1",
            "process_surface_domain" to "full_strip","light_leak_domain" to "full_strip",
            "factory_response" to if(ink!=null) "surface_ink_transmission_v1" else if(markType=="laser_etched") "bare_support_ablation_v1" else "full_strip_exposure_v1")
        return printInk(stamp(optical,geometry),start)
    }

    fun record(): Map<String,Any?> = mapOf("model" to FilmRebateGeometry.MODEL,"image_height" to layout.imageHeight,
        "transport_axis" to if(layout.vertical) "vertical" else "horizontal",
        "rebate_rows" to layout.rebate,"holes_per_row" to if(rows=="none") 0 else layout.holesPerRow,"label" to current.label,
        "perforation_windows_v1" to layout.windows(rows),
        "perforation_shift_frames" to window.shift.toDouble()/layout.width,
        "loading" to mapOf("offset_frames" to window.offset,"adjacent_frame_number" to window.adjacent.takeIf { window.shift!=0 }),
        "barcode" to mapOf("symbology" to codeStyle,
            "payload" to if(codeStyle!="code128c" || current.registeredPlate?.generatedSides.isNullOrEmpty()) null else FilmEdgeCode.payload(batch,factory.plate.frame),
            "generated_sides" to current.registeredPlate?.generatedSides.orEmpty()),
        "order" to "image_perforations_factory_marks","spatial_reference" to if(exposure==null) "uniform_two_sample_v1" else "interpolated_edge_footprint_v2",
        "factory_pattern_top" to topCode,"factory_pattern_bottom" to bottomCode,
        "factory_pattern_linked" to linked,"factory_flip_top" to flipTop,"factory_flip_bottom" to flipBottom,
        "factory_composition" to composition?.record(),
        "light_leak_domain" to if(leak==null) "none" else "edge_irradiance_continuation_v1",
        "factory_mark_type" to markType,"factory_mark_spread" to markSpread,
        "factory_exposure_ev" to lamp.ev,"factory_light_color_rgb" to lamp.rgb,
        "factory_response" to if(ink!=null) "surface_ink_transmission_v1" else if(markType=="laser_etched") "bare_support_ablation_v1" else if(exposure==null) "uniform_two_sample_v1" else "material_exposure_table_v1") +
        (if(ink!=null) mapOf("factory_ink_transmission_rgb" to ink) else emptyMap()) +
        (if(factory.halfFrame) mapOf("factory_window" to mapOf("frame" to factory.plate.frame,"half" to factory.part+1)) else emptyMap()) +
        (if(glowStrength>0) mapOf("rebate_glow" to glowStrength) else emptyMap())
    fun stamp(optical: OpticalMaster, geometry: Map<String,Any?> = record()): OpticalMaster {
        // The optical NPZ has one set of fields for every row, including the unprinted gate.
        val printedSpectrum=ink!=null && marks && optical.spectralComponentAmounts!=null
        val surface=optical.spectralPostProcessDensityRgb ?: if(printedSpectrum)
            FloatNd(optical.opticalDensityRgb.shape,FloatArray(optical.opticalDensityRgb.values.size)) else null
        var metadata=optical.metadata+(FilmRebateGeometry.KEY to geometry)
        if(printedSpectrum) {
            val spectrum=metadata[SpectralOptics.MEDIUM_KEY] as Map<*,*>
            metadata=metadata+(SpectralOptics.MEDIUM_KEY to (spectrum+("post_process_model" to SpectralOptics.POST_PROCESS_MODEL)))
        }
        return OpticalMaster(optical.opticalDensityRgb,metadata,
            optical.clearBaseOpticalDensityRgb,optical.spectralComponentAmounts,surface)
    }
    fun strip(start: Int,count: Int,reference: OpticalMaster): OpticalMaster {
        require(start>=0 && count>0 && start+count<=layout.outputHeight)
        require(layout.vertical || start+count<=layout.rebate || start>=layout.rebate+layout.imageHeight)
        fun field(channels: Int,source: FloatNd?,gate: FloatNd?): FloatNd? {
            if(source==null) return null
            val values=FloatArray(count*layout.outputWidth*channels)
            if(layout.vertical && gate!=null) {
                val span=layout.imageHeight*channels
                for(row in 0 until count)
                    gate.values.copyInto(values,(row*layout.outputWidth+layout.rebate)*channels,row*span,(row+1)*span)
            }
            val scratch=FloatArray(channels)
            val warm=FloatArray(channels)
            val levels=if(leak==null) 1 else RebateLightLeak.LEVELS
            val stride=source.values.size/levels
            edgePixels(start,count) { pixel,x,y ->
                val k=kind(x,y)
                if(k<0) return@edgePixels
                val at=pixel*channels
                val top=y<layout.rebate
                val outward=if(top) layout.rebate-1-y else y-layout.rebate-layout.imageHeight
                val weight=if(glowStrength>0) glow.at(factory.origin+x,outward,top) else 0f
                fun read(base: Int,target: FloatArray,offset: Int) {
                    if(exposure!=null && marks)
                        exposure.writePixel(factory.origin+x,outward,top,source.values,channels,target,offset,base)
                    else for(c in 0 until channels) target[offset+c]=source.values[base+k*channels+c]
                }
                fun sample(level: Int,target: FloatArray,offset: Int) {
                    val base=level*stride
                    read(base,target,offset)
                    if(weight>0) {
                        read(base+stride/2,warm,0)
                        for(c in 0 until channels) target[offset+c]+=(warm[c]-target[offset+c])*weight
                    }
                }
                val level=leak?.level(x,top) ?: 0f
                val low=level.toInt()
                sample(low,values,at)
                if(level>low) {
                    sample(low+1,scratch,0)
                    for(c in 0 until channels) values[at+c]+=(scratch[c]-values[at+c])*(level-low)
                }
            }
            return FloatNd(intArrayOf(count,layout.outputWidth,channels),values)
        }
        return printInk(stamp(OpticalMaster(field(3,samples.opticalDensityRgb,reference.opticalDensityRgb)!!,reference.metadata,reference.clearBaseOpticalDensityRgb,
            field(9,samples.spectralComponentAmounts,reference.spectralComponentAmounts),reference.spectralPostProcessDensityRgb?.let { val out=FloatArray(count*layout.outputWidth*3)
                if(layout.vertical) for(row in 0 until count) it.values.copyInto(out,(row*layout.outputWidth+layout.rebate)*3,
                    row*layout.imageHeight*3,(row+1)*layout.imageHeight*3)
                FloatNd(intArrayOf(count,layout.outputWidth,3),out) })),start)
    }
    fun surround(optical: OpticalMaster): OpticalMaster {
        if(layout.vertical) return strip(0,layout.outputHeight,optical)
        fun field(channels: Int,main: FloatNd?,top: FloatNd?,bottom: FloatNd?): FloatNd? {
            if(main==null && top==null && bottom==null) return null
            val values=FloatArray(layout.height*layout.width*channels)
            top?.values?.copyInto(values);main?.values?.copyInto(values,layout.rebate*layout.width*channels)
            bottom?.values?.copyInto(values,(layout.rebate+layout.imageHeight)*layout.width*channels)
            return FloatNd(intArrayOf(layout.height,layout.width,channels),values)
        }
        val a=strip(0,layout.rebate,optical);val b=strip(layout.rebate+layout.imageHeight,layout.rebate,optical)
        return stamp(OpticalMaster(field(3,optical.opticalDensityRgb,a.opticalDensityRgb,b.opticalDensityRgb)!!,optical.metadata,
            optical.clearBaseOpticalDensityRgb,field(9,optical.spectralComponentAmounts,a.spectralComponentAmounts,b.spectralComponentAmounts),
            field(3,optical.spectralPostProcessDensityRgb,a.spectralPostProcessDensityRgb,b.spectralPostProcessDensityRgb)))
    }
    companion object {
        fun prepare(kernel: ReducedKernel,recipe: Map<String,Any?>,width: Int,height: Int,capture: Map<*,*>?=null, formedShape: Boolean=false): FormedFilmBorder? {
            val config=recipe["film_border"] as? Map<*,*> ?: return null
            if(config["enabled"]!=true) return null
            val film=(recipe["film"] as? Map<*,*>)?.entries?.associate { it.key.toString() to it.value }.orEmpty()
            val develop=(recipe["develop"] as? Map<*,*>)?.entries?.associate { it.key.toString() to it.value }.orEmpty()
            val sampleRecipe=(RearExposure.frontRecipe(recipe)-"film_border")+mapOf("enable_mtf" to false,"enable_halation" to false,"enable_grain" to false,
                "film" to (film+mapOf("grain_strength" to 0.0,"emulsion_mtf_strength" to 0.0,"halation_strength" to 0.0,
                    "emulsion_highlight_spread_strength" to 0.0,"light_piping_strength" to 0.0)),
                "develop" to (develop+mapOf("grain_strength" to 0.0,"uneven_development" to 0.0,"chemical_stain" to 0.0,"silver_plating" to 0.0,"reticulation_strength" to 0.0,
                    "development_adjacency_strength" to 0.0,"dir_process_effectiveness" to 0.0)))
            val identity=recipe["frame_identity"] as? Map<*,*>
            val index=((config["frame_number"] ?: config["strip_frame_index"] ?: identity?.get("frame_index")) as? Number)?.toInt() ?: 1
            val name=(config["stock_label"] ?: film["name"] ?: "FILM FOUNDRY").toString().uppercase(java.util.Locale.ROOT)
                .map { if(it in glyphs || it==' ') it else ' ' }.joinToString("").trim().ifBlank { "FILM FOUNDRY" }.take(32)
            val geometry=FilmRebateGeometry.forStock(config,width,height,formedShape)
            val window=FilmStripWindow(index,geometry.width,(config["loading_offset"] as? Number)?.toDouble() ?: 0.0)
            val factory=FilmFactoryWindow(window,config["format_key"] in listOf("half","half_frame") && config["factory_repeat"]=="stock_frame")
            val codeStyle=config["factory_barcode"]?.toString() ?: "none"
            val marker=config["advance_marker_style"]?.toString() ?: "arrow"
            val leftward=config["advance_direction"]=="left"
            val registered=config["strip_layout"]=="registered_strip_v2"
            val composition=EdgeComposition.parse(config["factory_composition"])
            val sequence=(config["factory_barcode_seed"] as? Number)?.toInt() ?: 0
            val batch=(config["factory_batch"] as? Number)?.toInt() ?: 0
            val rows=config["sprocket_rows"]?.toString() ?: "both";require(rows in listOf("none","both","top","bottom"))
            fun pattern(key: String)=config[key]?.toString()?.takeIf { it.isNotBlank() }?.let(EdgeMarkPattern::decode)
            val top=pattern("factory_pattern_top");val bottom=pattern("factory_pattern_bottom");val linked=config["factory_pattern_linked"]!=false
            val type=config["factory_mark_type"]?.toString() ?: "hard";require(type in listOf("hard","laser","laser_etched","ink"))
            val ink=if(type=="ink") FilmRebateGeometry.inkTransmission(config) else null
            val spread=(config["factory_mark_spread"] as? Number)?.toDouble() ?: 1.0;require(spread.isFinite() && spread in 0.0..3.0)
            val flipTop=config["factory_flip_top"]==true;val flipBottom=config["factory_flip_bottom"]==true
            fun formed(rgb: FloatArray)=kernel.develop(kernel.expose(RgbVolume(rgb.size/3,1,linearRgb=rgb),sampleRecipe),sampleRecipe)
            val leak=RebateLightLeak.fromCapture(width,height,capture,geometry.vertical)
            val lamp=FactoryLamp.read(config)
            val glow = config["rebate_glow"] ?: 0.0
            require(glow is Number && glow.toDouble() in 0.0..1.0)
            val strength = glow.toFloat()
            fun responses(rgb: FloatArray): OpticalMaster {
                val factory=when(type) { "ink" -> FloatArray(rgb.size); "laser_etched" -> rgb; else -> lamp.applyTo(rgb) }
                if(config["formation_mode"] in listOf("native","sprocket_panorama")) return formed(factory)
                val table=if(strength==0f) factory else factory+FloatArray(factory.size) { i ->
                    factory[i]+strength*RebateGlow.warmRgb[i%3]
                }
                return formed(leak?.exposures(table) ?: table)
            }
            val optical=responses(floatArrayOf(0f,0f,0f,1.8f,1.8f,1.8f))
            if(type=="laser_etched") {
                // Sample 0 retains the unexposed, processed stock. Sample 1 retains
                // the transparent support and any surviving backing at that position.
                val density=optical.opticalDensityRgb.values
                val support=permanentSupport(film, recipe["image_polarity"] ?: recipe["medium"])
                val auxiliary=(film["auxiliary_layer_density_rgb"] ?: film["auxiliary_density_rgb"]) as? List<*>
                val pools=optical.spectralComponentAmounts?.values
                val ordered=(optical.metadata["film_process_model"] as? Map<*,*>)?.get("ordered_process") as? Map<*,*>
                val remaining=pools?.get(8) ?: ((ordered?.get("auxiliary_remaining") as? Number)?.toFloat() ?: 0f) *
                    ((film["auxiliary_layer_amount"] as? Number)?.toFloat() ?: 1f)
                for (level in 0 until density.size/6) {
                    for(c in 0..2) density[level*6+3+c]=support[c]+((auxiliary?.get(c) as? Number)?.toFloat() ?: 0f)*remaining
                    if(pools!=null) for(c in 0..8) pools[level*18+9+c]=if(c==6 || c==8) pools[level*18+c] else 0f
                }
            }
            val base=FormedFilmBorder(geometry,optical,name,window,factory,batch,codeStyle,marker,leftward,registered,sequence,composition,rows,config["factory_marks"]!=false,top,if(linked) top else bottom,linked,flipTop,flipBottom,type,spread,null,leak,lamp,strength,ink)
            if(type!="laser" || spread==0.0 || config["factory_marks"]==false) return base
            val exposure=FactoryMarkExposure(factory.plate.width,geometry.imageHeight,geometry.rebate,film,spread,recipe["enable_mtf"]!=false,recipe["enable_halation"]!=false,
                geometry.rebate*(geometry.textBottom-geometry.textTop)) { x,out,isTop -> base.markKind(x,out,isTop)>0 }
            return FormedFilmBorder(geometry,responses(exposure.sampleRgb),name,window,factory,batch,codeStyle,marker,leftward,registered,sequence,composition,rows,true,top,if(linked) top else bottom,linked,flipTop,flipBottom,type,spread,exposure,leak,lamp,strength,ink)
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
