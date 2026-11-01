package com.filmfoundry.workflow

import java.io.File
import java.io.IOException
import java.nio.channels.FileChannel
import java.nio.channels.FileLock
import java.nio.channels.OverlappingFileLockException
import java.nio.file.*
import java.nio.file.attribute.BasicFileAttributes
import java.util.UUID

/** App-private leases; never written into master.json or a medium NPZ. */
class MaterialMasterWorkspace(private val freeBytes: (File)->Long={ it.usableSpace }) {
    companion object {
        const val REGISTRY=".ff-material-jobs"
        private val owner="film_foundry_material_job_v1\n".toByteArray(Charsets.US_ASCII)
        private val jobName=Regex("job-[0-9a-f]{8}(-[0-9a-f]{4}){3}-[0-9a-f]{12}")
        private val mutex=Any()
        private const val RESERVE=16L*1024*1024
        private fun plain(path: Path,directory: Boolean): Boolean {
            if(!Files.exists(path,LinkOption.NOFOLLOW_LINKS)) return false
            val attributes=Files.readAttributes(path,BasicFileAttributes::class.java,LinkOption.NOFOLLOW_LINKS)
            if(attributes.isSymbolicLink || attributes.isOther) return false
            return if(directory) attributes.isDirectory else attributes.isRegularFile
        }
        private fun tryLock(channel: FileChannel): FileLock? = try {
            channel.tryLock(0,1,false)
        } catch(_: OverlappingFileLockException) { null }
        private fun remove(job: File) {
            // walkFileTree without FOLLOW_LINKS deletes links themselves only.
            Files.walkFileTree(job.toPath(),object: SimpleFileVisitor<Path>() {
                override fun preVisitDirectory(path: Path,attributes: BasicFileAttributes): FileVisitResult {
                    // Windows junctions can report both isDirectory and isOther.
                    if(attributes.isSymbolicLink || attributes.isOther) {
                        Files.delete(path);return FileVisitResult.SKIP_SUBTREE
                    }
                    return FileVisitResult.CONTINUE
                }
                override fun visitFile(path: Path,attributes: BasicFileAttributes): FileVisitResult {
                    Files.delete(path);return FileVisitResult.CONTINUE
                }
                override fun postVisitDirectory(path: Path,error: IOException?): FileVisitResult {
                    if(error != null) throw error
                    Files.delete(path);return FileVisitResult.CONTINUE
                }
            })
        }
    }

    fun checkSpace(parent: File) {
        if(freeBytes(parent) < RESERVE) throw IOException("Material master stopped: less than 16 MiB free space")
    }

    private fun <T> guard(parent: File,control: WorkflowControl,block: (File)->T): T = synchronized(mutex) {
        val registry=File(parent,REGISTRY)
        registry.mkdir()
        require(plain(registry.toPath(),true)) { "Invalid material job registry" }
        val path=File(registry,"guard.lock").toPath()
        if(Files.exists(path,LinkOption.NOFOLLOW_LINKS)) require(plain(path,false))
        // Stable inode: never remove this guard, even with no jobs left.
        FileChannel.open(path,StandardOpenOption.CREATE,StandardOpenOption.WRITE,LinkOption.NOFOLLOW_LINKS).use { channel ->
            var lock: FileLock?=null
            while(lock == null) { control.checkCancel();lock=tryLock(channel);if(lock == null) Thread.sleep(25) }
            lock.use { control.checkCancel();block(registry) }
        }
    }

    private fun recoverLocked(registry: File): Int {
        var count=0
        for(job in registry.listFiles().orEmpty()) {
            if(!jobName.matches(job.name) || !plain(job.toPath(),true)) continue
            val marker=File(job,"owner").toPath();val lease=File(job,"lease.lock").toPath()
            if(!plain(marker,false) || !plain(lease,false) || Files.size(marker) != owner.size.toLong()) continue
            if(!Files.readAllBytes(marker).contentEquals(owner)) continue
            val idle=FileChannel.open(lease,StandardOpenOption.WRITE,LinkOption.NOFOLLOW_LINKS).use { channel ->
                val lock=tryLock(channel) ?: return@use false
                lock.release();true
            }
            if(idle) { remove(job);count++ }
        }
        return count
    }

    fun recover(parent: File,control: WorkflowControl=WorkflowControl()): Int {
        val root=parent.canonicalFile
        if(!root.isDirectory) return 0
        return guard(root,control) { recoverLocked(it) }
    }

    inner class Job internal constructor(private val parent: File,private val directory: File,
        private val channel: FileChannel,private val lease: FileLock): AutoCloseable {
        val payload=File(directory,"payload")
        private var closed=false
        override fun close() {
            guard(parent,WorkflowControl()) {
                if(!closed) {
                    closed=true
                    lease.release();channel.close()
                    require(directory.parentFile == File(parent,REGISTRY) && jobName.matches(directory.name) && plain(directory.toPath(),true))
                    remove(directory)
                }
            }
        }
    }

    fun open(parent: File,control: WorkflowControl): Job {
        val root=parent.canonicalFile
        check(root.isDirectory || root.mkdirs()) { "Cannot create master output parent" }
        return guard(root,control) { registry ->
            recoverLocked(registry)
            checkSpace(root)
            val directory=File(registry,"job-${UUID.randomUUID()}")
            check(directory.mkdir())
            var channel: FileChannel?=null
            try {
                channel=FileChannel.open(File(directory,"lease.lock").toPath(),StandardOpenOption.CREATE_NEW,StandardOpenOption.WRITE)
                val lease=tryLock(channel) ?: error("Cannot acquire new material job lease")
                FileChannel.open(File(directory,"owner").toPath(),StandardOpenOption.CREATE_NEW,StandardOpenOption.WRITE).use {
                    val bytes=java.nio.ByteBuffer.wrap(owner)
                    while(bytes.hasRemaining()) it.write(bytes)
                    it.force(true)
                }
                check(File(directory,"payload").mkdir())
                Job(root,directory,channel,lease)
            } catch(error: Throwable) { channel?.close();remove(directory);throw error }
        }
    }
}
