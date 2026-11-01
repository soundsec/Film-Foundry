package com.filmfoundry.interchange

import org.json.JSONArray
import org.json.JSONObject
import org.json.JSONTokener
import java.io.ByteArrayInputStream
import java.io.ByteArrayOutputStream
import java.io.InputStream
import java.io.OutputStream

class InterchangeException(message: String) : IllegalArgumentException(message)

data class LatentArchive(
    val arrays: Map<String, FloatNd>,
    val metadata: Map<String, Any?>,
)

data class DevelopedOpticalArchive(
    val opticalDensityRgb: FloatNd,
    val metadata: Map<String, Any?>,
    val clearBaseOpticalDensityRgb: FloatArray? = null,
    val spectralComponentAmounts: FloatNd? = null,
    val spectralPostProcessDensityRgb: FloatNd? = null,
)

interface MediumArchive {
    fun readLatent(bytes: ByteArray, allowConsumed: Boolean = false): LatentArchive
    fun writeLatent(archive: LatentArchive): ByteArray
    fun readDevelopedOptical(bytes: ByteArray): DevelopedOpticalArchive
    fun writeDevelopedOptical(archive: DevelopedOpticalArchive): ByteArray
    fun markLatentConsumed(bytes: ByteArray): ByteArray
    // Compatibility defaults for external/legacy implementations. NpzMediumArchive streams directly.
    fun readLatent(input: InputStream, allowConsumed: Boolean = false): LatentArchive = readLatent(input.readBytes(), allowConsumed)
    fun writeLatent(archive: LatentArchive, output: OutputStream) { output.write(writeLatent(archive)) }
    fun readDevelopedOptical(input: InputStream): DevelopedOpticalArchive = readDevelopedOptical(input.readBytes())
    fun writeDevelopedOptical(archive: DevelopedOpticalArchive, output: OutputStream) { output.write(writeDevelopedOptical(archive)) }
}

class NpzMediumArchive : MediumArchive {
    /** Header-only inspection; callers must still validate numerical arrays when loading a medium. */
    fun developedHeader(raw: NpyValue): Map<String,Any?> {
        val metadata=jsonObject(mapOf(DataKernel.DEVELOPED_METADATA_KEY to raw),DataKernel.DEVELOPED_METADATA_KEY)
        requireIdentity(metadata)
        normalizeFrameIdentity(metadata)
        return metadata
    }

    override fun readLatent(bytes: ByteArray, allowConsumed: Boolean): LatentArchive {
        return readLatent(ByteArrayInputStream(bytes), allowConsumed)
    }

    override fun readLatent(input: InputStream, allowConsumed: Boolean): LatentArchive {
        val retained = DataKernel.LATENT_REQUIRED_ARRAYS + DataKernel.LATENT_OPTIONAL_ARRAYS +
            setOf(DataKernel.LATENT_METADATA_KEY, DataKernel.OPTICAL_DENSITY_KEY, "density_cmy")
        val entries = NpzFile.read(input) { it in retained }
        return latentFromEntries(entries, allowConsumed)
    }

    fun latentFromEntries(entries: Map<String, NpyValue>, allowConsumed: Boolean = false): LatentArchive {
        if (
            (DataKernel.OPTICAL_DENSITY_KEY in entries || "density_cmy" in entries) &&
            "developability" !in entries
        ) {
            throw InterchangeException(
                "archive is a developed medium, not an undeveloped latent.",
            )
        }
        rejectPickle(entries)
        val metadata = jsonObject(entries, DataKernel.LATENT_METADATA_KEY)
        if (metadata[DataKernel.LATENT_KIND_KEY] != DataKernel.LATENT_KIND) {
            throw InterchangeException(
                "latent kind must be '${DataKernel.LATENT_KIND}'.",
            )
        }
        if (metadata[DataKernel.LATENT_STAGE_KEY] != DataKernel.LATENT_STAGE) {
            throw InterchangeException(
                "latent stage must be '${DataKernel.LATENT_STAGE}'.",
            )
        }
        val layerCount = intValue(metadata["layer_count"], "layer_count")
        if (layerCount <= 0) {
            throw InterchangeException("layer_count must be a positive integer.")
        }
        normalizeFrameIdentity(metadata)
        val arrays = linkedMapOf<String, FloatNd>()
        if(metadata.containsKey(DataKernel.MATERIAL_SNAPSHOT_KEY))
            MaterialSnapshot.film(metadata[DataKernel.MATERIAL_SNAPSHOT_KEY])
        for (key in DataKernel.LATENT_REQUIRED_ARRAYS) {
            arrays[key] = layerPool(entries, key, layerCount)
        }
        for (key in DataKernel.LATENT_OPTIONAL_ARRAYS) {
            if (key in entries) {
                arrays[key] = layerPool(entries, key, layerCount)
            }
        }
        if (metadata["consumed"] == true && !allowConsumed) {
            throw InterchangeException(
                "latent has been consumed; copy the archive before developing again.",
            )
        }
        return LatentArchive(arrays, metadata)
    }

