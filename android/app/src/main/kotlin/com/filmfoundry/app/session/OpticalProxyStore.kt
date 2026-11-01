package com.filmfoundry.app.session

import com.filmfoundry.interchange.*
import java.io.*
import java.nio.file.Files
import java.nio.file.StandardCopyOption
import java.util.zip.Deflater

/** Disposable display proxies with their own envelope, never a medium or formal scan source. */
class OpticalProxyStore(private val mediaRoot: File,cacheRoot: File,private val budget: Long=64L*1024*1024,
    private val readSource: (InputStream,Int,()->Unit)->OpticalPreview={ input,edge,check->readOpticalPreview(input,edge,check) }) {
    private val directory=File(cacheRoot,"optical-proxies-v1")
    private val safeId=Regex("[A-Za-z0-9_-]{1,180}")
    @Volatile private var generation=0L
    init { require(budget>=0) }
    fun get(id: String,path: String,edge: Int,check: ()->Unit={}): OpticalPreview {
        require(edge in 1..2048)
        check()
        val root=mediaRoot.canonicalFile;val source=File(root,path).canonicalFile
        require(source.path.startsWith(root.path+File.separator) && source.isFile) { "Source film is missing" }
        // Legacy library IDs are not restricted to filename-safe characters. Caching is optional.
        if(!safeId.matches(id)) return source.inputStream().buffered(65536).use { readSource(it,edge,check) }
        val length=source.length();val modified=source.lastModified();val ticket=generation
        val cached=File(directory,"$id-$edge.ffpv")
        if(cached.isFile) {
            val hit=runCatching {
                require(cached.length() in 1..budget)
                cached.inputStream().buffered(65536).use { stream ->
                    val input=DataInputStream(stream)
                    require(input.readInt()==0x46465031 && input.readUTF()==path && input.readLong()==length && input.readLong()==modified && input.readInt()==edge)
                    val w=input.readInt();val h=input.readInt();val stride=input.readInt();val sampler=input.readUTF()
                    require(w>0 && h>0 && stride>0 && sampler=="box_mean_optical_preview_v3")
                    // This is already a sampled proxy. Decode its arrays once, without running
                    // the full-source area sampler again; retain strict bounded archive checks.
                    readCachedOpticalPreview(input,w,h,stride,edge,check)
                }
            }.getOrNull()
            check()
            if(hit!=null) { cached.setLastModified(System.currentTimeMillis());return hit }
        }
        val value=source.inputStream().buffered(65536).use { readSource(it,edge,check) }
        check()
        if(generation==ticket && source.length()==length && source.lastModified()==modified) runCatching {
            if(!directory.isDirectory) require(directory.mkdirs() || directory.isDirectory)
            val pending=File.createTempFile(".proxy-",".tmp",directory)
            try {
                pending.outputStream().buffered(65536).use { stream ->
                    val output=DataOutputStream(stream)
                    output.writeInt(0x46465031);output.writeUTF(path);output.writeLong(length);output.writeLong(modified);output.writeInt(edge)
                    output.writeInt(value.sourceWidth);output.writeInt(value.sourceHeight);output.writeInt(value.stride);output.writeUTF(value.sampler)
                    val entries=NpzMediumArchive().opticalEntries(value.archive)
                    NpzFile.writeMembers(entries.mapValues { (_,v)-> { out: OutputStream->NpyCodec.write(v,out) } },output,Deflater.BEST_SPEED)
                }
                check()
                if(generation==ticket && pending.length()<=budget && source.isFile && source.length()==length && source.lastModified()==modified) {
                    Files.move(pending.toPath(),cached.toPath(),StandardCopyOption.ATOMIC_MOVE,StandardCopyOption.REPLACE_EXISTING)
                    trim()
                }
            } finally { pending.delete() }
        }
        check();return value
    }
    private fun trim() {
        val files=directory.listFiles().orEmpty().filter { it.isFile && it.name.endsWith(".ffpv") }.sortedByDescending { it.lastModified() }
        var used=0L
        for((i,file) in files.withIndex()) { used+=file.length();if(used>budget || i>=32) file.delete() }
    }
    fun clear() {
        generation++
        directory.listFiles().orEmpty().filter { it.isFile && it.name.endsWith(".ffpv") }.forEach { it.delete() }
    }
    fun dropResult(id: String) {
        if(!safeId.matches(id)) return
        generation++
        directory.listFiles().orEmpty().filter {
            it.isFile && it.name.startsWith("$id-") && it.name.endsWith(".ffpv") &&
                it.name.removePrefix("$id-").removeSuffix(".ffpv").toIntOrNull()!=null
        }.forEach { it.delete() }
    }
}
