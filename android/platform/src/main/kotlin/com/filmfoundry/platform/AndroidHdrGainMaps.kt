package com.filmfoundry.platform

import android.annotation.TargetApi
import android.graphics.Bitmap
import android.graphics.Gainmap
import android.os.Build

/** Extracts metadata and encoded gain samples, without a display-dependent render. */
internal object AndroidHdrGainMaps {
    fun openRows(bitmap: Bitmap,exif: Int): HdrGainSource? {
        if(Build.VERSION.SDK_INT<34) return null
        return openRows34(bitmap,exif)
    }

    @TargetApi(34)
    private fun openRows34(bitmap: Bitmap,exif: Int): HdrGainSource? {
        val gain=validated34(bitmap) ?: return null
        val map=gain.gainmapContents
        return SampledHdrGain(map.width,map.height,exif,map.config==Bitmap.Config.ALPHA_8,
            gain.ratioMin,gain.ratioMax,gain.gamma,gain.epsilonSdr,gain.epsilonHdr,
            PackedPixelTiles(map.width,map.height) { left,top,width,height,pixels -> map.getPixels(pixels,0,width,left,top,width,height) })
    }

    @TargetApi(34)
    private fun validated34(bitmap: Bitmap): Gainmap? {
        val gain=bitmap.gainmap ?: return null
        if(bitmap.colorSpace?.isSrgb!=true)
            throw CaptureNotReady("Ultra HDR ingest currently requires sRGB base primaries; no silent gamut conversion before gain reconstruction.")
        if(Build.VERSION.SDK_INT>=36) validate36(gain)
        val map=gain.gainmapContents
        if(map.colorSpace!=null && !map.colorSpace!!.isSrgb) throw CaptureNotReady("Unsupported encoded gain sample color space.")
        return gain
    }
    fun read(bitmap: Bitmap,checkAllocation: (Long)->Unit={}): HdrGainMap? {
        if (Build.VERSION.SDK_INT < 34) return null
        return read34(bitmap,checkAllocation)
    }

    @TargetApi(34)
    private fun read34(bitmap: Bitmap,checkAllocation: (Long)->Unit): HdrGainMap? {
        val gainmap = validated34(bitmap) ?: return null
        val map = gainmap.gainmapContents
        // Extraction plus a possible EXIF-oriented copy; the base bitmap is
        // already resident and accounted for by the fresh memory snapshot.
        checkAllocation(map.width.toLong()*map.height*24+map.width.toLong()*4+1048576)
        if (map.colorSpace != null && !map.colorSpace!!.isSrgb)
            throw CaptureNotReady("Unsupported encoded gain sample color space.")
        val values = if (map.config == Bitmap.Config.ALPHA_8) {
            // Single-plane gainmaps may be exposed as alpha-only bitmaps.
            val result = FloatArray(Math.multiplyExact(Math.multiplyExact(map.width,map.height),3))
            val row = IntArray(map.width)
            for (y in 0 until map.height) {
                map.getPixels(row,0,map.width,0,y,map.width,1)
                for (x in row.indices) for (c in 0..2)
                    result[(y*map.width+x)*3+c] = ((row[x] ushr 24) and 255)/255f
            }
            result
        } else ArgbRowConverter.convert(map.width,map.height) { pixels,start,rows ->
            map.getPixels(pixels,0,map.width,0,start,map.width,rows)
        }
        return HdrGainMap(map.width,map.height,values,gainmap.ratioMin,gainmap.ratioMax,
            gainmap.gamma,gainmap.epsilonSdr,gainmap.epsilonHdr)
    }

    @TargetApi(36)
    private fun validate36(gainmap: Gainmap) {
        if (gainmap.gainmapDirection != Gainmap.GAINMAP_DIRECTION_SDR_TO_HDR)
            throw CaptureNotReady("HDR-base gainmaps need a separate ingest path.")
        val primaries = gainmap.alternativeImagePrimaries
        if (primaries != null && !primaries.isSrgb)
            throw CaptureNotReady("Ultra HDR gainmap application primaries are not sRGB.")
    }
}
