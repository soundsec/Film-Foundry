package com.filmfoundry.app.ui

import com.filmfoundry.interchange.RecipeShareCodec
import com.filmfoundry.workflow.FilmLibrary
import com.filmfoundry.workflow.RecipeCatalog
import com.filmfoundry.workflow.Roll

sealed class ProcessRoute {
    data object LoadRoll : ProcessRoute()
    data object Shoot : ProcessRoute()
    data object RollStrip : ProcessRoute()
    data object RollLibrary : ProcessRoute()
    data object Develop : ProcessRoute()
    data object ResultLibrary : ProcessRoute()
    data object Viewer : ProcessRoute()
    data object Scanner : ProcessRoute()
    data object Export : ProcessRoute()
}

object RecipeIntake {
    fun rollFromShareCode(rollId: String, code: String, capacity: Int, label: String = ""): Roll {
        val recipe = com.filmfoundry.workflow.frozenRecipe(RecipeShareCodec.decode(code))
        val film = recipe["film"] as? Map<*, *> ?: emptyMap<String, Any?>()
        return Roll(
            rollId = rollId,
            capacity = capacity,
            filmRecipe = film.entries.associate { it.key.toString() to it.value },
            developRecipe = (recipe["develop"] as? Map<*, *>)
                ?.entries
                ?.associate { it.key.toString() to it.value }
                ?: emptyMap(),
            scannerRecipe = (recipe["scanner"] as? Map<*, *>)
                ?.entries
                ?.associate { it.key.toString() to it.value }
                ?: emptyMap(),
            label = label,
        )
    }

    fun saveFromShareCode(
        catalog: RecipeCatalog,
        id: String,
        kind: String,
        code: String,
        label: String = "",
    ) = catalog.saveFromShareCode(id, label, kind, code)

    fun loadRollFromCatalog(
        library: FilmLibrary,
        catalog: RecipeCatalog,
        rollId: String,
        filmId: String,
        capacity: Int,
        label: String = "",
        scannerId: String? = null,
    ): Roll {
        val film = catalog.film(filmId)
        val scanner = scannerId?.let { catalog.scanner(it).recipe } ?: emptyMap()
        return library.loadRoll(
            rollId = rollId,
            capacity = capacity,
            filmRecipe = film.recipe,
            label = label.ifBlank { film.label },
            scannerRecipe = scanner,
        )
    }
}
