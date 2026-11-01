package com.filmfoundry.interchange

import java.io.*
import java.util.zip.Deflater
import java.util.zip.ZipEntry
import java.util.zip.ZipInputStream
import java.util.zip.ZipOutputStream

data class NpzReadLimits(val maxMembers: Int=Int.MAX_VALUE,val maxTotalBytes: Long=Long.MAX_VALUE,
    val maxStringBytes: Long=NpyCodec.MAX_BYTES,val maxRetainedBytes: Long=NpyCodec.MAX_BYTES,
    val onMember: ((String,Long,Long)->Unit)?=null)

object NpzFile {
    fun read(bytes: ByteArray): Map<String, NpyValue> = read(ByteArrayInputStream(bytes))

    /** Caller owns the stream. Every entry, including unused fields, is validated. */
    fun read(input: InputStream): Map<String, NpyValue> = read(input) { true }

    fun read(input: InputStream, retain: (String) -> Boolean): Map<String, NpyValue> = readChunks(input, retain, null)

    /** Chunk values are borrowed until the callback returns. No numeric array is retained unless requested. */
    fun readChunks(input: InputStream, retain: (String) -> Boolean,
        numericSink: ((String, IntArray, Int, FloatArray, Int) -> Unit)?): Map<String, NpyValue> {
        return readChunks(input,retain,NpzReadLimits(),numericSink)
    }

    fun readChunks(input: InputStream, retain: (String) -> Boolean,limits: NpzReadLimits,
        numericSink: ((String, IntArray, Int, FloatArray, Int) -> Unit)?): Map<String, NpyValue> {
        val prefix = ByteArray(4)
        NpyCodec.readFully(input, prefix, 4)
        if (prefix[0] != 'P'.code.toByte() || prefix[1] != 'K'.code.toByte())
            throw NpyException("NPZ is truncated or not a valid ZIP/NPZ archive.")
        val out = linkedMapOf<String, NpyValue>()
        val seen = mutableSetOf<String>()
        var members=0
        var total=0L
        val combined = SequenceInputStream(ByteArrayInputStream(prefix), object : FilterInputStream(input) { override fun close() {} })
        BufferedZipInput(object : FilterInputStream(combined) { override fun close() {} }).use { zip ->
            val drain = ByteArray(8192)
            while (true) {
                val entry = zip.nextEntry ?: break
                if(++members > limits.maxMembers) throw NpyException("NPZ has too many members.")
                if (entry.isDirectory) {
                    if(entry.size > 0 || zip.read() != -1) throw NpyException("NPZ directory must be empty.")
                    zip.closeEntry();limits.onMember?.invoke(entry.name,entry.size,entry.crc);continue
                }
                val filename = entry.name.substringAfterLast('/').substringAfterLast('\\')
                if (!filename.endsWith(".npy")) throw NpyException("NPZ member '$filename' is not an NPY array.")
                val key = filename.removeSuffix(".npy")
                if (key.isEmpty()) throw NpyException("NPZ member name is empty.")
                if (!seen.add(key)) throw NpyException("NPZ contains duplicate array '$key'.")
                if (entry.size > NpyCodec.MAX_BYTES) throw NpyException("NPZ member '$filename' is too large.")
                val limited = EntryInput(zip, filename) { count ->
                    total=Math.addExact(total,count.toLong())
                    if(total > limits.maxTotalBytes) throw NpyException("NPZ expanded data is too large.")
                }
                val keep = retain(key)
                val value = NpyCodec.readValue(limited, if (entry.size >= 0) entry.size else NpyCodec.MAX_BYTES, keep,
                    numericSink?.let { sink -> { shape, offset, values, count -> sink(key, shape, offset, values, count) } },
                    maxStringBytes=limits.maxStringBytes,maxRetainedBytes=limits.maxRetainedBytes)
                if (keep) out[key] = value!!
                while (limited.read(drain) != -1) { }
                zip.closeEntry()
                limits.onMember?.invoke(entry.name,entry.size,entry.crc)
            }
        }
        if (seen.isEmpty()) throw NpyException("NPZ archive has no arrays.")
        return out
    }

    fun write(entries: Map<String, NpyValue>): ByteArray = ByteArrayOutputStream().also { write(entries, it) }.toByteArray()

    /** Caller owns output; closing ZIP releases compression state without closing output. */
    fun write(entries: Map<String, NpyValue>, output: OutputStream) {
        writeMembers(entries.mapValues { (_, value) -> { member: OutputStream -> NpyCodec.write(value, member) } }, output)
    }

    /** Same ZIP transport for full arrays and file-backed complete NPY members. */
    fun writeMembers(entries: Map<String, (OutputStream) -> Unit>, output: OutputStream,
        compressionLevel: Int = Deflater.DEFAULT_COMPRESSION) {
        if (entries.isEmpty()) throw NpyException("NPZ archive has no arrays.")
        require(compressionLevel in Deflater.DEFAULT_COMPRESSION..Deflater.BEST_COMPRESSION) { "invalid NPZ compression level" }
        object: ZipOutputStream(object : FilterOutputStream(output) {
            override fun write(bytes: ByteArray, offset: Int, length: Int) = out.write(bytes, offset, length)
            override fun close() = flush()
        }) {
            // Avoid feeding large media through the JDK's 512-byte deflater output buffer.
            // Compression level, member order and NPY payloads are unchanged.
            init { buf=ByteArray(65536) }
        }.use { zip ->
            zip.setMethod(ZipOutputStream.DEFLATED)
            zip.setLevel(compressionLevel)
            for ((key, value) in entries) {
                if (key.isEmpty() || '/' in key || '\\' in key) throw NpyException("NPZ array name '$key' is invalid.")
                zip.putNextEntry(ZipEntry("$key.npy"))
                value(zip)
                zip.closeEntry()
            }
        }
    }

    /** Inflater and pushback capacities must match: ZIP can read ahead into the next member. */
    private class BufferedZipInput(source: InputStream): ZipInputStream(source) {
        init {
            buf=ByteArray(65536)
            // ZipInputStream normally creates a 512-byte pushback stream. Increasing only buf
            // would overflow it when returning read-ahead bytes after an entry's inflater ends.
            this.`in`=PushbackInputStream(source,buf.size)
        }
    }

    private class EntryInput(private val zip: InputStream, private val name: String,private val onRead: (Int)->Unit) : InputStream() {
        private var total = 0L
        private fun count(n: Int): Int {
            if (n > 0) { total += n;onRead(n) }
            if (total > NpyCodec.MAX_BYTES) throw NpyException("NPZ member '$name' is too large.")
            return n
        }
        override fun read(): Int = zip.read().also { if (it >= 0) count(1) }
        override fun read(bytes: ByteArray, offset: Int, length: Int): Int = count(zip.read(bytes, offset, length))
    }
}
