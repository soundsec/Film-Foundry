package com.filmfoundry.platform

import java.nio.ByteBuffer
import java.nio.ByteOrder

/** Native-sample DNG ingest, never an embedded preview. Initial explicit model:
 * uncompressed 8/16-bit rectangular Bayer, bilinear demosaic, calibration set 1.
 * Unsupported required corrections/compression fail before exposure.
 */
object DngRawDecoder {
    const val MODEL = "dng_bilinear_matrix1_linear_srgb_v1"
    const val ORIENTED_MODEL = "dng_bilinear_matrix1_exif_linear_srgb_v2"

    fun decode(source: DngCapture): WorkingExposure {
        val image = open(source)
        val rgb = FloatArray(Math.multiplyExact(Math.multiplyExact(image.width,image.height),3))
        for (start in 0 until image.height step 32) {
            val rows = minOf(32,image.height-start)
            image.readRows(start,rows).copyInto(rgb,start*image.width*3)
        }
        return WorkingExposure(image.width,image.height,rgb,CaptureRecord(
            CaptureKind.SENSOR_RAW_DNG,false,sourceEncoding=MODEL,bitDepth=image.bitDepth,
            sceneReferredClaim=false))
    }

    fun open(source: DngCapture): RawLinearRows = Reader(DngInput.memory(source.bytes)).image(source.width,source.height)
    fun open(file: java.io.File): RawLinearRows {
        val input=DngInput.file(file)
        return try { Reader(input).image(0,0) } catch(error: Throwable) { input.close();throw error }
    }
    /** Geometry only: unsupported development can fail later while the captured DNG stays retained. */
    fun storedDimensions(file: java.io.File,applyOrientation: Boolean=false): Pair<Int,Int> =
        DngInput.file(file).use { Reader(it).geometry(applyOrientation) }

    interface RawLinearRows: AutoCloseable {
        val width: Int
        val height: Int
        val bitDepth: Int
        val rotation: Int
        val exifOrientation: Int get()=1
        fun readRows(start: Int,count: Int): FloatArray
        fun readWindowRows(left: Int,start: Int,windowWidth: Int,count: Int): FloatArray {
            require(left>=0 && windowWidth>0 && left.toLong()+windowWidth<=width)
            val full=readRows(start,count)
            if(left==0 && windowWidth==width) return full
            return FloatArray(windowWidth*count*3).also { out -> for(y in 0 until count) full.copyInto(out,y*windowWidth*3,(y*width+left)*3,(y*width+left+windowWidth)*3) }
        }
        override fun close()=Unit
    }

