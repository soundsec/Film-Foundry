package com.filmfoundry.workflow

import com.filmfoundry.kernel.*
import kotlin.math.pow

/** A physical strip travels through the normal exposure and bath row workers. */
internal class NativeFilmStrip private constructor(val plate: FormedFilmBorder, private val snapshot: Map<String,Any?>,
    private val gain: Float, private val rear: RearExposure?) {
    private val mode = snapshot.getValue("formation_mode") as String

    fun source(input: LinearRgbRows): RgbRows = object: RgbRows {
        private val incident = rear?.bind(input) ?: input
        override val width = plate.layout.outputWidth
        override val height = plate.layout.outputHeight
        override fun checkActive() = input.checkActive()
        override fun readRows(start: Int,count: Int): RgbVolume {
            val first = if(mode=="native" && !plate.layout.vertical) plate.layout.rebate else 0
            val a = maxOf(start,first)
            val b = minOf(start+count,first+input.height)
            val block = RgbVolume(width,count)
            if (a<b) {
                val rgb=incident.readRows(a-first,b-a).linearRgb
                if(width==input.width) rgb.copyInto(block.linearRgb,(a-start)*width*3)
                else for(row in 0 until b-a) rgb.copyInto(block.linearRgb,((a-start+row)*width+plate.layout.rebate)*3,
                    row*input.width*3,(row+1)*input.width*3)
            }
            plate.exposeEdge(block.linearRgb,start,count,mode=="native",gain)
            return block
        }
    }

    fun freeze(latent: LatentState,start: Int): LatentState {
        val count=latent.arrays.getValue("halide").shape[0]
        val pools=listOfNotNull(latent.arrays["halide"],latent.arrays["coupler"],latent.arrays["developability"])
        plate.eachCut(start,count) { pixel,air -> if(air) {
            for (it in pools) {
                val channels=it.shape[2];it.values.fill(0f,pixel*channels,(pixel+1)*channels)
            }
        } }
        val metadata = latent.metadata+("film_border_snapshot" to snapshot)
        return LatentState(latent.arrays,if (rear == null) metadata else metadata+mapOf("film_side" to "base","rear_optics" to rear.record))
    }

    fun finish(master: OpticalMaster,start: Int) = plate.finishNative(master,start,mode)

    fun exposureRecipe(recipe: Map<String,Any?>, width: Int, height: Int) =
        plate.fullStripRecipe(RearExposure.frontRecipe(recipe),width,height)

    fun expose(kernel: ReducedKernel,input: RgbVolume,recipe: Map<String,Any?>): LatentState {
        val rows = source(workingRgbRows(input.width,input.height) { y,n ->
            input.linearRgb.copyOfRange(y*input.width*3,(y+n)*input.width*3)
        })
        return freeze(kernel.expose(rows.readRows(0,rows.height),exposureRecipe(recipe,input.width,input.height)),0)
    }

    companion object {
        fun from(kernel: ReducedKernel,recipe: Map<String,Any?>,width: Int,height: Int,capture: Map<*,*>?=null,
            formed: Boolean=false): NativeFilmStrip? {
            val config = recipe["film_border"] as? Map<*,*> ?: return null
            if(config["enabled"]!=true || config["formation_mode"] !in listOf("native","sprocket_panorama")) return null
            val plate=FormedFilmBorder.prepare(kernel,recipe,width,height,capture,formed) ?: return null
            val index=(recipe["frame_identity"] as? Map<*,*>)?.get("frame_index") ?: 1
            val snapshot=config.entries.associate { it.key.toString() to it.value } + mapOf(
                "transport_axis" to if(plate.layout.vertical) "vertical" else "horizontal",
                "model" to "film_rebate_exposure_v1", "frame_number" to (config["frame_number"] ?: config["strip_frame_index"] ?: index),
                "rebate_height_ratio" to (config["rebate_height_ratio"] ?: if(config["format_key"]=="120" && config["formation_mode"]!="sprocket_panorama") .075 else .23))
            val ev=((recipe["look"] as? Map<*,*>)?.get("exposure_ev") as? Number)?.toDouble() ?: 0.0
            return NativeFilmStrip(plate,snapshot,2.0.pow(ev).toFloat(),if (formed) null else RearExposure.from(recipe,width,height))
        }

        /** A pre-existing gate-sized latent cannot acquire native edges during development. */
        fun developmentRecipe(recipe: Map<String,Any?>,metadata: Map<String,Any?>): Map<String,Any?> {
            val frozen=metadata["film_border_snapshot"] as? Map<*,*>
            if(frozen!=null) return recipe+("film_border" to frozen)
            val requested=recipe["film_border"] as? Map<*,*> ?: return recipe
            return if(requested["formation_mode"] in listOf("native","sprocket_panorama"))
                recipe+("film_border" to (requested.entries.associate { it.key.toString() to it.value }+("formation_mode" to "attached")))
            else recipe
        }
    }
}

