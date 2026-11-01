package com.filmfoundry.app.session

import com.filmfoundry.platform.*
import com.filmfoundry.workflow.*
import kotlinx.coroutines.runBlocking
import org.junit.jupiter.api.Assertions.*
import org.junit.jupiter.api.Test

class BatchScanSessionTest {
    @Test fun legacyFrozenScanTaskKeepsItsMathAndReceiptsWhileNewTasksUseResultDefaults()=runBlocking {
        val f=Fixture();val base=f.session.create("roll",f.frames,emptyMap())
        f.session.abandon("roll",base.id)
        val old=base.copy(id=java.util.UUID.randomUUID().toString(),observationModel=ObservationImplementation.LEGACY)
        f.tasks.create(old)
        var calls=0
        f.session.run("roll",old.id) { _,runtime,_,out ->
            assertEquals(com.filmfoundry.interchange.PhoneScanModels.LEGACY,runtime[com.filmfoundry.interchange.PhoneScanModels.OPTION])
            assertEquals(ObservationImplementation.LEGACY,ObservationImplementation.forRecipe(runtime));calls++;out.write(1);3 to 2
        }
        assertEquals(2,calls)
        assertTrue(f.scans.listNewest().all { it.observationModel==ObservationImplementation.LEGACY })
        val next=f.session.create("roll",f.frames,emptyMap())
        assertEquals(ObservationImplementation.RANGE_FIRST,next.observationModel)
        assertFalse(ObservationImplementation.matches(ObservationImplementation.LEGACY,next.observationModel))
        Unit
    }
    private class Fixture {
        val storage=MemoryStorageAdapter();val library=FilmLibrary(MemoryLibraryIndexStore())
        val scans=PrivateScanStore(storage);val tasks=PrivateScanTaskStore(storage)
        val session=BatchScanSession(library,storage,scans,tasks)
        val frames: List<DevelopedResult>
        init {
            library.loadRoll("roll",12,emptyMap())
            for(i in 1..2) library.commitCameraShot("roll",i,"$i.darkroom_latent.npz")
            library.seal("roll")
            frames=(1..2).map { i -> library.recordDevelopedResult("batch-$i","roll",i,"$i.darkroom_negative.npz",emptyMap()).also {
                storage.writePrivate(it.opticalPath,byteArrayOf(i.toByte()))
            } }
        }
    }
    @Test fun pauseRestoreKeepsRecipeAndDoesNotDuplicateTheCompletedPhoto()=runBlocking {
        val f=Fixture();val recipe=mutableMapOf<String,Any?>("scanner" to mapOf("scan_saturation" to .7))
        val task=f.session.create("roll",f.frames,recipe);recipe["scanner"]=mapOf("scan_saturation" to .1)
        var stop=false;var renders=0
        val paused=f.session.run("roll",task.id,WorkflowControl(cancelRequested={stop}),{ stop=true }) { _,settings,_,out ->
            assertEquals(.7,((settings["scanner"] as Map<*,*>)["scan_saturation"] as Number).toDouble());renders++;out.write(7);3 to 2
        }
        assertEquals("paused",paused.status);assertEquals(1,paused.completed);assertFalse(f.library.hasActiveRollJob("roll"))
        val first=f.scans.listNewest().single().id
        val restored=BatchScanSession(f.library,f.storage,PrivateScanStore(f.storage),PrivateScanTaskStore(f.storage))
        val completed=restored.run("roll",task.id) { _,_,_,out -> renders++;out.write(8);3 to 2 }
        assertEquals("completed",completed.status);assertEquals(2,completed.completed);assertEquals(2,renders)
        assertEquals(2,f.scans.count());assertTrue(f.scans.listNewest().any { it.id==first });Unit
    }
    @Test fun missingNegativeAndRemovedCompletedPhotoAreReportedAndNeverRecreated()=runBlocking {
        val f=Fixture();val task=f.session.create("roll",f.frames,emptyMap());var stop=false
        f.session.run("roll",task.id,WorkflowControl(cancelRequested={stop}),{ stop=true }) { _,_,_,out -> out.write(1);3 to 2 }
        val saved=f.scans.listNewest().single();f.scans.remove(saved.id);f.storage.deletePrivate(saved.imagePath)
        try { f.session.run("roll",task.id) { _,_,_,_->error("must not render") };fail<Unit>("missing output must fail") } catch(e: IllegalArgumentException) { assertTrue(e.message!!.contains("removed")) }
        assertEquals(0,f.scans.count());assertFalse(f.storage.existsPrivate(saved.imagePath))
        f.session.abandon("roll",task.id)
        val next=f.session.create("roll",f.frames,emptyMap());f.library.removeDevelopedResult(f.frames[0].resultId)
        try { f.session.run("roll",next.id) { _,_,_,_->error("must not render") };fail<Unit>("missing input must fail") } catch(e: IllegalStateException) { assertTrue(e.message!!.contains("missing")) }
        assertEquals("failed",f.tasks.latest("roll")!!.status);Unit
    }
    @Test fun photoCommitBeforeCheckpointIsRecoveredAndDiscardedExposureRollStillScans()=runBlocking {
        val f=Fixture();val task=f.session.create("roll",f.frames,emptyMap());val first=task.frames[0]
        f.scans.saveOwnedFrame(first.scanId,task.id,f.frames[0],emptyMap()) { it.write(1);3 to 2 }
        assertEquals(0,f.tasks.read("roll",task.id).completed)
        f.library.discardRoll("roll");var renders=0
        val result=f.session.run("roll",task.id) { _,_,_,out -> renders++;out.write(2);3 to 2 }
        assertEquals(1,renders);assertEquals(2,result.completed);assertEquals(2,f.scans.count());Unit
    }
    @Test fun intentionalPhotoDeletionBeforeTaskCheckpointCannotBeResurrected()=runBlocking {
        val f=Fixture();val task=f.session.create("roll",f.frames,emptyMap());val first=task.frames[0]
        val saved=f.scans.saveOwnedFrame(first.scanId,task.id,f.frames[0],emptyMap()) { it.write(1);3 to 2 }
        f.scans.remove(saved.id);f.storage.deletePrivate(saved.imagePath);f.storage.deletePrivate("scans/${saved.id}/scan.json")
        try { f.session.run("roll",task.id) { _,_,_,_->error("deleted photo must not render") };fail<Unit>("must report deletion") }
        catch(e: IllegalArgumentException) { assertTrue(e.message!!.contains("removed")) }
        assertEquals(0,f.scans.count());assertFalse(f.storage.existsPrivate(saved.imagePath));Unit
    }
    @Test fun activeScanPreventsNegativeRemovalBeforeResourceStaging()=runBlocking {
        val f=Fixture();val task=f.session.create("roll",f.frames,emptyMap())
        f.session.run("roll",task.id) { frame,_,_,out ->
            assertTrue(f.library.hasActiveRollJob("roll"))
            assertThrows(IllegalArgumentException::class.java) { f.library.removeDevelopedResult(frame.resultId) }
            out.write(1);3 to 2
        }
        assertFalse(f.library.hasActiveRollJob("roll"));assertEquals(2,f.library.developedResults().size);Unit
    }
}