    private class Reader(private val bytes: DngInput) {
        private val data: DngInput
        private data class Tag(val type: Int,val count: Int,val offset: Int)
        init {
            if (bytes.size < 8) throw CaptureRejected("DNG header is truncated; no JPEG thumbnail fallback.")
            val order = when {
                bytes[0] == 73.toByte() && bytes[1] == 73.toByte() -> ByteOrder.LITTLE_ENDIAN
                bytes[0] == 77.toByte() && bytes[1] == 77.toByte() -> ByteOrder.BIG_ENDIAN
                else -> throw CaptureRejected("DNG requires a TIFF header; no JPEG thumbnail fallback.")
            }
            data=bytes.order(order)
            if (u16(2) != 42) throw CaptureNotReady("BigTIFF DNG is not supported by $MODEL.")
        }
        private fun check(offset: Long,size: Long): Int {
            if (offset < 0 || size < 0 || offset > bytes.size-size)
                throw CaptureRejected("DNG offset or payload is outside the file.")
            return offset.toInt()
        }
        private fun u16(offset: Int)=data.getShort(check(offset.toLong(),2)).toInt() and 65535
        private fun u32(offset: Int)=data.getInt(check(offset.toLong(),4)).toLong() and 0xffffffffL
        private fun ifd(offset: Int): Pair<Map<Int,Tag>,Int> {
            val count=u16(offset)
            if (count > 4096) throw CaptureRejected("DNG IFD has too many entries.")
            check(offset.toLong()+2,count*12L+4)
            val tags=linkedMapOf<Int,Tag>()
            for (i in 0 until count) {
                val pos=offset+2+i*12
                val kind=u16(pos+2); val n=u32(pos+4)
                val size=when(kind) { 1,2,6,7 -> 1; 3,8 -> 2; 4,9,11,13 -> 4; 5,10,12 -> 8; else -> 0 }
                if (size == 0) throw CaptureNotReady("Unsupported TIFF tag type in DNG.")
                val length=n*size
                val payload=if (length <= 4) pos+8 else check(u32(pos+8),length)
                if (n > Int.MAX_VALUE) throw CaptureRejected("DNG tag length overflows.")
                if (tags.put(u16(pos),Tag(kind,n.toInt(),payload)) != null) throw CaptureRejected("Duplicate DNG tag.")
            }
            return tags to check(u32(offset+2+count*12),0)
        }
        private fun values(tag: Tag?): DoubleArray? {
            if (tag == null) return null
            if (tag.count > 100000) throw CaptureRejected("DNG numeric metadata is too large.")
            return DoubleArray(tag.count) { i ->
                val p=tag.offset
                val value=when(tag.type) {
                    1,7 -> (bytes[p+i].toInt() and 255).toDouble()
                    3 -> u16(p+i*2).toDouble()
                    4,13 -> u32(p+i*4).toDouble()
                    9 -> data.getInt(p+i*4).toDouble()
                    5 -> u32(p+i*8).toDouble()/u32(p+i*8+4)
                    10 -> data.getInt(p+i*8).toDouble()/data.getInt(p+i*8+4)
                    11 -> data.getFloat(p+i*4).toDouble()
                    12 -> data.getDouble(p+i*8)
                    else -> throw CaptureNotReady("Unsupported DNG numeric tag type.")
                }
                if (!value.isFinite()) throw CaptureRejected("DNG metadata contains a nonfinite value.")
                value
            }
        }
        private fun integers(tag: Tag?): IntArray? = values(tag)?.map {
            if (it < 0 || it > Int.MAX_VALUE || it % 1.0 != 0.0) throw CaptureRejected("DNG integer metadata is invalid.")
            it.toInt()
        }?.toIntArray()

