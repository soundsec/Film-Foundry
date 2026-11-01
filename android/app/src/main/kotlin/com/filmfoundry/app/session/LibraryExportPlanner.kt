package com.filmfoundry.app.session

import com.filmfoundry.platform.*
import com.filmfoundry.workflow.*
import com.filmfoundry.interchange.*

data class LibraryExportRequest(val kind: String,val ids: List<String>,val recipe: Map<String,Any?> = emptyMap(),val format: String="PNG",val jpegQuality: Int=95) {
    init {
        require(jpegQuality in 1..100)
        require(format in ScanImageFormat.entries.map { it.name })
        require(kind in setOf("photos","optical","light_table","projection","latents","originals"))
        require(ids.isNotEmpty() && ids.size<=1000 && ids.distinct().size==ids.size)
    }
    fun encode()=RecipeJson.text(mapOf("kind" to kind,"ids" to ids,"recipe" to recipe,"format" to format,"jpeg_quality" to jpegQuality))
    companion object {
        fun decode(text: String): LibraryExportRequest {
            require(text.length<=262144)
            val o=RecipeJson.objectMap(text)
            @Suppress("UNCHECKED_CAST")
            val quality=o["jpeg_quality"]?.let { value ->
                val number=requireNotNull(value as? Number) { "Invalid JPEG quality" }.toDouble()
                require(number.isFinite() && number in 1.0..100.0 && number==number.toInt().toDouble()) { "Invalid JPEG quality" }
                number.toInt()
            } ?: 95
            return LibraryExportRequest(o["kind"] as String,(o["ids"] as List<*>).map { it as String },o["recipe"] as Map<String,Any?>,o["format"] as? String ?: "PNG",quality)
        }
    }
}

/** Application-owned resource lookup; UI supplies only stable IDs and explicit observation settings. */
class LibraryExportPlanner(private val library: FilmLibrary,private val scans: PrivateScanStore,
    private val captures: CaptureReceiptStore,private val deferred: DeferredShootSession,
    private val exports: LibraryExportSession,private val budget: ()->Long) {
    fun allowedFormats(request: LibraryExportRequest): Set<ScanImageFormat> {
        if(request.kind!="photos") return ScanImageFormat.entries.toSet()
        val selected=scans.matching().associateBy { it.id }
        return request.ids.map { id -> val photo=selected[id] ?: error("Photo missing");ScanDelivery.allowed(photo.format,photo.precisionBits) }
            .reduce { a,b -> a.intersect(b) }
    }
    suspend fun items(request: LibraryExportRequest,control: WorkflowControl): List<LibraryExportSession.Item> {
        control.checkCancel()
        if(request.kind=="photos") {
            val photos=scans.matching(check=control::checkCancel).associateBy { it.id }
            return request.ids.map { exports.photo(photos[it] ?: error("Selected photo is missing"),ScanImageFormat.valueOf(request.format),request.jpegQuality) }
        }
        if(request.kind in listOf("optical","light_table","projection")) {
            val results=library.developedResults().associateBy { it.resultId }
            return request.ids.map { id ->
                val result=results[id] ?: error("Selected negative is missing")
                if(request.kind=="optical") exports.optical(result) else exports.image(result,request.recipe,
                    if(request.kind=="light_table") "viewer_light_table" else "viewer_projection",budget,ScanImageFormat.valueOf(request.format),request.jpegQuality)
            }
        }
        return request.ids.flatMap { id ->
            control.checkCancel();val roll=library.roll(id)
            if(request.kind=="latents") {
                require(roll.lifecycle==RollLifecycle.SEALED) { "Seal the selected rolls before exporting latents" }
                roll.frames.filter { it.state in listOf(FrameState.CAPTURED,FrameState.LATENT) }.map { frame ->
                    LibraryExportSession.Item("${roll.rollId}:${frame.frameIndex}","FilmFoundry-${roll.rollId.take(8)}-${frame.frameIndex}${DataKernel.LATENT_SUFFIX}","application/octet-stream",false) { cancel ->
                        val model=if(roll.captureSettings[PhoneGrainModels.ROLL_SETTING]==PhoneGrainModels.LAYERED) PhoneGrainModels.LAYERED else PhoneGrainModels.CURRENT
                        val path=deferred.prepareFrame(roll.rollId,frame.frameIndex,cancel,model).latentPath ?: error("Latent is missing")
                        exports.file("latent",path,"latent.npz","application/octet-stream").prepare(cancel)
                    }
                }
            } else roll.frames.filter { it.capturePath!=null }.flatMap { frame ->
                control.checkCancel();val path=frame.capturePath!!
                captures.read(path).sources.mapIndexed { i,source ->
                    val extension=if(source.path.endsWith(".dng")) "dng" else "jpg"
                    exports.file("${roll.rollId}:${frame.frameIndex}:$i",path.substringBeforeLast('/')+"/"+source.path,
                        "FilmFoundry-${roll.rollId.take(8)}-${frame.frameIndex}-${i+1}.$extension",if(extension=="dng") "image/x-adobe-dng" else "image/jpeg")
                }
            }
        }.also { require(it.isNotEmpty()) { "No source files to export" } }
    }
}
