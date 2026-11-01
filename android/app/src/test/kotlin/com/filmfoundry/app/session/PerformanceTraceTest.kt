package com.filmfoundry.app.session

import java.nio.file.Path
import java.util.UUID
import org.junit.jupiter.api.Assertions.*
import org.junit.jupiter.api.Test
import org.junit.jupiter.api.io.TempDir

class PerformanceTraceTest {
    @TempDir lateinit var root: Path
    @Test fun boundedCheckpointsPreserveTotalsAndFinalStatesWithoutPrivateValues() {
        var clock=100L
        val trace=PerformanceTrace(UUID.randomUUID().toString(),mapOf("cpu_workers" to 2)){clock}
        repeat(400) { i ->
            clock++
            trace.timing("scan","linear_compute",2_000_000)
            trace.sample(mapOf("java_heap_bytes" to i,"unexpected_private_field" to 7))
            trace.event("forming",i,400)
        }
        trace.timing("private/path","bad",123);trace.event("private filename")
        val store=PerformanceTraceStore(root.toFile());store.save(trace)
        assertEquals("running",store.records(trace.id).single()["status"])
        assertEquals("interrupted",store.records().single()["status"])
        trace.finish("paused","IllegalStateException");clock+=5000
        trace.timing("scan","linear_compute",99);trace.sample(mapOf("java_heap_bytes" to 999));store.save(trace)
        val saved=store.records().single()
        assertEquals("paused",saved["status"]);assertEquals(400,(saved["wall_ms"] as Number).toInt())
        val timings=saved["stage_timings"] as Map<*,*>;assertEquals(1,timings.size)
        val stage=timings["scan.linear_compute"] as Map<*,*>
        assertEquals(400,(stage["count"] as Number).toInt());assertEquals(800.0,(stage["total_ms"] as Number).toDouble())
        val samples=saved["samples"] as List<*>;assertEquals(360,samples.size)
        assertFalse((samples.last() as Map<*,*>).containsKey("unexpected_private_field"))
        assertEquals(256,(saved["events"] as List<*>).size)
        assertEquals(400,((saved["event_totals"] as Map<*,*>)["forming"] as Number).toInt())
        assertEquals(399.0,((saved["resource_peaks"] as Map<*,*>)["java_heap_bytes"] as Number).toDouble())
    }
    @Test fun concurrentStageTotalsAndStoreRetentionRemainBounded() {
        val trace=PerformanceTrace(UUID.randomUUID().toString(),emptyMap()){1L}
        val workers=List(2) { Thread { repeat(1000) { trace.timing("develop_latent","material_compute",1000) } }.apply { start() } }
        workers.forEach { it.join() }
        trace.finish("completed")
        val stage=(trace.snapshot()["stage_timings"] as Map<*,*>)["develop_latent.material_compute"] as Map<*,*>
        assertEquals(2000,stage["count"]);assertEquals(2.0,stage["total_ms"])
        val store=PerformanceTraceStore(root.toFile())
        repeat(23) { store.save(PerformanceTrace(UUID.randomUUID().toString(),emptyMap()){1L}.apply { finish("completed") }) }
        assertEquals(20,store.records().size);assertEquals(20,root.toFile().listFiles()!!.size)
        store.clear();assertTrue(root.toFile().listFiles()!!.isEmpty())
    }
}
