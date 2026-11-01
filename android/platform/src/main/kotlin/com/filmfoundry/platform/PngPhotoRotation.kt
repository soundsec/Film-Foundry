package com.filmfoundry.platform

import java.io.*
import java.util.zip.CRC32
import java.util.zip.Inflater
import java.util.zip.InflaterInputStream

/** Read the RGB/RGBA8, non-interlaced PNGs produced by the app, one decoded row at a time. */
internal object ScanPngRows {
    fun read(source: InputStream,check: ()->Unit,begin: (Int,Int,Int)->Unit,row: (Int,ByteArray)->Unit) {
        val input=DataInputStream(source)
        val signature=ByteArray(8);input.readFully(signature)
        require(signature.contentEquals(byteArrayOf(137.toByte(),80,78,71,13,10,26,10))) { "Saved photo is not a PNG" }
        val chunks=Chunks(input,check)
        chunks.next();require(chunks.type=="IHDR" && chunks.remaining==13)
        val header=ByteArray(13);chunks.readFully(header);chunks.end()
        val bytes=java.nio.ByteBuffer.wrap(header)
        val w=bytes.int;val h=bytes.int;val depth=bytes.get().toInt() and 255;val color=bytes.get().toInt() and 255
        require(w>0 && h>0 && w.toLong()*h<=Int.MAX_VALUE/4 && depth in listOf(8,16) && color in listOf(2,6) &&
            bytes.get().toInt()==0 && bytes.get().toInt()==0 && bytes.get().toInt()==0) { "Unsupported saved-photo PNG format" }
        val channels=if(color==2) 3 else 4
        val sampleBytes=depth/8;val pixelBytes=channels*sampleBytes
        val stride=Math.multiplyExact(w,pixelBytes)
        var previous=ByteArray(stride);var current=ByteArray(stride);val rgb=ByteArray(w*3*sampleBytes)
        val idat=object: InputStream() {
            private var done=false;private var started=false;private var afterData=false
            override fun read(): Int { val b=ByteArray(1);return if(read(b,0,1)<0) -1 else b[0].toInt() and 255 }
            override fun read(b: ByteArray,off: Int,len: Int): Int {
                if(len==0) return 0
                while(!done) {
                    check()
                    if(chunks.type=="IDAT" && chunks.remaining>0) return chunks.read(b,off,len)
                    if(chunks.type=="IDAT") chunks.end()
                    chunks.next()
                    when(chunks.type) {
                        "IDAT" -> { require(!afterData);started=true }
                        "IEND" -> { require(started && chunks.remaining==0);chunks.end();done=true }
                        else -> {
                            require(chunks.type[0].isLowerCase()) { "Unknown critical PNG chunk" }
                            if(started) afterData=true
                            chunks.skip();chunks.end()
                        }
                    }
                }
                return -1
            }
        }
        val codec=Inflater()
        try {
            val raw=DataInputStream(InflaterInputStream(idat,codec,65536));begin(w,h,depth)
            for(y in 0 until h) {
                check();val filter=raw.readUnsignedByte();require(filter in 0..4);raw.readFully(current)
                for(i in current.indices) {
                    val a=if(i>=pixelBytes) current[i-pixelBytes].toInt() and 255 else 0
                    val b=previous[i].toInt() and 255
                    val c=if(i>=pixelBytes) previous[i-pixelBytes].toInt() and 255 else 0
                    val predictor=when(filter) {
                        0->0;1->a;2->b;3->(a+b)/2
                        else->{ val p=a+b-c;val da=kotlin.math.abs(p-a);val db=kotlin.math.abs(p-b);val dc=kotlin.math.abs(p-c)
                            if(da<=db && da<=dc) a else if(db<=dc) b else c }
                    }
                    current[i]=((current[i].toInt() and 255)+predictor).toByte()
                }
                if(channels==3) row(y,current)
                else {
                    for(x in 0 until w) {
                        for(a in 0 until sampleBytes) require((current[x*pixelBytes+3*sampleBytes+a].toInt() and 255)==255) { "Saved scan must be opaque" }
                        current.copyInto(rgb,x*3*sampleBytes,x*pixelBytes,x*pixelBytes+3*sampleBytes)
                    }
                    row(y,rgb)
                }
                val old=previous;previous=current;current=old
            }
            require(raw.read()==-1) { "PNG contains extra decoded rows" }
            val drain=ByteArray(8192);while(idat.read(drain)!=-1) { check() }
            require(input.read()==-1) { "Trailing saved-photo data" }
        } finally { codec.end() }
    }
    private class Chunks(private val input: DataInputStream,private val check: ()->Unit) {
        var type="";private set
        var remaining=0;private set
        private var open=false;private var count=0;private val crc=CRC32()
        fun next() {
            check();check(!open);require(++count<=65536)
            remaining=input.readInt();require(remaining in 0..512*1024*1024)
            val name=ByteArray(4);input.readFully(name);require(name.all { it.toInt() in 65..90 || it.toInt() in 97..122 })
            type=name.toString(Charsets.US_ASCII);crc.reset();crc.update(name);open=true
        }
        fun read(bytes: ByteArray,offset: Int,length: Int): Int {
            require(open && remaining>0);check()
            val n=input.read(bytes,offset,minOf(length,remaining));if(n<0) throw EOFException("Truncated PNG chunk")
            crc.update(bytes,offset,n);remaining-=n;return n
        }
        fun readFully(bytes: ByteArray) { var offset=0;while(offset<bytes.size) offset+=read(bytes,offset,bytes.size-offset) }
        fun skip() { val buffer=ByteArray(8192);while(remaining>0) read(buffer,0,minOf(buffer.size,remaining)) }
        fun end() { check();require(open && remaining==0);require(input.readInt().toLong() and 0xFFFFFFFFL==crc.value) { "Invalid PNG chunk checksum" };open=false;type="" }
    }
}

object PngPhotoRotation {
    fun write(storage: StorageAdapter,path: String,degrees: Int,output: OutputStream,check: ()->Unit={}): Pair<Int,Int> {
        val angle=OutputOrientation.normalized(degrees);require(angle!=0)
        val scratch=storage.rowScratchRoot() ?: error("Rotation requires temporary file storage")
        var png: RgbPngRows?=null;var rotated: RotatingPngRows?=null
        try {
            storage.readPrivate(path) { ScanPngRows.read(it,check,{ w,h,depth ->
                png=RgbPngRows(output,check,depth)
                rotated=RotatingPngRows(scratch,png!!,angle,check,depth);rotated!!.begin(w,h)
            },{ y,row -> rotated!!.packedRow(y,row) }) }
            rotated!!.finish();png!!.finish();return png!!.width to png!!.height
        } finally { try { rotated?.close() } finally { png?.close() } }
    }
}