    override fun writeLatent(archive: LatentArchive): ByteArray {
        return ByteArrayOutputStream().also { writeLatent(archive, it) }.toByteArray()
    }

    override fun writeLatent(archive: LatentArchive, output: OutputStream) {
        NpzFile.write(latentEntries(archive), output)
    }

    fun latentEntries(archive: LatentArchive): Map<String, NpyValue> {
        val metadata = archive.metadata.toMutableMap()
        metadata.putIfAbsent(DataKernel.LATENT_KIND_KEY, DataKernel.LATENT_KIND)
        metadata.putIfAbsent(DataKernel.LATENT_STAGE_KEY, DataKernel.LATENT_STAGE)
        metadata.putIfAbsent("consumable", true)
        metadata.putIfAbsent("consumed", false)
        if (metadata[DataKernel.LATENT_KIND_KEY] != DataKernel.LATENT_KIND) {
            throw InterchangeException("latent kind must be '${DataKernel.LATENT_KIND}'.")
        }
        if (metadata[DataKernel.LATENT_STAGE_KEY] != DataKernel.LATENT_STAGE) {
            throw InterchangeException("latent stage must be '${DataKernel.LATENT_STAGE}'.")
        }
        val halide = archive.arrays["halide"]
            ?: throw InterchangeException("latent archive missing halide.")
        if (halide.shape.size != 3) {
            throw InterchangeException("latent pools must have shape HxWxL.")
        }
        metadata["layer_count"] = halide.shape[2]
        requireIdentity(metadata)
        normalizeFrameIdentity(metadata)
        if(metadata.containsKey(DataKernel.MATERIAL_SNAPSHOT_KEY))
            MaterialSnapshot.film(metadata[DataKernel.MATERIAL_SNAPSHOT_KEY])
        val entries = linkedMapOf<String, NpyValue>()
        for (key in DataKernel.LATENT_REQUIRED_ARRAYS) {
            val array = archive.arrays[key]
                ?: throw InterchangeException("latent archive missing $key.")
            entries[key] = NpyValue.Float32(layerPoolValue(array, key, halide.shape[2]))
        }
        for (key in DataKernel.LATENT_OPTIONAL_ARRAYS) {
            val array = archive.arrays[key] ?: continue
            entries[key] = NpyValue.Float32(layerPoolValue(array, key, halide.shape[2]))
        }
        entries[DataKernel.LATENT_METADATA_KEY] = NpyValue.UnicodeScalar(encodeJson(metadata))
        return entries
    }

    override fun readDevelopedOptical(bytes: ByteArray): DevelopedOpticalArchive {
        return readDevelopedOptical(ByteArrayInputStream(bytes))
    }

    override fun readDevelopedOptical(input: InputStream): DevelopedOpticalArchive {
        val retained = setOf(DataKernel.OPTICAL_DENSITY_KEY, DataKernel.DEVELOPED_METADATA_KEY,
            DataKernel.CLEAR_BASE_KEY, SpectralOptics.AMOUNTS_KEY, SpectralOptics.POST_PROCESS_KEY)
        val entries = NpzFile.read(input) { it in retained }
        return opticalFromEntries(entries)
    }

    internal fun opticalFromEntries(entries: Map<String,NpyValue>): DevelopedOpticalArchive {
        rejectPickle(entries)
        val opticalEntry = entries[DataKernel.OPTICAL_DENSITY_KEY]
            ?: throw InterchangeException("developed archive missing optical_density_rgb.")
        // The specialized validators below check finiteness together with their range.
        val optical = requireRgb(floatArray(opticalEntry, DataKernel.OPTICAL_DENSITY_KEY,checkFinite=false), nonNegative = true)
        val metadata = jsonObject(entries, DataKernel.DEVELOPED_METADATA_KEY)
        requireIdentity(metadata)
        normalizeFrameIdentity(metadata)
        val clear = entries[DataKernel.CLEAR_BASE_KEY]?.let { raw ->
            val array = floatArray(raw, DataKernel.CLEAR_BASE_KEY)
            if (array.shape.contentEquals(intArrayOf(3)).not() || array.values.any { it < 0f }) {
                throw InterchangeException(
                    "clear_base_optical_density_rgb must contain three finite nonnegative values.",
                )
            }
            array.values.copyOf()
        }
        val amounts = entries[SpectralOptics.AMOUNTS_KEY]?.let { floatArray(it, SpectralOptics.AMOUNTS_KEY,checkFinite=false) }
        val postProcess = entries[SpectralOptics.POST_PROCESS_KEY]?.let { floatArray(it, SpectralOptics.POST_PROCESS_KEY,checkFinite=false) }
        SpectralOptics.validateMedium(metadata, amounts, optical.shape, postProcess)
        return DevelopedOpticalArchive(optical, metadata, clear, amounts, postProcess)
    }

