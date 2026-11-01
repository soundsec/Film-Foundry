package com.filmfoundry.workflow

import com.filmfoundry.interchange.OpticalPreview

/** Borrowed read-only display proxies of immutable library results. Never caches a full master. */
class OpticalPreviewCache(private val maxPixelBytes: Long=16L*1024*1024) {
    private val entries=java.util.LinkedHashMap<String,OpticalPreview>(16,0.75f,true)
    private val sizes=HashMap<String,Long>()
    private var used=0L
    private var revision=0L
    init { require(maxPixelBytes >= 0) }
    fun get(resultKey: String,load: ()->OpticalPreview): OpticalPreview {
        val token=synchronized(this) {
            entries[resultKey]?.let { return it }
            revision
        }
        // I/O must not hold the monitor: UI clear/trim and newer readers stay responsive.
        val value=load();val archive=value.archive
        val bytes=(archive.opticalDensityRgb.values.size.toLong()+(archive.spectralComponentAmounts?.values?.size ?: 0)+
            (archive.spectralPostProcessDensityRgb?.values?.size ?: 0))*4
        synchronized(this) { if(token==revision && bytes in 1..maxPixelBytes) retain(resultKey,value,bytes) }
        return value
    }
    /** Drop every proxy whose key belongs to this result. Other results stay. */
    @Synchronized fun dropResult(resultId: String) {
        revision++ // Readers that began before deletion cannot refill removed proxies.
        val gone=entries.keys.filter { key -> key.removePrefix("192:").substringBefore(':')==resultId }
        for(key in gone) { entries.remove(key);used-=sizes.remove(key) ?: 0L }
    }
    @Synchronized fun clear() { revision++;entries.clear();sizes.clear();used=0L }
    private fun retain(key: String,value: OpticalPreview,bytes: Long) {
        if(entries.put(key,value)!=null) used-=sizes.remove(key) ?: 0L
        sizes[key]=bytes;used+=bytes
        while(used>maxPixelBytes && entries.size>1) {
            val eldest=entries.entries.first()
            if(eldest.key==key) break
            used-=sizes.remove(eldest.key) ?: 0L
            entries.remove(eldest.key)
        }
    }
}
