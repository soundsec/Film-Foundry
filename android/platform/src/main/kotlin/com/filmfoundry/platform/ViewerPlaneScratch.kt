package com.filmfoundry.platform

import java.nio.ByteBuffer

/** Serial, synchronous viewer stream only. Borrowed copies never become capture or stored packets. */
class ViewerPlaneScratch {
    private val planes=arrayOfNulls<ByteArray>(3)
    fun copy(plane: Int,input: ByteBuffer): ByteArray {
        require(plane in planes.indices)
        val view=input.duplicate()
        val bytes=planes[plane]?.takeIf { it.size==view.remaining() } ?: ByteArray(view.remaining()).also { planes[plane]=it }
        view.get(bytes);return bytes
    }
}
