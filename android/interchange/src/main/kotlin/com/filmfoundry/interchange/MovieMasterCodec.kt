package com.filmfoundry.interchange

import java.math.BigInteger
import org.json.JSONObject
import org.json.JSONArray

data class MovieMaterialFrame(val index: Int,val path: String,val pts: Long,
    val timeBaseNum: Long,val timeBaseDen: Long,val duration: Long?)
data class MovieMaterialMaster(val masterId: String,val rollId: String,val stage: String,
    val width: Int,val height: Int,val seed: Long,val recipe: Map<String,Any?>,
    val frames: List<MovieMaterialFrame>,val sourceMasterId: String?=null,val source: Map<String,Any?>?=null)

/** Shared application timeline. Each referenced frame remains the existing NPZ schema. */
object MovieMasterCodec {
    const val MODEL="material_movie_master_v1"
    const val MANIFEST="master.json"
    fun encode(value: MovieMaterialMaster): ByteArray {
        validate(value)
        val root=linkedMapOf<String,Any?>("model" to MODEL,"master_id" to value.masterId,"roll_id" to value.rollId,
            "stage" to value.stage,"width" to value.width,"height" to value.height,"seed" to value.seed,"recipe" to value.recipe,
            "frames" to value.frames.map { mapOf("index" to it.index,"path" to it.path,"pts" to it.pts,
                "time_base_num" to it.timeBaseNum,"time_base_den" to it.timeBaseDen,"duration" to it.duration) })
        value.sourceMasterId?.let { root["source_master_id"]=it }
        value.source?.let { root["source"]=it }
        return RecipeShareCodec.canonicalJson(root).toByteArray(Charsets.UTF_8)
    }

    fun decode(bytes: ByteArray): MovieMaterialMaster {
        val raw=objectMap(jsonValue(JSONObject(bytes.toString(Charsets.UTF_8))))
        val required=setOf("model","master_id","roll_id","stage","width","height","seed","recipe","frames")
        require(raw.keys.containsAll(required) && (raw.keys-required-setOf("source_master_id","source")).isEmpty()) { "Invalid movie master fields" }
        require(raw["model"] == MODEL) { "Unsupported movie master model" }
        val frames=(raw["frames"] as? List<*>)?.map { value ->
            val f=objectMap(value)
            require(f.keys == setOf("index","path","pts","time_base_num","time_base_den","duration")) { "Invalid movie frame fields" }
            MovieMaterialFrame(int(f["index"]),text(f["path"]),long(f["pts"]),long(f["time_base_num"]),long(f["time_base_den"]),
                f["duration"]?.let { long(it) })
        } ?: error("Movie master needs frames")
        return MovieMaterialMaster(text(raw["master_id"]),text(raw["roll_id"]),text(raw["stage"]),
            int(raw["width"]),int(raw["height"]),long(raw["seed"]),objectMap(raw["recipe"]),frames,
            raw["source_master_id"]?.let { text(it) },raw["source"]?.let { objectMap(it) }).also(::validate)
    }

    fun snapshot(recipe: Map<String,Any?>): Map<String,Any?> = objectMap(jsonValue(JSONObject(RecipeShareCodec.canonicalJson(recipe))))

    fun validate(master: MovieMaterialMaster) {
        require(master.masterId.isNotBlank() && master.rollId.isNotBlank())
        require(master.stage in setOf("exposed_latent","developed_medium"))
        require(master.width > 0 && master.height > 0 && master.seed in 0..0xffffffffL && master.frames.isNotEmpty())
        require(master.sourceMasterId == null || master.sourceMasterId.isNotBlank())
        val paths=mutableSetOf<String>()
        var previous: MovieMaterialFrame?=null
        for((i,frame) in master.frames.withIndex()) {
            require(frame.index == i+1 && frame.timeBaseNum > 0 && frame.timeBaseDen > 0 && (frame.duration == null || frame.duration >= 0))
            val parts=frame.path.split('/')
            val suffix=if(master.stage == "exposed_latent") listOf(DataKernel.LATENT_SUFFIX) else listOf(DataKernel.NEGATIVE_SUFFIX,DataKernel.POSITIVE_SUFFIX)
            require(parts.size == 2 && parts[0] == "frames" && parts[1].isNotEmpty() && ':' !in frame.path && '\\' !in frame.path &&
                suffix.any { frame.path.endsWith(it) } && paths.add(frame.path)) { "Invalid movie NPZ path" }
            previous?.let { prior ->
                fun stamp(f: MovieMaterialFrame)=BigInteger.valueOf(f.pts).multiply(BigInteger.valueOf(f.timeBaseNum))
                require(stamp(frame).multiply(BigInteger.valueOf(prior.timeBaseDen)) > stamp(prior).multiply(BigInteger.valueOf(frame.timeBaseDen))) {
                    "Movie presentation timestamps must increase"
                }
            }
            previous=frame
        }
    }
    private fun text(value: Any?)=(value as? String)?.also { require(it.isNotBlank()) } ?: error("Expected string")
    private fun long(value: Any?): Long {
        require(value is Byte || value is Short || value is Int || value is Long) { "Expected integer" }
        return (value as Number).toLong()
    }
    private fun int(value: Any?): Int = long(value).also { require(it in Int.MIN_VALUE..Int.MAX_VALUE) }.toInt()
    private fun objectMap(value: Any?): Map<String,Any?> {
        require(value is Map<*,*>) { "Expected object" }
        return value.entries.associate { (it.key as String) to it.value }
    }
    private fun jsonValue(value: Any?): Any?=when(value) {
        null,JSONObject.NULL -> null
        is JSONObject -> value.keys().asSequence().associateWith { jsonValue(value.get(it)) }
        is JSONArray -> (0 until value.length()).map { jsonValue(value.get(it)) }
        else -> value
    }
}
