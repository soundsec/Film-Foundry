package com.filmfoundry.workflow

import com.filmfoundry.interchange.*
import java.io.*
import java.nio.file.Files
import java.nio.file.StandardCopyOption
import java.util.Locale
import java.util.zip.CRC32
import java.util.zip.ZipEntry
import java.util.zip.ZipFile
import java.util.zip.ZipOutputStream

/** Transport only. Imported frame bytes and material/timeline identities stay unchanged. */
class MovieMasterZip(private val storage: MaterialMasterWorkspace=MaterialMasterWorkspace(),
    private val limits: Limits=Limits()) {
    data class Limits(val maxArchiveBytes: Long=64L*1024*1024*1024,val maxExpandedBytes: Long=64L*1024*1024*1024,
        val maxFrameBytes: Long=2L*1024*1024*1024,val maxManifestBytes: Int=16*1024*1024,val maxFrames: Int=100000) {
        init { require(maxArchiveBytes > 0 && maxExpandedBytes > 0 && maxFrameBytes > 0 && maxManifestBytes > 0 && maxFrames > 0) }
    }

    fun import(input: InputStream,output: File,control: WorkflowControl=WorkflowControl()): File {
        val target=output.canonicalFile
        require(!target.exists()) { "Import needs a new master directory" }
        val parent=target.parentFile!!
        storage.open(parent,control).use { job ->
            val pending=job.payload
            val packed=File(pending.parentFile,"source.zip")
            var lastCheck=System.nanoTime()-250_000_000L
            fun check() {
                control.checkCancel()
                val now=System.nanoTime()
                if(now-lastCheck >= 250_000_000L) { storage.checkSpace(parent);lastCheck=now }
            }
            FileOutputStream(packed).use { out -> transfer(input,out,limits.maxArchiveBytes,::check);out.fd.sync() }
            ZipFile(packed).use { zip ->
                val entries=linkedMapOf<String,ZipEntry>()
                val aliases=mutableSetOf<String>()
                var expanded=0L
                val iterator=zip.entries()
                while(iterator.hasMoreElements()) {
                    check()
                    val entry=iterator.nextElement();val name=entry.name
                    require(entries.size.toLong() < limits.maxFrames.toLong()+3) { "Too many master ZIP entries" }
                    require(safeName(name) && aliases.add(name.lowercase(Locale.ROOT))) { "Invalid or duplicate master ZIP path" }
                    require(entry.size >= 0) { "ZIP entry has no size" }
                    expanded=Math.addExact(expanded,entry.size)
                    require(expanded <= limits.maxExpandedBytes) { "Master ZIP expanded data exceeds limit" }
                    entries[name]=entry
                }
                val manifests=entries.keys.filter { it == MovieMasterCodec.MANIFEST ||
                    (it.count { c -> c == '/' } == 1 && it.endsWith("/"+MovieMasterCodec.MANIFEST)) }
                require(manifests.size == 1) { "ZIP needs one root master.json (optionally one enclosing folder)" }
                val manifestName=manifests.single()
                val prefix=manifestName.removeSuffix(MovieMasterCodec.MANIFEST)
                val raw=ByteArrayOutputStream()
                readEntry(zip,entries.getValue(manifestName),raw,limits.maxManifestBytes.toLong(),::check)
                val document=MovieMasterCodec.decode(raw.toByteArray())
                require(document.frames.size <= limits.maxFrames) { "Too many movie frames" }
                val allowed=(document.frames.map { prefix+it.path }+manifestName).toSet()
                val directories=setOf(prefix,prefix+"frames/")-""
                require(entries.keys.all { it in allowed || it in directories }) { "ZIP contains unreferenced files" }
                require(entries.keys.containsAll(allowed)) { "ZIP is missing material frames" }
                require(entries.filterKeys { it in directories }.values.all { it.isDirectory && it.size == 0L })
                check(File(pending,"frames").mkdir())
                val codec=NpzMediumArchive()
                for(frame in document.frames) {
                    check();control.report("movie_import","loading")
                    val file=File(pending,frame.path)
                    FileOutputStream(file).use { out ->
                        readEntry(zip,entries.getValue(prefix+frame.path),out,limits.maxFrameBytes,::check);out.fd.sync()
                    }
                    // ZipFile verifies a complete central directory; the stream inspector
                    // then validates all NPY payloads/CRC without full-frame allocations.
                    val members=linkedMapOf<String,ZipEntry>()
                    ZipFile(file).use { inner ->
                        require(inner.size() in 1..64) { "Invalid material NPZ" }
                        val listing=inner.entries()
                        while(listing.hasMoreElements()) {
                            val member=listing.nextElement()
                            require(!members.containsKey(member.name)) { "Duplicate NPZ member" }
                            members[member.name]=member
                        }
                    }
                    val checked=object: FilterInputStream(file.inputStream()) {
                        override fun read(): Int { check();return super.read() }
                        override fun read(bytes: ByteArray,offset: Int,length: Int): Int { check();return `in`.read(bytes,offset,length) }
                    }
                    val header=checked.use { codec.inspect(it,document.stage == "exposed_latent",::check) { name,size,crc ->
                        val member=members.remove(name) ?: error("NPZ local member disagrees with directory")
                        require(member.size == size && member.crc == crc) { "NPZ directory checksum or size disagrees" }
                    } }
                    require(members.isEmpty()) { "NPZ directory has missing local members" }
                    require(header.width == document.width && header.height == document.height) { "Frame dimensions disagree with master" }
                    val identity=header.metadata["frame_identity"] as? Map<*,*>
                    require(identity != null && identity["roll_id"] == document.rollId &&
                        (identity["frame_index"] as? Number)?.toLong() == frame.index.toLong()) { "Frame identity disagrees with master" }
                    if(document.stage == "developed_medium") {
                        val suffix=if(header.metadata["image_polarity"].toString().trim().lowercase(Locale.ROOT) == "positive") DataKernel.POSITIVE_SUFFIX else DataKernel.NEGATIVE_SUFFIX
                        require(frame.path.endsWith(suffix)) { "Frame suffix disagrees with polarity" }
                    }
                }
                FileOutputStream(File(pending,MovieMasterCodec.MANIFEST)).use { out -> out.write(raw.toByteArray());out.fd.sync() }
            }
            check(packed.delete())
            control.checkCancel();storage.checkSpace(parent)
            require(!target.exists())
            Files.move(pending.toPath(),target.toPath(),StandardCopyOption.ATOMIC_MOVE)
        }
        return target
    }

    fun export(root: File,output: OutputStream,control: WorkflowControl=WorkflowControl()) {
        val document=MovieMasterSession().read(root)
        ZipOutputStream(object: FilterOutputStream(output) {
            override fun write(bytes: ByteArray,offset: Int,length: Int)=out.write(bytes,offset,length)
            override fun close()=flush()
        }).use { zip ->
            zip.setLevel(0)
            for(name in listOf(MovieMasterCodec.MANIFEST)+document.frames.map { it.path }) {
                control.checkCancel();zip.putNextEntry(ZipEntry(name))
                File(root,name).inputStream().use { transfer(it,zip,Long.MAX_VALUE,control::checkCancel) }
                zip.closeEntry()
            }
        }
    }

    private fun readEntry(zip: ZipFile,entry: ZipEntry,output: OutputStream,max: Long,check: ()->Unit) {
        require(!entry.isDirectory && entry.size <= max) { "Master ZIP member exceeds limit" }
        val crc=CRC32()
        val stream=object: FilterInputStream(zip.getInputStream(entry)) {
            override fun read(bytes: ByteArray,offset: Int,length: Int): Int {
                val n=`in`.read(bytes,offset,length);if(n > 0) crc.update(bytes,offset,n);return n
            }
        }
        val count=stream.use { transfer(it,output,max,check) }
        require(count == entry.size && crc.value == entry.crc) { "Master ZIP member is truncated or corrupt" }
    }

    private fun transfer(input: InputStream,output: OutputStream,max: Long,check: ()->Unit): Long {
        val buffer=ByteArray(64*1024);var total=0L
        while(true) {
            check();val n=input.read(buffer);if(n < 0) break
            total=Math.addExact(total,n.toLong());require(total <= max) { "Master ZIP exceeds import limit" }
            output.write(buffer,0,n)
        }
        return total
    }
    private fun safeName(name: String): Boolean {
        if(name.isEmpty() || name.length > 1024 || name.startsWith('/') || name.any { it == '\\' || it == ':' || it.code < 32 }) return false
        return name.removeSuffix("/").split('/').all { it.isNotEmpty() && it != "." && it != ".." }
    }
}
