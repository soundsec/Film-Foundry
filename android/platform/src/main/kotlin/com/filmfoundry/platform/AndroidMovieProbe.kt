package com.filmfoundry.platform

import android.media.MediaExtractor
import android.media.MediaFormat
import java.io.File

/** Metadata probe only; decoding/processing must independently check codec/color support. */
object AndroidMovieProbe {
    fun read(file: File): MovieInfo {
        val extractor=MediaExtractor()
        try {
            extractor.setDataSource(file.absolutePath)
            val video=mutableListOf<MediaFormat>()
            var audio=0
            for(i in 0 until extractor.trackCount) {
                val format=extractor.getTrackFormat(i)
                val mime=format.getString(MediaFormat.KEY_MIME).orEmpty()
                if(mime.startsWith("video/")) video.add(format)
                if(mime.startsWith("audio/")) audio++
            }
            if(video.size != 1) throw CaptureRejected("Movie source must contain exactly one video track.")
            val format=video.single()
            val transfer=if(format.containsKey(MediaFormat.KEY_COLOR_TRANSFER)) format.getInteger(MediaFormat.KEY_COLOR_TRANSFER) else 0
            return MovieInfo(format.getInteger(MediaFormat.KEY_WIDTH),format.getInteger(MediaFormat.KEY_HEIGHT),
                format.getLong(MediaFormat.KEY_DURATION),
                if(format.containsKey(MediaFormat.KEY_ROTATION)) format.getInteger(MediaFormat.KEY_ROTATION) else 0,
                format.getString(MediaFormat.KEY_MIME)!!,audio,
                transfer in setOf(MediaFormat.COLOR_TRANSFER_HLG,MediaFormat.COLOR_TRANSFER_ST2084))
        } finally { extractor.release() }
    }
}
