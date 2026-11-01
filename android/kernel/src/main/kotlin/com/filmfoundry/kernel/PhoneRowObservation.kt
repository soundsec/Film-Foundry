package com.filmfoundry.kernel

import com.filmfoundry.interchange.FloatNd
import kotlin.math.floor

/** Same pointwise scanner math, with one global exact normalization measured across all rows. */
class PhoneRowObservation(private val recipe: Map<String,Any?>,private val transmissionCompute: DensityTransmissionCompute?=null) {
    private val scanner=recipe["scanner"] as? Map<*,*>
    private fun field(key: String)=scanner?.get(key) ?: recipe[key]
    val projection=recipe["observe_mode"]=="viewer_projection"
    private val viewer=projection || recipe["observe_mode"]=="viewer_light_table"
    private val rowRecipe=(recipe-"scan_grid_long_edge")+("scanner" to (scanner?.entries?.associate { it.key.toString() to it.value }.orEmpty()-"scan_grid_long_edge"))
    private val fallbackBase=PhoneReducedKernel().baseDensity(recipe)
    private val device=if(viewer) null else PhoneScannerCapture(scanner?.entries?.associate { it.key.toString() to it.value },rowRecipe)
    private var actualInversion: Boolean?=null
    fun linear(optical: OpticalMaster): ScanObservation=PhoneObserve.run(optical,rowRecipe,fallbackBase,true,device,transmissionCompute).also {
        actualInversion=it.metadata["invert_transmission"] as Boolean
    }
    fun encode(values: FloatArray) {
        check(actualInversion!=null) { "Read final film interpretation before encoding scan rows" }
        PhoneObserve.encodeRows(values,rowRecipe,viewer,invertTransmission=actualInversion)
    }
    fun normalizer(pixels: Int,eachBlock: ((FloatArray)->Unit)->Unit): (FloatArray)->Unit {
        val bounded=!viewer && PhoneObserve.rangeFirstNegative(recipe,checkNotNull(actualInversion))
        val plan=measureNormalization(pixels,eachBlock)
        return { values -> if(plan!=null) plan.apply(values,boundedInput=bounded) else if(bounded) PhoneObserve.boundNegative(values) }
    }
    fun outputPlan(pixels: Int,eachBlock: ((FloatArray)->Unit)->Unit): ScanOutputPlan {
        val invert=checkNotNull(actualInversion) { "Read final film interpretation before encoding scan rows" }
        return ScanOutputPlan(measureNormalization(pixels,eachBlock),!viewer && PhoneObserve.rangeFirstNegative(recipe,invert),
            PhoneObserve.outputEncoding(rowRecipe,viewer,invert))
    }
    fun output(values: FloatArray,plan: ScanOutputPlan) {
        val result=transmissionCompute?.encodeOutput(values,plan)
        if(result==null) plan.applyInPlace(values)
        else {
            require(result.size==values.size && result.all { it>=0f && it<=1f }) { "Invalid accelerated scan output" }
            result.copyInto(values)
        }
    }
    private fun measureNormalization(pixels: Int,eachBlock: ((FloatArray)->Unit)->Unit): ScanNormalization? {
        val strength=(field("scan_normalize_strength") as? Number)?.toFloat()?.coerceIn(0f,1f) ?: .45f
        if(viewer || field("scan_normalize")==false || strength<=0f) return null
        return ScanNormalization.measureRows(pixels,strength,field("scan_normalize_mode")?.toString() ?: "luma",
            (field("scan_black_percentile") as? Number)?.toFloat() ?: .3f,(field("scan_white_percentile") as? Number)?.toFloat() ?: 99.7f,eachBlock)
    }
    companion object {
        /** Exact existing box/bilinear grid math; only one/two source rows cached. */
        fun sampleRows(sourceWidth: Int,sourceHeight: Int,width: Int,height: Int,start: Int,count: Int,read: (Int)->OpticalMaster): OpticalMaster {
            require(start>=0 && count>0 && start.toLong()+count<=height)
            val cache=object: LinkedHashMap<Int,OpticalMaster>(2,.75f,true) { override fun removeEldestEntry(e: MutableMap.MutableEntry<Int,OpticalMaster>?)=size>2 }
            fun row(y: Int)=cache.getOrPut(y){read(y)}
            val first=row(0)
            fun resize(channels: Int,field: (OpticalMaster)->FloatNd?): FloatNd? {
                if(field(first)==null) return null
                val out=FloatArray(width*count*channels)
                val bx=sourceWidth/width;val by=sourceHeight/height
                val box=bx>=1 && by>=1 && bx*width==sourceWidth && by*height==sourceHeight
                for(y in 0 until count) {
                    if(box) {
                        for(yy in 0 until by) {
                            val values=field(row((start+y)*by+yy))!!.values
                            for(x in 0 until width) for(c in 0 until channels) for(xx in 0 until bx) out[(y*width+x)*channels+c]+=values[(x*bx+xx)*channels+c]
                        }
                        val n=(bx*by).toFloat();for(i in y*width*channels until (y+1)*width*channels) out[i]/=n
                    } else {
                        val gy=(start+y+.5f)*sourceHeight/height-.5f
                        val y0=floor(gy.toDouble()).toInt().coerceIn(0,sourceHeight-1);val y1=minOf(y0+1,sourceHeight-1);val fy=(gy-y0).coerceIn(0f,1f)
                        val a=field(row(y0))!!.values;val b=field(row(y1))!!.values
                        for(x in 0 until width) {
                            val gx=(x+.5f)*sourceWidth/width-.5f
                            val x0=floor(gx.toDouble()).toInt().coerceIn(0,sourceWidth-1);val x1=minOf(x0+1,sourceWidth-1);val fx=(gx-x0).coerceIn(0f,1f)
                            for(c in 0 until channels) out[(y*width+x)*channels+c]=a[x0*channels+c]*(1-fx)*(1-fy)+a[x1*channels+c]*fx*(1-fy)+b[x0*channels+c]*(1-fx)*fy+b[x1*channels+c]*fx*fy
                        }
                    }
                }
                return FloatNd(intArrayOf(count,width,channels),out)
            }
            return OpticalMaster(resize(3){it.opticalDensityRgb}!!,first.metadata,first.clearBaseOpticalDensityRgb,
                resize(9){it.spectralComponentAmounts},resize(3){it.spectralPostProcessDensityRgb})
        }
    }
}