        private fun directories(): List<Map<Int,Tag>> {
            val pending=ArrayDeque<Int>(); pending.add(check(u32(4),2))
            val seen=mutableSetOf<Int>(); val directories=mutableListOf<Map<Int,Tag>>()
            while (pending.isNotEmpty()) {
                val next=pending.removeFirst()
                if (next == 0) continue
                if (!seen.add(next) || seen.size > 64) throw CaptureRejected("DNG IFD graph is cyclic or too large.")
                val (tags, sibling)=ifd(next); directories.add(tags)
                if (sibling != 0) pending.add(sibling)
                integers(tags[330])?.forEach { pending.add(it) }
            }
            return directories
        }
        private fun rawDirectories(): Pair<Map<Int,Tag>,Map<Int,Tag>> {
            val directories=directories()
            val root=directories.firstOrNull() ?: throw CaptureRejected("DNG has no IFD.")
            if (root[50706] == null) throw CaptureRejected("TIFF file has no DNG version.")
            val candidates=directories.filter { integers(it[262])?.singleOrNull() == 32803 && (integers(it[254])?.singleOrNull() ?: 0) == 0 }
            if (candidates.size != 1) throw CaptureNotReady("DNG needs one full-resolution Bayer raw IFD; previews are not used.")
            val raw=candidates.single()
            return root to raw
        }
        private fun orientation(root: Map<Int,Tag>,raw: Map<Int,Tag>): Int {
            val value=integers(root[274] ?: raw[274]) ?: return 1
            if(value.size!=1 || value[0] !in 1..8) throw CaptureRejected("DNG orientation is invalid.")
            return value[0]
        }
        fun geometry(applyOrientation: Boolean): Pair<Int,Int> {
            val (root,raw)=rawDirectories()
            fun ints(key: Int)=integers(raw[key] ?: root[key])
            val width=ints(256)?.singleOrNull() ?: throw CaptureRejected("DNG width is missing")
            val height=ints(257)?.singleOrNull() ?: throw CaptureRejected("DNG height is missing")
            val active=ints(50829) ?: intArrayOf(0,0,height,width)
            require(width>0 && height>0 && active.size==4 && active[0]>=0 && active[1]>=0 && active[2]<=height && active[3]<=width)
            val origin=ints(50719) ?: intArrayOf(0,0)
            val size=ints(50720) ?: intArrayOf(active[3]-active[1],active[2]-active[0])
            require(origin.size==2 && origin.all { it>=0 } && size.size==2 && size.all { it>0 } && origin[0].toLong()+size[0]<=active[3]-active[1] && origin[1].toLong()+size[1]<=active[2]-active[0]) { "DNG crop geometry is invalid" }
            val exif=if(applyOrientation) orientation(root,raw) else 1
            return if(exif>=5) size[1] to size[0] else size[0] to size[1]
        }
        fun image(expectedWidth: Int,expectedHeight: Int): RawLinearRows {
            val (root,raw)=rawDirectories()
            fun nums(key: Int)=values(raw[key] ?: root[key])
            fun ints(key: Int)=integers(raw[key] ?: root[key])
            fun scalar(key: Int,default: Int?=null): Int {
                val array=ints(key)
                if (array != null && array.size != 1) throw CaptureRejected("DNG scalar tag $key has invalid shape.")
                return array?.single() ?: default ?: throw CaptureRejected("DNG missing scalar tag $key.")
            }
            if (nums(50718)?.let { it.size != 2 || it.any { value -> value != 1.0 } } == true)
                throw CaptureNotReady("Non-unit DNG DefaultScale requires explicit resampling.")
            val fullWidth=scalar(256); val fullHeight=scalar(257); val bits=scalar(258)
            if (fullWidth < 2 || fullHeight < 2 || fullWidth.toLong()*fullHeight > 100_000_000)
                throw CaptureRejected("DNG dimensions are invalid or exceed ingest capacity.")
            if (scalar(259,1) != 1 || bits !in listOf(8,16) || scalar(277,1) != 1 || scalar(339,1) != 1 || scalar(284,1) != 1)
                throw CaptureNotReady("$MODEL requires uncompressed unsigned 8/16-bit single-plane DNG.")
            if (scalar(50711,1) != 1 || !((ints(33421) ?: intArrayOf()).contentEquals(intArrayOf(2,2))))
                throw CaptureNotReady("DNG requires a rectangular 2x2 Bayer CFA.")
            val planes=ints(50710) ?: intArrayOf(0,1,2)
            val pattern=(ints(33422) ?: throw CaptureRejected("DNG missing CFA pattern.")).map {
                planes.getOrNull(it) ?: throw CaptureRejected("DNG CFA plane index is invalid.")
            }
            if (pattern.size != 4 || pattern.count { it == 0 } != 1 || pattern.count { it == 1 } != 2 || pattern.count { it == 2 } != 1)
                throw CaptureNotReady("DNG CFA is not RGB Bayer.")
            // Required opcodes cannot silently be skipped. Optional corrections
            // are omitted by this explicit ingest model, never by a quality knob.
            for (key in listOf(51008,51009,51022)) {
                val tag=raw[key] ?: continue
                if (tag.count < 4) throw CaptureRejected("DNG opcode list is truncated.")
                val op=bytes.metadata(tag.offset,tag.count).order(ByteOrder.BIG_ENDIAN)
                val n=op.int
                if (n !in 0..4096) throw CaptureRejected("DNG opcode count is invalid.")
                repeat(n) {
                    if (op.remaining() < 16) throw CaptureRejected("DNG opcode is truncated.")
                    op.int; op.int; val flags=op.int; val length=op.int
                    if (length < 0 || length > op.remaining()) throw CaptureRejected("DNG opcode payload is truncated.")
                    if (flags and 1 == 0) throw CaptureNotReady("DNG has a required correction unsupported by $MODEL.")
                    op.position(op.position()+length)
                }
            }
            val active=ints(50829) ?: intArrayOf(0,0,fullHeight,fullWidth)
            if (active.size != 4 || active[0] < 0 || active[1] < 0 || active[2] > fullHeight || active[3] > fullWidth || active[2]-active[0] < 2 || active[3]-active[1] < 2)
                throw CaptureRejected("DNG ActiveArea is invalid.")
            val origin=ints(50719) ?: intArrayOf(0,0)
            val size=ints(50720) ?: intArrayOf(active[3]-active[1],active[2]-active[0])
            if (origin.size != 2 || size.size != 2 || size.any { it <= 0 } || origin[0].toLong()+size[0] > active[3]-active[1] || origin[1].toLong()+size[1] > active[2]-active[0])
                throw CaptureRejected("DNG default crop is invalid.")
            if (expectedWidth > 0 && expectedWidth != fullWidth || expectedHeight > 0 && expectedHeight != fullHeight)
                throw CaptureRejected("DNG dimensions disagree with the camera packet.")
            val cropLeft=active[1]+origin[0]; val cropTop=active[0]+origin[1]
            val blackDim=ints(50713) ?: intArrayOf(1,1)
            if (blackDim.size != 2 || blackDim.any { it !in 1..8 }) throw CaptureRejected("DNG black pattern is invalid.")
            val black=nums(50714) ?: DoubleArray(blackDim[0]*blackDim[1])
            if (black.size != blackDim[0]*blackDim[1]) throw CaptureRejected("DNG black level shape is invalid.")
            val deltaH=nums(50715); val deltaV=nums(50716)
            if (deltaH != null && deltaH.size != active[3]-active[1] || deltaV != null && deltaV.size != active[2]-active[0])
                throw CaptureRejected("DNG black delta shape is invalid.")
            val whiteValues=nums(50717)
            if (whiteValues != null && whiteValues.size != 1) throw CaptureRejected("DNG white level must have one value.")
            val white=whiteValues?.single() ?: ((1 shl bits)-1).toDouble()
            if (white <= 0) throw CaptureRejected("DNG white level is invalid.")
            val linearization=nums(50712)
            val offsets=ints(273); val byteCounts=ints(279)
            val tileOffsets=ints(324); val tileBytes=ints(325)
            if (offsets != null && tileOffsets != null) throw CaptureRejected("DNG raw storage is ambiguous.")
            val tileW=if (tileOffsets != null) scalar(322) else fullWidth
            val tileH=if (tileOffsets != null) scalar(323) else scalar(278,fullHeight)
            if (tileW <= 0 || tileH <= 0) throw CaptureRejected("DNG storage geometry is invalid.")
            val storedOffsets=offsets ?: tileOffsets ?: throw CaptureRejected("DNG raw offsets are missing.")
            val storedBytes=byteCounts ?: tileBytes ?: throw CaptureRejected("DNG raw byte counts are missing.")
            val across=if (offsets != null) 1 else ((fullWidth.toLong()+tileW-1)/tileW).toInt()
            val down=((fullHeight.toLong()+tileH-1)/tileH).toInt()
            if (storedOffsets.size.toLong() != across.toLong()*down || storedBytes.size != storedOffsets.size)
                throw CaptureRejected("DNG raw block count is invalid.")
            for (i in storedOffsets.indices) check(storedOffsets[i].toLong(),storedBytes[i].toLong())
            val matrix=rawToSrgb(nums(50721),nums(50964),nums(50723),nums(50727),nums(50728))
            val exif=orientation(root,raw)

            fun sensor(x: Int,y: Int): Double {
                val block=(y/tileH)*across+x/tileW
                val index=((y%tileH).toLong()*tileW+x%tileW)*(bits/8)
                if (index+bits/8 > storedBytes[block]) throw CaptureRejected("DNG raw block is truncated.")
                val pos=check(storedOffsets[block].toLong()+index,(bits/8).toLong())
                val dn=if (bits == 16) u16(pos) else bytes[pos].toInt() and 255
                val value=linearization?.getOrNull(dn) ?: if (linearization == null) dn.toDouble() else throw CaptureRejected("DNG linearization table is short.")
                val ax=x-active[1]; val ay=y-active[0]
                val zero=black[(ay%blackDim[0])*blackDim[1]+ax%blackDim[1]]+(deltaH?.get(ax) ?: 0.0)+(deltaV?.get(ay) ?: 0.0)
                if (white <= zero) throw CaptureRejected("DNG black level reaches white level.")
                return maxOf(value-zero,0.0)/(white-zero)
            }
            fun channel(x: Int,y: Int)=pattern[(y and 1)*2+(x and 1)]
            return object : RawLinearRows {
                override fun close()=bytes.close()
                override val width=size[0]; override val height=size[1]; override val bitDepth=bits; override val rotation=0
                override val exifOrientation=exif
                override fun readRows(start: Int,count: Int): FloatArray {
                    return readWindowRows(0,start,width,count)
                }
                override fun readWindowRows(left: Int,start: Int,windowWidth: Int,count: Int): FloatArray {
                    require(start >= 0 && count > 0 && start.toLong()+count <= height)
                    require(left>=0 && windowWidth>0 && left.toLong()+windowWidth<=width)
                    val output=FloatArray(Math.multiplyExact(Math.multiplyExact(windowWidth,count),3))
                    val camera=DoubleArray(3)
                    // One read of each Bayer sample the bilinear neighborhood can touch. The formula below is unchanged.
                    val cacheTop=(cropTop+start-1).coerceAtLeast(active[0])
                    val cacheBottom=(cropTop+start+count).coerceAtMost(active[2]-1)
                    val cacheLeft=(cropLeft+left-1).coerceAtLeast(active[1])
                    val cacheRight=(cropLeft+left+windowWidth).coerceAtMost(active[3]-1)
                    if (cacheBottom < cacheTop || cacheRight < cacheLeft) throw CaptureRejected("DNG Bayer neighborhood has no sample.")
                    val spanX=cacheRight-cacheLeft+1
                    val samples=DoubleArray(Math.multiplyExact(spanX,cacheBottom-cacheTop+1))
                    for (sy in cacheTop..cacheBottom) for (sx in cacheLeft..cacheRight) samples[(sy-cacheTop)*spanX+(sx-cacheLeft)]=sensor(sx,sy)
                    fun sample(x: Int,y: Int)=samples[(y-cacheTop)*spanX+(x-cacheLeft)]
                    for (row in 0 until count) for (col in 0 until windowWidth) {
                        val x=cropLeft+left+col; val y=cropTop+start+row
                        for (c in 0..2) {
                            if (channel(x,y) == c) camera[c]=sample(x,y)
                            else {
                                var sum=0.0; var n=0
                                for (dy in -1..1) for (dx in -1..1) {
                                    val sx=x+dx; val sy=y+dy
                                    if (sx in active[1] until active[3] && sy in active[0] until active[2] && channel(sx,sy) == c) { sum+=sample(sx,sy); n++ }
                                }
                                if (n == 0) throw CaptureRejected("DNG Bayer neighborhood has no sample.")
                                camera[c]=sum/n
                            }
                        }
                        for (c in 0..2) {
                            val value=matrix[c*3]*camera[0]+matrix[c*3+1]*camera[1]+matrix[c*3+2]*camera[2]
                            if (!value.isFinite() || kotlin.math.abs(value) > Float.MAX_VALUE) throw CaptureRejected("DNG color transform overflows.")
                            output[(row*windowWidth+col)*3+c]=value.toFloat()
                        }
                    }
                    return output
                }
            }
        }
    }

