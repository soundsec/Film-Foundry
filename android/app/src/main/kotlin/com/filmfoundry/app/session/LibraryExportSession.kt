package com.filmfoundry.app.session

import com.filmfoundry.platform.*
import com.filmfoundry.workflow.*
import java.io.OutputStream

/** Exports derived images or portable materials, without taking ownership of their sources. */
class LibraryExportSession(private val storage: StorageAdapter,private val medium: MediumStore,
    private val photoFormat: ()->ScanImageFormat={ ScanImageFormat.PNG },private val photoJpegQuality: ()->Int={95},private val filmRotation: (DevelopedResult)->Int={0}) {
    data class Item(val id: String,val name: String,val mime: String,val image: Boolean,
        val prepare: suspend (WorkflowControl)->((OutputStream)->Unit))
    data class Outcome(val saved: Int,val failed: List<String>,val cancelled: Boolean=false)
    fun file(id: String,path: String,name: String,mime: String,image: Boolean=false)=Item(id,name,mime,image) { control ->
        require(storage.existsPrivate(path)) { "Source file is missing: $name" }
        val writer: (OutputStream)->Unit = { output -> storage.readPrivate(path) { input ->
            val buffer=ByteArray(64*1024)
            while(true) { control.checkCancel();val n=input.read(buffer);if(n<0) break;output.write(buffer,0,n) }
        } };writer
    }
    fun photo(saved: SavedScan): Item=photo(saved,photoFormat(),photoJpegQuality())
    fun photo(saved: SavedScan,format: ScanImageFormat,jpegQuality: Int=95): Item {
        require(jpegQuality in 1..100)
        ScanDelivery.requireExport(saved,format)
        if(saved.viewRotationDegrees==0 && format==saved.format) return file(saved.id,saved.imagePath,saved.displayName,saved.format.mime,true)
        return Item(saved.id,saved.displayName.substringBeforeLast('.')+"."+format.extension,format.mime,true) { control ->
            require(format!=ScanImageFormat.TIFF || saved.precisionBits>=16) { "这张旧照片没有16位扫描数据，请从来源底片重新扫描后导出 TIFF / Rescan this older photo for 16-bit TIFF" }
            require(storage.existsPrivate(saved.imagePath)) { "Saved photo file is missing" }
            val writer: (OutputStream)->Unit={ output -> ScanImageExport.write(storage,saved.displayPath,format,saved.viewRotationDegrees,output,control::checkCancel,if(saved.format==ScanImageFormat.JPEG) minOf(jpegQuality,ScanDelivery.jpegQuality(saved.editingRecipe())) else jpegQuality);Unit };writer
        }
    }
    fun optical(result: DevelopedResult)=file(result.resultId,result.opticalPath,
        "FilmFoundry-${result.resultId}${com.filmfoundry.interchange.DataKernel.NEGATIVE_SUFFIX}","application/octet-stream")
    fun image(result: DevelopedResult,recipe: Map<String,Any?>,mode: String,budget: ()->Long): Item=
        image(result,recipe,mode,budget,ScanImageFormat.PNG)
    fun image(result: DevelopedResult,recipe: Map<String,Any?>,mode: String,budget: ()->Long,format: ScanImageFormat=ScanImageFormat.PNG,jpegQuality: Int=95): Item {
        require(jpegQuality in 1..100)
        require(mode in listOf("viewer_light_table","viewer_projection"))
        val oriented=OutputOrientation.apply(recipe,OutputOrientation.read(recipe)+filmRotation(result))
        val frozen=frozenRecipe((oriented-"scan_grid_long_edge")+mapOf("observe_mode" to mode,
            "scanner" to ((oriented["scanner"] as? Map<*,*>)?.entries?.associate { it.key.toString() to it.value }.orEmpty()-"scan_grid_long_edge")))
        return Item("${result.resultId}:$mode","FilmFoundry-${result.resultId}-${if(mode=="viewer_light_table") "light-table" else "projection"}.${format.extension}",format.mime,true) { control ->
            require(storage.existsPrivate(result.opticalPath)) { "Source negative is missing" }
            val writer: (OutputStream)->Unit = { output -> if(format==ScanImageFormat.PNG) medium.scanPng(result.opticalPath,frozen,output,control,budget()) else {
                val temporary=java.io.File.createTempFile("film-export-",".png",storage.rowScratchRoot() ?: error("Temporary storage unavailable"))
                try {
                    temporary.outputStream().buffered(65536).use { medium.scanPng(result.opticalPath,frozen,it,control,budget(),bitDepth=16) }
                    val adapter=object: StorageAdapter by storage { override fun <T> readPrivate(relative: String,reader: (java.io.InputStream)->T): T=temporary.inputStream().buffered(65536).use(reader) }
                    ScanImageExport.write(adapter,"scan.png",format,0,output,control::checkCancel,jpegQuality)
                } finally { temporary.delete() }
            } };writer
        }
    }
    suspend fun run(items: List<Item>,control: WorkflowControl,progress: (Int,Int)->Unit={ _,_-> },
        commit: (Item,(OutputStream)->Unit)->Unit): Outcome {
        require(items.isNotEmpty() && items.map { it.id }.distinct().size==items.size)
        var saved=0;val failed=mutableListOf<String>()
        for((index,item) in items.withIndex()) {
            if(control.cancelRequested()) return Outcome(saved,failed,true)
            try { val writer=item.prepare(control);control.checkCancel();control.measured("export","file_commit") { commit(item,writer) };saved++;control.measured("export","file_saved") {} }
            catch(error: Exception) {
                if(control.cancelRequested() || error is WorkflowException && error.code=="cancelled") return Outcome(saved,failed,true)
                failed+=item.name+(error.message?.let { ": $it" } ?: "")
                control.measured("export","file_failed") {}
            }
            progress(index+1,items.size)
        }
        return Outcome(saved,failed)
    }
    suspend fun gallery(items: List<Item>,control: WorkflowControl,progress: (Int,Int)->Unit={ _,_-> }): Outcome {
        require(items.all { it.image && it.mime in listOf("image/png","image/jpeg") }) { "TIFF and materials must be exported to files; use JPG or PNG for the gallery" }
        return run(items,control,progress) { item,writer -> storage.publishGalleryStream(item.name,item.mime,control::checkCancel,writer) }
    }
}
