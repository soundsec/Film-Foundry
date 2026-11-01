package com.filmfoundry.app.session

import com.filmfoundry.platform.StorageAdapter
import java.io.File

/**
 * Private light-table tiles for the gallery. These files are not negatives,
 * scans, or anything a later observation may read back as a medium.
 */
object NegativeDisplayStore {
    const val DIRECTORY="display-cache"
    fun relative(resultId: String): String {
        val safe=resultId.map { if(it.isLetterOrDigit() || it=='-' || it=='_') it else '_' }.joinToString("")
        require(safe.isNotEmpty() && safe.length<=180) { "display cache id" }
        // Existing v2 thumbnails already use area averaging and remain valid display tiles.
        return "$DIRECTORY/negative-$safe-light192-v2.png"
    }
    fun delete(storage: StorageAdapter,resultId: String) {
        val path=runCatching { relative(resultId) }.getOrNull() ?: return
        for(version in 1..3) { val candidate=path.removeSuffix("-v2.png")+"-v$version.png";if(storage.existsPrivate(candidate)) storage.deletePrivate(candidate) }
        runCatching { NegativeViewCache(storage).delete(resultId) }
    }
    fun clear(storage: StorageAdapter) {
        val dir=File(storage.privateDir(),DIRECTORY)
        if(!dir.isDirectory) return
        dir.listFiles()?.forEach { file ->
            if(file.isFile && file.name.startsWith("negative-") && (1..3).any { file.name.endsWith("-light192-v$it.png") }) file.delete()
        }
    }
}
