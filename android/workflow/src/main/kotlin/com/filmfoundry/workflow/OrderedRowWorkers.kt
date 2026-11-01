package com.filmfoundry.workflow

import java.util.concurrent.*
import java.util.concurrent.atomic.AtomicBoolean
import java.util.concurrent.atomic.AtomicReference

/** At most two live blocks; consume in material row order and join before releasing source files. */
internal object OrderedRowWorkers {
    fun <T> run(starts: IntProgression,workers: Int,aborted: AtomicBoolean,check: ()->Unit,compute: (Int)->T,consume: (Int,T)->Unit) {
        require(workers in 1..2)
        if(workers==1) { for(start in starts) { check();val result=compute(start);check();consume(start,result) };return }
        val executor=Executors.newFixedThreadPool(workers) { runnable -> Thread(runnable,"FilmFoundry-row") }
        val pending=java.util.ArrayDeque<Pair<Int,Future<T>>>()
        val iterator=starts.iterator()
        val failure=AtomicReference<Throwable?>()
        fun checkActive() { failure.get()?.let { throw it };check() }
        fun submit() {
            if(iterator.hasNext()) { val start=iterator.nextInt();pending.add(start to executor.submit(Callable {
                try { checkActive();compute(start) } catch(error: Throwable) { failure.compareAndSet(null,error);throw error }
            })) }
        }
        fun await(future: Future<T>): T {
            while(true) {
                checkActive()
                try { return future.get(50,TimeUnit.MILLISECONDS) }
                catch(_: TimeoutException) { /* Recheck cancellation and failures from either worker. */ }
                catch(error: ExecutionException) { throw error.cause ?: error }
            }
        }
        try {
            repeat(workers) { submit() }
            while(pending.isNotEmpty()) {
                checkActive();val (start,future)=pending.first()
                val result=await(future)
                checkActive();consume(start,result);pending.removeFirst();submit()
            }
        } finally {
            aborted.set(true);pending.forEach { it.second.cancel(true) };executor.shutdownNow()
            var interrupted=false
            while(true) try { if(executor.awaitTermination(100,TimeUnit.MILLISECONDS)) break }
                catch(_: InterruptedException) { interrupted=true }
            if(interrupted) Thread.currentThread().interrupt()
        }
    }
}
