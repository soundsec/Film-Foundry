package com.filmfoundry.platform

import com.filmfoundry.interchange.DataKernel
import com.filmfoundry.interchange.FloatNd
import com.filmfoundry.interchange.LatentArchive
import com.filmfoundry.workflow.FilmLibrary
import com.filmfoundry.workflow.RecipeCatalog
import com.filmfoundry.workflow.RollLifecycle
import org.junit.jupiter.api.Assertions.assertEquals
import org.junit.jupiter.api.Assertions.assertThrows
import org.junit.jupiter.api.Assertions.assertTrue
import org.junit.jupiter.api.Test

class MediumStoreTest {
    @Test
    fun customArchiveIsNotSilentlyBypassedByRowExecution() {
        val storage = object : StorageAdapter by MemoryStorageAdapter() {
            override fun rowScratchRoot() = java.io.File("unused-row-root")
        }
        val custom = object : com.filmfoundry.interchange.MediumArchive by com.filmfoundry.interchange.NpzMediumArchive() {}
        org.junit.jupiter.api.Assertions.assertNull(MediumStore(storage, custom).rowExecutionIo())
    }

    @Test
    fun privateNpzRoundTripKeepsCaptureRecord() {
        val storage = MemoryStorageAdapter()
        val store = MediumStore(storage)
        val n = 2 * 2 * 3
        val capture = mapOf(
            "kind" to "display_referred_srgb",
            "isp_processed" to true,
            "scene_referred_claim" to false,
        )
        val written = LatentArchive(
            arrays = mapOf(
                "halide" to FloatNd(intArrayOf(2, 2, 3), FloatArray(n) { 1f }),
                "developability" to FloatNd(intArrayOf(2, 2, 3), FloatArray(n) { 0.2f }),
                "metallic_silver" to FloatNd(intArrayOf(2, 2, 3), FloatArray(n)),
            ),
            metadata = mapOf(
                "kind" to DataKernel.LATENT_KIND,
                "stage" to DataKernel.LATENT_STAGE,
                "layer_count" to 3,
                "medium_family" to "film",
                "medium_process" to "negative",
                "image_polarity" to "negative",
                "view_mode" to "transmissive",
                "base_type" to "orange_mask",
                "color_system" to "color_negative_dye",
                "compatible_interpreters" to listOf("negative_scan"),
                DataKernel.CAPTURE_OBJECT_KEY to capture,
            ),
        )
        store.writeLatent("frame.darkroom_latent.npz", written)
        val restored = store.readLatent("frame.darkroom_latent.npz")
        val restoredCapture = restored.metadata[DataKernel.CAPTURE_OBJECT_KEY] as Map<*, *>
        assertEquals("display_referred_srgb", restoredCapture["kind"])
        assertTrue(restoredCapture["isp_processed"] as Boolean)
    }

    @Test
    fun wrongSuffixIsRejected() {
        val store = MediumStore(MemoryStorageAdapter())
        assertThrows(CaptureRejected::class.java) {
            store.writeLatent(
                "frame.jpg",
                LatentArchive(emptyMap(), emptyMap()),
            )
        }
    }

    @Test
    fun systemGalleryRejectsNpzAndAcceptsPng() {
        val storage = MemoryStorageAdapter()
        assertThrows(com.filmfoundry.workflow.WorkflowException::class.java) {
            storage.publishGallery("frame.darkroom_negative.npz", "image/png", byteArrayOf(1))
        }
        storage.publishGallery("frame.png", "image/png", byteArrayOf(137.toByte(), 80, 78, 71))
    }

    @Test
    fun libraryIndexRoundTripKeepsResultsAfterDiscard() {
        val storage = MemoryStorageAdapter()
        storage.writePrivate("keep.darkroom_latent.npz", byteArrayOf(1, 2, 3))
        val library = FilmLibrary(PrivateLibraryIndexStore(storage))
        library.loadRoll("roll-e", 12, emptyMap())
        library.commitCameraShot("roll-e", 1, "keep.darkroom_latent.npz")
        library.seal("roll-e")
        library.recordDevelopedResult(
            "kept",
            "roll-e",
            1,
            "kept.darkroom_negative.npz",
            emptyMap(),
        )
        library.discardRoll("roll-e") { path -> storage.deletePrivate(path) }
        assertTrue(!storage.existsPrivate("keep.darkroom_latent.npz"))
        assertTrue(storage.existsPrivate(DataKernel.PHONE_LIBRARY_INDEX_FILENAME))
        val restored = FilmLibrary(PrivateLibraryIndexStore(storage))
        assertEquals(RollLifecycle.DISCARDED, restored.roll("roll-e").lifecycle)
        assertEquals("kept.darkroom_negative.npz", restored.developedResults().single().opticalPath)
        assertTrue(!restored.developedResults().single().sourceRollPresent)
        assertThrows(com.filmfoundry.workflow.WorkflowException::class.java) {
            storage.publishGallery(
                DataKernel.PHONE_LIBRARY_INDEX_FILENAME,
                "application/json",
                storage.readPrivate(DataKernel.PHONE_LIBRARY_INDEX_FILENAME),
            )
        }
    }

    @Test
    fun recipeCatalogStaysPrivateAndOutOfGallery() {
        val storage = MemoryStorageAdapter()
        val catalog = RecipeCatalog(PrivateRecipeCatalogStore(storage))
        catalog.saveFilm("pocket-neg", "Pocket", mapOf("contrast" to 1.1))
        assertTrue(storage.existsPrivate(DataKernel.PHONE_RECIPE_CATALOG_FILENAME))
        val restored = RecipeCatalog(PrivateRecipeCatalogStore(storage))
        assertEquals(1.1, (restored.film("pocket-neg").recipe["contrast"] as Number).toDouble(), 1e-12)
        assertThrows(com.filmfoundry.workflow.WorkflowException::class.java) {
            storage.publishGallery(
                DataKernel.PHONE_RECIPE_CATALOG_FILENAME,
                "application/json",
                storage.readPrivate(DataKernel.PHONE_RECIPE_CATALOG_FILENAME),
            )
        }
    }
}
