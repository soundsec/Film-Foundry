package com.filmfoundry.automation

import android.content.Context
import android.graphics.Bitmap
import android.graphics.BitmapFactory
import android.graphics.Gainmap
import android.os.Build
import com.filmfoundry.kernel.*
import com.filmfoundry.interchange.*
import com.filmfoundry.platform.*
import java.io.File
import kotlin.math.abs

/** Synthetic files only. No camera, capture permissions, MediaStore or real library. */
internal object CaptureIngestProbe {
    fun run(testContext: Context,root: File,verify: (String,()->Unit)->Unit) {
        fun pipeline(width: Int,height: Int,values: FloatArray) {
            check(values.all { it.isFinite() })
            val kernel=PhoneReducedKernel()
            val latent=kernel.expose(RgbVolume(width,height,linearRgb=values),emptyMap())
            val optical=kernel.develop(latent,emptyMap());val before=optical.opticalDensityRgb.values.copyOf()
            val scan=kernel.observe(optical,emptyMap())
            check(scan.outputRgb.shape.contentEquals(intArrayOf(height,width,3)))
            check(scan.outputRgb.values.all { it.isFinite() && it in 0f..1f })
            check(before.contentEquals(optical.opticalDensityRgb.values))
        }
        verify("synthetic_dng_native_rows_to_scan") {
            val original=testContext.assets.open("fixtures/synthetic_bayer.dng").use { it.readBytes() }
            val file=File(root,"synthetic.dng").apply { writeBytes(original) }
            DngStoredExposurePixels(file).use { pixels ->
                pixels.configureCapture(mapOf("stored_pixel_orientation" to "dng_exif_v1"))
                val rgb=pixels.linearWindowRows(0,0,pixels.width,pixels.height,CaptureKind.SENSOR_RAW_DNG)
                val reference=DngRawDecoder.decode(DngCapture(original))
                check(reference.linearRgb.contentEquals(rgb))
                pipeline(pixels.width,pixels.height,rgb)
            }
            check(original.contentEquals(file.readBytes()))
        }
        if(Build.VERSION.SDK_INT<34) {
            verify("hdr_api_unavailable_explicit") { check(Build.VERSION.SDK_INT<34) }
            return
        }
        hdr(root,verify,::pipeline)
    }

    @android.annotation.TargetApi(34)
    private fun hdr(root: File,verify: (String,()->Unit)->Unit,pipeline: (Int,Int,FloatArray)->Unit) {
        val width=64;val height=48
        val file=File(root,"synthetic-hdr.jpg");val sdrFile=File(root,"synthetic-sdr.jpg")
        val base=Bitmap.createBitmap(width,height,Bitmap.Config.ARGB_8888)
        val map=Bitmap.createBitmap(4,3,Bitmap.Config.ARGB_8888)
        try {
            base.eraseColor(0xff808080.toInt());map.eraseColor(-1)
            sdrFile.outputStream().use { check(base.compress(Bitmap.CompressFormat.JPEG,100,it)) }
            val gain=Gainmap(map).apply {
                setRatioMin(1f,1f,1f);setRatioMax(8f,8f,8f);setGamma(1f,1f,1f)
                setEpsilonSdr(0f,0f,0f);setEpsilonHdr(0f,0f,0f)
                setMinDisplayRatioForHdrTransition(1f);setDisplayRatioForFullHdr(8f)
            }
            base.setGainmap(gain)
            file.outputStream().use { check(base.compress(Bitmap.CompressFormat.JPEG,100,it)) }
        } finally { base.recycle();map.recycle() }
        val before=file.readBytes()
        verify("ultra_hdr_real_file_gainmap_to_scan") {
            val bitmap=checkNotNull(BitmapFactory.decodeFile(file.path))
            val expected=try {
                check(bitmap.hasGainmap()) { "Android did not preserve the synthetic JPEG gainmap" }
                TransferFunctions.srgbToLinear(((bitmap.getPixel(16,16) ushr 16) and 255)/255f)*8f
            } finally { bitmap.recycle() }
            var previous: FloatArray?=null
            for(bounded in listOf(false,true)) AndroidStoredExposurePixels(file,bounded=bounded).use { pixels ->
                pixels.configureCapture(mapOf("kind" to CaptureKind.ULTRA_HDR_GAINMAP.wireName))
                val rgb=pixels.linearWindowRows(0,0,width,height,CaptureKind.ULTRA_HDR_GAINMAP)
                check(rgb.all { it>1f && abs(it-expected)<.02f }) { "HDR highlights were lost or gain applied incorrectly" }
                previous?.let { reference -> check(reference.indices.all { abs(reference[it]-rgb[it])<1e-6f }) }
                val window=pixels.linearWindowRows(7,9,13,5,CaptureKind.ULTRA_HDR_GAINMAP)
                for(y in 0 until 5) for(x in 0 until 13*3) check(abs(window[y*39+x]-rgb[((y+9)*width+7)*3+x])<1e-6f)
                if(previous==null) pipeline(width,height,rgb)
                previous=rgb
            }
            check(before.contentEquals(file.readBytes()))
        }
        verify("missing_gainmap_rejected_original_retained") {
            var rejected=false
            AndroidStoredExposurePixels(sdrFile).use { pixels ->
                pixels.configureCapture(mapOf("kind" to CaptureKind.ULTRA_HDR_GAINMAP.wireName))
                try { pixels.linearWindowRows(0,0,width,1,CaptureKind.ULTRA_HDR_GAINMAP) }
                catch(error: CaptureRejected) { rejected=true }
            }
            check(rejected && sdrFile.isFile)
        }
    }
}
