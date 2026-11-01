package com.filmfoundry.app.ui

import android.graphics.Matrix
import android.graphics.RectF
import android.widget.ImageView
import com.filmfoundry.platform.OutputOrientation

/** Rotate the display matrix, never allocate another image just to turn its preview. */
fun orientOutputView(view: ImageView,degrees: Int,crop: Boolean=false) {
    val source=view.drawable ?: return
    if(view.width<=0 || view.height<=0 || source.intrinsicWidth<=0 || source.intrinsicHeight<=0) return
    val matrix=Matrix().apply { setRotate(OutputOrientation.normalized(degrees).toFloat()) }
    val bounds=RectF(0f,0f,source.intrinsicWidth.toFloat(),source.intrinsicHeight.toFloat());matrix.mapRect(bounds)
    val sx=view.width/bounds.width();val sy=view.height/bounds.height();val scale=if(crop) maxOf(sx,sy) else minOf(sx,sy)
    matrix.postTranslate(-bounds.left,-bounds.top);matrix.postScale(scale,scale)
    matrix.postTranslate((view.width-bounds.width()*scale)/2,(view.height-bounds.height()*scale)/2)
    view.scaleType=ImageView.ScaleType.MATRIX;view.imageMatrix=matrix
}
