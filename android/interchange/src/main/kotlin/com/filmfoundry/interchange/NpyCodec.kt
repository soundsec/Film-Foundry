package com.filmfoundry.interchange

import java.io.ByteArrayInputStream
import java.io.ByteArrayOutputStream
import java.io.InputStream
import java.io.OutputStream
import java.nio.ByteBuffer
import java.nio.ByteOrder
import java.nio.charset.StandardCharsets

class NpyException(message: String) : IllegalArgumentException(message)

class FloatNd(val shape: IntArray, val values: FloatArray) {
    init {
        if (shape.any { it <= 0 }) throw NpyException("array dimensions must be positive.")
        val expected = shape.fold(1L) { acc, n -> Math.multiplyExact(acc, n.toLong()) }
        if (expected != values.size.toLong())
            throw NpyException("array size ${values.size} does not match shape ${shape.contentToString()}.")
    }
    fun copy(): FloatNd = FloatNd(shape.copyOf(), values.copyOf())
}

sealed class NpyValue {
    data class Float32(val array: FloatNd) : NpyValue()
    data class UnicodeScalar(val text: String) : NpyValue()
}

object NpyCodec {
    internal const val MAX_BYTES = 512L * 1024 * 1024
    private const val CHUNK_BYTES = 64 * 1024
    private const val MAX_HEADER_BYTES = 1024 * 1024
    private val MAGIC = byteArrayOf(0x93.toByte(), 78, 85, 77, 80, 89)
    private val DESCR_RE = Regex("""'descr':\s*'([^']+)'""")
    private val FORTRAN_RE = Regex("""'fortran_order':\s*(True|False)""")
    private val SHAPE_RE = Regex("""'shape':\s*\(([^)]*)\)""")
    private val UNICODE_RE = Regex("""^([<>|=])U(\d+)$""")
    private val BYTES_RE = Regex("""^[<>|=|]?S(\d+)$""")

    fun encode(value: NpyValue): ByteArray = ByteArrayOutputStream().also { write(value, it) }.toByteArray()

    /** Does not close or flush the caller's stream. Scratch storage is at most 64 KiB. */
    fun write(value: NpyValue, output: OutputStream) {
        val scratch = ByteBuffer.allocate(CHUNK_BYTES).order(ByteOrder.LITTLE_ENDIAN)
        fun flushChunk() {
            output.write(scratch.array(), 0, scratch.position())
            scratch.clear()
        }
        when (value) {
            is NpyValue.Float32 -> {
                output.write(header("<f4", value.array.shape))
                val values=value.array.values;val floats=scratch.asFloatBuffer()
                var offset=0
                while(offset<values.size) {
                    val n=minOf(floats.capacity(),values.size-offset)
                    for(i in offset until offset+n) {
                        val v=values[i]
                        if(!(v>=-Float.MAX_VALUE && v<=Float.MAX_VALUE)) throw NpyException("array cannot contain NaN or Infinity.")
                    }
                    floats.clear();floats.put(values,offset,n)
                    output.write(scratch.array(),0,n*4);offset+=n
                }
            }
            is NpyValue.UnicodeScalar -> {
                val text = value.text
                output.write(header("<U${text.codePointCount(0, text.length)}", intArrayOf()))
                var index = 0
                while (index < text.length) {
                    val code = text.codePointAt(index)
                    scratch.putInt(code)
                    if (!scratch.hasRemaining()) flushChunk()
                    index += Character.charCount(code)
                }
            }
        }
        if (scratch.position() != 0) flushChunk()
    }

    fun decode(bytes: ByteArray): NpyValue = read(ByteArrayInputStream(bytes), bytes.size.toLong())

    /** Reads one NPY without closing the stream. maxBytes includes header and payload. */
    fun read(input: InputStream, maxBytes: Long = MAX_BYTES): NpyValue = readValue(input, maxBytes, true)!!

