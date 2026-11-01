package com.filmfoundry.app.ui

import com.filmfoundry.interchange.RecipeShareCodec
import com.filmfoundry.workflow.FilmLibrary
import com.filmfoundry.workflow.RecipeCatalog
import org.junit.jupiter.api.Assertions.assertEquals
import org.junit.jupiter.api.Test

class RecipeIntakeTest {
    @Test
    fun savedFilmStockLoadsARoll() {
        val catalog = RecipeCatalog()
        val code = RecipeShareCodec.encode(
            mapOf(
                "film" to mapOf("name" to "Pocket Custom", "contrast" to 1.2),
                "scanner" to mapOf("remove_base_mask" to true),
            ),
        )
        RecipeIntake.saveFromShareCode(catalog, "pocket-neg", "film", code, label = "Pocket")
        RecipeIntake.saveFromShareCode(catalog, "neutral", "scanner", code, label = "Neutral")
        val library = FilmLibrary()
        val roll = RecipeIntake.loadRollFromCatalog(
            library,
            catalog,
            rollId = "roll-a",
            filmId = "pocket-neg",
            capacity = 12,
            scannerId = "neutral",
        )
        assertEquals("Pocket Custom", roll.filmRecipe["name"])
        assertEquals(true, roll.scannerRecipe["remove_base_mask"])
        assertEquals("Pocket", roll.label)
    }
}
