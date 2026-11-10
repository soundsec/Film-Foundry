package com.filmfoundry.workflow

import com.filmfoundry.interchange.*
import com.filmfoundry.kernel.*
import kotlin.math.pow

/** A synthetic sample through the actual camera/material/processing/scan models. No device I/O. */
object CameraEffectsPreview {
    val shapes=PhoneCaptureConditions.SHAPES
    val sides=PhoneCaptureConditions.SIDES
    val printStyles=PhoneCaptureConditions.PRINT_STYLES
    data class Images(val clean: FloatNd,val effects: FloatNd)
    class Request(film: Map<String,Any?>,capture: Map<String,Any?> = emptyMap(),selected: Map<String,Any?> = emptyMap(),val longEdge: Int=480) {
        init { require(longEdge in 64..480) }
        val film=frozenRecipe(film);val capture=frozenRecipe(capture);val selected=frozenRecipe(selected)
        internal fun matches(other: Request)=this===other || film==other.film && capture==other.capture && selected==other.selected && longEdge==other.longEdge
    }
    fun validate(camera: Map<String,Any?>) { PhoneCaptureConditions(480,320,mapOf("model" to PHONE_CAPTURE_CONDITIONS_MODEL,"camera" to camera)) }
    fun render(camera: Map<String,Any?>,film: Map<String,Any?>,capture: Map<String,Any?> = emptyMap(),
        selected: Map<String,Any?> = emptyMap(),exposureEv: Double=0.0): Images {
        val pipeline=Pipeline(Request(film,capture,selected))
        return Images(pipeline.image(emptyMap(),exposureEv),pipeline.image(camera,exposureEv))
    }
    /** The direct path above remains available to check reuse against fresh preparation. */
    internal class Pipeline(request: Request) {
        private val film=request.film;private val capture=request.capture;private val selected=request.selected
        private val format=capture["format"]?.toString() ?: "135"
        private val geometry=StillFrameFormat.create(format,false,request.longEdge,request.longEdge*3/4,StillFrameFormat.MODEL)
        private val w=geometry.width;private val h=geometry.height
        val sampleBytes get()=sample.size*4L
        private val colors=arrayOf(floatArrayOf(.65f,.06f,.025f),floatArrayOf(.05f,.5f,.1f),floatArrayOf(.04f,.12f,.7f),floatArrayOf(.52f,.33f,.19f))
        private val cell=(14*request.longEdge/480).coerceAtLeast(1)
        private val sample=FloatArray(w*h*3) { i -> val x=i/3%w;val y=i/3/w;val c=i%3
            when { y<h/3 -> .02f+x.toFloat()/w*.8f
                y<h*2/3 -> colors[(x*4/w).coerceAtMost(3)][c]
                else -> if((x/cell+y/cell)%2==0) .12f else .32f }
        }
        private val kernel=PhoneReducedKernel()
        // Stock selection happens before loading supplies this reference. Use the same
        // default here, without changing the selected stock or an already loaded roll.
        private val previewFilm=film+("spatial_reference_short_edge_mm" to (film["spatial_reference_short_edge_mm"] ?: 24.0))
        private val exposure=PhoneMaterialDefaults.exposureRecipe(previewFilm,capture)
        private val scanner=(selected["scanner"] as? Map<*,*>)?.entries?.associate { it.key.toString() to it.value } ?: PhoneFinishRecipes.scanner(PhoneFinishRecipes.AUTO)
        private val recipe=exposure+(selected-PhoneFormationProfile.OPTION)+mapOf("film" to exposure["film"],"enable_subtractive" to (MaterialSelection.tags(film).color!="bw"),"develop" to ((selected["develop"] as? Map<String,Any?>)
            ?: ((exposure["develop"] as? Map<String,Any?>).orEmpty()+("program_key" to "auto"))),
            PhoneProcessModels.OPTION to PhoneProcessModels.CURRENT,PhoneMaterialPhenomena.DEVELOPMENT_OPTION to PhoneMaterialPhenomena.CURRENT,
            PhoneOpticalDensityModels.OPTION to PhoneOpticalDensityModels.CURRENT,
            "scanner" to (scanner-"scan_grid_long_edge"),"observe_mode" to "scanner","frame_identity" to mapOf("roll_id" to "settings-sample","frame_index" to 1))
        private val preparedExposure by lazy { kernel.prepareExposure(recipe,w,h) }
        private val preparedBorder by lazy { FormedFilmBorder.prepare(kernel,recipe,w,h) }
        fun image(settings: Map<String,Any?>,exposureEv: Double,reuse: Boolean=false,check: ()->Unit={}): FloatNd {
            require(exposureEv.isFinite() && exposureEv in -4.0..4.0)
            check()
            val values=sample.copyOf()
            val gain=2.0.pow(exposureEv).toFloat()
            for(i in values.indices) values[i]*=gain
            PhoneCaptureConditions(w,h,mapOf("model" to PHONE_CAPTURE_CONDITIONS_MODEL,"camera" to settings)).apply(values,0,h)
            PhoneFilmStorageConditions(capture).apply(values)
            check()
            val source=RgbVolume(w,h,linearRgb=values)
            val latent=if(reuse) preparedExposure.exposeRows(source) else kernel.expose(source,recipe)
            check()
            val formed=kernel.develop(latent,recipe)
            check()
            val border=if(reuse) preparedBorder else FormedFilmBorder.prepare(kernel,recipe,w,h)
            val output=kernel.observe(border?.surround(formed) ?: formed,recipe).outputRgb
            check();return output
        }
    }
}
