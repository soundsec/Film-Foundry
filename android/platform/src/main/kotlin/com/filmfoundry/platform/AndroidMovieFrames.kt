package com.filmfoundry.platform

import android.graphics.ImageFormat
import android.media.Image
import android.media.MediaCodec
import android.media.MediaCodecInfo
import android.media.MediaExtractor
import android.media.MediaFormat
import com.filmfoundry.workflow.*
import java.io.File

/** Real sequential decoder, preserving MediaCodec presentation microseconds.
 * Does not use preview frames, thumbnails, FPS resampling, or video encoders.
 */
class AndroidMovieFrames(file: File,private val control: WorkflowControl=WorkflowControl()): MovieFrameReader {
    private val extractor=MediaExtractor()
    private var codec: MediaCodec?=null
    private var image: Image?=null
    private var held=-1
    private var inputEos=false
    private var outputEos=false
    private var rotation=0
    private var fullRange=false
    private var previous: Long?=null
    private var closed=false
    init {
        try {
            extractor.setDataSource(file.absolutePath)
            val videos=(0 until extractor.trackCount).filter { extractor.getTrackFormat(it).getString(MediaFormat.KEY_MIME).orEmpty().startsWith("video/") }
            require(videos.size == 1) { "Movie source needs one video track" }
            val track=videos.single();val format=extractor.getTrackFormat(track)
            validateColor(format)
            rotation=if(format.containsKey(MediaFormat.KEY_ROTATION)) format.getInteger(MediaFormat.KEY_ROTATION) else 0
            require(rotation in setOf(0,90,180,270))
            format.setInteger(MediaFormat.KEY_ROTATION,0) // Rows explicitly orient samples once.
            format.setInteger(MediaFormat.KEY_COLOR_FORMAT,MediaCodecInfo.CodecCapabilities.COLOR_FormatYUV420Flexible)
            extractor.selectTrack(track)
            val decoder=MediaCodec.createDecoderByType(format.getString(MediaFormat.KEY_MIME)!!)
            codec=decoder
            decoder.configure(format,null,null,0)
            decoder.start()
        } catch(error: Throwable) { close();throw error }
    }

    override fun next(): MovieLinearFrame? {
        check(!closed)
        releaseFrame()
        if(outputEos) return null
        val decoder=codec!!
        val info=MediaCodec.BufferInfo()
        var lastProgress=System.nanoTime()
        while(true) {
            control.checkCancel()
            if(!inputEos) {
                val slot=decoder.dequeueInputBuffer(10000)
                if(slot >= 0) {
                    val buffer=decoder.getInputBuffer(slot) ?: throw CaptureRejected("Decoder has no input buffer")
                    buffer.clear()
                    val n=extractor.readSampleData(buffer,0)
                    if(n < 0) {
                        decoder.queueInputBuffer(slot,0,0,0,MediaCodec.BUFFER_FLAG_END_OF_STREAM)
                        inputEos=true
                    } else {
                        if(extractor.sampleFlags and MediaExtractor.SAMPLE_FLAG_ENCRYPTED != 0) throw CaptureRejected("Encrypted movie source is unsupported")
                        decoder.queueInputBuffer(slot,0,n,extractor.sampleTime,0)
                        extractor.advance()
                    }
                    lastProgress=System.nanoTime()
                }
            }
            val slot=decoder.dequeueOutputBuffer(info,10000)
            if(slot == MediaCodec.INFO_OUTPUT_FORMAT_CHANGED) { validateColor(decoder.outputFormat);lastProgress=System.nanoTime();continue }
            if(slot >= 0) {
                val eos=info.flags and MediaCodec.BUFFER_FLAG_END_OF_STREAM != 0
                if(info.flags and MediaCodec.BUFFER_FLAG_CODEC_CONFIG != 0) { decoder.releaseOutputBuffer(slot,false);continue }
                val decoded=decoder.getOutputImage(slot)
                if(decoded == null) {
                    decoder.releaseOutputBuffer(slot,false)
                    if(eos) { outputEos=true;return null }
                    throw CaptureNotReady("This decoder does not expose YUV images; no thumbnail fallback.")
                }
                image=decoded;held=slot
                if(eos) outputEos=true
                if(decoded.format != ImageFormat.YUV_420_888) throw CaptureNotReady("Decoder did not return 8-bit YUV420.")
                val pts=info.presentationTimeUs
                previous?.let { require(pts > it) { "Movie presentation timestamps must increase" } }
                previous=pts
                val crop=decoded.cropRect
                val planes=decoded.planes.map { MovieYuvPlane(it.buffer.duplicate(),it.rowStride,it.pixelStride) }
                require(planes.size == 3)
                val rows=YuvMovieRows(crop.width(),crop.height(),crop.left,crop.top,planes[0],planes[1],planes[2],rotation,fullRange)
                return MovieLinearFrame(movieRgbRows(rows.width,rows.height,rows::readRows),pts)
            }
            if(System.nanoTime()-lastProgress > 30_000_000_000L) throw CaptureNotReady("Movie decoder made no progress for 30 seconds.")
        }
    }
    private fun validateColor(format: MediaFormat) {
        fun value(key: String,default: Int)=if(format.containsKey(key)) format.getInteger(key).let { if(it == 0) default else it } else default
        val transfer=value(MediaFormat.KEY_COLOR_TRANSFER,MediaFormat.COLOR_TRANSFER_SDR_VIDEO)
        val standard=value(MediaFormat.KEY_COLOR_STANDARD,MediaFormat.COLOR_STANDARD_BT709)
        val range=value(MediaFormat.KEY_COLOR_RANGE,if(fullRange) MediaFormat.COLOR_RANGE_FULL else MediaFormat.COLOR_RANGE_LIMITED)
        if(transfer != MediaFormat.COLOR_TRANSFER_SDR_VIDEO || standard != MediaFormat.COLOR_STANDARD_BT709 ||
            range !in setOf(MediaFormat.COLOR_RANGE_FULL,MediaFormat.COLOR_RANGE_LIMITED))
            throw CaptureNotReady("Material movie ingest supports 8-bit SDR BT.709; HDR/wide-gamut needs explicit color ingest.")
        fullRange=range == MediaFormat.COLOR_RANGE_FULL
    }
    private fun releaseFrame() {
        image?.close();image=null
        if(held >= 0) { codec?.releaseOutputBuffer(held,false);held=-1 }
    }
    override fun close() {
        if(closed) return
        closed=true
        try { releaseFrame() } finally {
            try { codec?.let { decoder -> try { runCatching { decoder.stop() } } finally { decoder.release() } } }
            finally { codec=null;extractor.release() }
        }
    }
}
