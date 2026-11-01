package com.filmfoundry.interchange

import java.io.*
import java.nio.ByteBuffer
import java.nio.ByteOrder
import java.nio.file.Files

/** Disposable raw FP32 workspace. Only the completed standard NPZ is a medium. */
class RowArchiveSpool(root: File, height: Int, width: Int,
    private val checkCancel: () -> Unit = {}) : Closeable {
    private val directory: File
    private val lease: OwnedScratch.Lease
    private val fields = linkedMapOf<String, Field>()
    private val constants = linkedMapOf<String, NpyValue>()
    private var keys: Set<String>? = null
    private var rowsWritten = 0
    private val scratch = ByteBuffer.allocate(65536).order(ByteOrder.LITTLE_ENDIAN)
    private val scratchFloats=scratch.asFloatBuffer()
    var height: Int = height
        private set
    var width: Int = width
        private set

    init {
        if (!root.isDirectory && !root.mkdirs() && !root.isDirectory) throw IOException("cannot create row workspace")
        lease=OwnedScratch.create(root)
        directory=lease.directory
    }

    private inner class Field(val shape: IntArray) {
        val file = File(directory, "array_${fields.size}.f32")
        private var stream: OutputStream? = null
        private var reader: RandomAccessFile? = null
        private var sealed = false
        private var materialized = false
        private var constantBits = 0
        var count = 0L

        // Only the synchronous NpyCodec numeric sink may mark its values already finite.
        fun append(values: FloatArray, size: Int, finiteValidated: Boolean = false) {
            if (sealed) throw IOException("row field is sealed")
            var offset = 0
            while (offset < size) {
                checkCancel()
                val n = minOf(size - offset, scratch.capacity() / 4)
                if (count == 0L) constantBits = values[offset].toRawBits()
                if (!finiteValidated) for (i in offset until offset+n) {
                    val v=values[i]
                    if (!(v>=-Float.MAX_VALUE && v<=Float.MAX_VALUE)) throw NpyException("array cannot contain NaN or Infinity.")
                }
                var constant = stream == null
                if (constant) for (i in offset until offset+n) {
                    if (values[i].toRawBits() != constantBits) { constant=false;break }
                }
                if (stream == null && !constant) {
                    // Preserve the exact constant prefix, including the sign of zero.
                    stream = file.outputStream()
                    materialized = true
                    writeConstant(stream!!, count)
                }
                if (stream != null) {
                    // Constant fields need neither serialization nor a scratch-file write.
                    scratchFloats.clear();scratchFloats.put(values,offset,n)
                    stream!!.write(scratch.array(), 0, n * 4)
                }
                count += n
                offset += n
            }
        }

        private fun writeConstant(output: OutputStream, size: Long) {
            scratch.clear()
            val chunk = minOf(size, (scratch.capacity() / 4).toLong()).toInt()
            repeat(chunk) { scratch.putInt(constantBits) }
            var remaining = size
            while (remaining > 0) {
                checkCancel()
                val n = minOf(remaining, chunk.toLong()).toInt()
                output.write(scratch.array(), 0, n * 4)
                remaining -= n
            }
        }

        fun seal() { sealed = true; stream?.close(); stream = null }

        fun close() { try { seal() } finally { reader?.close();reader=null } }

        fun writePayload(output: OutputStream) {
            if (!materialized) {
                writeConstant(output, count)
            } else file.inputStream().use { input ->
                while (true) {
                    checkCancel()
                    val n = input.read(scratch.array())
                    if (n < 0) break
                    output.write(scratch.array(), 0, n)
                }
            }
        }

        fun rows(start: Int, countRows: Int): FloatNd {
            seal()
            val countValues = Math.multiplyExact(Math.multiplyExact(countRows, width), shape[2])
            val values = FloatArray(countValues)
            checkCancel()
            if (!materialized) values.fill(Float.fromBits(constantBits))
            else {
                // This spool already has a single-reader contract (shared scratch buffer).
                // Reuse its handle across row blocks; the owner closes it before deleting files.
                val input=reader ?: RandomAccessFile(file,"r").also { reader=it }
                input.seek(start.toLong() * width * shape[2] * 4)
                var offset = 0
                while (offset < values.size) {
                    checkCancel()
                    val n = minOf(values.size - offset, scratch.capacity() / 4)
                    input.readFully(scratch.array(), 0, n * 4)
                    scratch.clear()
                    scratchFloats.clear();scratchFloats.get(values,offset,n)
                    offset += n
                }
            }
            return FloatNd(intArrayOf(countRows, width, shape[2]), values)
        }
    }

    /** Entries have already passed the same typed codec as whole-frame writes. */
    fun append(entries: Map<String, NpyValue>, rows: Int) {
        checkCancel()
        require(rows > 0 && rowsWritten.toLong() + rows <= height) { "row output exceeds full frame" }
        if (keys == null) keys = entries.keys.toSet() else require(keys == entries.keys) { "row fields changed" }
        for ((key, value) in entries) {
            if (value is NpyValue.Float32 && value.array.shape.size == 3) {
                val shape = value.array.shape
                require(shape[0] == rows && shape[1] == width) { "row block dimensions mismatch" }
                val global = intArrayOf(height, width, shape[2])
                require(global.fold(1L) { a, b -> Math.multiplyExact(a, b.toLong()) } * 4 + NpyCodec.floatHeader(global).size <= NpyCodec.MAX_BYTES) { "NPZ member exceeds size limit" }
                val field = fields.getOrPut(key) { Field(global) }
                require(field.shape.contentEquals(global)) { "row channel count changed" }
                field.append(value.array.values, value.array.values.size)
            } else {
                val old = constants[key]
                if (old != null) require(equalValue(old, value)) { "row metadata or base changed" }
                else constants[key] = value
            }
        }
        rowsWritten += rows
    }

    fun pack(output: OutputStream) = pack(output, java.util.zip.Deflater.DEFAULT_COMPRESSION)

    /** Compression changes transport cost only; all NPY payloads retain their exact bits. */
    fun pack(output: OutputStream, compressionLevel: Int) {
        require(rowsWritten == height && height > 0) { "row medium is incomplete" }
        val members = linkedMapOf<String, (OutputStream) -> Unit>()
        for ((key, field) in fields) {
            field.seal()
            require(field.count == field.shape.fold(1L) { a, b -> Math.multiplyExact(a, b.toLong()) })
            members[key] = { member ->
                member.write(NpyCodec.floatHeader(field.shape))
                field.writePayload(member)
            }
        }
        for ((key, value) in constants) members[key] = { member -> checkCancel(); NpyCodec.write(value, member) }
        NpzFile.writeMembers(members, output, compressionLevel)
        checkCancel()
    }

    fun latentRows(start: Int, rows: Int, allowConsumed: Boolean = false): LatentArchive {
        require(start >= 0 && rows > 0 && start.toLong() + rows <= height)
        val entries = constants.toMutableMap()
        for ((key, field) in fields) entries[key] = NpyValue.Float32(field.rows(start, rows))
        return NpzMediumArchive().latentFromEntries(entries, allowConsumed)
    }
    fun opticalRows(start: Int,rows: Int): DevelopedOpticalArchive {
        require(start>=0 && rows>0 && start.toLong()+rows<=height)
        val entries=constants.toMutableMap()
        for((key,field) in fields) entries[key]=NpyValue.Float32(field.rows(start,rows))
        return NpzMediumArchive().opticalFromEntries(entries)
    }

    override fun close() {
        var failure: Exception? = null
        for (field in fields.values) {
            try { field.close() } catch (exc: Exception) { failure = failure ?: exc }
            if (field.file.exists() && !field.file.delete()) failure = failure ?: IOException("cannot remove row field")
        }
        try { lease.close() } catch(exc: Exception) { failure=failure ?: exc }
        failure?.let { throw it }
    }

    companion object {
        fun readOptical(input: InputStream,root: File,checkCancel: ()->Unit={}): RowArchiveSpool {
            val spool=RowArchiveSpool(root,0,0,checkCancel)
            try {
                val channels=mapOf(DataKernel.OPTICAL_DENSITY_KEY to 3,SpectralOptics.AMOUNTS_KEY to 9,SpectralOptics.POST_PROCESS_KEY to 3)
                val scalars=NpzFile.readChunks(input,{ it==DataKernel.DEVELOPED_METADATA_KEY || it==DataKernel.CLEAR_BASE_KEY }) { key,shape,offset,values,n ->
                    checkCancel()
                    channels[key]?.let { c ->
                        require(shape.size==3 && shape[2]==c && shape[0]>0 && shape[1]>0) { "Invalid optical row shape" }
                        val field=spool.fields.getOrPut(key) {
                            require(root.usableSpace>=shape[0].toLong()*shape[1]*c*4+16L*1024*1024) { "Insufficient optical scratch storage" }
                            spool.Field(shape.copyOf())
                        }
                        require(field.shape.contentEquals(shape) && field.count==offset.toLong())
                        if(key!=SpectralOptics.POST_PROCESS_KEY) for(i in 0 until n) require(values[i]>=0f) { "Invalid optical sample" }
                        field.append(values,n,finiteValidated=true)
                    }
                }
                spool.constants.putAll(scalars)
                val shape=spool.fields[DataKernel.OPTICAL_DENSITY_KEY]?.shape ?: error("Missing optical density")
                spool.height=shape[0];spool.width=shape[1]
                for(field in spool.fields.values) {
                    require(field.shape[0]==spool.height && field.shape[1]==spool.width && field.count==field.shape.fold(1L){ a,b->a*b })
                    field.seal()
                }
                spool.opticalRows(0,1) // Canonical polarity, spectral pairing, base and metadata checks.
                return spool
            } catch(error: Throwable) { try { spool.close() } catch(cleanup: Exception) { error.addSuppressed(cleanup) };throw error }
        }
        fun readLatent(input: InputStream, root: File, checkCancel: () -> Unit = {}): RowArchiveSpool {
            val spool = RowArchiveSpool(root, 0, 0, checkCancel)
            try {
                val pools = (DataKernel.LATENT_REQUIRED_ARRAYS + DataKernel.LATENT_OPTIONAL_ARRAYS).toSet()
                val scalar = NpzFile.readChunks(input, { it == DataKernel.LATENT_METADATA_KEY }) { key, shape, offset, values, n ->
                    checkCancel()
                    if (key in pools) {
                        require(shape.size == 3 && shape[2] in listOf(1, 3)) { "latent pool must be HxWx1 or HxWx3" }
                        val field = spool.fields.getOrPut(key) { spool.Field(shape.copyOf()) }
                        require(field.count == offset.toLong()) { "nonsequential numeric chunks" }
                        field.append(values, n, finiteValidated=true)
                    }
                }
                spool.constants.putAll(scalar)
                val full = spool.fields["halide"]?.shape ?: throw InterchangeException("latent archive missing halide")
                spool.height = full[0]; spool.width = full[1]
                for (key in DataKernel.LATENT_REQUIRED_ARRAYS) require(key in spool.fields) { "latent archive missing $key" }
                for (field in spool.fields.values) {
                    require(field.shape.contentEquals(full)) { "latent pools must share shape HxWxL" }
                    require(field.count == full.fold(1L) { a, b -> a * b }) { "incomplete latent pool" }
                    field.seal()
                }
                // Use the canonical metadata/identity/consumed checks without loading full pools.
                spool.latentRows(0, 1)
                return spool
            } catch (exc: Throwable) {
                try { spool.close() } catch (cleanup: Exception) { exc.addSuppressed(cleanup) }
                throw exc
            }
        }

        private fun equalValue(a: NpyValue, b: NpyValue): Boolean = when {
            a is NpyValue.UnicodeScalar && b is NpyValue.UnicodeScalar -> a.text == b.text
            a is NpyValue.Float32 && b is NpyValue.Float32 -> a.array.shape.contentEquals(b.array.shape) && a.array.values.contentEquals(b.array.values)
            else -> false
        }
    }
}
