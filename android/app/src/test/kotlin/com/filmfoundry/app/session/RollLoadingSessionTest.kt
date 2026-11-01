package com.filmfoundry.app.session

import com.filmfoundry.workflow.*
import java.util.concurrent.CountDownLatch
import java.util.concurrent.Executors
import java.util.concurrent.TimeUnit
import org.junit.jupiter.api.Assertions.*
import org.junit.jupiter.api.Test

class RollLoadingSessionTest {
    private fun draft(library: FilmLibrary,film: Map<String,Any?> = emptyMap())=
        RollLoadingSession(library,"new-roll",12,film,"My roll",mapOf("time_min" to 4),emptyMap(),mapOf("format" to "135","stock_length" to 12))

    @Test fun animationDraftFreezesConfigurationAndRepeatedCompletionPublishesOnlyOneEmptyRoll() {
        val index=MemoryLibraryIndexStore();val library=FilmLibrary(index)
        val matrix=mutableListOf<Any?>(1.0,2.0)
        val film=mutableMapOf<String,Any?>("custom" to matrix)
        val session=draft(library,film);matrix[0]=99.0;film.clear()
        assertTrue(library.shootingRolls().isEmpty())
        assertEquals("new-roll",session.commit());val saved=index.readIndex()!!
        assertEquals("new-roll",session.commit());assertArrayEquals(saved,index.readIndex())
        val roll=FilmLibrary(index).shootingRolls().single()
        assertEquals("My roll",roll.label);assertEquals(12,roll.capacity);assertEquals(0,roll.exposedCount())
        assertEquals(listOf(1.0,2.0),(roll.filmRecipe["custom"] as List<*>).map { (it as Number).toDouble() })
        assertEquals(4,(roll.developRecipe["time_min"] as Number).toInt())
        assertDoesNotThrow { java.time.Instant.parse(roll.captureSettings["created_at"] as String) }
        session.cancel();assertEquals(RollLoadingSession.State.LOADED,session.state)
    }

    @Test fun cancellingBeforePublicationCreatesNoRoll() {
        val index=MemoryLibraryIndexStore();val library=FilmLibrary(index);val session=draft(library)
        session.cancel();assertThrows(IllegalStateException::class.java) { session.commit() }
        assertNull(index.readIndex());assertTrue(library.shootingRolls().isEmpty())
    }

    @Test fun indexFailureRollsBackAndRetryUsesTheSameDraftIdentity() {
        val memory=MemoryLibraryIndexStore();var fail=true
        val library=FilmLibrary(object: LibraryIndexStore {
            override fun readIndex()=memory.readIndex()
            override fun writeIndex(bytes: ByteArray) { if(fail) error("disk unavailable");memory.writeIndex(bytes) }
        })
        val session=draft(library)
        assertThrows(IllegalStateException::class.java) { session.commit() }
        assertTrue(library.shootingRolls().isEmpty());assertNull(memory.readIndex())
        fail=false;session.commit();assertEquals("new-roll",FilmLibrary(memory).shootingRolls().single().rollId)
    }

    @Test fun leavingDuringPublicationDoesNotBlockOrDuplicateOrDeleteTheCommittedRoll() {
        val entered=CountDownLatch(1);val release=CountDownLatch(1);val memory=MemoryLibraryIndexStore()
        val library=FilmLibrary(object: LibraryIndexStore {
            override fun readIndex()=memory.readIndex()
            override fun writeIndex(bytes: ByteArray) { entered.countDown();check(release.await(5,TimeUnit.SECONDS));memory.writeIndex(bytes) }
        })
        val session=draft(library);val executor=Executors.newSingleThreadExecutor()
        try {
            val future=executor.submit<String> { session.commit() }
            assertTrue(entered.await(5,TimeUnit.SECONDS));session.cancel()
            assertEquals(RollLoadingSession.State.COMMITTING,session.state)
            assertThrows(IllegalStateException::class.java) { session.commit() }
            release.countDown();assertEquals("new-roll",future.get(5,TimeUnit.SECONDS))
            assertEquals(1,FilmLibrary(memory).shootingRolls().size)
        } finally { release.countDown();executor.shutdownNow() }
    }
}
