package com.filmfoundry.platform

import com.filmfoundry.interchange.OwnedScratch
import com.filmfoundry.workflow.RowObservationSession
import java.io.*

/** RGB8 delivery transpose in bounded column strips. No second full-frame pixel allocation. */
class RotatingPngRows(private val root: File,private val output: RgbPngRows,degrees: Int,
    private val check: ()->Unit={},private val bitDepth: Int=8): RowObservationSession.Output,AutoCloseable {
    private val angle=OutputOrientation.normalized(degrees)
    private val tileWidth=128
    private val pixelBytes=3*(bitDepth/8)
    private var w=0;private var h=0;private var next=0;private var finished=false
    private var lease: OwnedScratch.Lease?=null
    private var files=emptyList<File>()
    private var streams=emptyList<OutputStream>()
    private var row=ByteArray(0)
    override fun begin(width: Int,height: Int) {
        require(w==0 && width>0 && height>0 && angle!=0 && bitDepth in listOf(8,16));check()
        require(root.isDirectory || root.mkdirs())
        require(root.usableSpace>=width.toLong()*height*pixelBytes+16L*1048576) { "Not enough temporary storage to rotate output" }
        w=width;h=height;row=ByteArray(Math.multiplyExact(w,pixelBytes))
        val owned=OwnedScratch.create(root);lease=owned
        files=List((w+tileWidth-1)/tileWidth) { File(owned.directory,"strip-$it.rgb") }
        val opened=mutableListOf<OutputStream>()
        try { for(file in files) opened+=file.outputStream().buffered(8192);streams=opened }
        catch(error: Throwable) { opened.forEach { runCatching { it.close() } };close();throw error }
    }
    override fun rows(start: Int,count: Int,rgb: FloatArray) {
        require(start==next && count>0 && start.toLong()+count<=h && rgb.size==w*count*3)
        for(y in 0 until count) {
            check()
            for(i in 0 until w*3) {
                val v=(rgb[y*w*3+i].coerceIn(0f,1f)*(if(bitDepth==16) 65535 else 255)).toInt()
                if(bitDepth==8) row[i]=v.toByte() else { row[i*2]=(v ushr 8).toByte();row[i*2+1]=v.toByte() }
            }
            packedRow(start+y,row)
        }
    }
    fun packedRow(start: Int,bytes: ByteArray) {
        require(!finished && w>0 && start==next && next<h && bytes.size==w*pixelBytes);check()
        for(i in streams.indices) streams[i].write(bytes,i*tileWidth*pixelBytes,minOf(tileWidth,w-i*tileWidth)*pixelBytes)
        next++
    }
    fun finish() {
        require(!finished && next==h && w>0);check()
        streams.forEach { it.close() };streams=emptyList()
        val (width,height)=OutputOrientation.size(w,h,angle);output.begin(width,height)
        val packed=ByteArray(width*pixelBytes);var destination=0
        if(angle==90 || angle==270) {
            val indices=if(angle==90) files.indices.toList() else files.indices.reversed()
            for(index in indices) {
                check();val columns=minOf(tileWidth,w-index*tileWidth)
                val block=ByteArray(columns*h*pixelBytes)
                files[index].inputStream().use { DataInputStream(it).readFully(block);require(it.read()==-1) }
                val xs=if(angle==90) (0 until columns).toList() else (0 until columns).reversed()
                for(x in xs) {
                    check()
                    for(y in 0 until h) {
                        val from=(y*columns+x)*pixelBytes;val to=(if(angle==90) h-1-y else y)*pixelBytes
                        block.copyInto(packed,to,from,from+pixelBytes)
                    }
                    output.packedRow(destination++,packed)
                }
            }
        } else {
            val readers=mutableListOf<RandomAccessFile>()
            try {
                files.forEach { readers+=RandomAccessFile(it,"r") }
                val segment=ByteArray(tileWidth*pixelBytes)
                for(y in h-1 downTo 0) {
                    check()
                    for(index in files.indices) {
                        val columns=minOf(tileWidth,w-index*tileWidth);val input=readers[index]
                        input.seek(y.toLong()*columns*pixelBytes);input.readFully(segment,0,columns*pixelBytes)
                        for(x in 0 until columns) segment.copyInto(packed,(w-1-index*tileWidth-x)*pixelBytes,x*pixelBytes,x*pixelBytes+pixelBytes)
                    }
                    output.packedRow(destination++,packed)
                }
            } finally { readers.forEach { it.close() } }
        }
        check(destination==height);finished=true
    }
    override fun close() {
        var failure: Throwable?=null
        streams.forEach { try { it.close() } catch(error: Throwable) { failure=failure ?: error } };streams=emptyList()
        try { lease?.close() } catch(error: Throwable) { failure=failure ?: error };lease=null;row=ByteArray(0)
        failure?.let { throw it }
    }
}
