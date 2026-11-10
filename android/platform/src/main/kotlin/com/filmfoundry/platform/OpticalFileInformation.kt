package com.filmfoundry.platform

import com.filmfoundry.interchange.*
import java.io.File
import java.time.Instant
import java.util.zip.ZipFile

data class OpticalFileInformation(val width: Int,val height: Int,val polarity: String,
    val createdAt: String?,val fileTime: String?,val metadata: Map<String,Any?>)

class PrivateOpticalInformation(root: File) {
    private val files=PrivateMediumFiles(root)
    data class Signature(val exists: Boolean,val length: Long,val modified: Long)
    fun signature(path: String)=files.resolve(path).let { Signature(it.isFile,it.length(),it.lastModified()) }
    fun read(path: String)=OpticalFileInformationReader.read(files.resolve(path))
}

/** Read-only UI facts. ZIP random access avoids decompressing image arrays for a label.
 * This does not replace full material validation when observing or processing the archive.
 */
object OpticalFileInformationReader {
    fun read(file: File): OpticalFileInformation = ZipFile(file).use { zip ->
        val entries=zip.entries().asSequence().take(129).toList();require(entries.size<=128)
        fun member(key: String)=entries.filter { !it.isDirectory && it.name.substringAfterLast('/').substringAfterLast('\\')=="$key.npy" }
            .also { require(it.size==1) { "Missing or duplicate $key" } }.single()
        val image=member(DataKernel.OPTICAL_DENSITY_KEY)
        val header=zip.getInputStream(image).use { NpyCodec.readHeader(it,image.size) }
        require(header.shape.size==3 && header.shape[2]==3 && header.shape.all { it>0 })
        val bytes=Math.multiplyExact(header.shape.fold(1L) { acc,n -> Math.multiplyExact(acc,n.toLong()) },NpyCodec.numericItemBytes(header.descriptor).toLong())
        require(image.size>=header.headerBytes+bytes)
        val metadata=member(DataKernel.DEVELOPED_METADATA_KEY)
        require(metadata.size in 1..8L*1024*1024) { "Metadata too large" }
        val raw=zip.getInputStream(metadata).use { NpyCodec.read(it,metadata.size) }
        val record=NpzMediumArchive().developedHeader(raw)
        val recorded=(record["created_at"] as? String)?.takeIf { runCatching { Instant.parse(it) }.isSuccess }
        OpticalFileInformation(header.shape[1],header.shape[0],record["image_polarity"].toString().trim().lowercase(),recorded,
            file.lastModified().takeIf { it>0 }?.let { Instant.ofEpochMilli(it).toString() },record)
    }
}