    private val identity=doubleArrayOf(1.0,0.0,0.0,0.0,1.0,0.0,0.0,0.0,1.0)
    private fun mul(a: DoubleArray,b: DoubleArray)=DoubleArray(9) { i -> (0..2).sumOf { k -> a[(i/3)*3+k]*b[k*3+i%3] } }
    private fun vector(a: DoubleArray,b: DoubleArray)=DoubleArray(3) { i -> (0..2).sumOf { k -> a[i*3+k]*b[k] } }
    private fun inverse(m: DoubleArray): DoubleArray {
        if (m.size != 9) throw CaptureRejected("DNG color matrix must be 3x3.")
        val c=doubleArrayOf(m[4]*m[8]-m[5]*m[7],m[2]*m[7]-m[1]*m[8],m[1]*m[5]-m[2]*m[4],
            m[5]*m[6]-m[3]*m[8],m[0]*m[8]-m[2]*m[6],m[2]*m[3]-m[0]*m[5],
            m[3]*m[7]-m[4]*m[6],m[1]*m[6]-m[0]*m[7],m[0]*m[4]-m[1]*m[3])
        val determinant=m[0]*c[0]+m[1]*c[3]+m[2]*c[6]
        if (!determinant.isFinite() || kotlin.math.abs(determinant) < 1e-12) throw CaptureRejected("DNG color matrix is singular.")
        return DoubleArray(9){c[it]/determinant}
    }
    private fun adapt(from: DoubleArray,to: DoubleArray): DoubleArray {
        val bradford=doubleArrayOf(.8951,.2664,-.1614,-.7502,1.7135,.0367,.0389,-.0685,1.0296)
        val src=vector(bradford,from); val dst=vector(bradford,to)
        if (src.any { it <= 0 || !it.isFinite() }) throw CaptureRejected("DNG neutral white is invalid.")
        val scale=DoubleArray(9){if(it%4 == 0) dst[it/4]/src[it/4] else 0.0}
        return mul(inverse(bradford),mul(scale,bradford))
    }
    private fun rawToSrgb(color: DoubleArray?,forward: DoubleArray?,calibration: DoubleArray?,analog: DoubleArray?,neutral: DoubleArray?): DoubleArray {
        if (neutral == null || neutral.size != 3 || neutral.any { it <= 0 }) throw CaptureRejected("DNG needs a positive AsShotNeutral.")
        val balance=analog ?: doubleArrayOf(1.0,1.0,1.0)
        val cc=calibration ?: identity
        if (balance.size != 3 || balance.any { it <= 0 } || cc.size != 9 || forward != null && forward.size != 9)
            throw CaptureRejected("DNG camera calibration shape is invalid.")
        val abcc=DoubleArray(9){balance[it/3]*cc[it]}
        val d65=doubleArrayOf(.95047,1.0,1.08883)
        val toXyz=if (forward != null) {
            val referenceNeutral=vector(inverse(abcc),neutral)
            if (referenceNeutral.any { it <= 0 }) throw CaptureRejected("DNG reference neutral is invalid.")
            val gains=DoubleArray(9){if(it%4 == 0) 1/referenceNeutral[it/4] else 0.0}
            mul(adapt(doubleArrayOf(.96422,1.0,.82521),d65),mul(forward,mul(gains,inverse(abcc))))
        } else {
            if (color == null || color.size != 9) throw CaptureRejected("DNG needs ColorMatrix1 or ForwardMatrix1.")
            val cameraToXyz=inverse(mul(abcc,color))
            val white=vector(cameraToXyz,neutral)
            if (white[1] <= 0) throw CaptureRejected("DNG neutral luminance is invalid.")
            val normalized=DoubleArray(3){white[it]/white[1]}
            mul(adapt(normalized,d65),DoubleArray(9){cameraToXyz[it]/white[1]})
        }
        val xyzToRgb=doubleArrayOf(3.2404542,-1.5371385,-.4985314,-.9692660,1.8760108,.0415560,.0556434,-.2040259,1.0572252)
        return mul(xyzToRgb,toXyz)
    }
}