    data class Inspection(val width: Int,val height: Int,val metadata: Map<String,Any?>)

    /** Validate every numeric chunk, retaining metadata and at most one pixel per required field.
     * The small probes reuse the ordinary archive validators; they are never returned as a medium.
     */
    fun inspect(input: InputStream,latent: Boolean,checkCancel: ()->Unit={},
        onMember: ((String,Long,Long)->Unit)?=null): Inspection {
        val pools=DataKernel.LATENT_REQUIRED_ARRAYS+DataKernel.LATENT_OPTIONAL_ARRAYS
        val numeric=if(latent) pools else setOf(DataKernel.OPTICAL_DENSITY_KEY,DataKernel.CLEAR_BASE_KEY,SpectralOptics.AMOUNTS_KEY,SpectralOptics.POST_PROCESS_KEY)
        val shapes=mutableMapOf<String,IntArray>()
        val probes=mutableMapOf<String,NpyValue>()
        val present=mutableSetOf<String>()
        val metaKey=if(latent) DataKernel.LATENT_METADATA_KEY else DataKernel.DEVELOPED_METADATA_KEY
        val scalar=NpzFile.readChunks(input,{ present.add(it);it == metaKey },
            NpzReadLimits(maxMembers=64,maxTotalBytes=2L*1024*1024*1024,maxStringBytes=1024*1024,maxRetainedBytes=1024*1024,
                onMember=onMember)) { key,shape,offset,values,n ->
            checkCancel()
            if(key in numeric) {
                if(key == DataKernel.CLEAR_BASE_KEY) {
                    require(shape.contentEquals(intArrayOf(3)) && offset == 0 && n == 3)
                    probes[key]=NpyValue.Float32(FloatNd(shape.copyOf(),values.copyOf(3)))
                    return@readChunks
                }
                require(shape.size == 3)
                val channels=when(key) { SpectralOptics.AMOUNTS_KEY -> 9
                    in pools -> shape[2].also { require(it in listOf(1,3)) }
                    else -> 3 }
                require(shape[2] == channels)
                if(!latent && key != SpectralOptics.POST_PROCESS_KEY)
                    for(i in 0 until n) require(values[i] >= 0f) { "Negative optical density or component amount" }
                if(offset == 0) {
                    require(n >= channels)
                    shapes[key]=shape.copyOf()
                    probes[key]=NpyValue.Float32(FloatNd(intArrayOf(1,1,channels),values.copyOf(channels)))
                }
            }
        }
        checkCancel()
        require((present intersect numeric).all { it in probes }) { "Material arrays must be numeric" }
        val shape=shapes[if(latent) "halide" else DataKernel.OPTICAL_DENSITY_KEY] ?: error("Missing material array")
        require(shapes.values.all { it[0] == shape[0] && it[1] == shape[1] }) { "Material dimensions disagree" }
        val entries=scalar+probes
        val metadata=if(latent) latentFromEntries(entries).metadata else opticalFromEntries(entries).metadata
        return Inspection(shape[1],shape[0],metadata)
    }

    override fun writeDevelopedOptical(archive: DevelopedOpticalArchive): ByteArray {
        return ByteArrayOutputStream().also { writeDevelopedOptical(archive, it) }.toByteArray()
    }

    override fun writeDevelopedOptical(archive: DevelopedOpticalArchive, output: OutputStream) {
        NpzFile.write(opticalEntries(archive), output)
    }

