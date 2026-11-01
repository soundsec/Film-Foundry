package com.filmfoundry.app.ui

import java.util.concurrent.Executor
import java.util.concurrent.RejectedExecutionException

/** One read-only worker, one replaceable pending request, cooperative cancellation. No business lock. */
class LatestViewWork(private val executor: Executor,private val post: (()->Unit)->Unit) {
    private data class Task(val revision: Long,val work: (()->Boolean)->Unit,val failed: (Throwable)->Unit)
    private var revision=0L
    private var pending: Task?=null
    private var draining=false
    @Synchronized fun submit(work: (()->Boolean)->Unit,failed: (Throwable)->Unit) {
        pending=Task(++revision,work,failed)
        if(!draining) {
            draining=true
            try { executor.execute(::drain) }
            catch(error: RejectedExecutionException) {
                draining=false
                val task=pending;pending=null
                if(task!=null) post { if(current(task.revision)) task.failed(error) }
            }
        }
    }
    @Synchronized fun cancel() { revision++;pending=null }
    @Synchronized private fun current(token: Long)=token==revision
    private fun drain() {
        while(true) {
            val task=synchronized(this) {
                pending.also { pending=null;if(it==null) draining=false }
            } ?: return
            val result=runCatching { task.work { !current(task.revision) } }
            result.exceptionOrNull()?.let { error -> post { if(current(task.revision)) task.failed(error) } }
        }
    }
}
