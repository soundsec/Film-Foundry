package com.filmfoundry.platform

import java.io.File
import java.io.FileOutputStream
import java.io.InputStream
import java.io.OutputStream
import java.nio.file.Files
import java.nio.file.StandardCopyOption

/** File transport only. A failed serializer leaves the previous medium intact. */
internal class PrivateMediumFiles(private val root: File) {
    fun resolve(relative: String): File {
        val directory = root.canonicalFile
        val target = File(directory, relative).canonicalFile
        if (target == directory || !target.path.startsWith(directory.path + File.separator))
            throw CaptureRejected("private path escapes filesDir")
        return target
    }

    fun <T> read(relative: String, reader: (InputStream) -> T): T {
        val file = resolve(relative)
        if (!file.isFile) throw CaptureRejected("private file not found: $relative")
        return file.inputStream().buffered(64 * 1024).use(reader)
    }

    fun write(relative: String, writer: (OutputStream) -> Unit) = write(relative, {}, writer)

    fun write(relative: String, beforeCommit: () -> Unit, writer: (OutputStream) -> Unit) {
        val target = resolve(relative)
        val parent = target.parentFile!!
        if (!parent.isDirectory && !parent.mkdirs() && !parent.isDirectory)
            throw CaptureRejected("cannot create private medium directory")
        val pending = File.createTempFile(".film-foundry-", ".pending", parent)
        try {
            FileOutputStream(pending).use { file ->
                val buffered = file.buffered(64 * 1024)
                writer(buffered)
                buffered.flush()
                file.fd.sync()
            }
            // Same-directory rename; if atomic replacement is unsupported, fail without committing.
            beforeCommit()
            Files.move(pending.toPath(), target.toPath(), StandardCopyOption.ATOMIC_MOVE, StandardCopyOption.REPLACE_EXISTING)
        } finally {
            pending.delete()
        }
    }
}
