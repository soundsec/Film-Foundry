package com.filmfoundry.interchange

import org.json.JSONArray
import org.json.JSONObject
import org.json.JSONTokener
import java.nio.charset.StandardCharsets

class LibraryIndexException(message: String) : IllegalArgumentException(message)

data class LibraryIndexDocument(
    val rolls: List<Map<String, Any?>>,
    val results: List<Map<String, Any?>>,
)

/**
 * Application-private roll/result catalog. This is not a medium NPZ, not a
 * second optical schema. Capture recovery uses complete source receipts;
 * formed media continue to require complete NPZ files on disk.
 */
object LibraryIndexCodec {
    private val rootKeys = setOf("kind", "version", "rolls", "results")
    private val rollKeys = setOf(
        "roll_id",
        "capacity",
        "lifecycle",
        "label",
        "film",
        "develop",
        "scanner",
        "frames",
        "capture",
    )
    private val frameKeys = setOf("frame_index", "frame_id", "state", "latent_path", "labels", "capture_path", "capture_exposures", "shutter_armed", "gate_panorama")
    private val resultKeys = setOf(
        "result_id",
        "source_roll_id",
        "source_frame_index",
        "optical_path",
        "develop",
        "source_roll_present",
    )

    fun encode(document: LibraryIndexDocument): ByteArray {
        val payload = linkedMapOf(
            "kind" to DataKernel.PHONE_LIBRARY_INDEX_KIND,
            "version" to DataKernel.PHONE_LIBRARY_INDEX_VERSION,
            "rolls" to document.rolls.map { normalizeRoll(it) },
            "results" to document.results.map { normalizeResult(it) },
        )
        return (toJson(payload) as JSONObject).toString().toByteArray(StandardCharsets.UTF_8)
    }

    fun decode(bytes: ByteArray): LibraryIndexDocument {
        if (bytes.isEmpty()) {
            throw LibraryIndexException("library index is empty.")
        }
        val root = try {
            JSONObject(JSONTokener(String(bytes, StandardCharsets.UTF_8)))
        } catch (exc: Exception) {
            throw LibraryIndexException("library index is not valid JSON.")
        }
        val keys = mutableSetOf<String>()
        val iterator = root.keys()
        while (iterator.hasNext()) {
            keys.add(iterator.next())
        }
        if (keys != rootKeys) {
            throw LibraryIndexException("library index has unknown or missing fields.")
        }
        if (root.opt("kind") != DataKernel.PHONE_LIBRARY_INDEX_KIND) {
            throw LibraryIndexException("library index kind is not ${DataKernel.PHONE_LIBRARY_INDEX_KIND}.")
        }
        val version = (root.opt("version") as? Number)?.toInt()
        if (version !in listOf(1,2,DataKernel.PHONE_LIBRARY_INDEX_VERSION)) {
            throw LibraryIndexException("unsupported library index version ${root.opt("version")}.")
        }
        val rollsRaw = root.opt("rolls")
        val resultsRaw = root.opt("results")
        if (rollsRaw !is JSONArray || resultsRaw !is JSONArray) {
            throw LibraryIndexException("library index rolls and results must be arrays.")
        }
        val rolls = jsonToValue(rollsRaw) as List<*>
        val results = jsonToValue(resultsRaw) as List<*>
        val document = LibraryIndexDocument(
            rolls = rolls.map { normalizeRoll(asObject(it, "roll")) },
            results = results.map { normalizeResult(asObject(it, "result")) },
        )
        if(version == 1 && document.rolls.any { roll -> (roll["frames"] as List<*>).any { raw ->
            val frame=raw as Map<*,*>; frame["capture_path"] != null || frame["state"] in listOf("capturing","captured")
        } }) throw LibraryIndexException("version 1 cannot declare deferred captures")
        val rollIds = document.rolls.map { it.getValue("roll_id") }
        if (rollIds.toSet().size != rollIds.size) {
            throw LibraryIndexException("duplicate roll_id.")
        }
        val resultIds = document.results.map { it.getValue("result_id") }
        if (resultIds.toSet().size != resultIds.size) {
            throw LibraryIndexException("duplicate result_id.")
        }
        return document
    }