    data class Header(val descriptor: String,val shape: IntArray,val headerBytes: Long)
    /** Display inspection only: reads no numeric payload and does not validate its values. */
    fun readHeader(input: InputStream,maxBytes: Long=MAX_BYTES): Header {
        val prefix = ByteArray(10)
        readFully(input, prefix, prefix.size)
        if (!MAGIC.indices.all { prefix[it] == MAGIC[it] }) throw NpyException("not a NumPy NPY array.")
        val major = prefix[6].toInt() and 255
        val minor = prefix[7].toInt() and 255
        val headerOffset: Int
        val headerLength: Int
        when (major) {
            1 -> {
                headerOffset = 10
                headerLength = ByteBuffer.wrap(prefix, 8, 2).order(ByteOrder.LITTLE_ENDIAN).short.toInt() and 65535
            }
            2, 3 -> {
                val length = ByteArray(4)
                length[0] = prefix[8]; length[1] = prefix[9]
                val rest = ByteArray(2)
                readFully(input, rest, 2)
                length[2] = rest[0]; length[3] = rest[1]
                headerOffset = 12
                headerLength = ByteBuffer.wrap(length).order(ByteOrder.LITTLE_ENDIAN).int
            }
            else -> throw NpyException("unsupported NPY version $major.$minor.")
        }
        if (headerLength < 0 || headerLength > MAX_HEADER_BYTES || headerOffset.toLong() + headerLength > maxBytes)
            throw NpyException("NPY header is truncated or too large.")
        val header = ByteArray(headerLength)
        readFully(input, header, header.size)
        val charset = if (major >= 3) StandardCharsets.UTF_8 else StandardCharsets.ISO_8859_1
        val text = String(header, charset).trim()
        val descr = DESCR_RE.find(text)?.groupValues?.get(1) ?: throw NpyException("NPY header missing descr.")
        val fortran = FORTRAN_RE.find(text)?.groupValues?.get(1) ?: throw NpyException("NPY header missing fortran_order.")
        val shapeInner = SHAPE_RE.find(text)?.groupValues?.get(1) ?: throw NpyException("NPY header missing shape.")
        if (fortran == "True") throw NpyException("NPY fortran_order arrays are not accepted.")
        if (descr.contains('O') || descr.contains('p') || descr.contains('V')) throw NpyException("object/pickle NPY arrays are not accepted.")
        val shape = parseShape(shapeInner)
        return Header(descr,shape,headerOffset.toLong()+headerLength)
    }

    /** Unretained numeric arrays receive exactly the same validation, using only chunk scratch. */
    internal fun readValue(input: InputStream, maxBytes: Long, retain: Boolean,
        numericSink: ((IntArray, Int, FloatArray, Int) -> Unit)? = null,
        maxStringBytes: Long = MAX_BYTES,maxRetainedBytes: Long = MAX_BYTES): NpyValue? {
        val header=readHeader(input,maxBytes)
        val descr=header.descriptor;val shape=header.shape
        val available = maxBytes - header.headerBytes
        val unicode = UNICODE_RE.matchEntire(descr)
        val bytesMatch = BYTES_RE.matchEntire(descr)
        if (unicode != null || bytesMatch != null) {
            if (shape.isNotEmpty()) throw NpyException("metadata NPY string must be a 0-d scalar.")
            val width = (unicode?.groupValues?.get(2) ?: bytesMatch!!.groupValues[1]).toInt()
            val need = Math.multiplyExact(width.toLong(), if (unicode != null) 4L else 1L)
            if (need > available || need > Int.MAX_VALUE || need > maxStringBytes) throw NpyException("NPY string payload is truncated or too large.")
            if (bytesMatch != null) {
                val data = ByteArray(width)
                readFully(input, data, width)
                var end = width
                while (end > 0 && data[end - 1] == 0.toByte()) end--
                return NpyValue.UnicodeScalar(String(data, 0, end, StandardCharsets.ISO_8859_1))
            }
            val scratch = ByteArray(minOf(CHUNK_BYTES.toLong(), need).toInt())
            val buffer = ByteBuffer.wrap(scratch).order(if (unicode!!.groupValues[1] == ">") ByteOrder.BIG_ENDIAN else ByteOrder.LITTLE_ENDIAN)
            val result = StringBuilder()
            var trailing = false
            var remaining = width
            while (remaining > 0) {
                val count = minOf(remaining, scratch.size / 4)
                readFully(input, scratch, count * 4)
                buffer.clear()
                repeat(count) {
                    val code = buffer.int
                    if (code == 0) trailing = true
                    else if (trailing) throw NpyException("NPY unicode payload has embedded NUL.")
                    else result.appendCodePoint(code)
                }
                remaining -= count
            }
            return NpyValue.UnicodeScalar(result.toString())
        }
        val itemBytes = numericItemBytes(descr)
        if (shape.isEmpty()) throw NpyException("numeric NPY array must have a shape.")
        val count = shape.fold(1L) { acc, n ->
            if (n <= 0) throw NpyException("array dimensions must be positive.")
            Math.multiplyExact(acc, n.toLong())
        }
        // Retained arrays are Float32 even when the archive stores FP16/uint8.
        if(retain && Math.multiplyExact(count,maxOf(4,itemBytes).toLong()) > maxRetainedBytes)
            throw NpyException("retained NPY array is too large.")
        val need = Math.multiplyExact(count, itemBytes.toLong())
        if (need > available || count > Int.MAX_VALUE) throw NpyException("NPY numeric payload is truncated or too large.")
        val values = if (retain) FloatArray(count.toInt()) else null
        val chunkValues = FloatArray(minOf(count.toInt(), CHUNK_BYTES / maxOf(4,itemBytes)))
        val scratch = ByteArray(chunkValues.size * itemBytes)
        val buffer = ByteBuffer.wrap(scratch).order(ByteOrder.LITTLE_ENDIAN)
        val floats=buffer.asFloatBuffer()
        var offset = 0
        while (offset < count.toInt()) {
            val n = minOf(count.toInt() - offset, scratch.size / itemBytes)
            readFully(input, scratch, n * itemBytes)
            buffer.clear()
            when(itemBytes) {
                1 -> for(i in 0 until n) chunkValues[i]=(buffer.get().toInt() and 255).toFloat()
                2 -> for(i in 0 until n) chunkValues[i]=halfToFloat(buffer.short.toInt() and 65535)
                4 -> { floats.clear();floats.get(chunkValues,0,n) }
                8 -> for(i in 0 until n) chunkValues[i]=buffer.double.toFloat()
            }
            for (i in 0 until n) {
                val v = chunkValues[i]
                // Ordered comparisons reject NaN as well as both infinities, without
                // per-sample Float.isInfinite/isNaN calls on Android's cold path.
                if (!(v>=-Float.MAX_VALUE && v<=Float.MAX_VALUE)) throw NpyException("array cannot contain NaN or Infinity.")
            }
            if(values!=null) chunkValues.copyInto(values,offset,0,n)
            numericSink?.invoke(shape, offset, chunkValues, n)
            offset += n
        }
        return values?.let { NpyValue.Float32(FloatNd(shape, it)) }
    }

