package com.filmfoundry.workflow

import com.filmfoundry.interchange.DataKernel
import org.junit.jupiter.api.Assertions.assertEquals
import org.junit.jupiter.api.Assertions.assertFalse
import org.junit.jupiter.api.Assertions.assertNull
import org.junit.jupiter.api.Assertions.assertThrows
import org.junit.jupiter.api.Assertions.assertTrue
import org.junit.jupiter.api.Test

class FilmLibraryTest {
    private val library = FilmLibrary()

    @Test fun rollNamesPersistIndependentlyOfIdentityAndRollBackOnWriteFailure() {
        val memory=MemoryLibraryIndexStore();var fail=false
        val store=object: LibraryIndexStore by memory { override fun writeIndex(bytes: ByteArray) { if(fail) throw java.io.IOException("failed");memory.writeIndex(bytes) } }
        val live=FilmLibrary(store);val roll=live.loadRoll("original-id",12,mapOf("contrast" to 1.3),label="old")
        live.commitCameraShot(roll.rollId,1,"original/path.darkroom_latent.npz")
        live.renameRoll(roll.rollId,"  山间  ")
        assertEquals("山间",FilmLibrary(store).roll(roll.rollId).label)
        assertEquals("original/path.darkroom_latent.npz",roll.frames[0].latentPath)
        assertEquals(1.3,roll.filmRecipe["contrast"])
        fail=true;assertThrows(java.io.IOException::class.java) { live.renameRoll(roll.rollId,"new") }
        assertEquals("山间",roll.label)
        assertThrows(IllegalArgumentException::class.java) { live.renameRoll(roll.rollId,"bad\nname") }
        val t=java.time.LocalDateTime.of(2026,10,17,23,0,1)
        assertEquals("20261017-230001-3",RollNaming.atTime(t,listOf("20261017-230001","20261017-230001-2")))
    }

    @Test
    fun loadedMaterialsAreDetachedAndReadOnlyIncludingNestedValuesAndRestores() {
        val index=MemoryLibraryIndexStore()
        val live=FilmLibrary(index)
        val values=mutableListOf<Any?>(1.0,2.0)
        val nested=mutableMapOf<String,Any?>("values" to values)
        val recipe=mutableMapOf<String,Any?>("profile" to nested,"contrast" to 1.08)
        val roll=live.loadRoll("frozen",12,recipe)
        values[0]=9.0; nested["other"]=true; recipe["contrast"]=3.0
        assertEquals(1.08,roll.filmRecipe["contrast"])
        val profile=roll.filmRecipe["profile"] as Map<*,*>
        assertEquals(listOf(1.0,2.0),profile["values"])
        assertFalse(profile.containsKey("other"))
        assertThrows(UnsupportedOperationException::class.java) { (profile["values"] as MutableList<Any?>)[0]=7 }
        val restored=FilmLibrary(index).roll("frozen")
        assertThrows(UnsupportedOperationException::class.java) {
            (restored.filmRecipe as MutableMap<String,Any?>)["contrast"]=4.0
        }
    }

    @Test
    fun failedIndexWritesDoNotAdvanceLoadShotSealOrResultState() {
        val saved=MemoryLibraryIndexStore()
        var fail=false
        val index=object: LibraryIndexStore by saved {
            override fun writeIndex(bytes: ByteArray) { if(fail) throw java.io.IOException("index write failed"); saved.writeIndex(bytes) }
        }
        val live=FilmLibrary(index)
        fail=true
        assertThrows(java.io.IOException::class.java) { live.loadRoll("failure",12,emptyMap()) }
        assertTrue(live.shootingRolls().isEmpty())
        fail=false; val roll=live.loadRoll("failure",12,emptyMap())
        fail=true
        assertThrows(java.io.IOException::class.java) { live.commitCameraShot("failure",1,"f1.darkroom_latent.npz") }
        assertEquals(FrameState.EMPTY,roll.frames[0].state)
        assertNull(roll.frames[0].latentPath)
        assertEquals(RollLifecycle.LOADING,roll.lifecycle)
        assertEquals(0,FilmLibrary(index).roll("failure").latentCount())
        fail=false; live.commitCameraShot("failure",1,"f1.darkroom_latent.npz")
        fail=true
        assertThrows(java.io.IOException::class.java) { live.seal("failure") }
        assertEquals(RollLifecycle.SHOOTING,roll.lifecycle)
        assertThrows(java.io.IOException::class.java) { kotlinx.coroutines.runBlocking {
            live.sealWithSavedShots("failure") { mapOf(2 to "f2.darkroom_latent.npz") }
        } }
        assertEquals(RollLifecycle.SHOOTING,roll.lifecycle)
        assertEquals(1,roll.latentCount())
        assertEquals(FrameState.EMPTY,roll.frames[1].state)
        assertNull(roll.frames[1].latentPath)
        fail=false; live.seal("failure")
        fail=true
        assertThrows(java.io.IOException::class.java) { live.recordDevelopedResult("output","failure",1,"out.darkroom_negative.npz",emptyMap()) }
        assertTrue(live.developedResults().isEmpty())
        assertTrue(FilmLibrary(index).developedResults().isEmpty())
    }

