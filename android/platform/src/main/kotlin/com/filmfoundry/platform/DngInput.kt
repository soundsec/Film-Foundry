package com.filmfoundry.platform

import java.io.File
import java.io.RandomAccessFile
import java.nio.ByteBuffer
import java.nio.ByteOrder

/** TIFF random access with a fixed four-page file cache; no whole-file heap buffer. */
internal class DngInput private constructor(private val memory: ByteArray?,private val file: RandomAccessFile?): AutoCloseable {
    val size: Long=memory?.size?.toLong() ?: file!!.length()
    private var little=true
    private var lastPage=-1L
    private var lastBytes=ByteArray(0)
    private val pages=object: LinkedHashMap<Long,ByteArray>(4,.75f,true) {
        override fun removeEldestEntry(eldest: MutableMap.MutableEntry<Long,ByteArray>?)=size>4
    }
    operator fun get(offset: Int): Byte {
        require(offset>=0 && offset.toLong()<size) { "DNG read outside file" }
        memory?.let { return it[offset] }
        val page=offset.toLong()/65536
        if(page==lastPage) return lastBytes[offset%65536]
        val bytes=pages.getOrPut(page) {
            val start=page*65536
            ByteArray(minOf(65536L,size-start).toInt()).also { file!!.seek(start);file.readFully(it) }
        }
        lastPage=page;lastBytes=bytes
        return bytes[offset%65536]
    }
    fun order(value: ByteOrder): DngInput { little=value==ByteOrder.LITTLE_ENDIAN;return this }
    private fun bits(offset: Int,count: Int): Long {
        var value=0L
        for(i in 0 until count) value=value or ((get(offset+i).toLong() and 255L) shl ((if(little) i else count-1-i)*8))
        return value
    }
    fun getShort(offset: Int)=bits(offset,2).toShort()
    fun getInt(offset: Int)=bits(offset,4).toInt()
    fun getFloat(offset: Int)=Float.fromBits(getInt(offset))
    fun getDouble(offset: Int)=Double.fromBits(bits(offset,8))
    fun metadata(offset: Int,count: Int): ByteBuffer {
        require(count in 0..1048576 && offset>=0 && offset.toLong()+count<=size) { "DNG metadata exceeds bounded read" }
        return ByteBuffer.wrap(ByteArray(count) { get(offset+it) })
    }
    override fun close() { pages.clear();lastBytes=ByteArray(0);lastPage=-1;file?.close() }
    companion object {
        fun memory(bytes: ByteArray)=DngInput(bytes,null)
        fun file(file: File): DngInput {
            require(file.length() in 8..(512L*1024*1024)) { "DNG size outside supported file range" }
            return DngInput(null,RandomAccessFile(file,"r"))
        }
    }
}
