package com.filmfoundry.platform

import android.app.ActivityManager
import android.content.Context
import android.os.Debug

/** A job-start snapshot, not a promise of allocatable RAM or a pixel-size limit. */
data class ProcessingMemorySnapshot(
    val heapLimit: Long,
    val heapUsed: Long,
    val nativeAllocated: Long = 0,
    val systemAvailable: Long? = null,
    val systemThreshold: Long = 0,
    val systemLowMemory: Boolean = false,
    val normalClassMiB: Int? = null,
    val largeClassMiB: Int? = null,
) {
    // Include tracked native allocations in our conservative working allowance:
    // modern Android Bitmaps need not appear in Runtime's Java heap usage.
    val reserveBytes: Long get() = (heapLimit / 16).coerceIn(16L*1048576,64L*1048576)
    val budgetBytes: Long get() {
        if(systemLowMemory) return 0
        val heap=(heapLimit-heapUsed.coerceAtLeast(0)-nativeAllocated.coerceAtLeast(0)-reserveBytes).coerceAtLeast(0)
        val system=systemAvailable?.let { (it-systemThreshold.coerceAtLeast(0)-reserveBytes).coerceAtLeast(0) }
        return if(system==null) heap else minOf(heap,system)
    }
    fun requireAllocation(bytes: Long,operation: String) {
        require(bytes>=0)
        if(bytes>budgetBytes) throw CaptureNotReady("$operation needs about ${(bytes+1048575)/1048576} MiB; current working budget is ${budgetBytes/1048576} MiB. Original sources retained; retry after freeing memory. No automatic resize.")
    }
}

object AndroidProcessingMemory {
    fun snapshot(context: Context?=null): ProcessingMemorySnapshot {
        val runtime=Runtime.getRuntime()
        val manager=context?.getSystemService(Context.ACTIVITY_SERVICE) as? ActivityManager
        val info=manager?.let { ActivityManager.MemoryInfo().also(it::getMemoryInfo) }
        return ProcessingMemorySnapshot(runtime.maxMemory(),runtime.totalMemory()-runtime.freeMemory(),
            Debug.getNativeHeapAllocatedSize(),info?.availMem,info?.threshold ?: 0,info?.lowMemory ?: false,
            manager?.memoryClass,manager?.largeMemoryClass)
    }
}