    fun opticalEntries(archive: DevelopedOpticalArchive): Map<String, NpyValue> {
        val optical = requireRgb(archive.opticalDensityRgb, nonNegative = true)
        val metadata = archive.metadata.toMutableMap()
        requireIdentity(metadata)
        normalizeFrameIdentity(metadata)
        rejectCanonicalFullStamp(metadata)
        SpectralOptics.validateMedium(metadata, archive.spectralComponentAmounts, optical.shape, archive.spectralPostProcessDensityRgb)
        val entries = linkedMapOf<String, NpyValue>(
            DataKernel.OPTICAL_DENSITY_KEY to NpyValue.Float32(optical),
            DataKernel.DEVELOPED_METADATA_KEY to NpyValue.UnicodeScalar(encodeJson(metadata)),
        )
        archive.clearBaseOpticalDensityRgb?.let { clear ->
            if (clear.size != 3 || clear.any { !it.isFinite() || it < 0f }) {
                throw InterchangeException(
                    "clear_base_optical_density_rgb must contain three finite nonnegative values.",
                )
            }
            entries[DataKernel.CLEAR_BASE_KEY] = NpyValue.Float32(FloatNd(intArrayOf(3), clear.copyOf()))
        }
        archive.spectralComponentAmounts?.let { entries[SpectralOptics.AMOUNTS_KEY] = NpyValue.Float32(it) }
        archive.spectralPostProcessDensityRgb?.let { entries[SpectralOptics.POST_PROCESS_KEY] = NpyValue.Float32(it) }
        return entries
    }

    override fun markLatentConsumed(bytes: ByteArray): ByteArray {
        val archive = readLatent(bytes, allowConsumed = true)
        if (archive.metadata["consumed"] == true) {
            return bytes
        }
        val updated = archive.metadata.toMutableMap()
        updated["consumed"] = true
        return writeLatent(archive.copy(metadata = updated))
    }

    private fun rejectPickle(entries: Map<String, NpyValue>) {
        // Decode already rejected object dtypes; keep the guard explicit.
        if (entries.isEmpty()) {
            throw InterchangeException("NPZ archive has no arrays.")
        }
    }

    private fun jsonObject(entries: Map<String, NpyValue>, key: String): MutableMap<String, Any?> {
        val raw = entries[key] as? NpyValue.UnicodeScalar
            ?: throw InterchangeException("archive missing $key JSON object.")
        val parsed = try {
            JSONObject(JSONTokener(raw.text))
        } catch (exc: Exception) {
            throw InterchangeException("$key is not valid JSON.")
        }
        val value = jsonToValue(parsed)
        if (value !is Map<*, *>) {
            throw InterchangeException("$key root must be an object.")
        }
        @Suppress("UNCHECKED_CAST")
        return (value as Map<String, Any?>).toMutableMap()
    }

    private fun floatArray(value: NpyValue, key: String,checkFinite: Boolean=true): FloatNd {
        val array = (value as? NpyValue.Float32)?.array
            ?: throw InterchangeException("$key must be a float32 array.")
        if (checkFinite && array.values.any { !(it>=-Float.MAX_VALUE && it<=Float.MAX_VALUE) }) {
            throw InterchangeException("$key contains NaN or Infinity.")
        }
        return array
    }

    private fun layerPool(entries: Map<String, NpyValue>, key: String, layerCount: Int): FloatNd {
        val raw = entries[key] ?: throw InterchangeException("latent archive missing $key.")
        return layerPoolValue(floatArray(raw, key), key, layerCount)
    }

    private fun layerPoolValue(array: FloatNd, key: String, layerCount: Int): FloatNd {
        if (array.shape.size != 3 || array.shape[2] != layerCount) {
            throw InterchangeException("$key shape must be HxWx$layerCount.")
        }
        return array
    }

    private fun requireRgb(array: FloatNd, nonNegative: Boolean): FloatNd {
        if (array.shape.size != 3 || array.shape[2] != 3) {
            throw InterchangeException("optical_density_rgb shape must be HxWx3.")
        }
        val minimum=if(nonNegative) 0f else -Float.MAX_VALUE
        if (array.values.any { !(it>=minimum && it<=Float.MAX_VALUE) }) {
            throw InterchangeException("optical_density_rgb must contain finite${if(nonNegative) " nonnegative" else ""} density.")
        }
        return array
    }

