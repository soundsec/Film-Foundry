package com.filmfoundry.interchange

import org.json.JSONArray
import org.json.JSONObject

data class DevelopmentTaskFrame(val index: Int,val frameId: String,val sourcePath: String,val capturePath: String?,
    val phase: String="pending",val formationMs: Long=0,val developmentMs: Long=0)
data class DevelopmentTask(val id: String,val rollId: String,val recipe: Map<String,Any?>,
    val frames: List<DevelopmentTaskFrame>,val status: String="ready",val error: String?=null,
    val profile: String=DataKernel.PHONE_REDUCED_PROFILE_ID,val revision: String=DevelopmentTaskCodec.KIND)

/** Application task journal; neither a material archive nor a new medium schema. */
object DevelopmentTaskCodec {
    const val KIND="phone_roll_development_task_v1"
    const val PROVENANCE_KEY="development_task"
    val STATES=setOf("ready","running","paused","failed","completed","abandoned")
    val PHASES=setOf("pending","forming","developing","registered")
    fun encode(task: DevelopmentTask): ByteArray {
        validate(task)
        return RecipeShareCodec.canonicalJson(mapOf("kind" to KIND,"id" to task.id,"roll_id" to task.rollId,
            "recipe" to task.recipe,"status" to task.status,"error" to task.error,"profile" to task.profile,"revision" to task.revision,
            "frames" to task.frames.map { f -> mapOf("index" to f.index,"frame_id" to f.frameId,
                "source_path" to f.sourcePath,"capture_path" to f.capturePath,"phase" to f.phase,
                "formation_ms" to f.formationMs,"development_ms" to f.developmentMs) })).toByteArray(Charsets.UTF_8)
            .also { require(it.size <= 1024*1024) { "development task too large" } }
    }
    fun decode(bytes: ByteArray): DevelopmentTask {
        require(bytes.size <= 1024*1024)
        val raw=objectMap(json(JSONObject(bytes.toString(Charsets.UTF_8))))
        require(raw.keys == setOf("kind","id","roll_id","recipe","status","error","profile","revision","frames") && raw["kind"] == KIND)
        val frames=(raw["frames"] as List<*>).map { value ->
            val f=objectMap(value)
            require(f.keys == setOf("index","frame_id","source_path","capture_path","phase","formation_ms","development_ms"))
            DevelopmentTaskFrame(integer(f["index"]).also { require(it in 1L..72L) }.toInt(),f["frame_id"] as String,f["source_path"] as String,
                f["capture_path"] as String?,f["phase"] as String,integer(f["formation_ms"]),integer(f["development_ms"]))
        }
        return DevelopmentTask(raw["id"] as String,raw["roll_id"] as String,objectMap(raw["recipe"]),frames,
            raw["status"] as String,raw["error"] as String?,raw["profile"] as String,raw["revision"] as String).also(::validate)
    }
    fun encodeIds(ids: List<String>): ByteArray {
        validateIds(ids);return RecipeShareCodec.canonicalJson(ids).toByteArray(Charsets.UTF_8)
    }
    fun decodeIds(bytes: ByteArray): List<String> {
        require(bytes.size <= 512*1024)
        return (json(JSONArray(bytes.toString(Charsets.UTF_8))) as List<*>).map { it as String }.also(::validateIds)
    }
    fun provenance(task: DevelopmentTask,frame: DevelopmentTaskFrame): Map<String,Any?> = mapOf(
        "model" to KIND,"task_id" to task.id,"roll_id" to task.rollId,"frame_index" to frame.index,
        "frame_id" to frame.frameId,"source_path" to frame.sourcePath,"profile" to task.profile,
        "revision" to task.revision,"recipe" to task.recipe)
    private fun validate(task: DevelopmentTask) {
        require(task.id.matches(Regex("[A-Za-z0-9_-]{1,80}")) && task.rollId.isNotBlank())
        require(task.status in STATES && (task.error?.length ?: 0) <= 1000)
        require(task.profile.isNotBlank() && task.revision.isNotBlank() && task.frames.size in 1..72)
        require(task.frames.map { it.index }.distinct().size == task.frames.size)
        require(task.frames.map { it.index } == task.frames.map { it.index }.sorted())
        for(f in task.frames) {
            require(f.index in 1..72 && f.frameId.isNotBlank() && f.sourcePath.endsWith(DataKernel.LATENT_SUFFIX))
            require(f.capturePath == null || f.capturePath.endsWith(".capture/receipt.json"))
            require(f.phase in PHASES && f.formationMs >= 0 && f.developmentMs >= 0)
        }
        if(task.status == "completed") require(task.frames.all { it.phase == "registered" })
    }
    private fun validateIds(ids: List<String>) {
        require(ids.size <= 4096 && ids.distinct().size == ids.size && ids.all { it.matches(Regex("[A-Za-z0-9_-]{1,80}")) })
    }
    private fun integer(value: Any?): Long {
        val n=value as? Number ?: error("integer required")
        return n.toLong().also { require(it >= 0 && n.toDouble() == it.toDouble()) }
    }
    @Suppress("UNCHECKED_CAST") private fun objectMap(value: Any?)=value as? Map<String,Any?> ?: error("object required")
    private fun json(value: Any?): Any?=when(value) {
        is JSONObject -> value.keys().asSequence().associateWith { json(value.get(it)) }
        is JSONArray -> (0 until value.length()).map { json(value.get(it)) }
        JSONObject.NULL -> null
        else -> value
    }
}
