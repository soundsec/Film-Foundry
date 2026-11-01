package com.filmfoundry.interchange

import java.io.InputStream

/** Explicit display-only proxy. Never serialize this proxy as the original medium. */
data class OpticalPreview(val archive: DevelopedOpticalArchive, val sourceWidth: Int, val sourceHeight: Int, val stride: Int,
    val sampler: String="nearest_sample_optical_preview_v1")

/** Decode an already sampled private proxy once; validate dimensions and bounded expansion. */
fun readCachedOpticalPreview(input: InputStream,width: Int,height: Int,stride: Int,edge: Int,check: ()->Unit={}): OpticalPreview {
    require(width>0 && height>0 && stride>0 && edge in 1..2048)
    val pw=(width-1)/stride+1;val ph=(height-1)/stride+1
    require(pw<=edge && ph<=edge)
    val pixels=pw.toLong()*ph
    val keys=setOf(DataKernel.DEVELOPED_METADATA_KEY,DataKernel.CLEAR_BASE_KEY,DataKernel.OPTICAL_DENSITY_KEY,SpectralOptics.AMOUNTS_KEY,SpectralOptics.POST_PROCESS_KEY)
    val entries=NpzFile.readChunks(input,{ check();it in keys },NpzReadLimits(maxMembers=8,
        maxTotalBytes=pixels*15*4+2L*1024*1024,maxStringBytes=1024*1024,
        maxRetainedBytes=maxOf(pixels*9*4,1024L*1024))) { _,_,_,_,_->check() }
    check()
    val archive=NpzMediumArchive().opticalFromEntries(entries)
    require(archive.opticalDensityRgb.shape[1]==pw && archive.opticalDensityRgb.shape[0]==ph)
    return OpticalPreview(archive,width,height,stride,"box_mean_optical_preview_v3")
}

fun readOpticalPreview(input: InputStream, maxLongEdge: Int = 960, checkCancel: () -> Unit = {}): OpticalPreview {
    require(maxLongEdge in 1..2048)
    val channels = mapOf(DataKernel.OPTICAL_DENSITY_KEY to 3, SpectralOptics.AMOUNTS_KEY to 9, SpectralOptics.POST_PROCESS_KEY to 3)
    val arrays = linkedMapOf<String, FloatNd>()
    val shapes = linkedMapOf<String, IntArray>()
    val strips=HashMap<String,DoubleArray>()
    val seen = mutableSetOf<String>()
    val scalar = NpzFile.readChunks(input, { key ->
        seen.add(key)
        key == DataKernel.DEVELOPED_METADATA_KEY || key == DataKernel.CLEAR_BASE_KEY
    }, NpzReadLimits(maxMembers = 64, maxStringBytes = 1024*1024, maxRetainedBytes = 1024*1024)) { key, shape, offset, values, count ->
        checkCancel()
        val c = channels[key]
        if (c != null) {
            require(shape.size == 3 && shape[0] > 0 && shape[1] > 0 && shape[2] == c) { "Invalid optical preview field $key" }
            val h = shape[0]; val w = shape[1]
            val stride = ((maxOf(h,w).toLong()+maxLongEdge-1)/maxLongEdge).toInt()
            val ph = (h-1)/stride+1; val pw = (w-1)/stride+1
            val array = arrays.getOrPut(key) {
                shapes[key] = shape.copyOf()
                if(stride>1) strips[key]=DoubleArray(pw*c)
                FloatNd(intArrayOf(ph,pw,c),FloatArray(ph*pw*c))
            }
            require(shapes.getValue(key).contentEquals(shape))
            // NpyCodec already rejects non-finite values before delivering this borrowed chunk.
            val signed=key==SpectralOptics.POST_PROCESS_KEY
            if(stride==1) {
                if(!signed) for(i in 0 until count) require(values[i]>=0f) { "Invalid optical value in $key" }
                values.copyInto(array.values,offset,0,count)
                return@readChunks
            }
            val strip=if(stride>1) strips.getValue(key) else null
            val pixel=offset/c
            var y=pixel/w;var x=pixel%w;var channel=offset%c
            var within=x%stride;var target=(x/stride)*c+channel
            for(i in 0 until count) {
                val value=values[i]
                require(signed || value>=0f) { "Invalid optical value in $key" }
                val sums=strip!!
                sums[target]+=value.toDouble();target++;channel++
                if(channel<c) continue
                channel=0;x++
                if(x==w) {
                  if((y+1)%stride==0 || y==h-1) {
                    val outY=y/stride;val cellHeight=minOf(stride,h-outY*stride)
                    // One bounded accumulation strip, then one division per output component.
                    // Avoid repeated division and FP32 rounding for every source component.
                    for(xx in 0 until pw) {
                        val n=minOf(stride,w-xx*stride)*cellHeight
                        for(k in 0 until c) {
                            val j=xx*c+k
                            array.values[outY*pw*c+j]=(sums[j]/n).toFloat();sums[j]=0.0
                        }
                    }
                  }
                  x=0;y++;target=0;within=0
                } else if(within+1==stride) {
                    within=0
                } else {
                    within++;target-=c
                }
            }
        }
    }
    checkCancel()
    require(seen.intersect(channels.keys) == arrays.keys) { "Optical fields must be numeric" }
    val shape = shapes[DataKernel.OPTICAL_DENSITY_KEY] ?: error("Missing optical density")
    require(shapes.values.all { it[0] == shape[0] && it[1] == shape[1] }) { "Optical field dimensions disagree" }
    val entries = scalar.toMutableMap()
    arrays.forEach { (key, array) -> entries[key] = NpyValue.Float32(array) }
    val archive = NpzMediumArchive().opticalFromEntries(entries)
    return OpticalPreview(archive, shape[1], shape[0], ((maxOf(shape[0],shape[1]).toLong()+maxLongEdge-1)/maxLongEdge).toInt(),"box_mean_optical_preview_v3")
}
