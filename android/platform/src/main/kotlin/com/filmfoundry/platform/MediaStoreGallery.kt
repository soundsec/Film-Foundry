package com.filmfoundry.platform

import android.content.ContentValues
import android.content.Context
import android.media.MediaScannerConnection
import android.net.Uri
import android.os.Build
import android.os.Environment
import android.provider.MediaStore
import java.io.File
import java.io.OutputStream
import java.nio.file.Files
import java.nio.file.StandardCopyOption

/**
 * Commits an already-validated scanner observation into the system gallery.
 * Callers must go through [GalleryPublisher.prepare]; NPZ / FFC2 / preview
 * never reach this object.
 */
object MediaStoreGallery {
    fun commit(context: Context, insert: MediaStoreInsert, bytes: ByteArray): Uri {
        GalleryPublisher.prepare(insert.displayName, insert.mimeType, bytes)
        return commitStream(context,insert,{}, { it.write(bytes) })
    }

    fun commitStream(context: Context,insert: MediaStoreInsert,check: ()->Unit={},writer: (OutputStream)->Unit): Uri {
        GalleryPublisher.prepareStream(insert.displayName,insert.mimeType);check()
        return if (Build.VERSION.SDK_INT >= 29) {
            insertPending(context, insert, check, writer)
        } else {
            insertLegacy(context, insert, check, writer)
        }
    }

    private fun writeChecked(output: OutputStream,check: ()->Unit,writer: (OutputStream)->Unit) {
        var size=0L
        val checked=object: OutputStream() {
            override fun write(value: Int) { check();output.write(value);size++ }
            override fun write(bytes: ByteArray,offset: Int,length: Int) { check();output.write(bytes,offset,length);size+=length }
            override fun flush()=output.flush()
        }
        writer(checked);checked.flush();check()
        if(size==0L) throw CaptureRejected("Projection output is empty")
    }

    private fun insertPending(context: Context, insert: MediaStoreInsert,check: ()->Unit,writer: (OutputStream)->Unit): Uri {
        val values = ContentValues().apply {
            put(MediaStore.Images.Media.DISPLAY_NAME, insert.displayName)
            put(MediaStore.Images.Media.MIME_TYPE, insert.mimeType)
            put(MediaStore.Images.Media.RELATIVE_PATH, insert.relativePath.trimEnd('/'))
            put(MediaStore.Images.Media.IS_PENDING, 1)
        }
        val resolver = context.contentResolver
        val uri = resolver.insert(MediaStore.Images.Media.EXTERNAL_CONTENT_URI, values)
            ?: throw CaptureNotReady("MediaStore insert failed.")
        try {
            resolver.openOutputStream(uri)?.use { stream -> writeChecked(stream,check,writer) }
                ?: throw CaptureNotReady("MediaStore openOutputStream failed.")
            val done = ContentValues().apply {
                put(MediaStore.Images.Media.IS_PENDING, 0)
            }
            check()
            if(resolver.update(uri, done, null, null)!=1) throw CaptureNotReady("MediaStore publication failed.")
            return uri
        } catch (error: Throwable) {
            resolver.delete(uri, null, null)
            throw error
        }
    }

    private fun insertLegacy(context: Context, insert: MediaStoreInsert,check: ()->Unit,writer: (OutputStream)->Unit): Uri {
        val directory = File(
            Environment.getExternalStoragePublicDirectory(Environment.DIRECTORY_PICTURES),
            "FilmFoundry",
        )
        if (!directory.exists() && !directory.mkdirs()) {
            throw CaptureNotReady("could not create Pictures/FilmFoundry")
        }
        val file = File(directory, insert.displayName)
        if(file.exists()) throw CaptureRejected("Projection already exists; existing file retained")
        val pending=File.createTempFile(".film-projection-",".pending",directory)
        try {
            java.io.FileOutputStream(pending).use { output ->
                val buffered=output.buffered(64*1024);writeChecked(buffered,check,writer);buffered.flush();output.fd.sync()
            }
            check()
            Files.move(pending.toPath(),file.toPath(),StandardCopyOption.ATOMIC_MOVE)
        } finally { pending.delete() }
        MediaScannerConnection.scanFile(
            context,
            arrayOf(file.absolutePath),
            arrayOf(insert.mimeType),
            null,
        )
        return Uri.fromFile(file)
    }
}
