package com.filmfoundry.app.session

/** Application-level ownership; a rebuilt screen cannot edit a stale library snapshot during work. */
class ProcessingState {
    data class Snapshot(val revision: Long=0,val owner: Any?=null,val active: Boolean=false,val title: String="",val message: String="")
    @Volatile var snapshot=Snapshot();private set
    @Synchronized fun begin(owner: Any,title: String): Long {
        check(!snapshot.active) { "A processing task is already running" }
        val id=snapshot.revision+1;snapshot=Snapshot(id,owner,true,title,title);return id
    }
    @Synchronized fun finish(id: Long,message: String): Boolean {
        val current=snapshot
        if(!current.active || current.revision!=id) return false
        snapshot=current.copy(revision=id+1,active=false,message=message);return true
    }
    fun availableTo(owner: Any)=snapshot.let { !it.active || it.owner===owner }
    fun needsReload(owner: Any,openedRevision: Long)=snapshot.let { !it.active && it.revision>openedRevision && it.owner!==owner }
}
