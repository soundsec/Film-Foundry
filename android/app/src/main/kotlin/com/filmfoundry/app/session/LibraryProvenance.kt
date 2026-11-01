package com.filmfoundry.app.session

import com.filmfoundry.interchange.RecipeShareCodec
import com.filmfoundry.platform.SavedScan
import com.filmfoundry.workflow.*

/** A read-only view of exact resource identities. A missing layer never selects another result. */
data class LibraryOrigin(val rollId: String,val rollLabel: String,val frameIndex: Int,val resultId: String,
    val exposure: ResourceAvailability,val developed: ResourceAvailability,val rollListed: Boolean,
    val sourceRecipe: Map<String,Any?>,val scanId: String?=null,val scanTime: String?=null) {
    val batchId get()=RollLibraryContents.batchId(resultId)
}

class LibraryProvenance(private val library: FilmLibrary,private val exists: (String)->Boolean) {
    fun resolve(rollId: String,frameIndex: Int,resultId: String,saved: SavedScan?=null): LibraryOrigin {
        require(saved==null || saved.rollId==rollId && saved.frameIndex==frameIndex && saved.resultId==resultId)
        val origin=ResourceOrigins.find(library,rollId,frameIndex,resultId,exists)
        val roll=library.allRolls().firstOrNull { it.rollId==rollId }
        // The scanner recipe can contain unrelated draft material/development fields.
        // Only the actual developed result or its saved snapshot establishes that history.
        val actual=origin.result?.developRecipe ?: saved?.sourceDevelopRecipeShare?.let {
            runCatching { RecipeShareCodec.decode(it) }.getOrNull()
        }.orEmpty()
        return LibraryOrigin(rollId,roll?.label?.takeIf { it.isNotBlank() } ?: rollId,frameIndex,resultId,
            origin.exposure,origin.developed,roll!=null && !roll.catalogRemoved,actual,saved?.id,saved?.createdAt)
    }
}
