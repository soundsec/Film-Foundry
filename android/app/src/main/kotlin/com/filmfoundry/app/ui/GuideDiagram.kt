package com.filmfoundry.app.ui

/** Frontend-only vector scenes, shared by the Android painter and offline visual review. */
object GuideDiagram {
    enum class Ink { PANEL, EDGE, TEXT, MUTED, ACCENT, IMAGE, FILM }
    sealed interface Mark { val ink: Ink }
    data class Box(val x: Float,val y: Float,val w: Float,val h: Float,val fill: Boolean=true,val radius: Float=4f,override val ink: Ink=Ink.EDGE): Mark
    data class Circle(val x: Float,val y: Float,val r: Float,val fill: Boolean=false,override val ink: Ink=Ink.EDGE): Mark
    data class Line(val x: Float,val y: Float,val x2: Float,val y2: Float,override val ink: Ink=Ink.EDGE): Mark
    data class Label(val x: Float,val y: Float,val value: String,val size: Float=11f,val width: Float=100f,override val ink: Ink=Ink.TEXT): Mark
    data class Triangle(val points: List<Float>,override val ink: Ink=Ink.MUTED): Mark
    fun color(ink: Ink,light: Boolean): Int=when(ink) {
        Ink.PANEL->if(light) 0xFFF2EDE3.toInt() else 0xFF242622.toInt()
        Ink.EDGE->if(light) 0xFF756B59.toInt() else 0xFF9A9E89.toInt()
        Ink.TEXT->if(light) 0xFF302C24.toInt() else 0xFFF0E9D9.toInt()
        Ink.MUTED->if(light) 0xFF8B9C90.toInt() else 0xFF547267.toInt()
        Ink.ACCENT->if(light) 0xFF916224.toInt() else 0xFFE6B765.toInt()
        Ink.IMAGE->if(light) 0xFFC2D6CD.toInt() else 0xFF324E48.toInt()
        Ink.FILM->if(light) 0xFFAC7655.toInt() else 0xFF825136.toInt()
    }
    fun scene(index: Int,zh: Boolean): List<Mark> = buildList {
        fun c(a: String,b: String)=if(zh) a else b
        fun box(x: Int,y: Int,w: Int,h: Int,ink: Ink=Ink.EDGE,fill: Boolean=true,r: Int=4) { add(Box(x.toFloat(),y.toFloat(),w.toFloat(),h.toFloat(),fill,r.toFloat(),ink)) }
        fun line(x: Int,y: Int,x2: Int,y2: Int,ink: Ink=Ink.EDGE) { add(Line(x.toFloat(),y.toFloat(),x2.toFloat(),y2.toFloat(),ink)) }
        fun label(x: Int,y: Int,s: String,size: Int=11,w: Int=100,ink: Ink=Ink.TEXT) { add(Label(x.toFloat(),y.toFloat(),s,size.toFloat(),w.toFloat(),ink)) }
        fun circle(x: Int,y: Int,r: Int,ink: Ink=Ink.EDGE,fill: Boolean=false) { add(Circle(x.toFloat(),y.toFloat(),r.toFloat(),fill,ink)) }
        fun arrow(x: Int,y: Int,end: Int) { line(x,y,end,y,Ink.ACCENT);line(end-6,y-5,end,y,Ink.ACCENT);line(end-6,y+5,end,y,Ink.ACCENT) }
        fun photo(x: Int,y: Int,w: Int,h: Int) {
            box(x,y,w,h,Ink.IMAGE)
            add(Triangle(listOf(x.toFloat(),(y+h).toFloat(),x+w*.43f,y+h*.28f,(x+w).toFloat(),(y+h).toFloat())))
            add(Triangle(listOf(x+w*.38f,(y+h).toFloat(),x+w*.77f,y+h*.52f,(x+w).toFloat(),(y+h).toFloat()),Ink.EDGE))
        }
        fun strip(x: Int,y: Int,w: Int,h: Int) {
            box(x,y,w,h,Ink.FILM)
            for(i in 0..5) { box(x+4+i*(w-10)/6,y+3,4,4,Ink.PANEL,r=1);box(x+4+i*(w-10)/6,y+h-7,4,4,Ink.PANEL,r=1) }
            for(i in 0..2) box(x+5+i*(w-8)/3,y+12,(w-16)/3,h-24,Ink.PANEL)
        }
        fun roll(x: Int,y: Int) {
            box(x,y,44,62,Ink.FILM,r=7);box(x-3,y,50,7,Ink.EDGE);box(x-3,y+55,50,7,Ink.EDGE)
            label(x+22,y+27,"FILM",10,38,Ink.TEXT);label(x+22,y+44,"135",13,38,Ink.TEXT)
        }
        box(0,0,360,152,Ink.PANEL,r=12)
        when(index) {
            0 -> {
                roll(15,29);strip(59,47,62,30);arrow(129,61,158)
                box(169,18,177,99,Ink.EDGE,false,9);box(176,25,25,83,Ink.PANEL)
                label(188,48,"EV",9,24,Ink.ACCENT);label(188,66,"0.0",10,24,Ink.ACCENT);label(188,90,"01/12",7,24,Ink.ACCENT)
                photo(207,28,100,67);box(212,33,90,57,Ink.TEXT,false,r=1)
                circle(326,63,12,Ink.ACCENT);circle(326,63,8,Ink.ACCENT,true);arrow(310,100,338)
                label(46,136,c("装填胶卷","Load film"),12,110);label(257,136,c("曝光 · 过片 / 多曝","Expose · Advance / Multi"),12,180)
            }
            1 -> {
                strip(13,45,66,48);arrow(84,68,103)
                for((i,name) in listOf("DEV","BL","FIX").withIndex()) {
                    val x=111+i*69
                    box(x,32,56,69,Ink.EDGE,false);box(x+3,63+i*5,50,35-i*5,if(i==1) Ink.IMAGE else Ink.FILM)
                    line(x+5,56+i*5,x+51,56+i*5,Ink.ACCENT);label(x+28,50,name,11,48)
                    if(i<2) arrow(x+58,72,x+65)
                }
                line(329,39,329,86,Ink.ACCENT);circle(329,92,7,Ink.ACCENT,true)
                label(46,133,c("已曝光胶卷","Exposed roll"),11,92);label(215,123,c("药液顺序 · 温度 · 时间","Bath order · Temperature · Time"),11,210)
                label(215,141,c("摇晃强度与时长","Agitation strength and duration"),10,210,Ink.ACCENT)
            }
            2 -> {
                strip(12,31,91,65);arrow(108,65,128)
                box(137,39,93,59,Ink.EDGE,false);line(135,28,227,17,Ink.ACCENT)
                for(x in listOf(156,180,204)) { line(x,40,x-7,60,Ink.ACCENT);line(x,40,x+7,60,Ink.ACCENT) }
                box(148,66,70,20,Ink.FILM);line(151,91,205,91,Ink.ACCENT)
                arrow(236,65,258);box(270,23,78,79,Ink.EDGE,false);photo(276,29,66,54)
                label(57,127,c("底片观片","View negative"),11,105);label(184,127,c("调整扫描","Adjust scan"),11,107);label(309,127,c("扫描结果","Saved scan"),11,92)
            }
            3 -> {
                for(x in listOf(8,130,252)) box(x,13,100,126,Ink.EDGE,false,8)
                roll(35,24);strip(139,38,82,45);photo(266,29,72,62)
                arrow(112,65,126);arrow(234,65,248)
                label(58,107,c("曝光 · 整卷","Exposure · roll"),11,94);label(180,107,c("底片 · 单张","Negative · frame"),11,94);label(302,107,c("扫描 · 单张","Scan · image"),11,94)
                label(58,127,c("可重新冲洗","Redevelop"),10,94,Ink.ACCENT);label(180,127,c("可重新扫描","Rescan"),10,94,Ink.ACCENT);label(302,127,c("可查看 / 导出","View / Export"),10,94,Ink.ACCENT)
            }
            4 -> {
                for(i in 0..5) {
                    val x=13+(i%3)*70;val y=15+(i/3)*49;photo(x,y,61,40)
                    if(i==0 || i==4) { box(x+44,y+2,15,15,Ink.ACCENT,r=3);line(x+47,y+8,x+50,y+11,Ink.PANEL);line(x+50,y+11,x+56,y+5,Ink.PANEL) }
                }
                box(245,15,101,88,Ink.EDGE,false);photo(253,23,85,60)
                arrow(254,116,335);line(254,116,260,111,Ink.ACCENT);line(254,116,260,121,Ink.ACCENT)
                label(111,138,c("长按多选 · 批量管理","Hold · Select · Manage"),11,211);label(295,138,c("滑动浏览","Swipe to browse"),11,113)
            }
            else -> error("Unknown guide scene")
        }
    }
}
