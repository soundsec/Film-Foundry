package com.filmfoundry.platform

import com.filmfoundry.workflow.RowObservationSession
import java.io.DataOutputStream
import java.io.OutputStream
import java.util.zip.CRC32
import java.util.zip.Deflater
import java.util.zip.DeflaterOutputStream

/** Lossless RGB8 delivery of the existing encoded scan rows. Never owns the caller's stream. */
class RgbPngRows(private val target: OutputStream,private val check: ()->Unit={},val bitDepth: Int=8): RowObservationSession.Output,AutoCloseable {
    constructor(target: OutputStream,check: ()->Unit): this(target,check,8)
    var width=0; private set
    var height=0; private set
    private var next=0
    private var finished=false
    private var closed=false
    private var row=ByteArray(0)
    private var deflater: Deflater?=null
    private var compressed: DeflaterOutputStream?=null
    private val data=DataOutputStream(target)
    private val chunks=object: OutputStream() {
        val buffer=ByteArray(64*1024)
        var used=0
        override fun write(value: Int) { buffer[used++]=value.toByte();if(used==buffer.size) flushChunk() }
        override fun write(bytes: ByteArray,offset: Int,length: Int) {
            var start=offset;var remaining=length
            while(remaining>0) { val count=minOf(remaining,buffer.size-used)
                bytes.copyInto(buffer,used,start,start+count);used+=count;start+=count;remaining-=count
                if(used==buffer.size) flushChunk()
            }
        }
        fun flushChunk() { if(used>0) { chunk("IDAT",buffer,used);used=0 } }
    }
    override fun begin(width: Int,height: Int) {
        check(!closed && this.width==0);require(width>0 && height>0);check()
        require(bitDepth==8 || bitDepth==16);this.width=width;this.height=height;row=ByteArray(Math.addExact(Math.multiplyExact(width,3*(bitDepth/8)),1))
        data.write(byteArrayOf(137.toByte(),80,78,71,13,10,26,10))
        val header=java.nio.ByteBuffer.allocate(13).putInt(width).putInt(height).put(bitDepth.toByte()).put(2).put(0).put(0).put(0).array()
        chunk("IHDR",header);chunk("sRGB",byteArrayOf(0))
        val codec=Deflater(if(bitDepth==16) Deflater.BEST_SPEED else Deflater.DEFAULT_COMPRESSION);deflater=codec
        compressed=DeflaterOutputStream(chunks,codec,32*1024)
    }
    override fun rows(start: Int,count: Int,rgb: FloatArray) {
        check(!closed && !finished && width>0);require(start==next && count>0 && start.toLong()+count<=height)
        require(rgb.size==Math.multiplyExact(Math.multiplyExact(width,count),3))
        val stride=width*3
        for(y in 0 until count) {
            check();row[0]=0 // PNG filter None; numeric quantization matches the former ARGB Bitmap path.
            for(i in 0 until stride) {
                val v=(rgb[y*stride+i].coerceIn(0f,1f)*(if(bitDepth==16) 65535 else 255)).toInt()
                if(bitDepth==8) row[i+1]=v.toByte() else { row[1+i*2]=(v ushr 8).toByte();row[2+i*2]=v.toByte() }
            }
            checkNotNull(compressed).write(row);next++
        }
    }
    fun finish() {
        check(!closed && !finished && width>0 && next==height);check()
        checkNotNull(compressed).finish();chunks.flushChunk();check();chunk("IEND",ByteArray(0));finished=true
    }
    fun packedRow(start: Int,rgb: ByteArray) {
        check(!closed && !finished && width>0);require(start==next && next<height && rgb.size==width*3*(bitDepth/8));check()
        row[0]=0;rgb.copyInto(row,1);checkNotNull(compressed).write(row);next++
    }
    private fun chunk(name: String,bytes: ByteArray,count: Int=bytes.size) {
        check();val type=name.toByteArray(Charsets.US_ASCII)
        val crc=CRC32();crc.update(type);crc.update(bytes,0,count)
        data.writeInt(count);data.write(type);data.write(bytes,0,count);data.writeInt(crc.value.toInt())
    }
    override fun close() { if(!closed) { closed=true;deflater?.end();row=ByteArray(0);compressed=null } }
}
