package com.filmfoundry.interchange

import org.junit.jupiter.api.Assertions.assertEquals
import org.junit.jupiter.api.Assertions.assertNull
import org.junit.jupiter.api.Assertions.assertThrows
import org.junit.jupiter.api.Assertions.assertTrue
import org.junit.jupiter.api.Test
import java.nio.charset.StandardCharsets

class RecipeCatalogCodecTest {
    @Test
    fun emptyCatalogRoundTrip() {
        val encoded = RecipeCatalogCodec.encode(RecipeCatalogDocument(emptyList(), emptyList()))
        val decoded = RecipeCatalogCodec.decode(encoded)
        assertTrue(decoded.films.isEmpty())
        assertTrue(decoded.scanners.isEmpty())
        assertNull(decoded.lastFilmId)
        val text = String(encoded, StandardCharsets.UTF_8)
        assertTrue(text.contains(DataKernel.PHONE_RECIPE_CATALOG_KIND))
        assertTrue(!text.contains(".npz"))
    }

    @Test
    fun filmAndScannerRoundTrip() {
        val encoded = RecipeCatalogCodec.encode(
            RecipeCatalogDocument(
                films = listOf(entry("pocket-neg", "film", mapOf("contrast" to 1.08))),
                scanners = listOf(entry("neutral", "scanner", mapOf("remove_base_mask" to true))),
                lastFilmId = "pocket-neg",
                lastScannerId = "neutral",
            ),
        )
        val decoded = RecipeCatalogCodec.decode(encoded)
        assertEquals("pocket-neg", decoded.lastFilmId)
        assertEquals(1.08, ((decoded.films.single()["recipe"] as Map<*, *>)["contrast"] as Number).toDouble(), 1e-12)
        assertEquals(true, (decoded.scanners.single()["recipe"] as Map<*, *>)["remove_base_mask"])
    }

    @Test
    fun lastIdMustExist() {
        assertThrows(RecipeCatalogException::class.java) {
            RecipeCatalogCodec.encode(
                RecipeCatalogDocument(
                    films = listOf(entry("a", "film", emptyMap())),
                    scanners = emptyList(),
                    lastFilmId = "missing",
                ),
            )
        }
    }

    @Test
    fun npzKeyInRecipeFailsClosed() {
        assertThrows(RecipeCatalogException::class.java) {
            RecipeCatalogCodec.encode(
                RecipeCatalogDocument(
                    films = listOf(entry("bad", "film", mapOf("frame.darkroom_latent.npz" to 1))),
                    scanners = emptyList(),
                ),
            )
        }
    }

    private fun entry(id: String, kind: String, recipe: Map<String, Any?>): Map<String, Any?> {
        return linkedMapOf(
            "id" to id,
            "kind" to kind,
            "label" to id,
            "recipe" to recipe,
        )
    }
}
