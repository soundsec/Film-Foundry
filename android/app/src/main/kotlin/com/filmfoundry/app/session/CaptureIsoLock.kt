package com.filmfoundry.app.session

/** Temporary native exposure controls belong to one foreground capture session and one roll. */
class CaptureIsoLock {
    private var rollId: String?=null
    private var generation=0L
    var unlocked=false
        private set
    class Snapshot internal constructor(internal val generation: Long,internal val unlocked: Boolean)
    fun selectRoll(id: String?) {
        if(id!=rollId) { rollId=id;reset() }
    }
    fun reset() { generation++;unlocked=false }
    fun leave() { rollId=null;reset() }
    fun setUnlocked(value: Boolean) { unlocked=value && rollId!=null }
    fun snapshot()=Snapshot(generation,unlocked)
    fun restore(value: Snapshot) { if(value.generation==generation) unlocked=value.unlocked }
}
