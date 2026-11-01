package com.filmfoundry.app.ui

import android.graphics.*
import android.view.*
import android.widget.*

/** A cached 256-bin path. Drawing never reads a bitmap or touches material data. */
internal class HistogramTrace {
    private val path=Path()
    private val paint=Paint(Paint.ANTI_ALIAS_FLAG)
    private var bound: DisplayHistogram?=null
    private var boundChannel=-1
    private var boundLogarithmic=false
    private var boundPeak=0
    fun bind(value: DisplayHistogram?,channel: Int,logarithmic: Boolean=false,referencePeak: Int=value?.plotPeak(channel..channel) ?: 1) {
        if(bound===value && boundChannel==channel && boundLogarithmic==logarithmic && boundPeak==referencePeak) return
        bound=value;boundChannel=channel;boundLogarithmic=logarithmic;boundPeak=referencePeak
        path.rewind()
        if(value==null) return
        val heights=value.heights(channel,logarithmic,referencePeak)
        path.moveTo(0f,1f)
        for(i in 0..255) {
            val y=1f-heights[i]
            path.lineTo(i/256f,y);path.lineTo((i+1)/256f,y)
        }
        path.lineTo(1f,1f);path.close()
    }
    fun draw(canvas: Canvas,left: Float,top: Float,right: Float,bottom: Float,color: Int) {
        canvas.save();canvas.translate(left,top);canvas.scale(right-left,bottom-top)
        paint.color=color;paint.style=Paint.Style.FILL;canvas.drawPath(path,paint);canvas.restore()
    }
}

/** Optional display-only chart in the preview corner; no extra pixel work. */
class ScanHistogramPanel(private val shell: MobileShell,parent: FrameLayout,shown: Boolean,onToggle: (Boolean)->Unit): LinearLayout(parent.context) {
    private val traces=List(4) { HistogramTrace() }
    private var caption=""
    private var histogram: DisplayHistogram?=null
    private var logarithmic=false
    private var pending=false
    private var initial=false
    private val title=TextView(context).apply { textSize=10f;setTextColor(Color.WHITE);maxLines=1;ellipsize=android.text.TextUtils.TruncateAt.END }
    private val chart=object: View(context) {
        private val guide=Paint(Paint.ANTI_ALIAS_FLAG)
        override fun onDraw(canvas: Canvas) {
            val bottom=height-shell.dp(13).toFloat()
            guide.color=0x33777777;guide.strokeWidth=shell.dp(1).toFloat()
            for(i in 1..3) canvas.drawLine(width*i/4f,0f,width*i/4f,bottom,guide)
            canvas.drawLine(0f,bottom,width.toFloat(),bottom,guide)
            val colors=intArrayOf(0x44777777,0x88D56C68.toInt(),0x886BB37A.toInt(),0x88769ED0.toInt())
            traces.forEachIndexed { i,trace->trace.draw(canvas,0f,shell.dp(3).toFloat(),width.toFloat(),bottom,colors[i]) }
            guide.color=0xFFAAAAAA.toInt();guide.textSize=shell.dp(9).toFloat()
            guide.textAlign=Paint.Align.LEFT;canvas.drawText("0",0f,height.toFloat()-1f,guide)
            guide.textAlign=Paint.Align.RIGHT;canvas.drawText("255",width.toFloat(),height.toFloat()-1f,guide)
            for(high in listOf(false,true)) {
                val channels=histogram?.clippedChannels(high) ?: 0
                guide.color=if(channels==0) 0xFF555555.toInt() else 0xFF000000.toInt() or channels
                val x=if(high) width.toFloat()-shell.dp(9) else 0f;val size=shell.dp(8).toFloat()
                val triangle=Path().apply { moveTo(x,0f);lineTo(x+size,0f);lineTo(x+size/2,size);close() }
                canvas.drawPath(triangle,guide)
            }
        }
    }
    private val toggle=Button(context)
    init {
        orientation=VERTICAL;setPadding(shell.dp(8),shell.dp(5),shell.dp(8),shell.dp(6))
        background=android.graphics.drawable.GradientDrawable().apply { setColor(0xBB111111.toInt());cornerRadius=shell.dp(10).toFloat() }
        title.minHeight=shell.dp(16)
        addView(title,LayoutParams(-1,-2));addView(chart,LayoutParams(-1,shell.dp(72)))
        parent.addView(this,FrameLayout.LayoutParams(shell.dp(176),-2,Gravity.TOP or Gravity.END).apply { topMargin=shell.dp(64);marginEnd=shell.dp(8) })
        shell.navigationIcon(toggle,NavigationIcon.HISTOGRAM,true)
        parent.addView(toggle,FrameLayout.LayoutParams(shell.dp(48),shell.dp(48),Gravity.TOP or Gravity.END).apply { topMargin=shell.dp(8);marginEnd=shell.dp(64) })
        fun display(show: Boolean) {
            visibility=if(show) View.VISIBLE else View.GONE;toggle.isSelected=show
            toggle.contentDescription=shell.copy(if(show) "隐藏直方图" else "显示直方图",if(show) "Hide histogram" else "Show histogram")
            toggle.tooltipText=toggle.contentDescription
        }
        display(shown)
        title.setOnClickListener { toggleScale() }
        chart.setOnClickListener { toggleScale() }
        title.tooltipText=shell.copy("最终扫描预览；横轴0–255，纵轴自动缩放，裁切峰值另示。点击切换线性／对数。","Final scan preview; 0–255 levels, auto vertical scale, clipping shown separately. Tap for linear/log.")
        toggle.setOnClickListener { val show=visibility!=View.VISIBLE;display(show);onToggle(show) }
    }
    fun bind(value: DisplayHistogram?,label: String,pending: Boolean=false,initial: Boolean=false) {
        caption=label;histogram=value;this.pending=pending;this.initial=initial;refresh()
    }
    private fun toggleScale() { logarithmic=!logarithmic;refresh() }
    private fun refresh() {
        val scale=if(logarithmic) shell.copy("对数","Log") else shell.copy("线性","Linear")
        title.text=when { histogram==null->shell.copy("等待扫描预览","Waiting for scan");pending && !initial->shell.copy("待刷新 · $scale","Pending · $scale");initial->shell.copy("初始扫描 · $scale","Initial scan · $scale");else->shell.copy("扫描结果 · $scale","Scan output · $scale") }
        chart.contentDescription="$caption · $scale · "+shell.copy("RGB共用纵轴；两端三角标示裁切；点击切换线性／对数","Shared RGB scale; corner triangles indicate clipping; tap for linear/log")
        val peak=histogram?.plotPeak() ?: 1
        traces.forEachIndexed { i,trace->trace.bind(histogram,i,logarithmic,peak) };chart.invalidate()
    }
}
