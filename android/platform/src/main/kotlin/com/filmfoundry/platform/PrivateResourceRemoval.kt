package com.filmfoundry.platform

import org.json.JSONArray
import org.json.JSONObject
import java.io.File
import java.io.IOException
import java.nio.file.*
import java.nio.file.attribute.BasicFileAttributes

/** One app-private deletion transaction. Only explicitly selected paths are ever staged.
 * Recovery consults durable catalog ownership, not age or a whole storage-directory scan.
 */
class PrivateResourceRemoval(root: File,private val referenced: (String)->Boolean) {
    // 72 half frames: one capture bundle + one latent each, plus the roll's reference pair.
    private val maxPaths=146
    private val root=root.canonicalFile
    private val registry=File(this.root,".resource-removal")
    val needsRecovery get()=Files.exists(registry.toPath(),LinkOption.NOFOLLOW_LINKS)
    private fun resolve(path: String): File {
        require(path.isNotBlank() && path.split('/').none { it.isBlank() || it in listOf(".","..") } && '\\' !in path && ':' !in path)
        require(!path.startsWith(".resource-removal"))
        val file=File(root,path)
        require(file.canonicalFile==file.absoluteFile && file.path.startsWith(root.path+File.separator)) { "resource path escapes private storage or follows a link" }
        return file
    }
    fun bytes(paths: List<String>): Long = paths.distinct().sumOf { size(resolve(it)) }
    private fun size(file: File): Long {
        if(!Files.exists(file.toPath(),LinkOption.NOFOLLOW_LINKS)) return 0
        var total=0L
        Files.walkFileTree(file.toPath(),object: SimpleFileVisitor<Path>() {
            override fun preVisitDirectory(path: Path,a: BasicFileAttributes): FileVisitResult = if(a.isOther || a.isSymbolicLink) FileVisitResult.SKIP_SUBTREE else FileVisitResult.CONTINUE
            override fun visitFile(path: Path,a: BasicFileAttributes): FileVisitResult { if(a.isRegularFile) total=Math.addExact(total,a.size());return FileVisitResult.CONTINUE }
        });return total
    }
    private fun erase(file: File) {
        if(!Files.exists(file.toPath(),LinkOption.NOFOLLOW_LINKS)) return
        Files.walkFileTree(file.toPath(),object: SimpleFileVisitor<Path>() {
            override fun preVisitDirectory(path: Path,a: BasicFileAttributes): FileVisitResult {
                if(a.isOther || a.isSymbolicLink) { Files.delete(path);return FileVisitResult.SKIP_SUBTREE };return FileVisitResult.CONTINUE
            }
            override fun visitFile(path: Path,a: BasicFileAttributes): FileVisitResult { Files.delete(path);return FileVisitResult.CONTINUE }
            override fun postVisitDirectory(path: Path,error: IOException?): FileVisitResult { if(error!=null) throw error;Files.delete(path);return FileVisitResult.CONTINUE }
        })
    }
    /** Referenced staged resources are restored; unreferenced ones finish removal. */
    @Synchronized fun recover() {
        if(!needsRecovery) return
        require(registry.canonicalFile==registry.absoluteFile && registry.isDirectory)
        val plan=File(registry,"plan.json")
        // A crash before publishing the plan cannot have moved any resources.
        if(!plan.exists()) { require(registry.list().orEmpty().isEmpty());Files.delete(registry.toPath());return }
        require(plan.isFile && plan.canonicalFile==plan.absoluteFile && plan.length()<=1024*1024)
        val record=JSONObject(plan.readText());require(record.getString("model")=="private_resource_removal_v1")
        val array=record.getJSONArray("paths");require(array.length() in 1..maxPaths)
        val paths=(0 until array.length()).map { array.getString(it).also(::resolve) }
        require(paths.distinct().size==paths.size)
        for((i,path) in paths.withIndex()) {
            val staged=File(registry,i.toString());val target=resolve(path)
            require(staged.canonicalFile==staged.absoluteFile)
            if(!Files.exists(staged.toPath(),LinkOption.NOFOLLOW_LINKS)) continue
            if(referenced(path)) {
                require(!Files.exists(target.toPath(),LinkOption.NOFOLLOW_LINKS)) { "resource recovery target already exists" }
                Files.move(staged.toPath(),target.toPath(),StandardCopyOption.ATOMIC_MOVE)
            } else erase(staged)
        }
        Files.delete(plan.toPath());Files.delete(registry.toPath())
    }
    /** Returns false only if removal committed but cleanup needs retry. Never undo a committed index. */
    @Synchronized fun remove(paths: List<String>,commit: ()->Unit): Boolean {
        recover()
        val selected=paths.distinct();require(selected.size in 1..maxPaths)
        val files=selected.map(::resolve)
        require(selected.all(referenced)) { "only catalog-owned resources can be removed" }
        require(files.none { a->files.any { b->a!=b && a.toPath().startsWith(b.toPath()) } })
        require(registry.mkdir())
        val bytes=JSONObject().put("model","private_resource_removal_v1").put("paths",JSONArray(selected)).toString().toByteArray()
        // Atomic durable plan precedes the first rename.
        try { PrivateMediumFiles(root).write(".resource-removal/plan.json") { it.write(bytes) } }
        catch(error: Exception) { registry.listFiles().orEmpty().forEach { it.delete() };registry.delete();throw error }
        try {
            files.forEachIndexed { i,file -> if(Files.exists(file.toPath(),LinkOption.NOFOLLOW_LINKS)) Files.move(file.toPath(),File(registry,i.toString()).toPath(),StandardCopyOption.ATOMIC_MOVE) }
            commit()
        } catch(error: Exception) {
            try { recover() } catch(recovery: Exception) { error.addSuppressed(recovery) }
            throw error
        }
        return try { recover();true } catch(_: Exception) { false }
    }
}
