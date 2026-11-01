package com.filmfoundry.interchange

import java.io.File
import java.nio.ByteBuffer
import java.nio.channels.FileChannel
import java.nio.channels.FileLock
import java.nio.channels.OverlappingFileLockException
import java.nio.file.*
import java.nio.file.attribute.BasicFileAttributes
import java.util.UUID

/** Disposable computation files only. Source media and unmarked legacy caches are never reclaimed. */
object OwnedScratch {
    const val MARKER="film_foundry_owned_scratch_v1"
    private const val PREFIX="film-owned-"
    data class Recovery(val reclaimed: Int,val busy: Int,val failed: Int)
    class Lease internal constructor(private val root: File,private val channel: FileChannel,private val lock: FileLock): AutoCloseable {
        val directory=File(root,"data")
        private var closed=false
        override fun close() {
            if(closed) return
            closed=true
            var cleaned=false
            try { removeData(root);cleaned=true } catch(_: java.io.IOException) { /* Retain the marker for the next recovery. */ }
            finally { try { lock.release() } finally { channel.close() } }
            if(cleaned) finish(root)
        }
    }
    fun create(parent: File): Lease {
        require(parent.isDirectory || parent.mkdirs()) { "cannot create scratch parent" }
        val root=File(parent.canonicalFile,PREFIX+UUID.randomUUID()).also { require(it.mkdir()) }
        val channel=FileChannel.open(File(root,"owner.lock").toPath(),StandardOpenOption.CREATE_NEW,StandardOpenOption.READ,StandardOpenOption.WRITE)
        var lock: FileLock?=null
        try {
            lock=channel.lock();val bytes=ByteBuffer.wrap(MARKER.toByteArray(Charsets.US_ASCII))
            while(bytes.hasRemaining()) channel.write(bytes)
            require(File(root,"data").mkdir())
            return Lease(root,channel,lock)
        } catch(error: Throwable) {
            lock?.release();channel.close();File(root,"owner.lock").delete();root.delete();throw error
        }
    }
    /** Run off the UI thread. A live process's lock is authoritative; age is never used. */
    fun recover(parent: File): Recovery {
        if(!parent.isDirectory) return Recovery(0,0,0)
        val canonical=parent.canonicalFile;var reclaimed=0;var busy=0;var failed=0
        for(root in canonical.listFiles().orEmpty()) {
            val id=root.name.removePrefix(PREFIX)
            if(!root.name.startsWith(PREFIX) || runCatching { UUID.fromString(id).toString()==id }.getOrDefault(false)!=true) continue
            if(!Files.isDirectory(root.toPath(),LinkOption.NOFOLLOW_LINKS) || root.canonicalFile.parentFile!=canonical) continue
            val owner=File(root,"owner.lock")
            if(!owner.isFile || Files.isSymbolicLink(owner.toPath()) || owner.length()!=MARKER.length.toLong()) continue
            try {
                var cleaned=false
                FileChannel.open(owner.toPath(),StandardOpenOption.READ,StandardOpenOption.WRITE).use channelUse@ { channel ->
                    val lock=try { channel.tryLock() } catch(_: OverlappingFileLockException) { null }
                    if(lock==null) { busy++;return@channelUse }
                    lock.use lockUse@ {
                        val bytes=ByteBuffer.allocate(MARKER.length);channel.position(0)
                        while(bytes.hasRemaining()) if(channel.read(bytes)<0) break
                        if(bytes.position()!=MARKER.length || String(bytes.array(),Charsets.US_ASCII)!=MARKER) return@lockUse
                        removeData(root);cleaned=true
                    }
                }
                if(cleaned) { finish(root);if(root.exists()) failed++ else reclaimed++ }
            } catch(_: java.io.IOException) { failed++ }
        }
        return Recovery(reclaimed,busy,failed)
    }
    private fun removeData(root: File) {
        val path=File(root,"data").toPath()
        if(!Files.exists(path,LinkOption.NOFOLLOW_LINKS)) return
        // walkFileTree does not follow symbolic links; only this exact owned data subtree is removed.
        Files.walkFileTree(path,object: SimpleFileVisitor<Path>() {
            override fun visitFile(file: Path,attrs: BasicFileAttributes): FileVisitResult { Files.delete(file);return FileVisitResult.CONTINUE }
            override fun postVisitDirectory(dir: Path,error: java.io.IOException?): FileVisitResult { if(error!=null) throw error;Files.delete(dir);return FileVisitResult.CONTINUE }
        })
    }
    private fun finish(root: File) { File(root,"owner.lock").delete();root.delete() }
}
