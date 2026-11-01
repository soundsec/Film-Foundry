package com.filmfoundry.app.session

import com.filmfoundry.platform.*
import com.filmfoundry.workflow.workingRgbRows

/** Read camera display encoding as oriented linear rows without another full-frame copy. */
class ViewerFrameRows(private val frame: ViewerPreviewFrame,private val check: ()->Unit={},private val scratch: ViewerRowScratch?=null) {
    private val orientation=CapturePixelOrientation(frame.width,frame.height,when((frame.rotationDegrees%360+360)%360) {
        0->1;90->6;180->3;270->8;else->throw CaptureRejected("Viewer rotation must be a quarter turn")
    })
    val width=orientation.width
    val height=orientation.height
    fun rows()=workingRgbRows(width,height) { start,count ->
        require(start>=0 && count>0 && start+count<=height);check()
        val values=scratch?.take(width*count*3) ?: FloatArray(width*count*3)
        for(y in 0 until count) for(x in 0 until width) {
            val source=(orientation.y(x,start+y)*frame.width+orientation.x(x,start+y))*3
            val target=(y*width+x)*3
            for(c in 0..2) values[target+c]=if(frame.encoding==ViewerPreviewEncoding.LINEAR) frame.rgb[source+c] else scratch?.decode(frame.rgb[source+c]) ?: TransferFunctions.srgbToLinear(frame.rgb[source+c])
        }
        values
    }
}

/** Two row sizes (full block and tail), owned by the serial scene renderer. */
class ViewerRowScratch {
    // Serial session-owned exact memoization for the existing YUV-derived Float samples.
    // No quantization, interpolation, global sharing or camera-frame retention.
    private val keys=IntArray(8192){Int.MIN_VALUE}
    private val decoded=FloatArray(8192)
    fun decode(value: Float): Float {
        val v=value.coerceIn(0f,1f);val bits=v.toRawBits()
        val slot=((bits*0x9E3779B9.toInt()) ushr 19) and 8191
        if(keys[slot]==bits) return decoded[slot]
        val linear=TransferFunctions.srgbToLinear(v)
        decoded[slot]=linear;keys[slot]=bits
        return linear
    }
    private var main: FloatArray?=null;private var tail: FloatArray?=null
    fun take(size: Int): FloatArray {
        main?.takeIf { it.size==size }?.let { return it }
        tail?.takeIf { it.size==size }?.let { val found=it;tail=main;main=found;return found }
        return FloatArray(size).also { tail=main;main=it }
    }
}
