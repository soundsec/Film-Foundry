package com.filmfoundry.app.session

import com.filmfoundry.platform.*
import com.filmfoundry.workflow.*
import kotlinx.coroutines.runBlocking
import org.junit.jupiter.api.Assertions.*
import org.junit.jupiter.api.Test
import org.junit.jupiter.api.io.TempDir
import java.io.*
import java.nio.file.*

class LibraryResourceSessionTest {
    @TempDir lateinit var root: Path
    private val camera=object: StoredStillCamera {
        override fun saveStill(target: File): StoredStillInfo {
            target.writeBytes(byteArrayOf(1,2,3));return StoredStillInfo(3,2,CaptureRecord(CaptureKind.DISPLAY_REFERRED_SRGB,true,sourceEncoding="iec61966_2_1_srgb"))
        }
    }
    private fun capture(library: FilmLibrary,storage: StorageAdapter)=DeferredShootSession(library,MediumStore(storage),CaptureReceiptStore(root.toFile()),root.resolve("scratch").toFile(),openPixels= {
        object: StoredExposurePixels {
            override val width=3;override val height=2
            override fun encodedRows(start: Int,count: Int)=FloatArray(width*count*3) { .4f }
            override fun close()=Unit
        }
    })
    @Test fun deleteRollRemovesCatalogAndExposureFilesButRetainsIndependentNegativesAndPhotosAfterRestart() = runBlocking {
        val base=storage();var fail=false
        val storage=object: StorageAdapter by base {
            override fun writePrivate(relative: String,bytes: ByteArray) {
                if(fail && relative=="phone_library_index.json") throw IOException("index failed")
                base.writePrivate(relative,bytes)
            }
        }
        val library=FilmLibrary(PrivateLibraryIndexStore(storage));val medium=MediumStore(storage)
        library.loadRoll("roll",12,emptyMap());val shoot=capture(library,storage);shoot.capture("roll",camera);shoot.seal("roll")
        val tasks=PrivateDevelopmentTaskStore(storage);val development=RollDevelopmentSession(library,medium,tasks,shoot)
        development.create("roll","one",emptyMap());val negative=development.run("roll","one").single()
        val scans=PrivateScanStore(storage);val photo=scans.save(negative,3,2,emptyMap()) { it.write(byteArrayOf(7,8)) }
        val latent=library.roll("roll").frames.first().latentPath!!;val optical=storage.readPrivate(negative.opticalPath)
        val resources=LibraryResourceSession(library,storage,scans,medium,tasks)
        fail=true;assertThrows(IOException::class.java) { resources.removeRoll("roll") }
        assertEquals(listOf("roll"),library.visibleRolls().map { it.rollId });assertFalse(library.roll("roll").catalogRemoved)
        assertTrue(negative.sourceRollPresent);assertTrue(storage.existsPrivate(latent));assertFalse(resources.needsRecovery)
        fail=false;val removed=resources.removeSelected(rolls=listOf("roll"),removeRollCatalog=true)
        assertNull(removed.error);assertEquals(1,removed.completed);assertTrue(library.visibleRolls().isEmpty())
        assertFalse(storage.existsPrivate(latent));assertFalse(root.resolve("roll/frame_01.capture").toFile().exists())
        val restored=FilmLibrary(PrivateLibraryIndexStore(storage));assertTrue(restored.visibleRolls().isEmpty())
        assertTrue(restored.roll("roll").catalogRemoved);assertFalse(restored.developedResults().single().sourceRollPresent)
        assertArrayEquals(optical,storage.readPrivate(negative.opticalPath));assertEquals(listOf(photo),scans.listNewest())
        assertArrayEquals(byteArrayOf(7,8),storage.readPrivate(photo.imagePath))
        val origin=ResourceOrigins.find(restored,"roll",1,negative.resultId,storage::existsPrivate)
        assertEquals(ResourceAvailability.REMOVED,origin.exposure);assertEquals(ResourceAvailability.AVAILABLE,origin.developed)
        val rescan=restored.withObservationJob("roll") {
            OpticalPreviewSession().scan(medium.readOptical(negative.opticalPath),restored.roll("roll").scannerRecipe)
        }
        assertEquals(3,rescan.width);assertEquals(2,rescan.height);assertTrue(rescan.rgb.all { it.isFinite() })
        assertArrayEquals(optical,storage.readPrivate(negative.opticalPath))
        // Empty, unfinished and previously released rolls can also leave the user catalog.
        restored.loadRoll("empty",12,emptyMap())
        val second=LibraryResourceSession(restored,storage,scans,medium,tasks)
        assertTrue(second.removeRoll("empty"));assertTrue(restored.visibleRolls().isEmpty())
        restored.loadRoll("shooting",12,emptyMap());val again=capture(restored,storage);again.capture("shooting",camera)
        assertTrue(second.removeRoll("shooting"));assertTrue(restored.visibleRolls().isEmpty())
        restored.loadRoll("released",12,emptyMap());again.capture("released",camera);again.seal("released");assertTrue(second.discardRoll("released"))
        assertEquals(listOf("released"),restored.visibleRolls().map { it.rollId });assertTrue(second.removeRoll("released"))
        assertTrue(FilmLibrary(PrivateLibraryIndexStore(storage)).visibleRolls().isEmpty());assertFalse(second.needsRecovery)
        Unit
    }
    @Test fun selectionPreflightRejectsUnsealedRollBeforeDeletingAnyAndPhotosCommitIndependently() = runBlocking {
        val base=storage();var failOnSecond=false;var writes=0
        val storage=object: StorageAdapter by base {
            override fun writePrivate(relative: String,bytes: ByteArray) {
                if(failOnSecond && relative=="phone_scan_index.json" && ++writes==2) throw IOException("selected index write failed")
                base.writePrivate(relative,bytes)
            }
        }
        val library=FilmLibrary(PrivateLibraryIndexStore(storage));val medium=MediumStore(storage)
        library.loadRoll("roll",12,emptyMap());val shoot=capture(library,storage);shoot.capture("roll",camera);shoot.seal("roll")
        val disk=PrivateDevelopmentTaskStore(storage);val development=RollDevelopmentSession(library,medium,disk,shoot)
        development.create("roll","one",emptyMap());val negative=development.run("roll","one").single()
        library.loadRoll("unsealed",12,emptyMap())
        val scans=PrivateScanStore(storage)
        val photos=(1..3).map { n -> scans.save(negative,3,2,emptyMap()) { it.write(byteArrayOf(n.toByte())) } }
        val resources=LibraryResourceSession(library,storage,scans,medium,disk)
        val latent=library.roll("roll").frames.first().latentPath!!
        val denied=resources.removeSelected(rolls=listOf("roll","unsealed"))
        assertEquals(0,denied.completed);assertNotNull(denied.error)
        assertTrue(storage.existsPrivate(latent));assertEquals(RollLifecycle.SEALED,library.roll("roll").lifecycle)
        failOnSecond=true
        val partial=resources.removeSelected(photos=photos)
        assertEquals(1,partial.completed);assertEquals(3,partial.total);assertNotNull(partial.error)
        assertEquals(listOf(photos[2],photos[1]),scans.listNewest())
        assertFalse(storage.existsPrivate(photos[0].imagePath))
        assertArrayEquals(byteArrayOf(2),storage.readPrivate(photos[1].imagePath))
        assertTrue(storage.existsPrivate(latent));assertTrue(storage.existsPrivate(negative.opticalPath));assertFalse(resources.needsRecovery)
        failOnSecond=false
        val removed=resources.removeSelected(negatives=listOf(negative))
        assertEquals(1,removed.completed);assertNull(removed.error)
        assertTrue(storage.existsPrivate(latent));assertEquals(2,scans.count())
        Unit
    }
    @Test fun releaseCapturesThenRemoveNegativeAndSourceRollKeepsIndependentPhotosAndOtherResults() = runBlocking {
        val storage=storage();val library=FilmLibrary(PrivateLibraryIndexStore(storage));val medium=MediumStore(storage)
        library.loadRoll("roll",12,emptyMap());val shoot=capture(library,storage);shoot.capture("roll",camera);shoot.seal("roll")
        val disk=PrivateDevelopmentTaskStore(storage);val development=RollDevelopmentSession(library,medium,disk,shoot)
        development.create("roll","one",emptyMap());val first=development.run("roll","one").single()
        development.create("roll","two",emptyMap());val second=development.run("roll","two").single()
        val scans=PrivateScanStore(storage);val a=scans.save(first,3,2,emptyMap()) { it.write(byteArrayOf(7)) }
        val b=scans.save(first,3,2,emptyMap()) { it.write(byteArrayOf(8)) }
        val session=LibraryResourceSession(library,storage,scans,medium,disk)
        val latent=library.roll("roll").frames[0].latentPath!!;val before=storage.readPrivate(latent)
        val info=session.inventory("roll");assertEquals(1,info.captures);assertEquals(2,info.negatives);assertEquals(2,info.photos)
        assertTrue(session.releaseCameraSources("roll",WorkflowControl()))
        assertArrayEquals(before,storage.readPrivate(latent));assertNull(FilmLibrary(PrivateLibraryIndexStore(storage)).roll("roll").frames[0].capturePath)
        assertFalse(root.resolve("roll/frame_01.capture").toFile().exists())
        assertTrue(session.removeNegative(first));assertFalse(storage.existsPrivate(first.opticalPath))
        assertTrue(storage.existsPrivate(second.opticalPath));assertEquals(2,scans.count())
        assertTrue(session.removePhoto(a));assertEquals(listOf(b),scans.listNewest());assertArrayEquals(byteArrayOf(8),storage.readPrivate(b.imagePath))
        assertTrue(session.discardRoll("roll"));assertFalse(storage.existsPrivate(latent))
        assertFalse(library.developedResults().single().sourceRollPresent);assertTrue(storage.existsPrivate(second.opticalPath));assertTrue(storage.existsPrivate(b.imagePath))
        assertFalse(session.needsRecovery)
        Unit
    }
    @Test fun unformedSourceAndUnfinishedTaskCannotBeCleanedAndCancellationRetainsOriginals() = runBlocking {
        val storage=storage();val library=FilmLibrary();library.loadRoll("roll",12,emptyMap())
        val shoot=capture(library,storage);shoot.capture("roll",camera);shoot.seal("roll")
        val medium=MediumStore(storage);val disk=PrivateDevelopmentTaskStore(storage);val resources=LibraryResourceSession(library,storage,PrivateScanStore(storage),medium,disk)
        assertThrows(IllegalArgumentException::class.java) { resources.releaseCameraSources("roll",WorkflowControl()) }
        val tasks=RollDevelopmentSession(library,medium,disk,shoot);tasks.create("roll","paused",emptyMap())
        assertThrows(IllegalArgumentException::class.java) { resources.discardRoll("roll") }
        tasks.run("roll","paused")
        assertThrows(WorkflowException::class.java) { resources.releaseCameraSources("roll",WorkflowControl(cancelRequested={true})) }
        assertNotNull(library.roll("roll").frames[0].capturePath);assertTrue(root.resolve("roll/frame_01.capture/receipt.json").toFile().exists())
        assertEquals(RollLifecycle.SEALED,library.roll("roll").lifecycle)
        Unit
    }
    @Test fun rollReferenceFilesFollowWholeRollCommitAndReturnWhenIndexWriteFails() = runBlocking {
        val storage=storage();val saved=MemoryLibraryIndexStore();var fail=false
        val index=object: LibraryIndexStore by saved {
            override fun writeIndex(bytes: ByteArray) { if(fail) throw IOException("index unavailable");saved.writeIndex(bytes) }
        }
        val library=FilmLibrary(index);library.loadRoll("roll",12,emptyMap())
        val shoot=capture(library,storage);shoot.capture("roll",camera);shoot.seal("roll")
        val refs=RollDirectReference.relativePaths("roll")
        refs.forEachIndexed { i,path->storage.writePrivate(path,byteArrayOf(i.toByte(),9)) }
        storage.writePrivate("unselected/keep.jpg",byteArrayOf(8))
        val session=LibraryResourceSession(library,storage,PrivateScanStore(storage),MediumStore(storage),PrivateDevelopmentTaskStore(storage))
        fail=true
        assertThrows(IOException::class.java) { session.discardRoll("roll") }
        assertEquals(RollLifecycle.SEALED,library.roll("roll").lifecycle)
        refs.forEachIndexed { i,path->assertArrayEquals(byteArrayOf(i.toByte(),9),storage.readPrivate(path)) }
        assertTrue(root.resolve("roll/frame_01.capture/receipt.json").toFile().isFile)
        assertFalse(session.needsRecovery)
        fail=false;assertTrue(session.discardRoll("roll"))
        assertEquals(RollLifecycle.DISCARDED,library.roll("roll").lifecycle)
        refs.forEach { assertFalse(storage.existsPrivate(it)) }
        assertArrayEquals(byteArrayOf(8),storage.readPrivate("unselected/keep.jpg"));assertFalse(session.needsRecovery)
        Unit
    }
    @Test fun activeRollProtectsItsMaterialsWhileAnotherRollCanBeManaged() = runBlocking {
        val storage=storage();val library=FilmLibrary(PrivateLibraryIndexStore(storage));val medium=MediumStore(storage)
        val shoot=capture(library,storage);val tasks=PrivateDevelopmentTaskStore(storage)
        val development=RollDevelopmentSession(library,medium,tasks,shoot)
        val negatives=listOf("active","other").map { id ->
            library.loadRoll(id,12,emptyMap());shoot.capture(id,camera);shoot.seal(id)
            development.create(id,"batch_$id",emptyMap());development.run(id,"batch_$id").single()
        }
        val scans=PrivateScanStore(storage)
        val photo=scans.save(negatives[1],3,2,emptyMap()) { it.write(byteArrayOf(7,8)) }
        val resources=LibraryResourceSession(library,storage,scans,medium,tasks)
        library.withSealedRollJob("active") {
            kotlinx.coroutines.withContext(kotlinx.coroutines.Dispatchers.Default) {
                val denied=resources.removeSelected(negatives=listOf(negatives[0]))
                assertNotNull(denied.error);assertEquals(0,denied.completed)
                assertTrue(storage.existsPrivate(negatives[0].opticalPath))
                val removed=resources.removeSelected(negatives=listOf(negatives[1]))
                assertNull(removed.error);assertEquals(1,removed.completed)
                assertTrue(storage.existsPrivate(photo.imagePath));assertEquals(90,scans.rotateClockwise(photo.id).viewRotationDegrees)
            }
        }
        val restored=FilmLibrary(PrivateLibraryIndexStore(storage))
        assertEquals(listOf(negatives[0].resultId),restored.developedResults().map { it.resultId })
        assertArrayEquals(byteArrayOf(7,8),storage.readPrivate(photo.imagePath))
        Unit
    }
    private fun storage(): StorageAdapter=object: StorageAdapter by MemoryStorageAdapter() {
        override fun privateDir()=root.toString()
        override fun existsPrivate(relative: String)=Files.isRegularFile(root.resolve(relative))
        override fun rowScratchRoot()=root.resolve("scratch").toFile()
        override fun samePrivateLocation(a: String,b: String)=root.resolve(a).normalize()==root.resolve(b).normalize()
        override fun readPrivate(relative: String)=root.resolve(relative).toFile().readBytes()
        override fun <T> readPrivate(relative: String,reader: (InputStream)->T)=root.resolve(relative).toFile().inputStream().use(reader)
        override fun writePrivate(relative: String,bytes: ByteArray)=writePrivate(relative) { it.write(bytes) }
        override fun writePrivate(relative: String,writer: (OutputStream)->Unit)=writePrivate(relative,{},writer)
        override fun writePrivate(relative: String,beforeCommit: ()->Unit,writer: (OutputStream)->Unit) {
            val path=root.resolve(relative);Files.createDirectories(path.parent);val pending=Files.createTempFile(path.parent,"pending-",".tmp")
            try { pending.toFile().outputStream().use(writer);beforeCommit();Files.move(pending,path,StandardCopyOption.ATOMIC_MOVE,StandardCopyOption.REPLACE_EXISTING) }
            finally { Files.deleteIfExists(pending) }
        }
    }
}