    @Test
    fun sequentialAdvanceRetriesTheSameSlotAndStopsAtCapacity() = kotlinx.coroutines.runBlocking {
        library.loadRoll("advance",12,emptyMap())
        var writes = 0
        assertThrows(IllegalStateException::class.java) { kotlinx.coroutines.runBlocking {
            library.captureNextCameraShot("advance") { _, frame ->
                assertEquals(1,frame.frameIndex); error("camera failed")
            }
        } }
        repeat(12) { index ->
            val shot = library.captureNextCameraShot("advance") { _, frame ->
                writes++; assertEquals(index+1,frame.frameIndex)
                "f${frame.frameIndex}.darkroom_latent.npz"
            }
            assertEquals(index+1,shot.frameIndex)
        }
        assertThrows(WorkflowException::class.java) { kotlinx.coroutines.runBlocking {
            library.captureNextCameraShot("advance") { _, _ -> writes++; "overflow.darkroom_latent.npz" }
        } }
        assertEquals(12,writes)
        library.seal("advance")
        assertTrue(library.shootingRolls().isEmpty())
    }

    @Test
    fun loadRollAcceptsFiniteStockCapacities() {
        library.loadRoll("r12", 12, emptyMap(), label = "pocket")
        library.loadRoll("r24", 24, emptyMap())
        library.loadRoll("r36", 36, emptyMap())
        assertThrows(WorkflowException::class.java) {
            library.loadRoll("r13", 13, emptyMap())
        }
        assertEquals(listOf(8, 10, 12, 16, 24, 36, 48, 72), DataKernel.PHONE_ROLL_CAPACITY_VALUES)
    }

    @Test
    fun cameraShotFreezesLatentAndTags() {
        library.loadRoll("roll-a", 12, mapOf("name" to "custom"))
        val frame = library.commitCameraShot(
            "roll-a",
            1,
            "frame.darkroom_latent.npz",
            labels = listOf("window"),
        )
        assertEquals(FrameState.LATENT, frame.state)
        assertEquals(listOf("window"), frame.labels)
        assertEquals(RollLifecycle.SHOOTING, library.roll("roll-a").lifecycle)
        assertThrows(WorkflowException::class.java) {
            library.commitCameraShot("roll-a", 1, "dup.darkroom_latent.npz")
        }
        assertThrows(WorkflowException::class.java) {
            library.commitCameraShot("roll-a", 13, "overflow.darkroom_latent.npz")
        }
        assertThrows(WorkflowException::class.java) {
            library.commitCameraShot("roll-a", 2, "frame.jpg")
        }
    }

    @Test
    fun galleryImportIsNotAFrame() {
        library.loadRoll("roll-b", 24, emptyMap())
        val error = assertThrows(WorkflowException::class.java) {
            library.commitGalleryImport("roll-b", 1, "photo.jpg")
        }
        assertTrue(error.message!!.contains("camera stills only"))
        assertEquals(FrameState.EMPTY, library.roll("roll-b").frames[0].state)
        val preview = assertThrows(WorkflowException::class.java) {
            library.commitPreviewAsShot("roll-b", 1)
        }
        assertTrue(preview.message!!.contains("preview"))
    }

    @Test
    fun sealThenDevelopLeavesSourceLatentAndAllowsManyResults() {
        library.loadRoll("roll-c", 36, emptyMap(), label = "night")
        library.commitCameraShot("roll-c", 1, "f1.darkroom_latent.npz", labels = listOf("neon"))
        library.seal("roll-c")
        assertEquals(1, library.sealedRolls().size)
        val first = library.recordDevelopedResult(
            "result-a",
            "roll-c",
            1,
            "a.darkroom_negative.npz",
            mapOf("time_min" to 8.0),
        )
        val second = library.recordDevelopedResult(
            "result-b",
            "roll-c",
            1,
            "b.darkroom_negative.npz",
            mapOf("time_min" to 12.0),
        )
        val source = library.roll("roll-c").frames[0]
        assertEquals(FrameState.LATENT, source.state)
        assertEquals("f1.darkroom_latent.npz", source.latentPath)
        assertEquals(2, library.resultsForRoll("roll-c").size)
        assertTrue(first.sourceRollPresent)
        assertTrue(second.sourceRollPresent)
        assertEquals("undeveloped_roll", DataKernel.PHONE_LIBRARY_KINDS[0])
        assertEquals("developed_result", DataKernel.PHONE_LIBRARY_KINDS[1])
    }