    private fun normalizeRoll(raw: Map<*, *>): Map<String, Any?> {
        if(raw.keys-"catalog_removed" != rollKeys && raw.keys-"catalog_removed" != rollKeys-"capture")
            throw LibraryIndexException("roll has unknown or missing fields")
        if(raw.containsKey("catalog_removed") && raw["catalog_removed"] !is Boolean)
            throw LibraryIndexException("catalog_removed must be a boolean")
        val rollId = text(raw["roll_id"], "roll_id")
        val capacity = intOf(raw["capacity"], "capacity")
        if (capacity !in DataKernel.PHONE_ROLL_CAPACITY_VALUES) {
            throw LibraryIndexException("unsupported roll capacity.")
        }
        StillFrameFormat.validateRoll(capacity,if(raw["capture"] == null) emptyMap() else objectMap(raw["capture"],"capture"))
        val lifecycle = text(raw["lifecycle"], "lifecycle")
        if (lifecycle !in DataKernel.PHONE_ROLL_LIFECYCLE) {
            throw LibraryIndexException("unknown roll lifecycle: $lifecycle")
        }
        val framesRaw = raw["frames"] as? List<*>
            ?: throw LibraryIndexException("roll frames must be an array.")
        if (framesRaw.size != capacity) {
            throw LibraryIndexException("roll frames must match capacity $capacity.")
        }
        val seen = mutableSetOf<Int>()
        val frames = framesRaw.map { item ->
            val frame = normalizeFrame(asObject(item, "frame"), rollId, capacity)
            val index = frame["frame_index"] as Int
            if (!seen.add(index)) {
                throw LibraryIndexException("duplicate frame_index $index.")
            }
            frame
        }
        if (seen != (1..capacity).toSet()) {
            throw LibraryIndexException("roll frames must cover 1..$capacity.")
        }
        val open=frames.count { it["state"] == "capturing" }
        if(open > 1 || (open > 0 && lifecycle !in listOf("loading","shooting")))
            throw LibraryIndexException("only one shooting frame may hold multiple exposures")
        if(raw["catalog_removed"]==true && (lifecycle!="discarded" || frames.any { it["latent_path"]!=null || it["capture_path"]!=null || it["state"]!="empty" }))
            throw LibraryIndexException("a removed roll cannot retain exposures")
        return linkedMapOf(
            "roll_id" to rollId,
            "capacity" to capacity,
            "lifecycle" to lifecycle,
            "label" to (raw["label"] as? String ?: ""),
            "film" to objectMap(raw["film"], "film"),
            "develop" to objectMap(raw["develop"], "develop"),
            "scanner" to objectMap(raw["scanner"], "scanner"),
            "capture" to if(raw["capture"] == null) emptyMap<String,Any?>() else objectMap(raw["capture"],"capture"),
            "frames" to frames,
        ).apply { if(raw["catalog_removed"]==true) put("catalog_removed",true) }
    }

    private fun normalizeFrame(
        raw: Map<*, *>,
        rollId: String,
        capacity: Int,
    ): Map<String, Any?> {
        if(raw.keys != frameKeys && raw.keys != frameKeys-setOf("shutter_armed","gate_panorama") && raw.keys != frameKeys-setOf("capture_path","capture_exposures","shutter_armed","gate_panorama"))
            throw LibraryIndexException("frame has unknown or missing fields")
        val index = intOf(raw["frame_index"], "frame_index")
        if (index < 1 || index > capacity) {
            throw LibraryIndexException("frame_index must be between 1 and $capacity.")
        }
        val state = text(raw["state"], "state")
        if (state !in DataKernel.PHONE_LIBRARY_FRAME_STATES) {
            throw LibraryIndexException("unknown library frame state: $state")
        }
        val path = optionalPath(raw["latent_path"], DataKernel.LATENT_SUFFIX, "latent_path")
        val capture = optionalPath(raw["capture_path"], ".capture/receipt.json", "capture_path")
        val exposures = raw["capture_exposures"]?.let { intOf(it,"capture_exposures") } ?: 0
        if(exposures !in 0..CaptureReceiptCodec.MAX_EXPOSURES || (capture == null) != (exposures == 0))
            throw LibraryIndexException("capture exposure count does not match receipt")
        if(state in listOf("capturing","captured") && (capture == null || path != null))
            throw LibraryIndexException("captured frames need a receipt and cannot claim a latent")
        if(state == "empty" && capture != null) throw LibraryIndexException("empty frame cannot reference a capture")
        if (state == "empty" && path != null) {
            throw LibraryIndexException("empty frames cannot keep a latent path.")
        }
        if (state == "latent" && path == null) {
            throw LibraryIndexException("latent frames require ${DataKernel.LATENT_SUFFIX}.")
        }
        val labels = raw["labels"] as? List<*>
            ?: throw LibraryIndexException("frame labels must be an array.")
        return linkedMapOf(
            "frame_index" to index,
            "frame_id" to (raw["frame_id"] as? String ?: "$rollId:$index"),
            "state" to state,
            "latent_path" to path,
            "capture_path" to capture,
            "capture_exposures" to exposures,
            "shutter_armed" to (raw["shutter_armed"]?.also { if(it !is Boolean) throw LibraryIndexException("shutter_armed must be boolean") } ?: (state != "capturing")),
            "gate_panorama" to (raw["gate_panorama"]?.also { if(it !is Boolean) throw LibraryIndexException("gate_panorama must be boolean") } ?: false),
            "labels" to labels.map { it?.toString() ?: "" },
        )
    }

