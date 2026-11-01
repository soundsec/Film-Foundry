package com.filmfoundry.platform

import android.content.Context
import java.io.File
import java.io.InputStream
import java.io.OutputStream

class AndroidStorageAdapter(
    private val context: Context,
) : StorageAdapter {
    private val files = PrivateMediumFiles(context.filesDir)
    override fun privateDir(): String = context.filesDir.absolutePath

    override fun writePrivate(relative: String, bytes: ByteArray) {
        writePrivate(relative) { it.write(bytes) }
    }

    override fun readPrivate(relative: String): ByteArray {
        return readPrivate(relative) { it.readBytes() }
    }

    override fun <T> readPrivate(relative: String, reader: (InputStream) -> T): T = files.read(relative, reader)

    override fun writePrivate(relative: String, writer: (OutputStream) -> Unit) = files.write(relative, writer)
    override fun writePrivate(relative: String, beforeCommit: () -> Unit, writer: (OutputStream) -> Unit) = files.write(relative, beforeCommit, writer)
    override fun rowScratchRoot(): File = context.cacheDir
    override fun samePrivateLocation(a: String, b: String): Boolean = files.resolve(a) == files.resolve(b)
    override fun privateVersion(relative: String): com.filmfoundry.workflow.RowMediumVersion? {
        val file=privateFile(relative)
        if(!file.isFile) return null
        val attributes=java.nio.file.Files.readAttributes(file.toPath(),java.nio.file.attribute.BasicFileAttributes::class.java)
        return com.filmfoundry.workflow.RowMediumVersion(file.canonicalPath,attributes.size(),attributes.lastModifiedTime().toMillis(),attributes.fileKey()?.toString())
    }

    override fun existsPrivate(relative: String): Boolean {
        return privateFile(relative).isFile
    }

    override fun deletePrivate(relative: String) {
        privateFile(relative).delete()
    }

    override fun exportUserVisible(privatePath: String, displayName: String) {
        readPrivate(privatePath) { input -> writePrivate("export/$displayName") { input.copyTo(it, 64 * 1024) } }
    }

    override fun publishGallery(displayName: String, mimeType: String, bytes: ByteArray) {
        val insert = GalleryPublisher.prepare(displayName, mimeType, bytes)
        MediaStoreGallery.commit(context, insert, bytes)
    }

    override fun publishGalleryStream(displayName: String,mimeType: String,check: ()->Unit,writer: (OutputStream)->Unit) {
        MediaStoreGallery.commitStream(context,GalleryPublisher.prepareStream(displayName,mimeType),check,writer)
    }

    private fun privateFile(relative: String): File {
        return files.resolve(relative)
    }
}