    @Test
    fun cannotDevelopUntilSealedAndCannotUndevelop() {
        library.loadRoll("roll-d", 12, emptyMap())
        library.commitCameraShot("roll-d", 1, "f1.darkroom_latent.npz")
        assertThrows(WorkflowException::class.java) {
            library.recordDevelopedResult(
                "early",
                "roll-d",
                1,
                "early.darkroom_negative.npz",
                emptyMap(),
            )
        }
        library.seal("roll-d")
        val error = assertThrows(WorkflowException::class.java) {
            library.undevelop("anything")
        }
        assertTrue(error.message!!.contains("cannot recreate"))
    }

    @Test
    fun discardRollFreesLatentsAndKeepsResults() {
        val deleted = mutableListOf<String>()
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
        library.discardRoll("roll-e") { path -> deleted.add(path) }
        assertEquals(listOf("keep.darkroom_latent.npz"), deleted)
        assertEquals(RollLifecycle.DISCARDED, library.roll("roll-e").lifecycle)
        assertTrue(library.sealedRolls().isEmpty())
        assertNull(library.roll("roll-e").frames[0].latentPath)
        val result = library.developedResults().single()
        assertEquals("kept.darkroom_negative.npz", result.opticalPath)
        assertFalse(result.sourceRollPresent)
        assertThrows(WorkflowException::class.java) {
            library.recordDevelopedResult(
                "after-discard",
                "roll-e",
                1,
                "no.darkroom_negative.npz",
                emptyMap(),
            )
        }
    }

    @Test
    fun sealRequiresAShotAndStopsShooting() {
        library.loadRoll("roll-f", 12, emptyMap())
        assertThrows(WorkflowException::class.java) { library.seal("roll-f") }
        library.commitCameraShot("roll-f", 2, "f2.darkroom_latent.npz")
        library.seal("roll-f")
        assertThrows(WorkflowException::class.java) {
            library.commitCameraShot("roll-f", 3, "f3.darkroom_latent.npz")
        }
    }

    @Test
    fun indexStoreRestoresSealedRollAndResults() {
        val store = MemoryLibraryIndexStore()
        val live = FilmLibrary(store)
        live.loadRoll("roll-c", 12, mapOf("name" to "custom"), label = "night")
        live.commitCameraShot("roll-c", 1, "f1.darkroom_latent.npz", labels = listOf("neon"))
        live.seal("roll-c")
        live.recordDevelopedResult(
            "result-a",
            "roll-c",
            1,
            "a.darkroom_negative.npz",
            mapOf("time_min" to 8.0),
        )
        val restored = FilmLibrary(store)
        val roll = restored.roll("roll-c")
        assertEquals(RollLifecycle.SEALED, roll.lifecycle)
        assertEquals("night", roll.label)
        assertEquals("custom", roll.filmRecipe["name"])
        assertEquals(FrameState.LATENT, roll.frames[0].state)
        assertEquals("f1.darkroom_latent.npz", roll.frames[0].latentPath)
        assertEquals(listOf("neon"), roll.frames[0].labels)
        assertEquals(1, restored.sealedRolls().size)
        val result = restored.developedResults().single()
        assertEquals("a.darkroom_negative.npz", result.opticalPath)
        assertTrue(result.sourceRollPresent)
        assertEquals(8.0, (result.developRecipe["time_min"] as Number).toDouble(), 0.0)
    }

    @Test
    fun corruptIndexFailsClosed() {
        val store = MemoryLibraryIndexStore()
        store.writeIndex("{".toByteArray())
        val error = assertThrows(WorkflowException::class.java) {
            FilmLibrary(store)
        }
        assertEquals("invalid_request", error.code)
    }

    @Test
    fun discardPersistsWithoutLatentPaths() {
        val store = MemoryLibraryIndexStore()
        val live = FilmLibrary(store)
        live.loadRoll("roll-e", 12, emptyMap())
        live.commitCameraShot("roll-e", 1, "keep.darkroom_latent.npz")
        live.seal("roll-e")
        live.recordDevelopedResult(
            "kept",
            "roll-e",
            1,
            "kept.darkroom_negative.npz",
            emptyMap(),
        )
        live.discardRoll("roll-e")
        val restored = FilmLibrary(store)
        assertEquals(RollLifecycle.DISCARDED, restored.roll("roll-e").lifecycle)
        assertEquals(FrameState.EMPTY, restored.roll("roll-e").frames[0].state)
        assertNull(restored.roll("roll-e").frames[0].latentPath)
        assertFalse(restored.developedResults().single().sourceRollPresent)
        assertTrue(restored.sealedRolls().isEmpty())
    }
}
