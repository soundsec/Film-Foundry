package com.filmfoundry.app.ui

import android.app.AlertDialog
import android.graphics.*
import android.view.View
import android.widget.*
import android.text.Editable
import android.text.TextWatcher
import androidx.activity.ComponentActivity
import com.filmfoundry.workflow.FilmBorderPreview
import com.filmfoundry.workflow.FilmEdgeSettings
import com.filmfoundry.interchange.EdgeMarkPattern

/** An explicit layout diagram: no option here claims to have exposed or scanned film rebate. */
fun showFilmBorderPreview(activity: ComponentActivity,shell: MobileShell,format: String,custom: String="",marks: Boolean=true,
    factorySettings: Map<String,Any?> = emptyMap(),previewSample: (Map<String,Any?>)->Unit={},save: (String,Boolean,Map<String,Any?>)->Unit={ _,_,_->}) {
    fun c(zh: String,en: String)=shell.copy(zh,en)
    val panel=LinearLayout(activity).apply { orientation=LinearLayout.VERTICAL;setPadding(shell.dp(20),shell.dp(12),shell.dp(20),shell.dp(16)) }
    shell.note(panel,"新卷可保留完整片边；厂印随材料与冲洗形成。","New rolls can retain film edges; factory marks respond to stock and processing.")
    var key=when(format) { "half"->"half_frame";"6x4.5","6x6","6x7","6x9"->"120";else->format }.takeIf { it in FilmBorderPreview.formats } ?: "135"
    val row="both";var text=custom.ifBlank { "FILM FOUNDRY" }+" 01";val top=false
    var showMarks=marks
    var patterns=FilmEdgeSettings.patterns(factorySettings)
    val paint=Paint(Paint.ANTI_ALIAS_FLAG)
    val preview=object: View(activity) {
        override fun onDraw(canvas: Canvas) {
            super.onDraw(canvas)
            val gate=when(key) { "half_frame"->240 to 320;"120"->400 to 400;else->480 to 320 }
            val layout=FilmBorderPreview.layout(key,gate.first,gate.second,row,text,top)
            val scale=minOf(width.toFloat()/layout.width,height.toFloat()/layout.height);canvas.save();canvas.translate((width-layout.width*scale)/2,(height-layout.height*scale)/2);canvas.scale(scale,scale)
            paint.color=Color.rgb(76,44,25);canvas.drawRect(0f,0f,layout.width.toFloat(),layout.height.toFloat(),paint)
            paint.color=Color.rgb(29,29,29);canvas.drawRect(0f,layout.rebate.toFloat(),layout.width.toFloat(),(layout.height-layout.rebate).toFloat(),paint)
            paint.color=Color.rgb(242,238,229);for(hole in layout.holes) { val radius=(minOf(hole.right-hole.left,hole.bottom-hole.top)*.2).toFloat();canvas.drawRoundRect(hole.left.toFloat(),hole.top.toFloat(),hole.right.toFloat(),hole.bottom.toFloat(),radius,radius,paint) }
            paint.color=Color.rgb(217,169,86);paint.typeface=Typeface.MONOSPACE;paint.textSize=layout.textBand.fontSize.toFloat()
            if(showMarks) for(isTop in listOf(true,false)) {
                val band=FilmBorderPreview.layout(key,gate.first,gate.second,row,text,isTop).textBand
                val source=if(isTop || patterns["factory_pattern_linked"]==true) "factory_pattern_top" else "factory_pattern_bottom"
                val pattern=patterns[source]?.toString()?.takeIf { it.isNotBlank() }?.let(EdgeMarkPattern::decode)
                canvas.save()
                if(patterns[if(isTop) "factory_flip_top" else "factory_flip_bottom"]==true) canvas.rotate(180f,layout.width/2f,((band.top+band.bottom)/2).toFloat())
                paint.textSize=band.fontSize.toFloat();val metrics=paint.fontMetrics
                if(pattern==null) {
                    val chars=paint.breakText(layout.text,true,layout.width-16f,null)
                    canvas.drawText(layout.text.take(chars),8f,((band.top+band.bottom)/2).toFloat()-(metrics.ascent+metrics.descent)/2,paint)
                } else {
                    val left=layout.width*.04f;val width=layout.width*.78f;val height=(band.bottom-band.top).toFloat()
                    for(y in 0 until pattern.height) for(x in 0 until pattern.width) if(pattern.ink(x,y)) canvas.drawRect(left+x*width/pattern.width,(band.top+y*height/pattern.height).toFloat(),left+(x+1)*width/pattern.width,(band.top+(y+1)*height/pattern.height).toFloat(),paint)
                    canvas.drawText("01",layout.width*.86f,((band.top+band.bottom)/2).toFloat()-(metrics.ascent+metrics.descent)/2,paint)
                }
                canvas.restore()
            }
            paint.color=Color.rgb(160,160,160);paint.textSize=20f;val title=c("成像区域","IMAGE GATE")
            canvas.drawText(title,(layout.width-paint.measureText(title))/2f,layout.height/2f,paint);canvas.restore()
        }
    }
    val screen=activity.resources.displayMetrics.heightPixels
    val root=object: LinearLayout(activity) {
        init { orientation=VERTICAL }
        override fun onMeasure(w: Int,h: Int) {
            val limit=if(MeasureSpec.getMode(h)==MeasureSpec.UNSPECIFIED) screen else MeasureSpec.getSize(h)
            val height=minOf((screen*.72).toInt(),limit)
            preview.layoutParams?.height=minOf(shell.dp(220),(height*.43).toInt())
            super.onMeasure(w,MeasureSpec.makeMeasureSpec(height,MeasureSpec.EXACTLY))
        }
    }
    root.addView(preview,LinearLayout.LayoutParams(-1,shell.dp(220)))
    root.addView(ScrollView(activity).apply { addView(panel) },LinearLayout.LayoutParams(-1,0,1f))
    shell.note(panel,"照片 → 齿孔 → 边字；120没有齿孔。这里是排版示意，扩散与光晕看冲扫样张。","Image → sprockets → marks; 120 has no holes. This is a layout diagram; the processed sample shows spread and halation.")
    val print=CheckBox(activity).apply { this.text=c("打印厂印边字","Print factory edge text");isChecked=marks };panel.addView(print)
    val label=EditText(activity).apply { setSingleLine(true);filters=arrayOf(android.text.InputFilter.AllCaps(),android.text.InputFilter.LengthFilter(32));setText(custom) }
    shell.field(panel,label,c("自定义边字","Custom edge text"),c("最多32个英文字母、数字、空格或 -。留空用材料名称，格号自动追加；仅用于新装卷。","Up to 32 letters, digits, spaces or -. Blank uses the stock name; frame numbers are automatic. New rolls only."))
    fun update(): String? {
        val value=runCatching { FilmEdgeSettings.customLabel(label.text.toString()) }.getOrElse {
            label.error=c("请使用英文字母、数字、空格或 -","Use letters, digits, spaces or -");return null
        }
        label.error=null;label.isEnabled=print.isChecked;showMarks=print.isChecked
        text=value.ifBlank { "FILM FOUNDRY" }+" 01";preview.invalidate();return value
    }
    label.addTextChangedListener(object: TextWatcher {
        override fun beforeTextChanged(s: CharSequence?,start: Int,count: Int,after: Int)=Unit
        override fun onTextChanged(s: CharSequence?,start: Int,before: Int,count: Int) { update() }
        override fun afterTextChanged(s: Editable?)=Unit
    })
    print.setOnCheckedChangeListener { _,_->update() };update()
    shell.action(panel,"绘制片边图案…","Draw edge stencil…") {
        showEdgeMarkEditor(activity,shell,patterns) { result -> patterns=result;preview.invalidate() }
    }
    shell.action(panel,"预览冲洗与扫描样张…","Preview processed & scanned sample…") {
        update()?.let { previewSample(mapOf("film_border" to FilmEdgeSettings.create(key,true,"FILM FOUNDRY",it,print.isChecked,patterns))) }
    }
    val dialog=AlertDialog.Builder(activity).setTitle(c("齿孔与厂印边字","Sprockets & factory edge text")).setView(root)
        .setNegativeButton(c("取消","Cancel"),null).setPositiveButton(c("保存为新卷默认","Save for new rolls"),null).create()
    dialog.setOnShowListener { dialog.getButton(AlertDialog.BUTTON_POSITIVE).setOnClickListener { update()?.let { save(it,print.isChecked,patterns);dialog.dismiss() } } }
    shell.decorate(panel);dialog.show()
}
