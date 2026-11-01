package com.filmfoundry.workflow

import com.filmfoundry.interchange.RecipeShareCodec
import org.junit.jupiter.api.Assertions.assertEquals
import org.junit.jupiter.api.Assertions.assertNull
import org.junit.jupiter.api.Assertions.assertThrows
import org.junit.jupiter.api.Assertions.assertTrue
import org.junit.jupiter.api.Test

class RecipeCatalogTest {
    @Test fun failedSaveAndDeleteRestoreDefinitionsAndLastSelection() {
        val memory=MemoryRecipeCatalogStore();var fail=false
        val store=object: RecipeCatalogStore by memory { override fun writeCatalog(bytes: ByteArray) { if(fail) throw java.io.IOException("failed");memory.writeCatalog(bytes) } }
        val catalog=RecipeCatalog(store);val values=mutableListOf<Any?>(1.0,2.0,3.0)
        catalog.saveFilm("old","Old",mapOf("film_base_density_rgb" to values));values[0]=9.0
        assertEquals(listOf(1.0,2.0,3.0),catalog.film("old").recipe["film_base_density_rgb"])
        fail=true
        assertThrows(java.io.IOException::class.java) { catalog.saveFilm("new","New",emptyMap()) }
        assertThrows(java.io.IOException::class.java) { catalog.remove("film","old") }
        assertEquals("old",catalog.lastFilmId);assertEquals(listOf("old"),catalog.films().map { it.id })
    }
    @Test
    fun saveFilmAndScannerThenRestore() {
        val store = MemoryRecipeCatalogStore()
        val catalog = RecipeCatalog(store)
        catalog.saveFilm("pocket-neg", "Pocket", mapOf("contrast" to 1.2))
        catalog.saveScanner("neutral", "Neutral", mapOf("remove_base_mask" to true))
        assertEquals("pocket-neg", catalog.lastFilmId)
        assertEquals("neutral", catalog.lastScannerId)
        val restored = RecipeCatalog(store)
        assertEquals(1.2, (restored.film("pocket-neg").recipe["contrast"] as Number).toDouble(), 1e-12)
        assertEquals(true, restored.scanner("neutral").recipe["remove_base_mask"])
        assertEquals("pocket-neg", restored.lastFilmId)
    }

    @Test
    fun shareCodeSavesTheNamedSectionOnly() {
        val catalog = RecipeCatalog()
        val code = RecipeShareCodec.encode(
            mapOf(
                "film" to mapOf("name" to "Pocket Custom"),
                "scanner" to mapOf("scan_normalize" to true),
            ),
        )
        catalog.saveFromShareCode("pocket-neg", "Pocket", "film", code)
        assertEquals("Pocket Custom", catalog.film("pocket-neg").recipe["name"])
        assertTrue(catalog.scanners().isEmpty())
        catalog.saveFromShareCode("neutral", "Neutral", "scanner", code)
        assertEquals(true, catalog.scanner("neutral").recipe["scan_normalize"])
    }

    @Test
    fun invalidIdFailsClosed() {
        val catalog = RecipeCatalog()
        assertThrows(WorkflowException::class.java) {
            catalog.saveFilm("bad id", "x", emptyMap())
        }
        assertTrue(catalog.films().isEmpty())
    }

    @Test
    fun lastIdClearsWhenRemoved() {
        val catalog = RecipeCatalog()
        catalog.saveFilm("a", "A", emptyMap())
        catalog.remove("film", "a")
        assertNull(catalog.lastFilmId)
        assertThrows(WorkflowException::class.java) { catalog.film("a") }
    }

    @Test
    fun corruptCatalogFailsClosed() {
        val store = MemoryRecipeCatalogStore()
        store.writeCatalog("{".toByteArray())
        assertThrows(WorkflowException::class.java) { RecipeCatalog(store) }
    }
}
