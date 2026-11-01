package com.filmfoundry.workflow

import org.junit.jupiter.api.Assertions.*
import org.junit.jupiter.api.Test

class ResourceRetentionTest {
    @Test fun sourceReleaseResultRemovalAndDiscardAreIndependentAndRollbackOnIndexFailure() {
        val memory=MemoryLibraryIndexStore();var fail=false
        val index=object: LibraryIndexStore by memory { override fun writeIndex(bytes: ByteArray) { if(fail) throw java.io.IOException("index failed");memory.writeIndex(bytes) } }
        val library=FilmLibrary(index);val roll=library.loadRoll("roll",12,mapOf("contrast" to 1.3))
        library.commitCameraShot("roll",1,"roll/1.darkroom_latent.npz")
        roll.frames[0].capturePath="roll/frame_01.capture/receipt.json";roll.frames[0].captureExposures=2
        library.seal("roll")
        val first=library.recordDevelopedResult("first","roll",1,"results/first.darkroom_negative.npz",emptyMap())
        library.recordDevelopedResult("second","roll",1,"results/second.darkroom_negative.npz",emptyMap())
        fail=true
        assertThrows(java.io.IOException::class.java) { library.releaseCaptureSources("roll") }
        assertEquals(2,roll.frames[0].captureExposures)
        assertThrows(java.io.IOException::class.java) { library.removeDevelopedResult(first.resultId) }
        var deleted=false
        assertThrows(java.io.IOException::class.java) { library.discardRoll("roll") { deleted=true } }
        assertFalse(deleted);assertEquals(RollLifecycle.SEALED,roll.lifecycle)
        assertEquals("roll/1.darkroom_latent.npz",roll.frames[0].latentPath);assertTrue(first.sourceRollPresent)
        fail=false
        library.releaseCaptureSources("roll")
        val restored=FilmLibrary(index);assertNull(restored.roll("roll").frames[0].capturePath)
        assertEquals(2,restored.developedResults().size)
        assertEquals("roll/1.darkroom_latent.npz",restored.roll("roll").frames[0].latentPath)
        library.removeDevelopedResult("first");assertEquals(listOf("second"),library.resultsForRoll("roll").map { it.resultId })
        library.discardRoll("roll");assertFalse(library.developedResults().single().sourceRollPresent)
    }
    @Test fun catalogRenamePreservesDefinitionAndSelectionWhileLoadedMaterialRemainsFrozen() {
        val memory=MemoryRecipeCatalogStore();val catalog=RecipeCatalog(memory)
        catalog.saveFilm("first","first",mapOf("contrast" to 1.2));catalog.saveFilm("second","second",mapOf("contrast" to .9))
        val library=FilmLibrary();val roll=library.loadRoll("loaded",12,catalog.film("first").recipe)
        catalog.rename("film","first"," renamed ")
        val restored=RecipeCatalog(memory)
        assertEquals("renamed",restored.film("first").label);assertEquals("second",restored.lastFilmId)
        assertEquals(1.2,roll.filmRecipe["contrast"]);catalog.remove("film","first");assertEquals(1.2,roll.filmRecipe["contrast"])
    }
}
