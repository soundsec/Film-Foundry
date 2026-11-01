package com.filmfoundry.app.session

import com.filmfoundry.platform.*
import com.filmfoundry.workflow.*
import kotlinx.coroutines.runBlocking
import org.junit.jupiter.api.Assertions.*
import org.junit.jupiter.api.Test
import org.junit.jupiter.api.io.TempDir
import com.filmfoundry.interchange.*
import java.io.File

class LibraryExportSessionTest {
    @Test fun lossyPhotoExportsItsOwnBytesAndCannotAcquireLosslessFormats() = runBlocking {
        val memory=MemoryStorageAdapter();val exports=LibraryExportSession(memory,MediumStore(memory))
        val saved=SavedScan(java.util.UUID.randomUUID().toString(),"r","roll",1,7,3,"2026-11-01T00:00:00Z",RecipeShareCodec.encode(emptyMap()),format=ScanImageFormat.JPEG)
        val bytes=byteArrayOf(0xff.toByte(),0xd8.toByte(),1,2,3,0xff.toByte(),0xd9.toByte());memory.writePrivate(saved.imagePath,bytes)
        for(format in listOf(ScanImageFormat.PNG,ScanImageFormat.TIFF)) assertThrows(IllegalArgumentException::class.java) { exports.photo(saved,format) }
        val item=exports.photo(saved,ScanImageFormat.JPEG);assertEquals("image/jpeg",item.mime);assertTrue(item.name.endsWith(".jpg"))
        val outcome=exports.gallery(listOf(item),WorkflowControl());assertEquals(1,outcome.saved)
        assertArrayEquals(bytes,memory.galleryBytes(item.name));assertArrayEquals(bytes,memory.readPrivate(saved.imagePath))
        Unit
    }
    @TempDir lateinit var root: File
    @Test fun galleryAndFileExportsFollowPhotoAndNegativeDirectionWhileMaterialsStayUnchanged() = runBlocking {
        val memory=MemoryStorageAdapter();val storage=object: StorageAdapter by memory { override fun rowScratchRoot()=root }
        val metadata=mapOf("medium_family" to "film","medium_process" to "negative","image_polarity" to "negative",
            "view_mode" to "transmissive","base_type" to "orange_mask","color_system" to "color_negative_dye","compatible_interpreters" to listOf("negative_scan"))
        val original=NpzMediumArchive().writeDevelopedOptical(DevelopedOpticalArchive(FloatNd(intArrayOf(7,9,3),FloatArray(7*9*3){(it%31)/15f}),metadata,floatArrayOf(.2f,.1f,.05f)))
        val path="frame"+DataKernel.NEGATIVE_SUFFIX;storage.writePrivate(path,original)
        val medium=MediumStore(storage);val result=DevelopedResult("r","roll",1,path,emptyMap())
        val base=java.io.ByteArrayOutputStream();medium.scanPng(path,mapOf("observe_mode" to "viewer_light_table"),base)
        val baseImage=javax.imageio.ImageIO.read(base.toByteArray().inputStream())
        val saved=PrivateScanStore(storage).save(result,9,7,emptyMap()) { it.write(base.toByteArray()) }
        val turned=PrivateScanStore(storage).rotateClockwise(saved.id)
        val session=LibraryExportSession(storage,medium) { 90 }
        val items=listOf(session.photo(turned),session.image(result,emptyMap(),"viewer_light_table"){64L*1048576})
        val gallery=session.gallery(items,WorkflowControl());assertEquals(2,gallery.saved);assertTrue(gallery.failed.isEmpty())
        val files=linkedMapOf<String,ByteArray>()
        val exported=session.run(items,WorkflowControl()) { item,writer -> files[item.name]=java.io.ByteArrayOutputStream().also(writer).toByteArray() }
        assertEquals(2,exported.saved)
        for(item in items) {
            assertArrayEquals(files[item.name],memory.galleryBytes(item.name))
            val image=javax.imageio.ImageIO.read(files[item.name]!!.inputStream());assertEquals(7,image.width);assertEquals(9,image.height)
            for(y in 0..6) for(x in 0..8) assertEquals(baseImage.getRGB(x,y),image.getRGB(6-y,x))
        }
        assertArrayEquals(original,storage.readPrivate(path));assertArrayEquals(base.toByteArray(),storage.readPrivate(saved.imagePath))
        assertTrue(root.listFiles().orEmpty().isEmpty());Unit
    }
    @Test fun negativeImagesUseNativeOpticalRowsAndDoNotExportSmallDisplayProxiesOrRewriteMaterials() = runBlocking {
        val memory=MemoryStorageAdapter()
        val storage=object: StorageAdapter by memory { override fun rowScratchRoot()=root }
        val metadata=mapOf("medium_family" to "film","medium_process" to "negative","image_polarity" to "negative",
            "view_mode" to "transmissive","base_type" to "orange_mask","color_system" to "color_negative_dye","compatible_interpreters" to listOf("negative_scan"))
        val source=NpzMediumArchive().writeDevelopedOptical(DevelopedOpticalArchive(FloatNd(intArrayOf(7,9,3),FloatArray(7*9*3){(it%31)/15f}),metadata,floatArrayOf(.2f,.1f,.05f)))
        val path="frame"+DataKernel.NEGATIVE_SUFFIX;storage.writePrivate(path,source)
        val session=LibraryExportSession(storage,MediumStore(storage));val result=DevelopedResult("r","roll",1,path,emptyMap())
        val recipe=mapOf("scan_grid_long_edge" to 2,"scanner" to mapOf("scan_grid_long_edge" to 2,"tone_curve" to listOf(listOf(0,1),listOf(1,0))))
        val items=listOf("viewer_light_table","viewer_projection").map { session.image(result,recipe,it){64L*1048576} }
        val outcome=session.gallery(items,WorkflowControl())
        assertEquals(2,outcome.saved);assertTrue(outcome.failed.isEmpty())
        val images=items.map { javax.imageio.ImageIO.read(memory.galleryBytes(it.name).inputStream()) }
        assertTrue(images.all { it.width==9 && it.height==7 })
        assertNotEquals(images[0].getRGB(0,0),images[1].getRGB(0,0))
        assertArrayEquals(source,storage.readPrivate(path));assertTrue(root.listFiles().orEmpty().isEmpty())
        Unit
    }
    @Test fun streamsExactOriginalsAndPhotosWithoutChangingSourcesAndContinuesAfterMissingFiles() = runBlocking {
        val storage=MemoryStorageAdapter();val session=LibraryExportSession(storage,MediumStore(storage))
        val original=ByteArray(180001){(it%251).toByte()};storage.writePrivate("original.dng",original)
        val items=listOf(session.file("raw","original.dng","camera.dng","image/x-adobe-dng"),
            session.file("missing","absent.npz","missing.npz","application/octet-stream"),
            session.file("photo","original.dng","photo.png","image/png",true))
        val received=linkedMapOf<String,ByteArray>()
        val events=mutableListOf<String>()
        val result=session.run(items,WorkflowControl(onTiming={ command,section,_->events+="$command.$section" })) { item,writer ->
            val output=java.io.ByteArrayOutputStream();writer(output);received[item.id]=output.toByteArray()
        }
        assertEquals(2,result.saved);assertEquals(1,result.failed.size);assertTrue(result.failed.single().startsWith("missing.npz:"));assertFalse(result.cancelled)
        assertArrayEquals(original,received["raw"]);assertArrayEquals(original,received["photo"])
        assertArrayEquals(original,storage.readPrivate("original.dng"));assertTrue(storage.galleryInserts.isEmpty())
        assertEquals(2,events.count { it=="export.file_saved" });assertEquals(1,events.count { it=="export.file_failed" })
    }
    @Test fun cancelledBatchRetainsCompletedExportsAndMaterialsCannotEnterGallery() = runBlocking {
        val storage=MemoryStorageAdapter();val session=LibraryExportSession(storage,MediumStore(storage))
        storage.writePrivate("a",byteArrayOf(1,2,3));var cancelled=false
        val control=WorkflowControl(cancelRequested={cancelled})
        val items=listOf("one","two").map { session.file(it,"a","$it.png","image/png",true) }
        val result=session.gallery(items,control) { done,_->if(done==1) cancelled=true }
        assertEquals(1,result.saved);assertTrue(result.cancelled);assertEquals(1,storage.galleryInserts.size)
        assertArrayEquals(byteArrayOf(1,2,3),storage.galleryBytes("one.png"));assertArrayEquals(byteArrayOf(1,2,3),storage.readPrivate("a"))
        val material=session.file("material","a","frame.npz","application/octet-stream")
        assertThrows(IllegalArgumentException::class.java) { runBlocking { session.gallery(listOf(material),WorkflowControl()) } }
        assertEquals(1,storage.galleryInserts.size)
        Unit
    }
    @Test fun exportRequestRoundTripsFrozenIdsAndLampSettingsAndRejectsAmbiguity() {
        val request=LibraryExportRequest("light_table",listOf("b","a"),mapOf("scanner" to mapOf("transmission_light_ev" to 1.25)),"JPEG",98)
        val restored=LibraryExportRequest.decode(request.encode())
        assertEquals(request.kind,restored.kind);assertEquals(request.ids,restored.ids)
        assertEquals("JPEG",restored.format);assertEquals(98,restored.jpegQuality)
        assertEquals(95,LibraryExportRequest.decode("""{"kind":"photos","ids":["old"],"recipe":{}}""").jpegQuality)
        for(value in listOf("0","101","95.5","\"98\"")) assertThrows(IllegalArgumentException::class.java) {
            LibraryExportRequest.decode("""{"kind":"photos","ids":["a"],"recipe":{},"jpeg_quality":$value}""")
        }
        assertEquals(1.25,((restored.recipe["scanner"] as Map<*,*>)["transmission_light_ev"] as Number).toDouble())
        assertThrows(IllegalArgumentException::class.java) { LibraryExportRequest("photos",listOf("a","a")) }
        assertThrows(IllegalArgumentException::class.java) { LibraryExportRequest("unknown",listOf("a")) }
    }
}
