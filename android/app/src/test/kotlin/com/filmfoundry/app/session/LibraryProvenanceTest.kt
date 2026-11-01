package com.filmfoundry.app.session

import com.filmfoundry.app.ui.LibraryInfoPresentation
import com.filmfoundry.interchange.RecipeShareCodec
import com.filmfoundry.platform.SavedScan
import com.filmfoundry.workflow.*
import org.junit.jupiter.api.Assertions.*
import org.junit.jupiter.api.Test

class LibraryProvenanceTest {
    @Test fun sameFrameKeepsItsExactBatchAndSavedDevelopmentHistoryAcrossIndependentDeletion() {
        val library=FilmLibrary();library.loadRoll("roll",12,emptyMap());library.renameRoll("roll","旅行")
        library.commitCameraShot("roll",1,"latent.darkroom_latent.npz");library.seal("roll")
        val first="aaaaaaaa-1234-4234-8234-123456789abc-1"
        val second="bbbbbbbb-1234-4234-8234-123456789abc-1"
        val actual=mapOf("film" to mapOf("name" to "original"),"develop" to mapOf("time_min" to 4))
        library.recordDevelopedResult(first,"roll",1,"a.darkroom_negative.npz",emptyMap())
        library.recordDevelopedResult(second,"roll",1,"b.darkroom_negative.npz",actual)
        val files=mutableSetOf("latent.darkroom_latent.npz","a.darkroom_negative.npz","b.darkroom_negative.npz")
        val provenance=LibraryProvenance(library,files::contains)
        val photo=SavedScan("photo",second,"roll",1,1280,853,"2026-10-29T01:00:00Z",
            RecipeShareCodec.encode(mapOf("film" to mapOf("name" to "unrelated scan draft"))),
            sourceDevelopRecipeShare=RecipeShareCodec.encode(actual))
        fun resolve()=provenance.resolve("roll",1,second,photo)
        val before=resolve();assertEquals(actual,before.sourceRecipe)
        assertTrue(LibraryInfoPresentation.origin(before,false).contains("旅行"))
        val label=LibraryInfoPresentation.batch(before.batchId,false)
        library.removeDevelopedResult(first)
        assertEquals(label,LibraryInfoPresentation.batch(resolve().batchId,false))
        files.remove("b.darkroom_negative.npz");assertEquals(ResourceAvailability.MISSING_FILE,resolve().developed)
        library.removeDevelopedResult(second)
        assertEquals(actual,resolve().sourceRecipe);assertEquals(ResourceAvailability.AVAILABLE,resolve().exposure)
        assertTrue(provenance.resolve("roll",1,second,photo.copy(sourceDevelopRecipeShare=null)).sourceRecipe.isEmpty())
        library.removeRoll("roll")
        assertFalse(resolve().rollListed);assertEquals("旅行",resolve().rollLabel)
        assertEquals(ResourceAvailability.REMOVED,resolve().exposure);assertEquals(actual,resolve().sourceRecipe)
        assertThrows(IllegalArgumentException::class.java) { provenance.resolve("roll",2,second,photo) }
    }
}
