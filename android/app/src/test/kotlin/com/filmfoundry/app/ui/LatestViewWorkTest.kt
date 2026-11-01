package com.filmfoundry.app.ui

import org.junit.jupiter.api.Assertions.*
import org.junit.jupiter.api.Test
import java.util.concurrent.Executor
import java.util.concurrent.RejectedExecutionException

class LatestViewWorkTest {
    @Test fun rejectedWorkerReportsOnceAndDoesNotLeaveTheQueueStuck() {
        var reject=true;val tasks=ArrayDeque<Runnable>();val posts=ArrayDeque<()->Unit>();val errors=mutableListOf<Throwable>();var completed=0
        val queue=LatestViewWork(Executor { if(reject) throw RejectedExecutionException("worker closed") else tasks.add(it) }) { posts.add(it) }
        assertDoesNotThrow { queue.submit({ fail<Unit>("rejected work must not start") },errors::add) }
        assertTrue(errors.isEmpty());posts.removeFirst()();assertEquals(1,errors.size)
        reject=false;queue.submit({ completed++ },{ fail<Unit>("retry failed") });tasks.removeFirst().run()
        assertEquals(1,completed);assertEquals(1,errors.size)
    }
    @Test fun rejectedRequestsDoNotDeliverAnErrorAfterNavigationCancelsThem() {
        val posts=ArrayDeque<()->Unit>()
        val queue=LatestViewWork(Executor { throw RejectedExecutionException("worker closed") }) { posts.add(it) }
        queue.submit({ fail<Unit>("must not start") },{ fail<Unit>("cancelled error must not appear") })
        queue.cancel();posts.removeFirst()()
    }
    @Test fun completedWorkRemainsInvalidAfterNewParametersEvenBeforeItsUiPostRuns() {
        val tasks=ArrayDeque<Runnable>();val posts=ArrayDeque<()->Unit>();val shown=mutableListOf<String>()
        val queue=LatestViewWork(Executor { tasks.add(it) }) { posts.add(it) }
        queue.submit({ stale -> posts.add { if(!stale()) shown+="old parameters" } },{ fail("unexpected") })
        tasks.removeFirst().run()
        queue.cancel() // Parameter edits invalidate results immediately, before debounce submits again.
        posts.removeFirst()();assertTrue(shown.isEmpty())
        queue.submit({ stale -> posts.add { if(!stale()) shown+="latest parameters" } },{ fail("unexpected") })
        tasks.removeFirst().run();posts.removeFirst()()
        assertEquals(listOf("latest parameters"),shown)
    }
    @Test fun rapidBrowsingKeepsOneWorkerAndOnlyTheLatestPendingReadAndFailure() {
        val tasks=ArrayDeque<Runnable>();val posts=ArrayDeque<()->Unit>();val seen=mutableListOf<String>()
        val queue=LatestViewWork(Executor { tasks.add(it) }) { posts.add(it) }
        queue.submit({ seen+="skipped" },{ fail("stale error") })
        queue.submit({ cancelled ->
            seen+="current";assertFalse(cancelled())
            queue.submit({ seen+="superseded" },{ fail("stale failure") })
            queue.submit({ seen+="latest" },{ fail("unexpected failure") })
            assertTrue(cancelled());error("cancelled old read")
        },{ fail("old error must not reach UI") })
        assertEquals(1,tasks.size);tasks.removeFirst().run();posts.forEach { it() }
        assertEquals(listOf("current","latest"),seen);assertTrue(tasks.isEmpty())
        queue.submit({ seen+="cancelled" },{ fail("cancelled") });queue.cancel();tasks.removeFirst().run()
        assertEquals(listOf("current","latest"),seen)
    }
}
