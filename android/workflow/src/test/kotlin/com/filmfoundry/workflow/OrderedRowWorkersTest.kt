package com.filmfoundry.workflow

import java.util.concurrent.*
import java.util.concurrent.atomic.*
import org.junit.jupiter.api.Assertions.*
import org.junit.jupiter.api.Test

class OrderedRowWorkersTest {
    @Test fun laterFailureInterruptsEarlierBlockedRowAndJoinsIt() {
        val started=CountDownLatch(1);val finished=AtomicBoolean();val aborted=AtomicBoolean()
        val error=assertThrows(IllegalStateException::class.java) {
            OrderedRowWorkers.run(0..2,2,aborted,{}, { row ->
                if(row==1) { check(started.await(2,TimeUnit.SECONDS));throw IllegalStateException("second row failed") }
                try { started.countDown();check(CountDownLatch(1).await(2,TimeUnit.SECONDS)) { "first row timed out" };row }
                finally { finished.set(true) }
            }) { _,_->fail("Failed operation must not publish rows") }
        }
        assertEquals("second row failed",error.message);assertTrue(aborted.get());assertTrue(finished.get())
    }
    @Test fun cancellationIsObservedWhileWaitingAndJoinsWorkers() {
        val started=CountDownLatch(2);val finished=AtomicInteger();val aborted=AtomicBoolean()
        val error=assertThrows(CancellationException::class.java) {
            OrderedRowWorkers.run(0..3,2,aborted,{ if(started.count==0L) throw CancellationException("paused") }, { row ->
                try { started.countDown();check(CountDownLatch(1).await(2,TimeUnit.SECONDS)) { "row timed out" };row }
                finally { finished.incrementAndGet() }
            }) { _,_->fail("Paused rows must not be published") }
        }
        assertEquals("paused",error.message);assertEquals(2,finished.get());assertTrue(aborted.get())
    }
    @Test fun outOfOrderComputationHasBoundedConcurrencyAndOrderedDelivery() {
        val second=CountDownLatch(1);val live=AtomicInteger();val peak=AtomicInteger();val delivered=mutableListOf<Int>()
        OrderedRowWorkers.run(0..7,2,AtomicBoolean(),{}, { start ->
            val count=live.incrementAndGet();peak.updateAndGet { maxOf(it,count) }
            try { if(start==0) check(second.await(2,TimeUnit.SECONDS));if(start==1) second.countDown();start*2 }
            finally { live.decrementAndGet() }
        }) { start,value -> assertEquals(start*2,value);delivered+=start }
        assertEquals((0..7).toList(),delivered);assertEquals(2,peak.get());assertEquals(0,live.get())
    }
    @Test fun failedBlockJoinsOtherWorkerBeforeReleasingSource() {
        val started=CountDownLatch(1);val finished=AtomicBoolean();val aborted=AtomicBoolean()
        assertThrows(IllegalStateException::class.java) {
            OrderedRowWorkers.run(0..2,2,aborted,{}, { row ->
                if(row==0) { check(started.await(2,TimeUnit.SECONDS));throw IllegalStateException("failed row") }
                else try { started.countDown();CountDownLatch(1).await() } finally { finished.set(true) }
            }) { _,_->fail("No incomplete rows may be delivered") }
        }
        assertTrue(aborted.get());assertTrue(finished.get())
    }
}
