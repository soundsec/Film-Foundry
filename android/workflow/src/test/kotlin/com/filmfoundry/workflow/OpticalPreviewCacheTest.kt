package com.filmfoundry.workflow

import com.filmfoundry.interchange.*
import kotlinx.coroutines.runBlocking
import org.junit.jupiter.api.Assertions.*
import org.junit.jupiter.api.Test

class OpticalPreviewCacheTest {
    @Test fun removingOneResultDuringItsReadRejectsLateRefillButRetainsOtherCachedResults() {
        val cache=OpticalPreviewCache();var reads=0
        fun value()=OpticalPreview(DevelopedOpticalArchive(FloatNd(intArrayOf(1,1,3),FloatArray(3)),emptyMap()),1,1,1)
        val other=cache.get("other:path",::value)
        cache.get("removed:path") { reads++;cache.dropResult("removed");value() }
        cache.get("removed:path") { reads++;value() }
        assertEquals(2,reads);assertSame(other,cache.get("other:path") { error("unrelated cached result must remain") })
    }
    @Test fun maximumDisplaySpectralProxyIsRetainedWithinTheAppBudget() {
        val cache=OpticalPreviewCache(56L*1048576);var loads=0
        fun load(): OpticalPreview {
            loads++
            return OpticalPreview(DevelopedOpticalArchive(FloatNd(intArrayOf(960,960,3),FloatArray(960*960*3)),emptyMap(),
                spectralComponentAmounts=FloatNd(intArrayOf(960,960,9),FloatArray(960*960*9)),
                spectralPostProcessDensityRgb=FloatNd(intArrayOf(960,960,3),FloatArray(960*960*3))),960,960,1)
        }
        val first=cache.get("spectral",::load)
        assertSame(first,cache.get("spectral",::load));assertEquals(1,loads)
        cache.clear();cache.get("spectral",::load);assertEquals(2,loads)
    }
    @Test fun clearDoesNotWaitForArchiveIoAndAReaderInvalidatedByClearCannotRefillTheCache() {
        val cache=OpticalPreviewCache();val entered=java.util.concurrent.CountDownLatch(1)
        val release=java.util.concurrent.CountDownLatch(1);val cleared=java.util.concurrent.CountDownLatch(1)
        val failure=java.util.concurrent.atomic.AtomicReference<Throwable?>()
        var loads=0
        fun value()=OpticalPreview(DevelopedOpticalArchive(FloatNd(intArrayOf(1,1,3),floatArrayOf(.1f,.2f,.3f)),emptyMap()),1,1,1)
        val reading=Thread { try { cache.get("a") { loads++;entered.countDown();check(release.await(5,java.util.concurrent.TimeUnit.SECONDS));value() } } catch(error: Throwable) { failure.set(error) } }
        reading.start();assertTrue(entered.await(1,java.util.concurrent.TimeUnit.SECONDS))
        val clearing=Thread { cache.clear();cleared.countDown() };clearing.start()
        try { assertTrue(cleared.await(1,java.util.concurrent.TimeUnit.SECONDS),"UI clear must not wait for disk I/O") }
        finally { release.countDown();reading.join(2000);clearing.join(2000) }
        assertNull(failure.get());assertFalse(reading.isAlive)
        cache.get("a") { loads++;value() };assertEquals(2,loads)
    }
    @Test fun observationEditsReuseOnlyOneReadOnlyProxyAndBudgetOverflowDoesNotRetainIt() = runBlocking {
        val cache=OpticalPreviewCache();var loads=0
        fun load(): OpticalPreview { loads++;return OpticalPreview(DevelopedOpticalArchive(FloatNd(intArrayOf(2,3,3),FloatArray(18){it*.05f}),emptyMap()),3,2,1) }
        val first=cache.get("frame-a",::load);val before=first.archive.opticalDensityRgb.values.copyOf()
        val reused=cache.get("frame-a",::load);assertSame(first,reused);assertEquals(1,loads)
        OpticalPreviewSession().observe(reused,mapOf("scanner" to mapOf("tone_curve" to listOf(listOf(0.0,.1),listOf(1.0,1.0)))),false)
        assertArrayEquals(before,reused.archive.opticalDensityRgb.values)
        cache.get("frame-b",::load);assertEquals(2,loads)
        cache.get("frame-a",::load);assertEquals(2,loads)
        cache.dropResult("frame-a");cache.get("frame-a",::load);assertEquals(3,loads)
        cache.get("frame-b",::load);assertEquals(3,loads)
        cache.clear();cache.get("frame-b",::load);assertEquals(4,loads)
        val noRetention=OpticalPreviewCache(0)
        noRetention.get("frame-c",::load);noRetention.get("frame-c",::load);assertEquals(6,loads)
        val one=OpticalPreviewCache(72)
        one.get("wide-a",::load);one.get("wide-b",::load);one.get("wide-a",::load);assertEquals(9,loads)
        Unit
    }
}
