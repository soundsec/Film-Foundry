package com.filmfoundry.interchange

import java.io.ByteArrayInputStream
import org.junit.jupiter.api.Assertions.*
import org.junit.jupiter.api.Test

class ArchiveReadBufferTest {
    private fun archive(): Pair<Map<String,NpyValue>,ByteArray> {
        val random=java.util.Random(17)
        val entries=linkedMapOf<String,NpyValue>(
            "constant" to NpyValue.Float32(FloatNd(intArrayOf(1024,1024,1),FloatArray(1024*1024) { -0f })),
            "variable" to NpyValue.Float32(FloatNd(intArrayOf(1024,1024,1),FloatArray(1024*1024) { random.nextFloat() })),
            "tail" to NpyValue.UnicodeScalar("last member"))
        return entries to NpzFile.write(entries)
    }
    @Test fun multiMemberReadAheadPreservesExactBitsAndLeavesCallerOpen() {
        val (expected,bytes)=archive();var closed=false
        val input=object: ByteArrayInputStream(bytes) { override fun close() { closed=true } }
        val actual=NpzFile.read(input)
        for(key in listOf("constant","variable")) {
            val a=(expected.getValue(key) as NpyValue.Float32).array.values;val b=(actual.getValue(key) as NpyValue.Float32).array.values
            assertEquals(a.size,b.size);for(i in a.indices) assertEquals(a[i].toRawBits(),b[i].toRawBits())
        }
        assertEquals(expected["tail"],actual["tail"]);assertFalse(closed)
    }
    @Test fun representativeReadTiming() {
        org.junit.jupiter.api.Assumptions.assumeTrue(System.getenv("FF_ARCHIVE_TIMING")=="1")
        val bytes=archive().second;val times=mutableListOf<Double>();val calls=mutableListOf<Int>()
        repeat(5) { pass ->
            var reads=0
            val input=object: ByteArrayInputStream(bytes) { override fun read(b: ByteArray,off: Int,len: Int): Int { reads++;return super.read(b,off,len) } }
            val start=System.nanoTime();NpzFile.readChunks(input,{ false },null)
            if(pass>=2) { times+=(System.nanoTime()-start)/1e6;calls+=reads }
        }
        println("archive_read_8MiB_payload compressed=${bytes.size} ms=$times compressed_reads=$calls")
    }
}
