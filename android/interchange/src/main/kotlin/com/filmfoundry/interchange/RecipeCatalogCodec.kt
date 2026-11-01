package com.filmfoundry.interchange

import org.json.JSONArray
import org.json.JSONObject
import org.json.JSONTokener
import java.nio.charset.StandardCharsets

class RecipeCatalogException(message: String) : IllegalArgumentException(message)

data class RecipeCatalogDocument(
    val films: List<Map<String, Any?>>,
    val scanners: List<Map<String, Any?>>,
    val lastFilmId: String? = null,
    val lastScannerId: String? = null,
)

/**
 * Named film and scanner recipes. Application-private JSON, not a medium
 * NPZ, not FFC2-in-the-gallery, and not an interrupt-resume unit.
 */
object RecipeCatalogCodec {
    private val rootKeys = setOf(
        "kind",
        "version",
        "films",
        "scanners",
        "last_film_id",
        "last_scanner_id",
    )
    private val entryKeys = setOf("id", "kind", "label", "recipe")
    private val idChars = (
        "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789_-"
        ).toSet()

    fun encode(document: RecipeCatalogDocument): ByteArray {
        val films = document.films.map { normalizeEntry(it, "film") }
        val scanners = document.scanners.map { normalizeEntry(it, "scanner") }
        requireUniqueIds(films, "film")
        requireUniqueIds(scanners, "scanner")
        val payload = linkedMapOf(
            "kind" to DataKernel.PHONE_RECIPE_CATALOG_KIND,
            "version" to DataKernel.PHONE_RECIPE_CATALOG_VERSION,
            "films" to films,
            "scanners" to scanners,
            "last_film_id" to optionalId(document.lastFilmId, films, "last_film_id"),
            "last_scanner_id" to optionalId(document.lastScannerId, scanners, "last_scanner_id"),
        )
        return (toJson(payload) as JSONObject).toString().toByteArray(StandardCharsets.UTF_8)
    }

    fun decode(bytes: ByteArray): RecipeCatalogDocument {
        if (bytes.isEmpty()) {
            throw RecipeCatalogException("recipe catalog is empty.")
        }
        val root = try {
            JSONObject(JSONTokener(String(bytes, StandardCharsets.UTF_8)))
        } catch (exc: Exception) {
            throw RecipeCatalogException("recipe catalog is not valid JSON.")
        }
        val keys = mutableSetOf<String>()
        val iterator = root.keys()
        while (iterator.hasNext()) {
            keys.add(iterator.next())
        }
        if (keys != rootKeys) {
            throw RecipeCatalogException("recipe catalog has unknown or missing fields.")
        }
        if (root.opt("kind") != DataKernel.PHONE_RECIPE_CATALOG_KIND) {
            throw RecipeCatalogException("recipe catalog kind is not ${DataKernel.PHONE_RECIPE_CATALOG_KIND}.")
        }
        val version = (root.opt("version") as? Number)?.toInt()
        if (version != DataKernel.PHONE_RECIPE_CATALOG_VERSION) {
            throw RecipeCatalogException("unsupported recipe catalog version ${root.opt("version")}.")
        }
        val filmsRaw = root.opt("films")
        val scannersRaw = root.opt("scanners")
        if (filmsRaw !is JSONArray || scannersRaw !is JSONArray) {
            throw RecipeCatalogException("recipe catalog films and scanners must be arrays.")
        }
        val films = (jsonToValue(filmsRaw) as List<*>).map { normalizeEntry(asObject(it, "film"), "film") }
        val scanners = (jsonToValue(scannersRaw) as List<*>).map {
            normalizeEntry(asObject(it, "scanner"), "scanner")
        }
        requireUniqueIds(films, "film")
        requireUniqueIds(scanners, "scanner")
        return RecipeCatalogDocument(
            films = films,
            scanners = scanners,
            lastFilmId = optionalId(nullableText(root.opt("last_film_id")), films, "last_film_id"),
            lastScannerId = optionalId(nullableText(root.opt("last_scanner_id")), scanners, "last_scanner_id"),
        )
    }

    private fun normalizeEntry(raw: Map<*, *>, expectedKind: String): Map<String, Any?> {
        requireKeys(raw, entryKeys, expectedKind)
        val id = text(raw["id"], "id")
        if (id.any { it !in idChars }) {
            throw RecipeCatalogException("recipe id may only contain letters, digits, '_' or '-'.")
        }
        val kind = text(raw["kind"], "kind")
        if (kind != expectedKind || kind !in DataKernel.PHONE_RECIPE_CATALOG_ENTRY_KINDS) {
            throw RecipeCatalogException("recipe kind must be $expectedKind.")
        }
        val label = raw["label"] as? String
            ?: throw RecipeCatalogException("recipe label must be a string.")
        val recipe = objectMap(raw["recipe"], "recipe")
        if (recipe.keys.any { key -> key.endsWith(".npz") || key.startsWith(DataKernel.CONFIG_SHARE_PREFIX) }) {
            throw RecipeCatalogException("recipe catalog cannot store NPZ or share-code keys.")
        }
        return linkedMapOf(
            "id" to id,
            "kind" to kind,
            "label" to label,
            "recipe" to recipe,
        )
    }

    private fun requireUniqueIds(entries: List<Map<String, Any?>>, kind: String) {
        val ids = entries.map { it.getValue("id") }
        if (ids.toSet().size != ids.size) {
            throw RecipeCatalogException("duplicate $kind recipe id.")
        }
    }

    private fun optionalId(value: String?, entries: List<Map<String, Any?>>, label: String): String? {
        if (value.isNullOrEmpty()) {
            return null
        }
        if (entries.none { it["id"] == value }) {
            throw RecipeCatalogException("$label does not match a saved recipe.")
        }
        return value
    }

    private fun nullableText(value: Any?): String? {
        if (value == null || value == JSONObject.NULL) {
            return null
        }
        return (value as? String)?.trim()?.ifEmpty { null }
    }

    private fun requireKeys(raw: Map<*, *>, allowed: Set<String>, label: String) {
        val keys = raw.keys.map { it.toString() }.toSet()
        if (keys != allowed) {
            throw RecipeCatalogException("$label has unknown or missing fields.")
        }
    }

    private fun asObject(value: Any?, label: String): Map<*, *> {
        return value as? Map<*, *> ?: throw RecipeCatalogException("$label must be an object.")
    }

    private fun objectMap(value: Any?, label: String): Map<String, Any?> {
        val map = value as? Map<*, *> ?: throw RecipeCatalogException("$label must be an object.")
        return map.entries.associate { it.key.toString() to it.value }
    }

    private fun text(value: Any?, label: String): String {
        val text = (value as? String)?.trim().orEmpty()
        if (text.isEmpty()) {
            throw RecipeCatalogException("$label must be a non-empty string.")
        }
        return text
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
            else -> throw RecipeCatalogException("recipe catalog cannot encode ${value::class.simpleName}.")
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
