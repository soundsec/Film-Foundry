package com.filmfoundry.app.session

import android.content.Context
import android.content.Intent
import android.os.Looper
import java.util.concurrent.CopyOnWriteArrayList
import java.util.concurrent.atomic.AtomicBoolean

/** Application-owned execution lease. UI lifetimes observe it; durable media journals remain authoritative. */
object ProcessingWork {
    class Work internal constructor(val id: Long,val owner: Any,val title: String,val cancel: ()->Unit,
        val body: ()->Result<String>,val complete: (Result<String>)->Unit,val allowBrowsing: Boolean) {
        internal val claimed=AtomicBoolean(false)
        @Volatile var progress=title
    }
    private val observers=CopyOnWriteArrayList<(ProcessingState.Snapshot)->Unit>()
    private val state=ProcessingState()
    @Volatile private var work: Work?=null
    val snapshot: ProcessingState.Snapshot get()=state.snapshot.let { s -> work?.takeIf { s.active }?.let { s.copy(message=it.progress) } ?: s }
    val busy get()=state.snapshot.active
    val browsingAllowed get()=work?.allowBrowsing==true
    fun availableTo(owner: Any)=state.availableTo(owner)
    fun needsReload(owner: Any,openedRevision: Long)=state.needsReload(owner,openedRevision)
    fun observe(observer: (ProcessingState.Snapshot)->Unit): ()->Unit {
        observers.add(observer);observer(snapshot);return { observers.remove(observer) }
    }
    private fun publish() { val value=snapshot;observers.forEach { runCatching { it(value) } } }
    fun launch(context: Context,owner: Any,title: String,cancel: ()->Unit,body: ()->Result<String>,complete: (Result<String>)->Unit,allowBrowsing: Boolean=false) {
        check(Looper.myLooper()==Looper.getMainLooper())
        val job=synchronized(this) {
            check(work==null) { "A processing task is already running" }
            Work(state.begin(owner,title),owner,title,cancel,body,complete,allowBrowsing).also { work=it }
        }
        try {
            context.applicationContext.startForegroundService(Intent(context.applicationContext,ProcessingService::class.java).putExtra("job_id",job.id))
            publish()
        } catch(error: Exception) { finish(job,Result.failure(error)) }
    }
    internal fun claim(id: Long)=work?.takeIf { it.id==id && it.claimed.compareAndSet(false,true) }
    fun progress(owner: Any,message: String) { work?.takeIf { it.owner===owner }?.let { it.progress=message } }
    internal fun pause(id: Long) { work?.takeIf { it.id==id }?.let { it.progress="Film Foundry · Pausing…";it.cancel() } }
    fun pauseCurrent() { work?.let { pause(it.id) } }
    internal fun finish(job: Work,result: Result<String>) {
        check(Looper.myLooper()==Looper.getMainLooper())
        synchronized(this) {
            if(work!==job) return
            check(state.finish(job.id,result.fold({ it },{ it.message.orEmpty() })))
            work=null
        }
        try { job.complete(result) } finally { publish() }
    }
}
