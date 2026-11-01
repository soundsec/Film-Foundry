package com.filmfoundry.app.session

import com.filmfoundry.platform.*
import org.junit.jupiter.api.Assertions.*
import org.junit.jupiter.api.Test
import org.junit.jupiter.api.io.TempDir
import java.io.*
import java.nio.file.*

class NegativeViewCacheTest {
    @TempDir lateinit var root: Path
    private val storage get()=object: StorageAdapter by MemoryStorageAdapter() {
        override fun privateDir()=root.toString()
        override fun existsPrivate(relative: String)=Files.isRegularFile(root.resolve(relative))
        override fun readPrivate(relative: String)=root.resolve(relative).toFile().readBytes()
        override fun <T> readPrivate(relative: String,reader: (InputStream)->T)=root.resolve(relative).toFile().inputStream().use(reader)
        override fun deletePrivate(relative: String) { Files.deleteIfExists(root.resolve(relative)) }
        override fun writePrivate(relative: String,bytes: ByteArray)=writePrivate(relative) { it.write(bytes) }
        override fun writePrivate(relative: String,writer: (OutputStream)->Unit)=writePrivate(relative,{},writer)
        override fun writePrivate(relative: String,beforeCommit: ()->Unit,writer: (OutputStream)->Unit) {
            val target=root.resolve(relative);Files.createDirectories(target.parent);val pending=Files.createTempFile(target.parent,"pending-",".tmp")
            try { pending.toFile().outputStream().use(writer);beforeCommit();Files.move(pending,target,StandardCopyOption.ATOMIC_MOVE,StandardCopyOption.REPLACE_EXISTING) }
            finally { Files.deleteIfExists(pending) }
        }
    }
    @Test fun reopeningReusesOnlyMatchingSourceLampAndModeWithoutReadingMaterialBytes() {
        val storage=storage;storage.writePrivate("source.npz",byteArrayOf(42))
        val recipe=mapOf("scanner" to mapOf("transmission_light_ev" to .5))
        val cache=NegativeViewCache(storage)
        cache.save("result","source.npz","viewer_light_table",recipe,4000,3000,{}) { it.write(byteArrayOf(1,2,3)) }
        val entry=NegativeViewCache(storage).find("result","source.npz","viewer_light_table",recipe,{})!!
        assertEquals(4000,entry.sourceWidth);assertEquals(3000,entry.sourceHeight);assertArrayEquals(byteArrayOf(1,2,3),storage.readPrivate(entry.path))
        assertNull(cache.find("result","source.npz","viewer_projection",recipe,{}))
        assertNull(cache.find("result","source.npz","viewer_light_table",emptyMap(),{}))
        storage.writePrivate("source.npz",byteArrayOf(42,43))
        assertNull(cache.find("result","source.npz","viewer_light_table",recipe,{}))
        assertArrayEquals(byteArrayOf(42,43),storage.readPrivate("source.npz"))
    }
    @Test fun interruptedWriteAndClearCannotLeaveAUsableWrongViewOrRemoveSource() {
        val storage=storage;storage.writePrivate("source.npz",byteArrayOf(42));val cache=NegativeViewCache(storage)
        cache.save("result","source.npz","viewer_light_table",emptyMap(),20,10,{}) { it.write(1) }
        assertThrows(IOException::class.java) {
            cache.save("result","source.npz","viewer_light_table",mapOf("lamp" to 1),20,10,{}) { it.write(2);throw IOException("interrupted") }
        }
        assertNull(cache.find("result","source.npz","viewer_light_table",emptyMap(),{}))
        assertThrows(IllegalStateException::class.java) {
            cache.save("result","source.npz","viewer_light_table",emptyMap(),20,10,{}) { it.write(3);cache.clear() }
        }
        assertNull(cache.find("result","source.npz","viewer_light_table",emptyMap(),{}))
        assertArrayEquals(byteArrayOf(42),storage.readPrivate("source.npz"))
    }
    @Test fun cacheHasBoundedDiskOwnershipAndDeletionKeepsOtherResults() {
        val storage=storage;storage.writePrivate("source.npz",byteArrayOf(42));val cache=NegativeViewCache(storage,4)
        for(id in listOf("first","second")) cache.save(id,"source.npz","viewer_light_table",emptyMap(),20,10,{}) { it.write(byteArrayOf(1,2,3)) }
        val directory=root.resolve(NegativeViewCache.DIRECTORY).toFile()
        assertTrue(directory.listFiles()!!.filter { it.name.endsWith(".png") }.sumOf { it.length() }<=4)
        cache.delete("first");assertNotNull(cache.find("second","source.npz","viewer_light_table",emptyMap(),{}))
        cache.clear();assertTrue(directory.listFiles().orEmpty().isEmpty());assertArrayEquals(byteArrayOf(42),storage.readPrivate("source.npz"))
    }
}
