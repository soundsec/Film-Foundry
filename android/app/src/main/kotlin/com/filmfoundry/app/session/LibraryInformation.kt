package com.filmfoundry.app.session

import com.filmfoundry.platform.*
import com.filmfoundry.workflow.*
import com.filmfoundry.interchange.StillFrameFormat
import java.io.File

data class LibraryFacts(val width: Int?=null,val height: Int?=null,val polarity: String?=null,
    val time: String?=null,val fileTime: Boolean=false,val predicted: Boolean=false,val mixedSizes: Boolean=false)

/** Application metadata only. Call reads off the UI thread; no image arrays or chemistry runs. */
class LibraryInformation(root: File) {
    private val files=PrivateOpticalInformation(root)
    private val receipts=CaptureReceiptStore(root)
    private data class Entry(val path: String,val length: Long,val modified: Long,val facts: LibraryFacts)
    private val negativeFacts=object: LinkedHashMap<String,Entry>(32,.75f,true) {
        override fun removeEldestEntry(eldest: MutableMap.MutableEntry<String,Entry>?)=size>80
    }
    @Synchronized fun peek(resultId: String): LibraryFacts?=negativeFacts[resultId]?.facts
    fun negative(result: DevelopedResult): LibraryFacts {
        val signature=files.signature(result.opticalPath)
        if(!signature.exists) return LibraryFacts()
        val length=signature.length;val modified=signature.modified
        synchronized(this) { negativeFacts[result.resultId]?.takeIf { it.path==result.opticalPath && it.length==length && it.modified==modified }?.let { return it.facts } }
        val read=files.read(result.opticalPath)
        val facts=LibraryFacts(read.width,read.height,read.polarity,read.createdAt ?: read.fileTime,read.createdAt==null)
        synchronized(this) { negativeFacts[result.resultId]=Entry(result.opticalPath,length,modified,facts) }
        return facts
    }
    fun opticalRecord(result: DevelopedResult)=files.read(result.opticalPath).metadata
    fun photo(saved: SavedScan,source: DevelopedResult?): LibraryFacts {
        val original=if(saved.sourcePolarity==null && source!=null) runCatching { negative(source) }.getOrNull() else null
        return LibraryFacts(saved.displayWidth,saved.displayHeight,saved.sourcePolarity ?: original?.polarity,saved.createdAt)
    }
    fun roll(roll: Roll): LibraryFacts {
        val sizes=roll.frames.mapNotNull { frame -> frame.capturePath?.let { path -> runCatching {
            receipts.read(path).sources.first().let { source ->
                val gate=StillFrameFormat.read(source.record["frame_format"],source.width,source.height)
                gate.width to gate.height
            }
        }.getOrNull() } }.distinct()
        val settings=roll.captureSettings
        val planned=if(sizes.isEmpty()) runCatching {
            val width=(settings["precision_width"] as? Number)?.toInt() ?: return@runCatching null
            val height=(settings["precision_height"] as? Number)?.toInt() ?: return@runCatching null
            StillFrameFormat.create(settings["format"]?.toString() ?: "135",false,width,height,
                settings["gate_revision"]?.toString() ?: StillFrameFormat.MODEL).let { it.width to it.height }
        }.getOrNull() else null
        val size=sizes.singleOrNull() ?: planned
        return LibraryFacts(size?.first,size?.second,roll.filmRecipe["image_polarity"]?.toString()?.trim()?.lowercase()?.takeIf { it in listOf("positive","negative") },
            settings["created_at"] as? String,predicted=planned!=null,mixedSizes=sizes.size>1)
    }
    fun captureRecord(roll: Roll,index: Int): Map<String,Any?> {
        val frame=roll.frames.firstOrNull { it.frameIndex==index } ?: return emptyMap()
        val path=frame.capturePath ?: return emptyMap()
        if(!receipts.exists(path)) return emptyMap()
        val record=receipts.read(path)
        return mapOf("exposures" to record.sources.size,"sources" to record.sources.map { mapOf("width" to it.width,"height" to it.height,"ev" to it.ev,"capture" to it.record) })
    }
}