    private fun normalizeResult(raw: Map<*, *>): Map<String, Any?> {
        requireKeys(raw, resultKeys, "result")
        val optical = text(raw["optical_path"], "optical_path")
        if (
            !optical.endsWith(DataKernel.NEGATIVE_SUFFIX) &&
            !optical.endsWith(DataKernel.POSITIVE_SUFFIX)
        ) {
            throw LibraryIndexException(
                "developed result must be ${DataKernel.NEGATIVE_SUFFIX} or ${DataKernel.POSITIVE_SUFFIX}.",
            )
        }
        val present = raw["source_roll_present"]
        if (present !is Boolean) {
            throw LibraryIndexException("source_roll_present must be a boolean.")
        }
        return linkedMapOf(
            "result_id" to text(raw["result_id"], "result_id"),
            "source_roll_id" to text(raw["source_roll_id"], "source_roll_id"),
            "source_frame_index" to intOf(raw["source_frame_index"], "source_frame_index").also { index ->
                if (index < 1) {
                    throw LibraryIndexException("source_frame_index must be >= 1.")
                }
            },
            "optical_path" to optical,
            "develop" to objectMap(raw["develop"], "develop"),
            "source_roll_present" to present,
        )
    }

    private fun requireKeys(raw: Map<*, *>, allowed: Set<String>, label: String) {
        val keys = raw.keys.map { it.toString() }.toSet()
        if (keys != allowed) {
            throw LibraryIndexException("$label has unknown or missing fields.")
        }
    }

    private fun asObject(value: Any?, label: String): Map<*, *> {
        return value as? Map<*, *> ?: throw LibraryIndexException("$label must be an object.")
    }

    private fun objectMap(value: Any?, label: String): Map<String, Any?> {
        val map = value as? Map<*, *> ?: throw LibraryIndexException("$label must be an object.")
        return map.entries.associate { it.key.toString() to it.value }
    }

    private fun text(value: Any?, label: String): String {
        val text = (value as? String)?.trim().orEmpty()
        if (text.isEmpty()) {
            throw LibraryIndexException("$label must be a non-empty string.")
        }
        return text
    }

    private fun intOf(value: Any?, label: String): Int {
        return (value as? Number)?.toInt()
            ?: throw LibraryIndexException("$label must be an integer.")
    }

    private fun optionalPath(value: Any?, suffix: String, label: String): String? {
        if (value == null || value == JSONObject.NULL) {
            return null
        }
        val path = (value as? String)?.trim().orEmpty()
        if (path.isEmpty()) {
            return null
        }
        if (!path.endsWith(suffix)) {
            throw LibraryIndexException("$label must end with $suffix.")
        }
        return path
    }

    private fun toJson(value: Any?): Any {
        return when (value) {
            null -> JSONObject.NULL
            is Boolean, is Number, is String -> value
            is Map<*, *> -> JSONObject().also { obj ->
                for ((key, item) in value) {
                    obj.put(key.toString(), toJson(item))
                }
            }
            is List<*> -> JSONArray().also { arr ->
                for (item in value) {
                    arr.put(toJson(item))
                }
            }
            else -> throw LibraryIndexException("library index cannot encode ${value::class.simpleName}.")
        }
    }

    private fun jsonToValue(value: Any?): Any? {
        return when (value) {
            null, JSONObject.NULL -> null
            is JSONObject -> {
                val out = linkedMapOf<String, Any?>()
                val keys = value.keys()
                while (keys.hasNext()) {
                    val key = keys.next()
                    out[key] = jsonToValue(value.get(key))
                }
                out
            }
            is JSONArray -> (0 until value.length()).map { jsonToValue(value.get(it)) }
            else -> value
        }
    }
}