    private fun requireIdentity(metadata: Map<String, Any?>) {
        val polarity = metadata["image_polarity"]?.toString()?.trim()?.lowercase()
        if (polarity == null || polarity !in DataKernel.POLARITY_VALUES) {
            throw InterchangeException("image_polarity must be negative or positive.")
        }
        val interpreters = metadata["compatible_interpreters"]
        if (interpreters !is List<*> || interpreters.isEmpty() || interpreters.any { it !is String || it.isBlank() }) {
            throw InterchangeException("compatible_interpreters must be a non-empty string list.")
        }
        if (polarity == "negative" && "negative_scan" !in interpreters) {
            throw InterchangeException("negative medium metadata needs negative_scan.")
        }
        if (polarity == "positive" && "positive_transparency_scan" !in interpreters) {
            throw InterchangeException("positive medium metadata needs positive_transparency_scan.")
        }
        for (key in DataKernel.MEDIUM_IDENTITY_FIELDS) {
            if (key == "compatible_interpreters" || key == "image_polarity") continue
            val value = metadata[key] ?: continue
            if (value !is String || value.isBlank()) {
                throw InterchangeException("medium metadata field '$key' must be a non-empty string.")
            }
        }
    }

    private fun normalizeFrameIdentity(metadata: MutableMap<String, Any?>) {
        if (!metadata.containsKey(DataKernel.FRAME_IDENTITY_KEY)) return
        val raw = metadata[DataKernel.FRAME_IDENTITY_KEY]
        if (raw == null) {
            metadata.remove(DataKernel.FRAME_IDENTITY_KEY)
            return
        }
        if (raw !is Map<*, *>) {
            throw InterchangeException("frame_identity must be an object.")
        }
        val unknown = raw.keys.map { it.toString() }.filter { it !in DataKernel.FRAME_IDENTITY_FIELDS }
        if (unknown.isNotEmpty()) {
            throw InterchangeException(
                "frame_identity has unknown fields: ${unknown.sorted().joinToString(", ")}.",
            )
        }
        val payload = linkedMapOf<String, Any?>()
        val rollId = raw["roll_id"]
        if (rollId != null) {
            if (rollId !is String || rollId.isBlank()) {
                throw InterchangeException("frame_identity.roll_id must be a non-empty string.")
            }
            payload["roll_id"] = rollId.trim()
        }
        val frameId = raw["frame_id"]
        if (frameId != null) {
            if (frameId !is String || frameId.isBlank()) {
                throw InterchangeException("frame_identity.frame_id must be a non-empty string.")
            }
            payload["frame_id"] = frameId.trim()
        }
        if ("frame_index" in raw) {
            payload["frame_index"] = intValue(raw["frame_index"], "frame_identity.frame_index")
            if (payload["frame_index"] as Int <= 0) {
                throw InterchangeException("frame_identity.frame_index must be 1-based.")
            }
        }
        if (payload.isEmpty()) {
            metadata.remove(DataKernel.FRAME_IDENTITY_KEY)
            return
        }
        if ("roll_id" !in payload) {
            throw InterchangeException("frame_identity must include roll_id.")
        }
        if ("frame_id" !in payload && "frame_index" !in payload) {
            throw InterchangeException("frame_identity must include frame_id or frame_index.")
        }
        metadata[DataKernel.FRAME_IDENTITY_KEY] = payload
    }

    private fun rejectCanonicalFullStamp(metadata: Map<String, Any?>) {
        val model = metadata["film_process_model"] as? Map<*, *> ?: return
        val execution = model["execution_implementation"] as? Map<*, *> ?: return
        if (execution["profile_id"] == "canonical_full_v1") {
            throw InterchangeException(
                "phone optical writer must not stamp canonical_full_v1.",
            )
        }
    }

    private fun intValue(value: Any?, name: String): Int {
        if (value is Boolean) {
            throw InterchangeException("$name must be an integer.")
        }
        val number = when (value) {
            is Int -> value
            is Long -> {
                if (value < Int.MIN_VALUE || value > Int.MAX_VALUE) {
                    throw InterchangeException("$name must be an integer.")
                }
                value.toInt()
            }
            else -> throw InterchangeException("$name must be an integer.")
        }
        return number
    }

    private fun encodeJson(metadata: Map<String, Any?>): String {
        return toJson(metadata).toString()
    }

    private fun toJson(value: Any?): Any {
        return when (value) {
            null -> JSONObject.NULL
            is Boolean, is String -> value
            is Int, is Long -> value
            is Float, is Double -> {
                val d = (value as Number).toDouble()
                if (!d.isFinite()) {
                    throw InterchangeException("metadata cannot contain NaN or Infinity.")
                }
                d
            }
            is Number -> value
            is Map<*, *> -> {
                val obj = JSONObject()
                for ((key, item) in value) {
                    obj.put(key.toString(), toJson(item))
                }
                obj
            }
            is List<*> -> {
                val array = JSONArray()
                for (item in value) array.put(toJson(item))
                array
            }
            else -> throw InterchangeException(
                "metadata cannot encode ${value::class.simpleName}.",
            )
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
