package com.filmfoundry.app.session

import android.graphics.Bitmap
import android.graphics.BitmapFactory
import com.filmfoundry.interchange.RecipeJson
import com.filmfoundry.platform.CaptureReceiptStore
import com.filmfoundry.workflow.RollDirectReference
import java.io.File
import java.io.FileOutputStream
import java.nio.file.Files
import java.nio.file.StandardCopyOption
import kotlin.math.max

/** Small JPEG plus stats beside the roll. Original captures and the optical master stay untouched. */
class RollDirectReferenceStore(private val root: File) {
    fun stats(rollId: String): Map<String,Any?>? {
        val file=File(directory(rollId),statsName)
        if(!file.isFile || file.length()>65536) return null
        val value=runCatching { RecipeJson.objectMap(file.readText()) }.getOrNull() ?: return null
        return if(value["model"]==RollDirectReference.MODEL) value else null
    }
    fun imageFile(rollId: String)=File(directory(rollId),imageName)
    /** Returns true only when a new reference was written. RAW has no display image and is skipped. */
    fun ensure(captures: CaptureReceiptStore,rollId: String): Boolean {
        val image=imageFile(rollId)
        if(image.isFile && File(image.parentFile,statsName).isFile) return false
        val path=captures.path(rollId,1)
        if(!captures.exists(path)) return false
        val source=runCatching { captures.read(path).sources.firstOrNull() }.getOrNull() ?: return false
        if(source.record["kind"]=="sensor_raw_dng" || !source.path.endsWith("source.jpg")) return false
        val jpeg=runCatching { captures.sourceFile(path,source) }.getOrNull() ?: return false
        if(!jpeg.isFile) return false
        return write(rollId,jpeg)
    }
    private fun write(rollId: String,jpeg: File): Boolean = synchronized(this) {
        val dir=directory(rollId);val image=File(dir,imageName);val statsFile=File(dir,statsName)
        if(image.isFile && statsFile.isFile) return false
        if(image.isFile) image.delete()
        val bounds=BitmapFactory.Options().apply { inJustDecodeBounds=true }
        BitmapFactory.decodeFile(jpeg.path,bounds)
        if(bounds.outWidth<=0 || bounds.outHeight<=0) return false
        var sample=1
        while(max(bounds.outWidth,bounds.outHeight)/sample>192) sample*=2
        val decoded=BitmapFactory.decodeFile(jpeg.path,BitmapFactory.Options().apply { inSampleSize=sample }) ?: return false
        val scaled=try {
            val long=max(decoded.width,decoded.height)
            if(long<=192) decoded else Bitmap.createScaledBitmap(decoded,(decoded.width*192f/long).toInt().coerceAtLeast(1),(decoded.height*192f/long).toInt().coerceAtLeast(1),true)
        } catch(error: Exception) { decoded.recycle();throw error }
        try {
            val pixels=IntArray(scaled.width*scaled.height)
            scaled.getPixels(pixels,0,scaled.width,0,0,scaled.width,scaled.height)
            val stats=RollDirectReference.fromSrgb(pixels,scaled.width,scaled.height)
            dir.mkdirs()
            val pending=File(dir,".direct-reference.tmp")
            FileOutputStream(pending).use { scaled.compress(Bitmap.CompressFormat.JPEG,85,it);it.fd.sync() }
            moveReplacing(pending,image)
            val json=File(dir,".direct-reference.json.tmp")
            FileOutputStream(json).use { it.write(RecipeJson.text(stats).toByteArray());it.fd.sync() }
            moveReplacing(json,statsFile)
            true
        } finally {
            if(scaled !== decoded) decoded.recycle()
            scaled.recycle()
        }
    }
    private fun moveReplacing(from: File,to: File) {
        try { Files.move(from.toPath(),to.toPath(),StandardCopyOption.ATOMIC_MOVE,StandardCopyOption.REPLACE_EXISTING) }
        catch(_: java.nio.file.AtomicMoveNotSupportedException) { Files.move(from.toPath(),to.toPath(),StandardCopyOption.REPLACE_EXISTING) }
    }
    private fun directory(rollId: String): File {
        require(rollId.isNotBlank() && rollId.none { it in "/\\:." })
        val dir=File(root,rollId)
        val canonical=dir.canonicalFile;val base=root.canonicalFile
        require(canonical.path.startsWith(base.path+File.separator))
        return dir
    }
    private companion object { const val imageName="direct_reference.jpg";const val statsName="direct_reference.json" }
}