    internal fun readFully(input: InputStream, bytes: ByteArray, count: Int) {
        var offset = 0
        while (offset < count) {
            val n = input.read(bytes, offset, count - offset)
            if (n < 0) throw NpyException("NPY payload is truncated.")
            if (n == 0) {
                val single = input.read()
                if (single < 0) throw NpyException("NPY payload is truncated.")
                bytes[offset++] = single.toByte()
            } else offset += n
        }
    }

    /** Standard archive storage types. Computation still receives Float32. */
    fun numericItemBytes(descriptor: String): Int = when(descriptor) {
        "<f2","|f2","=f2","f2" -> 2
        "<f4","|f4","=f4","f4" -> 4
        "<f8","|f8","=f8","f8" -> 8
        "|u1","<u1",">u1","=u1","u1" -> 1
        else -> throw NpyException("unsupported NPY dtype '$descriptor'.")
    }

    private fun halfToFloat(bits: Int): Float {
        val sign=(bits and 0x8000) shl 16
        val exponent=(bits ushr 10) and 31
        val fraction=bits and 1023
        if(exponent==0) {
            if(fraction==0) return Float.fromBits(sign)
            val value=fraction.toFloat()/16777216f
            return if(sign==0) value else -value
        }
        val expanded=if(exponent==31) 255 else exponent+112
        return Float.fromBits(sign or (expanded shl 23) or (fraction shl 13))
    }

    internal fun floatHeader(shape: IntArray): ByteArray = header("<f4", shape)

    private fun header(descr: String, shape: IntArray): ByteArray {
        val shapeText = when (shape.size) { 0 -> "()"; 1 -> "(${shape[0]},)"; else -> shape.joinToString(prefix = "(", postfix = ")", separator = ", ") }
        val dict = "{'descr': '$descr', 'fortran_order': False, 'shape': $shapeText, }".toByteArray(StandardCharsets.US_ASCII)
        val padding = 64 - ((10 + dict.size + 1) % 64)
        val header = dict + ByteArray(padding) { 32 } + byteArrayOf(10)
        if (header.size > 65535) throw NpyException("NPY header is too large.")
        return ByteBuffer.allocate(10 + header.size).order(ByteOrder.LITTLE_ENDIAN).apply {
            put(MAGIC); put(1); put(0); putShort(header.size.toShort()); put(header)
        }.array()
    }

    private fun parseShape(inner: String): IntArray {
        if (inner.trim().isEmpty()) return intArrayOf()
        return inner.split(',').map { it.trim() }.filter { it.isNotEmpty() }.map {
            it.toIntOrNull() ?: throw NpyException("invalid NPY shape.")
        }.toIntArray()
    }
}
