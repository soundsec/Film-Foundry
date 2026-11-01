package com.filmfoundry.app.ui

import android.graphics.Bitmap
import com.filmfoundry.workflow.OpticalPreviewSession
import com.filmfoundry.workflow.WorkflowControl
import com.filmfoundry.workflow.RowObservationSession
import com.filmfoundry.platform.MediumStore
import com.filmfoundry.platform.AndroidProcessingMemory
import com.filmfoundry.platform.ProcessingMemorySnapshot

/** One display bitmap with a single packed row, avoiding another full-frame IntArray. */
fun observationBitmap(observation: OpticalPreviewSession.Observation,control: WorkflowControl,histogram: DisplayHistogram.Builder?=null): Bitmap {
    val w=observation.width;val h=observation.height
    val bitmap=Bitmap.createBitmap(w,h,Bitmap.Config.ARGB_8888)
    try {
        val row=IntArray(w)
        for(y in 0 until h) {
            control.checkCancel()
            for(x in 0 until w) {
                val i=(y*w+x)*3
                row[x]=histogram?.addEncodedRgb(observation.rgb,i) ?: encodedPreviewArgb(observation.rgb,i)
            }
            bitmap.setPixels(row,0,w,0,y,w,1)
        }
        return bitmap
    } catch(error: Throwable) { bitmap.recycle();throw error }
}

/** Formal observation retains the final display bitmap, with no whole-frame float source/output. */
fun scanObservationBitmap(store: MediumStore,path: String,recipe: Map<String,Any?>,control: WorkflowControl,memoryBudgetBytes: Long,
    memory: ()->ProcessingMemorySnapshot={ AndroidProcessingMemory.snapshot() }): Bitmap {
    var bitmap: Bitmap?=null
    try {
        store.scanRows(path,recipe,object: RowObservationSession.Output {
            override fun begin(width: Int,height: Int) {
                memory().requireAllocation(width.toLong()*height*4+2L*1048576,"Scan output")
                bitmap=Bitmap.createBitmap(width,height,Bitmap.Config.ARGB_8888)
            }
            override fun rows(start: Int,count: Int,rgb: FloatArray) {
                val target=checkNotNull(bitmap);val width=target.width;val row=IntArray(width)
                for(y in 0 until count) {
                    control.checkCancel()
                    for(x in 0 until width) {
                        val i=(y*width+x)*3
                        fun channel(c: Int)=(rgb[i+c].coerceIn(0f,1f)*255).toInt()
                        row[x]=(255 shl 24) or (channel(0) shl 16) or (channel(1) shl 8) or channel(2)
                    }
                    target.setPixels(row,0,width,0,start+y,width,1)
                }
            }
        },control,memoryBudgetBytes)
        return checkNotNull(bitmap)
    } catch(error: Throwable) { bitmap?.recycle();throw error }
}
