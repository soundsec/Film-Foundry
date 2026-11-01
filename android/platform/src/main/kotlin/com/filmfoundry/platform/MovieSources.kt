package com.filmfoundry.platform

import java.io.File
import java.io.FileOutputStream
import java.io.InputStream
import java.nio.file.Files
import java.nio.file.StandardCopyOption
import java.util.UUID
import org.json.JSONObject

/** Source container catalog, deliberately separate from still rolls and NPZ media.
 * A committed source is captured_source, never an exposed or developed medium.
 * Draft directories are not listed. A finalization/probe failure never enters the catalog.
 */
data class MovieInfo(val width: Int,val height: Int,val durationUs: Long,val rotation: Int,
    val videoMime: String,val audioTracks: Int,val hdr: Boolean)
data class MovieSource(val id: String,val origin: String,val info: MovieInfo,val file: File,val displayName: String)

class MovieSources(private val root: File,private val probe: (File)->MovieInfo) {
    companion object { const val MODEL="phone_movie_source_v1" }
    private val directory get()=File(root,"movie_sources")

    class Draft internal constructor(internal val folder: File) {
        val file get()=File(folder,"source.media")
        fun abandon() {
            // folder was created by this store; never a user/computed external path.
            folder.listFiles()?.forEach { if (it.isFile) it.delete() }
            folder.delete()
        }
    }

    @Synchronized fun draft(): Draft {
        if (!directory.isDirectory && !directory.mkdirs()) throw CaptureRejected("Cannot create movie source library.")
        val folder=File(directory,".pending-${UUID.randomUUID()}")
        if (!folder.mkdir()) throw CaptureRejected("Cannot create movie draft.")
        return Draft(folder)
    }

    fun import(input: InputStream,displayName: String="source.video",checkCancel: ()->Unit={}): MovieSource {
        val draft=draft()
        try {
            FileOutputStream(draft.file).use { output ->
                val buffer=ByteArray(64*1024)
                var total=0L
                while(true) {
                    checkCancel()
                    val n=input.read(buffer)
                    if(n < 0) break
                    if(n == 0) continue
                    total+=n
                    if(total > 8L*1024*1024*1024) throw CaptureRejected("Movie source exceeds the explicit 8 GiB import limit.")
                    output.write(buffer,0,n)
                }
                output.fd.sync()
            }
            checkCancel()
            return commit(draft,"import",displayName,checkCancel)
        } finally { draft.abandon() }
    }

    @Synchronized fun commit(draft: Draft,origin: String,displayName: String="recording.mp4",checkCancel: ()->Unit={}): MovieSource {
        require(origin in setOf("import","camera"))
        require(draft.folder.parentFile?.canonicalFile == directory.canonicalFile && draft.folder.name.startsWith(".pending-"))
        if (!draft.file.isFile || draft.file.length() == 0L) throw CaptureRejected("Movie recording has no finalized source.")
        val info=probe(draft.file)
        if(info.width <= 0 || info.height <= 0 || info.durationUs <= 0 || info.rotation !in setOf(0,90,180,270) ||
            !info.videoMime.startsWith("video/") || info.audioTracks < 0) throw CaptureRejected("Movie source metadata is invalid.")
        val id=UUID.randomUUID().toString()
        val name=displayName.substringAfterLast('/').substringAfterLast('\\').take(256).ifBlank { "source.video" }
        val metadata=JSONObject().put("model",MODEL).put("stage","captured_source").put("id",id).put("origin",origin)
            .put("width",info.width).put("height",info.height).put("duration_us",info.durationUs).put("rotation",info.rotation)
            .put("video_mime",info.videoMime).put("audio_tracks",info.audioTracks).put("hdr",info.hdr).put("display_name",name)
        FileOutputStream(File(draft.folder,"source.json")).use { out -> out.write(metadata.toString().toByteArray(Charsets.UTF_8));out.fd.sync() }
        checkCancel()
        val target=File(directory,id)
        Files.move(draft.folder.toPath(),target.toPath(),StandardCopyOption.ATOMIC_MOVE)
        return MovieSource(id,origin,info,File(target,"source.media"),name)
    }

    @Synchronized fun list(): List<MovieSource> = directory.listFiles().orEmpty()
        .filter { it.isDirectory && !it.name.startsWith(".") }.sortedBy { it.name }.map { folder ->
            val obj=JSONObject(File(folder,"source.json").readText())
            if(obj.getString("model") != MODEL || obj.getString("stage") != "captured_source" || obj.getString("id") != folder.name)
                throw CaptureRejected("Invalid movie source catalog entry.")
            val media=File(folder,"source.media")
            if(!media.isFile || media.length() == 0L) throw CaptureRejected("Movie source file is missing.")
            MovieSource(folder.name,obj.getString("origin"),MovieInfo(obj.getInt("width"),obj.getInt("height"),obj.getLong("duration_us"),
                obj.getInt("rotation"),obj.getString("video_mime"),obj.getInt("audio_tracks"),obj.getBoolean("hdr")),media,obj.getString("display_name"))
        }
}
