package com.filmfoundry.workflow

import com.filmfoundry.interchange.DataKernel
import com.filmfoundry.interchange.RecipeCatalogCodec
import com.filmfoundry.interchange.RecipeCatalogDocument
import com.filmfoundry.interchange.RecipeCatalogException
import com.filmfoundry.interchange.RecipeShareCodec

interface RecipeCatalogStore {
    fun writeCatalog(bytes: ByteArray)
    fun readCatalog(): ByteArray?
}

class MemoryRecipeCatalogStore : RecipeCatalogStore {
    private var payload: ByteArray? = null

    override fun writeCatalog(bytes: ByteArray) {
        payload = bytes.copyOf()
    }

    override fun readCatalog(): ByteArray? {
        return payload?.copyOf()
    }
}

data class NamedRecipe(
    val id: String,
    val kind: String,
    val label: String,
    val recipe: Map<String, Any?>,
)

/**
 * Reusable film stocks and scanner setups. Independent of a loaded roll.
 * Persistence is application JSON, not NPZ and not the system gallery.
 */
class RecipeCatalog(
    private val store: RecipeCatalogStore? = null,
) {
    private val films = linkedMapOf<String, NamedRecipe>()
    private val scanners = linkedMapOf<String, NamedRecipe>()
    var lastFilmId: String? = null
        private set
    var lastScannerId: String? = null
        private set

    init {
        restore()
    }

    fun films(): List<NamedRecipe> = films.values.toList()

    fun scanners(): List<NamedRecipe> = scanners.values.toList()

    /** Rename a catalog definition without changing its parameters, identity or last selection. */
    @Synchronized fun rename(kind: String,id: String,label: String) = change {
        val name=RollNaming.validate(label)
        val entries=when(kind) { "film"->films;"scanner"->scanners;else->throw IllegalArgumentException("unknown recipe kind") }
        val old=entries[id] ?: throw WorkflowException("invalid_request","unknown recipe")
        entries[id]=old.copy(label=name);persist()
    }

    fun film(id: String): NamedRecipe {
        return films[id] ?: throw WorkflowException("invalid_request", "unknown film recipe: $id")
    }

    fun scanner(id: String): NamedRecipe {
        return scanners[id] ?: throw WorkflowException("invalid_request", "unknown scanner recipe: $id")
    }

    @Synchronized fun saveFilm(id: String, label: String, recipe: Map<String, Any?>): NamedRecipe = change {
        val entry = put("film", id, label, recipe)
        lastFilmId = entry.id
        persist()
        entry
    }

    @Synchronized fun saveScanner(id: String, label: String, recipe: Map<String, Any?>): NamedRecipe = change {
        val entry = put("scanner", id, label, recipe)
        lastScannerId = entry.id
        persist()
        entry
    }

    fun saveFromShareCode(id: String, label: String, kind: String, code: String): NamedRecipe {
        if (kind !in DataKernel.PHONE_RECIPE_CATALOG_ENTRY_KINDS) {
            throw WorkflowException("invalid_request", "recipe kind must be film or scanner")
        }
        val payload = try {
            RecipeShareCodec.decode(code)
        } catch (exc: Exception) {
            throw WorkflowException("invalid_request", exc.message ?: "config share is not readable")
        }
        val section = payload[kind] as? Map<*, *>
            ?: throw WorkflowException("invalid_request", "share code has no $kind section")
        val recipe = section.entries.associate { it.key.toString() to it.value }
        return if (kind == "film") saveFilm(id, label, recipe) else saveScanner(id, label, recipe)
    }

    @Synchronized fun remove(kind: String, id: String) = change {
        when (kind) {
            "film" -> {
                films.remove(id) ?: throw WorkflowException("invalid_request", "unknown film recipe: $id")
                if (lastFilmId == id) lastFilmId = null
            }
            "scanner" -> {
                scanners.remove(id) ?: throw WorkflowException("invalid_request", "unknown scanner recipe: $id")
                if (lastScannerId == id) lastScannerId = null
            }
            else -> throw WorkflowException("invalid_request", "recipe kind must be film or scanner")
        }
        persist()
    }

    private fun put(kind: String, id: String, label: String, recipe: Map<String, Any?>): NamedRecipe {
        val trimmed = id.trim()
        if (trimmed.isEmpty()) {
            throw WorkflowException("invalid_request", "recipe id must be non-empty")
        }
        if (trimmed.any { char -> char !in ID_CHARS }) {
            throw WorkflowException("invalid_request", "recipe id may only contain letters, digits, '_' or '-'")
        }
        val entry = NamedRecipe(
            id = trimmed,
            kind = kind,
            label = label.trim(),
            recipe = frozenRecipe(recipe),
        )
        if (kind == "film") films[trimmed] = entry else scanners[trimmed] = entry
        return entry
    }

    companion object {
        private val ID_CHARS = (
            "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789_-"
            ).toSet()
    }

    private fun persist() {
        val catalogStore = store ?: return
        try {
            catalogStore.writeCatalog(
                RecipeCatalogCodec.encode(
                    RecipeCatalogDocument(
                        films = films.values.map { entryToMap(it) },
                        scanners = scanners.values.map { entryToMap(it) },
                        lastFilmId = lastFilmId,
                        lastScannerId = lastScannerId,
                    ),
                ),
            )
        } catch (exc: RecipeCatalogException) {
            throw WorkflowException("invalid_request", exc.message ?: "recipe catalog encode failed")
        }
    }

    private fun <T> change(work: ()->T): T {
        val oldFilms=films.toMap();val oldScanners=scanners.toMap();val oldFilm=lastFilmId;val oldScanner=lastScannerId
        try { return work() } catch(error: Exception) {
            films.clear();films.putAll(oldFilms);scanners.clear();scanners.putAll(oldScanners)
            lastFilmId=oldFilm;lastScannerId=oldScanner;throw error
        }
    }

    private fun restore() {
        val bytes = try {
            store?.readCatalog()
        } catch (exc: Exception) {
            throw WorkflowException("io_error", exc.message ?: "recipe catalog could not be read")
        } ?: return
        if (bytes.isEmpty()) {
            return
        }
        val document = try {
            RecipeCatalogCodec.decode(bytes)
        } catch (exc: RecipeCatalogException) {
            throw WorkflowException("invalid_request", exc.message ?: "recipe catalog is not readable")
        }
        films.clear()
        scanners.clear()
        for (item in document.films) {
            val entry = entryFromMap(item)
            films[entry.id] = entry
        }
        for (item in document.scanners) {
            val entry = entryFromMap(item)
            scanners[entry.id] = entry
        }
        lastFilmId = document.lastFilmId
        lastScannerId = document.lastScannerId
    }

    private fun entryToMap(entry: NamedRecipe): Map<String, Any?> {
        return linkedMapOf(
            "id" to entry.id,
            "kind" to entry.kind,
            "label" to entry.label,
            "recipe" to entry.recipe,
        )
    }

    private fun entryFromMap(raw: Map<String, Any?>): NamedRecipe {
        return NamedRecipe(
            id = raw.getValue("id") as String,
            kind = raw.getValue("kind") as String,
            label = raw["label"] as? String ?: "",
            recipe = frozenRecipe(objectMap(raw["recipe"])),
        )
    }

    private fun objectMap(value: Any?): Map<String, Any?> {
        val raw = value as? Map<*, *> ?: emptyMap<String, Any?>()
        return raw.entries.associate { it.key.toString() to it.value }
    }
}
