package com.filmfoundry.workflow

import com.filmfoundry.interchange.RowArchiveSpool
import java.io.Closeable

/** Cheap identity of a committed private file; no image hashing or persistent cache. */
data class RowMediumVersion(val location: String,val bytes: Long,val modifiedMillis: Long,val fileKey: String?=null)

/** One frame, one execution chain. The durable NPZ is always committed before this lease is offered. */
class RowMediumHandoff: Closeable {
    private data class Entry(val io: RowMediumIo,val path: String,val version: RowMediumVersion,val rows: RowArchiveSpool)
    private var entry: Entry?=null
    private var closed=false
    internal fun retain(io: RowMediumIo,path: String,rows: RowArchiveSpool): Boolean {
        check(!closed && entry==null) { "Row medium handoff is already occupied or closed" }
        val version=try { io.version(path) } catch(_: Exception) { null } ?: return false
        entry=Entry(io,path,version,rows);return true
    }
    internal fun take(io: RowMediumIo,path: String): RowArchiveSpool? {
        check(!closed)
        val previous=entry ?: return null
        entry=null
        val current=try { io.version(path) } catch(_: Exception) { null }
        if(previous.io===io && previous.path==path && current==previous.version) return previous.rows
        previous.rows.close();return null
    }
    override fun close() {
        closed=true
        val previous=entry;entry=null;previous?.rows?.close()
    }
}
