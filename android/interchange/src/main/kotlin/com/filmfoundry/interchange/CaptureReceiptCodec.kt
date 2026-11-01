package com.filmfoundry.interchange

import org.json.JSONObject
import org.json.JSONArray

data class StoredExposure(val path: String,val width: Int,val height: Int,val ev: Double,val record: Map<String,Any?>)
data class CaptureReceipt(val rollId: String,val frameIndex: Int,val frameId: String,val film: Map<String,Any?>,
    val finalized: Boolean,val sources: List<StoredExposure>)
data class StoredExposureBundle(val rollId: String,val frameIndex: Int,val frameId: String,val film: Map<String,Any?>,val source: StoredExposure)

/** Application-private capture journal, separate from all portable medium schemas. */
object CaptureReceiptCodec {
    const val MAX_EXPOSURES=64
    fun sourceName(record: Map<String,Any?>)=if(record["kind"]=="sensor_raw_dng") "source.dng" else "source.jpg"
    fun validateMaterial(film: Map<String,Any?>) {
        require(RecipeShareCodec.canonicalJson(film).toByteArray(Charsets.UTF_8).size <= 256*1024) { "capture material recipe too large" }
    }
    fun encode(value: CaptureReceipt): ByteArray {
        validate(value)
        return RecipeShareCodec.canonicalJson(mapOf("model" to "frame_capture_receipt_v1","roll_id" to value.rollId,
            "frame_index" to value.frameIndex,"frame_id" to value.frameId,"film" to value.film,
            "finalized" to value.finalized,"sources" to value.sources.map(::sourceMap))).toByteArray(Charsets.UTF_8)
    }
    fun decode(bytes: ByteArray): CaptureReceipt {
        require(bytes.size <= 1024*1024) { "capture receipt too large" }
        val map=objectMap(json(JSONObject(bytes.toString(Charsets.UTF_8))))
        require(map.keys == setOf("model","roll_id","frame_index","frame_id","film","finalized","sources"))
        require(map["model"] == "frame_capture_receipt_v1")
        return CaptureReceipt(map["roll_id"] as String,integer(map["frame_index"]),map["frame_id"] as String,
            objectMap(map["film"]),map["finalized"] as Boolean,(map["sources"] as List<*>).map { source(objectMap(it)) }).also(::validate)
    }
    fun encodeBundle(value: StoredExposureBundle): ByteArray {
        validateSource(value.source)
        return RecipeShareCodec.canonicalJson(mapOf("roll_id" to value.rollId,"frame_index" to value.frameIndex,
            "frame_id" to value.frameId,"film" to value.film,"source" to sourceMap(value.source))).toByteArray(Charsets.UTF_8)
    }
    fun decodeBundle(bytes: ByteArray): StoredExposureBundle {
        require(bytes.size <= 1024*1024)
        val map=objectMap(json(JSONObject(bytes.toString(Charsets.UTF_8))))
        require(map.keys == setOf("roll_id","frame_index","frame_id","film","source"))
        return StoredExposureBundle(map["roll_id"] as String,integer(map["frame_index"]),map["frame_id"] as String,
            objectMap(map["film"]),source(objectMap(map["source"])))
    }
    private fun sourceMap(s: StoredExposure) = mapOf("path" to s.path,"width" to s.width,"height" to s.height,"ev" to s.ev,"capture" to s.record)
    private fun source(map: Map<String,Any?>): StoredExposure {
        require(map.keys == setOf("path","width","height","ev","capture"))
        return StoredExposure(map["path"] as String,integer(map["width"]),integer(map["height"]),
            (map["ev"] as Number).toDouble(),objectMap(map["capture"])).also(::validateSource)
    }
    private fun integer(value: Any?): Int {
        val number=value as? Number ?: error("integer required")
        return number.toInt().also { require(it.toDouble() == number.toDouble()) }
    }
    private fun validateSource(s: StoredExposure) {
        require(s.path.matches(Regex("exposure_[0-9]{6}/source\\.(jpg|dng)")) && s.path.endsWith("/"+sourceName(s.record)) && s.width > 0 && s.height > 0 && s.ev.isFinite() && s.ev in -20.0..20.0)
        require(s.record["kind"] in listOf("display_referred_srgb","ultra_hdr_gainmap","sensor_raw_dng") && s.record["working_space"] == DataKernel.CAPTURE_WORKING_SPACE)
        if(s.record["kind"]=="sensor_raw_dng") require(s.record["isp_processed"]==false && s.record["hdr_gainmap"]=="absent") { "RAW sources must contain sensor samples" }
        if(s.record["kind"]=="ultra_hdr_gainmap") require(s.record["hdr_gainmap"]=="present_not_applied") { "Stored HDR must retain its unapplied gain map" }
        StillFrameFormat.read(s.record["frame_format"],s.width,s.height)
        require(RecipeShareCodec.canonicalJson(s.record).toByteArray(Charsets.UTF_8).size <= 8*1024) { "capture record too large" }
    }
    private fun validate(r: CaptureReceipt) {
        validateMaterial(r.film)
        require(r.rollId.isNotBlank() && r.frameIndex > 0 && r.frameId.isNotBlank() && r.sources.size in 1..MAX_EXPOSURES)
        r.sources.forEachIndexed { i,s -> validateSource(s);require(s.path == "exposure_${(i+1).toString().padStart(6,'0')}/${sourceName(s.record)}") }
        require(r.sources.all { it.width == r.sources[0].width && it.height == r.sources[0].height }) { "exposure dimensions changed" }
    }
    @Suppress("UNCHECKED_CAST") private fun objectMap(value: Any?) = value as? Map<String,Any?> ?: error("capture object required")
    private fun json(value: Any?): Any? = when(value) {
        is JSONObject -> value.keys().asSequence().associateWith { json(value.get(it)) }
        is JSONArray -> (0 until value.length()).map { json(value.get(it)) }
        JSONObject.NULL -> null
        else -> value
    }
}
