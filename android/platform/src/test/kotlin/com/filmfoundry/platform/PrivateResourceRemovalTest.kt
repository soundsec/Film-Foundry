package com.filmfoundry.platform

import org.junit.jupiter.api.Assertions.*
import org.junit.jupiter.api.Test
import org.junit.jupiter.api.io.TempDir
import java.io.File
import java.nio.file.Files

class PrivateResourceRemovalTest {
    @TempDir lateinit var root: File
    @Test fun failedCatalogCommitRestoresFilesAndCommittedRemovalKeepsUnselectedResources() {
        val photo=File(root,"scans/a").apply { mkdirs() };File(photo,"image.png").writeBytes(byteArrayOf(1,2,3))
        val source=File(root,"source.npz").apply { writeBytes(byteArrayOf(8,9)) }
        var referenced=true;val removal=PrivateResourceRemoval(root) { it=="scans/a" && referenced }
        assertEquals(3L,removal.bytes(listOf("scans/a")))
        assertThrows(java.io.IOException::class.java) { removal.remove(listOf("scans/a")) { throw java.io.IOException("index failed") } }
        assertArrayEquals(byteArrayOf(1,2,3),File(photo,"image.png").readBytes());assertFalse(removal.needsRecovery)
        assertTrue(removal.remove(listOf("scans/a")) { referenced=false })
        assertFalse(photo.exists());assertArrayEquals(byteArrayOf(8,9),source.readBytes())
    }
    @Test fun interruptedRemovalRecoversAccordingToDurableCatalogAndIsRepeatable() {
        fun stage() {
            val registry=File(root,".resource-removal").apply { mkdir() }
            File(registry,"plan.json").writeText("""{"model":"private_resource_removal_v1","paths":["source.npz"]}""")
            Files.move(File(root,"source.npz").toPath(),File(registry,"0").toPath())
        }
        File(root,"source.npz").writeBytes(byteArrayOf(4));stage()
        val retained=PrivateResourceRemoval(root) { true };retained.recover();retained.recover()
        assertArrayEquals(byteArrayOf(4),File(root,"source.npz").readBytes())
        stage();val detached=PrivateResourceRemoval(root) { false };detached.recover();detached.recover()
        assertFalse(File(root,"source.npz").exists());assertFalse(detached.needsRecovery)
    }
    @Test fun longestHalfRollAndItsReferencePairFitTheBoundedRemovalPlan() {
        val paths=(1..146).map { "roll/resource_$it" }
        File(root,"roll").mkdir()
        val first=File(root,paths.first()).apply { writeText("first") }
        val last=File(root,paths.last()).apply { writeText("last") }
        var owned=true;val removal=PrivateResourceRemoval(root) { owned && it in paths }
        assertThrows(IllegalArgumentException::class.java) { removal.remove(paths+"roll/overflow") { fail<Unit>("over limit") } }
        assertEquals("first",first.readText());assertEquals("last",last.readText())
        assertTrue(removal.remove(paths) { owned=false })
        assertFalse(first.exists());assertFalse(last.exists());assertFalse(removal.needsRecovery)
    }
    @Test fun escapesAndOverlappingSelectionsFailBeforeAnyMove() {
        File(root,"a").mkdir();File(root,"a/b").writeText("keep")
        val removal=PrivateResourceRemoval(root) { true }
        for(paths in listOf(listOf("../outside"),listOf("a/../a/b"),listOf("a","a/b"),listOf(".resource-removal/plan.json"))) {
            assertThrows(IllegalArgumentException::class.java) { removal.remove(paths) { fail<Unit>("cannot commit") } }
            assertEquals("keep",File(root,"a/b").readText());assertFalse(removal.needsRecovery)
        }
    }
}
