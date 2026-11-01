package com.filmfoundry.app.session

import com.filmfoundry.platform.StorageAdapter
import com.filmfoundry.interchange.RecipeJson
import java.io.File
import java.io.OutputStream
import java.util.concurrent.atomic.AtomicLong

/** Disposable rendered views only. No material reader/exporter ever uses these PNGs. */
class NegativeViewCache(private val storage: StorageAdapter,private val maxBytes: Long=96L*1048576,
    private val stamp: (String)->String?={ path ->
        val root=File(storage.privateDir()).canonicalFile;val file=File(root,path).canonicalFile
        if(file.path.startsWith(root.path+File.separator) && file.isFile) "${file.length()}:${file.lastModified()}" else null
    }) {
    companion object { const val DIRECTORY="viewer-cache" }
    data class Entry(val path: String,val sourceWidth: Int,val sourceHeight: Int)
    private val epoch=AtomicLong()
    private fun base(id: String,mode: String): String {
        require(id.isNotBlank() && id.length<=180 && id.all { it.isLetterOrDigit() || it in "-_" })
        require(mode in listOf("viewer_light_table","viewer_projection"))
        return "$DIRECTORY/$id-$mode-v3"
    }
    private fun record(path: String,recipe: Map<String,Any?>): ByteArray? {
        val value=stamp(path) ?: return null
        return RecipeJson.text(mapOf("model" to "rendered_optical_view_v3","source" to path,"stamp" to value,"recipe" to recipe)).toByteArray(Charsets.UTF_8)
    }
    fun find(id: String,path: String,mode: String,recipe: Map<String,Any?>,check: ()->Unit): Entry? {
        check();val prefix=base(id,mode);val expected=record(path,recipe) ?: return null
        if(!storage.existsPrivate("$prefix.json") || !storage.existsPrivate("$prefix.png")) return null
        val value=runCatching { storage.readPrivate("$prefix.json") { input ->
            val output=java.io.ByteArrayOutputStream();val bytes=ByteArray(4096)
            while(true) { val n=input.read(bytes);if(n<0) break;require(output.size()+n<=1024*1024);output.write(bytes,0,n) }
            RecipeJson.objectMap(output.toString("UTF-8"))
        } }.getOrNull() ?: return null
        check()
        val matches=runCatching { RecipeJson.equivalent(RecipeJson.objectMap(value["key"] as String),RecipeJson.objectMap(expected.toString(Charsets.UTF_8))) }.getOrDefault(false)
        if(!matches) return null
        val w=(value["width"] as? Number)?.toInt() ?: return null;val h=(value["height"] as? Number)?.toInt() ?: return null
        if(w<=0 || h<=0 || w.toLong()*h>Int.MAX_VALUE/3) return null
        runCatching { File(storage.privateDir(),"$prefix.png").setLastModified(System.currentTimeMillis()) }
        return Entry("$prefix.png",w,h)
    }
    fun save(id: String,path: String,mode: String,recipe: Map<String,Any?>,width: Int,height: Int,check: ()->Unit,write: (OutputStream)->Unit) {
        check();val token=epoch.get();val prefix=base(id,mode);val expected=record(path,recipe) ?: return
        require(width>0 && height>0 && width.toLong()*height<=Int.MAX_VALUE/3)
        val metadata=RecipeJson.text(mapOf("key" to expected.toString(Charsets.UTF_8),"width" to width,"height" to height)).toByteArray(Charsets.UTF_8)
        require(metadata.size<=262144)
        fun valid() { check();kotlin.check(token==epoch.get() && expected.contentEquals(record(path,recipe))) { "View cache invalidated" } }
        // Invalidate the previous key first: interruption between the two commits is a cache miss.
        storage.deletePrivate("$prefix.json")
        try {
            storage.writePrivate("$prefix.png",::valid,write)
            valid();storage.writePrivate("$prefix.json",::valid) { it.write(metadata) };valid()
        } catch(error: Throwable) {
            storage.deletePrivate("$prefix.json");storage.deletePrivate("$prefix.png");throw error
        }
        trim("$prefix.png".substringAfterLast('/'))
    }
    private fun trim(newest: String) {
        val dir=File(storage.privateDir(),DIRECTORY)
        val files=dir.listFiles()?.filter { it.isFile && it.name.endsWith(".png") }?.sortedWith(compareBy<File> { it.name==newest }.thenBy { it.lastModified() }).orEmpty()
        var total=files.sumOf { it.length() }
        for(file in files) {
            if(total<=maxBytes) break
            total-=file.length();file.delete();File(dir,file.name.removeSuffix(".png")+".json").delete()
        }
    }
    fun delete(id: String) { epoch.incrementAndGet();for(mode in listOf("viewer_light_table","viewer_projection")) {
        val prefix=base(id,mode);storage.deletePrivate("$prefix.json");storage.deletePrivate("$prefix.png")
    } }
    fun clear() {
        epoch.incrementAndGet()
        File(storage.privateDir(),DIRECTORY).listFiles()?.filter { it.isFile && (it.name.endsWith(".png") || it.name.endsWith(".json")) }?.forEach { it.delete() }
    }
}
