package com.filmfoundry.platform

import com.filmfoundry.interchange.RecipeShareCodec
import com.filmfoundry.workflow.*
import org.junit.jupiter.api.Assertions.*
import org.junit.jupiter.api.Test

class PrivateScanStoreTest {
    @Test fun rotationPersistsWithoutRewritingPixelsOrInvalidatingOwnedTaskAndFailedWriteKeepsDirection() {
        val memory=MemoryStorageAdapter();var fail=false
        val storage=object: StorageAdapter by memory { override fun writePrivate(relative: String,bytes: ByteArray) {
            if(fail && relative.endsWith("/scan.json")) throw java.io.IOException("write failed")
            memory.writePrivate(relative,bytes)
        } }
        val scans=PrivateScanStore(storage);val result=DevelopedResult("result","roll",1,"film.darkroom_negative.npz",emptyMap(),true)
        val id=java.util.UUID.randomUUID().toString();val task=java.util.UUID.randomUUID().toString()
        val recipe=OutputOrientation.apply(mapOf("scanner" to mapOf("scan_saturation" to 1.3)),90)
        val original=scans.saveOwnedFrame(id,task,result,recipe) { it.write(byteArrayOf(1,2,3));5 to 7 }
        assertEquals(0,original.viewRotationDegrees)
        val rotated=scans.rotateClockwise(id);assertEquals(7,rotated.displayWidth);assertEquals(5,rotated.displayHeight)
        assertEquals(180,OutputOrientation.read(rotated.editingRecipe()));assertEquals(original.recipeShare,rotated.recipeShare)
        assertEquals(rotated,PrivateScanStore(storage).requireSaved(id,result.resultId,original.recipeShare,task))
        assertNull(scans.findNewest(result.resultId,original.recipeShare)) // A rotated gallery version differs from the requested raster.
        fail=true;assertThrows(java.io.IOException::class.java) { scans.rotateClockwise(id) }
        assertEquals(rotated,PrivateScanStore(storage).listNewest().single());assertArrayEquals(byteArrayOf(1,2,3),storage.readPrivate(original.imagePath))
        fail=false;repeat(3) { scans.rotateClockwise(id) }
        assertEquals(original,scans.listNewest().single());assertEquals(original,scans.findNewest(result.resultId,original.recipeShare))
    }
    @Test fun ownedCommittedPhotoCanRegisterAfterCrashButForeignOwnershipIsRejected() {
        val memory=MemoryStorageAdapter();val scans=PrivateScanStore(memory)
        val result=DevelopedResult("result","roll",1,"source.darkroom_negative.npz",emptyMap(),true)
        val id=java.util.UUID.randomUUID().toString();val task=java.util.UUID.randomUUID().toString()
        val saved=scans.saveOwnedFrame(id,task,result,emptyMap()) { it.write(8);3 to 2 }
        memory.writePrivate("phone_scan_index.json","{\"model\":\"phone_scan_index_v1\",\"ids\":[]}".toByteArray()) // Crash before index commit, not a user's removal.
        val restored=PrivateScanStore(memory).saveOwnedFrame(id,task,result,emptyMap()) { error("must reuse owned committed photo") }
        assertEquals(saved,restored);assertEquals(1,scans.count())
        assertThrows(IllegalArgumentException::class.java) {
            scans.saveOwnedFrame(id,java.util.UUID.randomUUID().toString(),result,emptyMap()) { error("must not overwrite") }
        }
        assertArrayEquals(byteArrayOf(8),memory.readPrivate(saved.imagePath))
    }
    @Test fun errorAfterIndexCommitCannotDeleteThePublishedImage() {
        val memory=MemoryStorageAdapter()
        val storage=object: StorageAdapter by memory { override fun writePrivate(relative: String,bytes: ByteArray) {
            memory.writePrivate(relative,bytes);if(relative=="phone_scan_index.json") throw java.io.IOException("after atomic commit")
        } }
        val result=DevelopedResult("result","roll",1,"source.darkroom_negative.npz",emptyMap(),true)
        val scans=PrivateScanStore(storage)
        assertThrows(java.io.IOException::class.java) { scans.save(result,3,2,emptyMap()) { it.write(9) } }
        val saved=scans.listNewest().single();assertArrayEquals(byteArrayOf(9),memory.readPrivate(saved.imagePath))
    }
    @Test fun previousUnappliedDevicePhotoRemainsReadableButCannotSatisfyTheNewFinish() {
        val memory=MemoryStorageAdapter();val store=PrivateScanStore(memory)
        val result=DevelopedResult("result","roll",1,"source.darkroom_negative.npz",emptyMap(),true)
        val recipe=mapOf("scanner" to mapOf("device_response_rgb" to List(3) { listOf(listOf(0.0,0.0),listOf(1.0,.5)) }))
        val saved=store.save(result,2,2,recipe) { it.write(byteArrayOf(7)) }
        val recordPath="scans/${saved.id}/scan.json"
        val record=org.json.JSONObject(memory.readPrivate(recordPath).toString(Charsets.UTF_8));record.remove("observation_implementation")
        memory.writePrivate(recordPath,record.toString().toByteArray())
        val restored=PrivateScanStore(memory)
        assertNull(restored.listNewest().single().observationModel)
        assertNull(restored.findNewest(result.resultId,saved.recipeShare))
        assertArrayEquals(byteArrayOf(7),memory.readPrivate(saved.imagePath))
        val current=restored.save(result,2,2,recipe) { it.write(byteArrayOf(8)) }
        assertEquals(current,restored.findNewest(result.resultId,saved.recipeShare))
        val neutral=restored.save(result,2,2,emptyMap()) { it.write(byteArrayOf(9)) }
        val neutralPath="scans/${neutral.id}/scan.json";val oldNeutral=org.json.JSONObject(memory.readPrivate(neutralPath).toString(Charsets.UTF_8));oldNeutral.remove("observation_implementation")
        memory.writePrivate(neutralPath,oldNeutral.toString().toByteArray())
        assertNull(PrivateScanStore(memory).findNewest(result.resultId,neutral.recipeShare)) // Missing implementation cannot satisfy the current range-first scanner.
    }
    @Test fun removingOnePhotoKeepsOtherVersionsAndFailedIndexRemovalKeepsItsEntry() {
        val memory=MemoryStorageAdapter();var fail=false
        val storage=object: StorageAdapter by memory { override fun writePrivate(relative: String,bytes: ByteArray) {
            if(fail && relative=="phone_scan_index.json") throw java.io.IOException("index failed");memory.writePrivate(relative,bytes)
        } }
        val result=DevelopedResult("result","roll",1,"source.darkroom_negative.npz",emptyMap(),true)
        val scans=PrivateScanStore(storage);val a=scans.save(result,2,2,emptyMap()) { it.write(byteArrayOf(1)) }
        val b=scans.save(result,2,2,emptyMap()) { it.write(byteArrayOf(2)) }
        fail=true;assertThrows(java.io.IOException::class.java) { scans.remove(a.id) };assertEquals(2,scans.count())
        fail=false;scans.remove(a.id);assertEquals(listOf(b),scans.listNewest());assertTrue(scans.ownsPath("scans/${b.id}"))
        assertFalse(scans.ownsPath("scans/${a.id}"));assertArrayEquals(byteArrayOf(2),memory.readPrivate(b.imagePath))
    }
    @Test fun galleryRollPagesAreNewestFirstCancellableAndNeverReadImageBytes() {
        val memory=MemoryStorageAdapter();var reads=0
        val storage=object: StorageAdapter by memory {
            override fun readPrivate(relative: String): ByteArray {
                check(!relative.endsWith(".png")) { "Catalog must not decode images" };reads++;return memory.readPrivate(relative)
            }
        }
        val library=FilmLibrary()
        for(id in listOf("a","b")) { library.loadRoll(id,12,emptyMap());library.commitCameraShot(id,1,"$id.darkroom_latent.npz");library.seal(id) }
        val a=library.recordDevelopedResult("a-result","a",1,"a.darkroom_negative.npz",emptyMap())
        val b=library.recordDevelopedResult("b-result","b",1,"b.darkroom_negative.npz",emptyMap())
        val scans=PrivateScanStore(storage)
        val first=scans.save(a,9,7,emptyMap()) { it.write(byteArrayOf(1)) }
        scans.save(b,9,7,emptyMap()) { it.write(byteArrayOf(2)) }
        val last=scans.save(a,9,7,emptyMap()) { it.write(byteArrayOf(3)) }
        reads=0
        val page=scans.pageNewest("a",1,1)
        assertEquals(2,page.total);assertEquals(listOf(first),page.items);assertEquals(4,reads)
        assertEquals(listOf(last),scans.pageNewest(limit=1).items)
        assertEquals(last,scans.findNewest(a.resultId,RecipeShareCodec.encode(emptyMap())))
        assertNull(scans.findNewest(a.resultId,RecipeShareCodec.encode(mapOf("scanner" to mapOf("scan_saturation" to .8)))))
        assertTrue(scans.pageNewest("missing").items.isEmpty())
        assertThrows(InterruptedException::class.java) { scans.pageNewest("a",check={ throw InterruptedException("left page") }) }
        assertEquals(3,scans.count());assertArrayEquals(byteArrayOf(1),memory.readPrivate(first.imagePath))
    }
    @Test fun savedScanRestoresItsRecipeAndIdentityWithoutPublishingOrChangingSources() {
        val memory=MemoryStorageAdapter()
        val storage=object: StorageAdapter by memory {
            override fun publishGallery(displayName: String,mimeType: String,bytes: ByteArray)=error("A saved scan cannot publish itself")
        }
        val lib=FilmLibrary();lib.loadRoll("roll",12,emptyMap());lib.commitCameraShot("roll",1,"source.darkroom_latent.npz");lib.seal("roll")
        val result=lib.recordDevelopedResult("result","roll",1,"source.darkroom_negative.npz",emptyMap())
        memory.writePrivate(result.opticalPath,byteArrayOf(2,3,4))
        val recipe=mapOf("scanner" to mapOf("scan_saturation" to .7,"scan_grid_long_edge" to 1920))
        val saved=PrivateScanStore(storage).save(result,1920,1280,recipe) { it.write(byteArrayOf(1,2,3)) }
        val restored=PrivateScanStore(storage)
        assertEquals(1,restored.count());assertEquals(saved,restored.listNewest().single())
        assertEquals(1920,((RecipeShareCodec.decode(saved.recipeShare)["scanner"] as Map<*,*>)["scan_grid_long_edge"] as Number).toInt())
        assertArrayEquals(byteArrayOf(2,3,4),memory.readPrivate(result.opticalPath))
    }
    @Test fun indexAndEncoderFailurePreserveEarlierScansAndNeverPublishPartialEntries() {
        val memory=MemoryStorageAdapter();var fail=false
        val storage=object: StorageAdapter by memory {
            override fun writePrivate(relative: String,bytes: ByteArray) {
                if(fail && relative == "phone_scan_index.json") throw java.io.IOException("index failed")
                memory.writePrivate(relative,bytes)
            }
        }
        val lib=FilmLibrary();lib.loadRoll("roll",12,emptyMap());lib.commitCameraShot("roll",1,"source.darkroom_latent.npz");lib.seal("roll")
        val result=lib.recordDevelopedResult("result","roll",1,"source.darkroom_negative.npz",emptyMap())
        val scans=PrivateScanStore(storage);val saved=scans.save(result,7,5,emptyMap()) { it.write(byteArrayOf(8)) }
        fail=true
        assertThrows(java.io.IOException::class.java) { scans.save(result,7,5,emptyMap()) { it.write(byteArrayOf(9)) } }
        assertEquals(listOf(saved),scans.listNewest());assertArrayEquals(byteArrayOf(8),memory.readPrivate(saved.imagePath))
        fail=false
        assertThrows(java.io.IOException::class.java) { scans.save(result,7,5,emptyMap()) { throw java.io.IOException("encoder failed") } }
        assertEquals(1,scans.count())
    }
}
