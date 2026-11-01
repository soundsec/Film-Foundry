package com.filmfoundry.platform

import com.filmfoundry.interchange.*
import com.filmfoundry.workflow.DevelopedResult
import java.nio.file.Path
import org.junit.jupiter.api.Assertions.*
import org.junit.jupiter.api.Test
import org.junit.jupiter.api.io.TempDir

class OpticalFileInformationTest {
    @TempDir lateinit var root: Path
    private fun source(): java.io.File {
        val metadata=mapOf("image_polarity" to "positive","compatible_interpreters" to listOf("positive_transparency_scan"),"created_at" to "2026-10-28T13:00:00Z")
        return root.resolve("positive.npz").toFile().apply { writeBytes(NpzMediumArchive().writeDevelopedOptical(
            DevelopedOpticalArchive(FloatNd(intArrayOf(7,9,3),FloatArray(7*9*3){.5f}),metadata))) }
    }
    @Test fun factsReadFinalPolarityAndSizeWithoutChangingTheArchive() {
        val file=source();val before=file.readBytes();val facts=OpticalFileInformationReader.read(file)
        assertEquals(9,facts.width);assertEquals(7,facts.height);assertEquals("positive",facts.polarity)
        assertEquals("2026-10-28T13:00:00Z",facts.createdAt);assertArrayEquals(before,file.readBytes())
        assertThrows(CaptureRejected::class.java) { PrivateOpticalInformation(root.toFile()).signature("../outside.npz") }
    }
    @Test fun savedPhotoKeepsSourceFactsAndFrozenRecipeAfterFilmRemovalAndOldRecordsStillRead() {
        val file=source();val memory=MemoryStorageAdapter()
        val storage=object: StorageAdapter by memory { override fun privateDir()=root.toString() }
        val result=DevelopedResult("result","roll",1,"positive.npz",mapOf("film" to mapOf("image_polarity" to "negative")))
        val recipe=mapOf("scanner" to mapOf("scan_saturation" to 1.4))
        val saved=PrivateScanStore(storage).save(result,1280,853,recipe) { it.write(byteArrayOf(1,2,3)) }
        assertTrue(file.delete());val read=PrivateScanStore(storage).listNewest().single()
        assertEquals("positive",read.sourcePolarity);assertEquals(9,read.sourceWidth);assertEquals(7,read.sourceHeight)
        assertEquals(saved.recipeShare,read.recipeShare);assertArrayEquals(byteArrayOf(1,2,3),memory.readPrivate(read.imagePath))
        assertEquals(result.developRecipe,RecipeShareCodec.decode(read.sourceDevelopRecipeShare!!))
        val path="scans/${saved.id}/scan.json";val old=org.json.JSONObject(memory.readPrivate(path).toString(Charsets.UTF_8))
        listOf("source_polarity","source_width","source_height","source_develop_recipe_share").forEach(old::remove);memory.writePrivate(path,old.toString().toByteArray())
        val legacy=PrivateScanStore(storage).listNewest().single()
        assertNull(legacy.sourcePolarity);assertNull(legacy.sourceDevelopRecipeShare)
    }
}
