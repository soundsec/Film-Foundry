package com.filmfoundry.kernel

import kotlin.math.max
import kotlin.math.min

/** Explicit phone exposure proxy. Camera geometry is stable across frames and row partitions. */
class PhoneCaptureConditions(private val width: Int,private val height: Int,val settings: Map<String,Any?>) {
    private val camera=settings["camera"] as? Map<*,*> ?: emptyMap<Any,Any>()
    private val leak=(camera["leak"] as? Number)?.toFloat()?.also { require(it.isFinite() && it in 0f..1f) } ?: 0f
    private val side=camera["leak_side"]?.toString() ?: "left"
    private val shape=camera["leak_shape"]?.toString() ?: "edge"
    private val vertical=side == "top" || side == "bottom"
    private val reverse=side == "right" || side == "bottom"
    private val seed=(camera["seed"] as? Number)?.toLong() ?: 1L
    private val legacy=settings["model"] == null || settings["model"] == LEGACY_MODEL
    private val modern=settings["model"] == MODEL
    private val gateSeed=(camera["gate_seed"] as? Number)?.toLong() ?: seed
    private data class Mark(val x0: Int,val y0: Int,val x1: Int,val y1: Int,val oval: Boolean,val energy: Float=1f)
    private data class Leak(val shape: String,val side: String,val strength: Float,val offset: Long)
    private val components=if(modern && camera["leak_components"]!=null) {
        val raw=camera["leak_components"];require(raw is List<*> && raw.size<=8) { "up to eight leak components" }
        raw.map { value ->
            require(value is Map<*,*>)
            val s=value["shape"]?.toString() ?: "edge";val edge=value["side"]?.toString() ?: "left"
            val weight=(value["strength"] as? Number)?.toFloat() ?: 1f
            require(s in SHAPES && edge in SIDES && weight.isFinite() && weight in 0f..1f) { "invalid leak component" }
            Leak(s,edge,weight,(value["seed"] as? Number)?.toLong() ?: 0L)
        }
    } else listOf(Leak(shape,side,1f,0L))
    private val imprintStyle=if(modern) camera["imprint_style"]?.toString() ?: "optical_amber" else "legacy"
    private val imprintSide=if(modern) camera["imprint_side"]?.toString() ?: "bottom" else "bottom"
    private fun printNumber(key: String,default: Float,max: Float): Float {
        if(!modern || !camera.containsKey(key)) return default
        val value=(camera[key] as? Number)?.toFloat() ?: error("Invalid $key")
        require(value.isFinite() && value in 0f..max) { "Invalid $key" };return value
    }
    private val printBrightness=printNumber("imprint_brightness",1f,2f)
    private val printX=printNumber("imprint_x",0f,1f)
    private val printY=printNumber("imprint_y",if(imprintSide=="top") 0f else 1f,1f)
    // These are relative exposure additions, not display colours. Keep the core below the
    // material shoulder so observation does not turn every imprint style into clipped white.
    private val imprintColor=when(imprintStyle) { "laser_dot"->floatArrayOf(.22f,.014f,.002f);"lab_edge"->floatArrayOf(.012f,.20f,.004f);else->floatArrayOf(.18f,.055f,.002f) }
    private val dirt=mutableListOf<Mark>()
    private val print=mutableListOf<Mark>()
    init {
        require(width > 0 && height > 0)
        require(legacy || settings["model"] in listOf(PREVIOUS_MODEL,MODEL))
        require(side in SIDES && shape in if(modern) SHAPES else listOf("edge","corner","half"))
        require(!modern || imprintStyle in PRINT_STYLES && imprintSide in listOf("top","bottom"))
        var state=if(legacy) seed else gateSeed
        fun random(): Float { state=state*6364136223846793005L+1442695040888963407L;return ((state ushr 40) and 0xffffff).toFloat()/16777216f }
        fun marks(count: Int,size: Float,oval: Boolean) {
            repeat(count) {
                val x=(random()*width).toInt();val y=(random()*height).toInt()
                val r=max(1,(min(width,height)*size*(.5f+random())).toInt())
                dirt.add(Mark(x-r,y-r,x+r+1,y+r+1,oval))
            }
        }
        if(camera["dust"] == true) {
            if(!legacy) state=gateSeed xor 0x2D5417B3L
            marks(12,.0014f,true)
        }
        if(camera["debris"] == true) {
            if(!legacy) state=gateSeed xor 0x703E219DL
            marks(2,.0046f,false)
        }
        // Independent gate streams: toggling one obstruction cannot move another.
        if(camera["hair"] == true) {
            state=gateSeed xor 0x1753A09BL
            val cx=random()*width;val cy=random()*height
            val length=min(width,height)*(.09f+random()*.09f)
            val bend=(random()-.5f)*length*.5f
            val radius=max(1,(min(width,height)*.00055f).toInt())
            for(step in 0..60) {
                val t=step/60f-.5f
                val x=(cx+t*length).toInt();val y=(cy+bend*4*t*t).toInt()
                dirt.add(Mark(x-radius,y-radius,x+radius+1,y+radius+1,true))
            }
        }
        if(camera["insect"] == true) {
            state=gateSeed xor 0x620B71E3L
            val cx=(random()*width).toInt();val cy=(random()*height).toInt()
            val r=max(2,(min(width,height)*.0068f).toInt())
            dirt.add(Mark(cx-r/2,cy-r,cx+r/2+1,cy+r+1,true))
            dirt.add(Mark(cx-2*r,cy-r,cx,cy+r,true))
            dirt.add(Mark(cx,cy-r,cx+2*r,cy+r,true))
        }
        val text=camera["imprint"]?.toString().orEmpty()
        val font=PhoneImprintFont.forText(text)
        val unit=if(font.width==5) max(1,min(width/300,height/126)) else if(modern) max(1,min(width/220,height/90)) else max(1,min(width/160,height/70))
        // Normalized placement spans the available space for the entire string, with the
        // original margins. Missing coordinates reproduce existing receipts exactly.
        val textWidth=max(0,(text.length*(font.width+1)-1)*unit)
        val left=if(modern) 3*unit+(max(0,width-6*unit-textWidth)*printX).toInt() else 3*unit
        val y0=if(modern && camera.containsKey("imprint_y")) 4*unit+(max(0,height-(font.height+8)*unit)*printY).toInt() else if(imprintSide=="top") 4*unit else height-(font.height+4)*unit
        for((index,ch) in text.withIndex()) {
            for(y in 0 until font.height) for(x in 0 until font.width) if(font.lit(ch,x,y) && imprintStyle!="none") {
                val x0=left+index*(font.width+1)*unit+x*unit
                if(modern && imprintStyle=="optical_amber") print.add(Mark(x0-1,y0+y*unit-1,x0+unit+1,y0+(y+1)*unit+1,true,.045f))
                print.add(Mark(x0,y0+y*unit,x0+unit,y0+(y+1)*unit,modern && imprintStyle=="laser_dot"))
            }
        }
    }
    fun apply(rgb: FloatArray,start: Int,count: Int) {
        require(start >= 0 && start+count <= height && rgb.size == width*count*3)
        fun paint(mark: Mark,imprint: Boolean) {
            val left=max(0,mark.x0);val right=min(width,mark.x1)
            val top=max(start,mark.y0);val bottom=min(start+count,mark.y1)
            if(left >= right || top >= bottom) return
            val cx=(mark.x0+mark.x1-1)*.5f;val cy=(mark.y0+mark.y1-1)*.5f
            val rx=max(1f,(mark.x1-mark.x0)*.5f);val ry=max(1f,(mark.y1-mark.y0)*.5f)
            for(y in top until bottom) for(x in left until right) {
                if(mark.oval && ((x-cx)/rx)*((x-cx)/rx)+((y-cy)/ry)*((y-cy)/ry) > 1f) continue
                val i=((y-start)*width+x)*3
                if(imprint) {
                    if(!modern) { rgb[i]+=1.4f;rgb[i+1]+=.55f;rgb[i+2]+=.08f }
                    else for(c in 0..2) rgb[i+c]+=imprintColor[c]*mark.energy*printBrightness
                }
                else for(c in 0..2) rgb[i+c]*=.22f
            }
        }
        for(mark in dirt) paint(mark,false)
        if(leak > 0f && modern) {
            for(component in components) {
                val vertical=component.side in listOf("top","bottom");val reverse=component.side in listOf("right","bottom")
                val center=(((seed xor component.offset) ushr 8) and 255).toFloat()/255f
                for(y in start until start+count) for(x in 0 until width) {
                    val nx=(x+.5f)/width;val ny=(y+.5f)/height
                    val axis=if(vertical) ny else nx;val distance=if(reverse) 1-axis else axis;val across=if(vertical) nx else ny
                    var a=when(component.shape) {
                        "perimeter"->max(0f,1f-min(min(nx,1-nx),min(ny,1-ny))/.12f)
                        "cone"->max(0f,1-distance/.85f)*max(0f,1-kotlin.math.abs(across-center)/(.06f+.6f*distance))
                        else->max(0f,1f-distance/(if(component.shape=="half") .65f else .18f))
                    }
                    if(component.shape=="corner") a*=max(0f,1-kotlin.math.abs(across-center)/.4f)
                    a=a*a*leak*.9f*component.strength
                    val i=((y-start)*width+x)*3;rgb[i]+=a;rgb[i+1]+=a*.22f;rgb[i+2]+=a*.06f
                }
            }
        } else if(leak > 0f) {
            val band=if(shape == "half") .65f else .18f
            val center=((seed ushr 8) and 255).toFloat()/255f
            for(y in start until start+count) {
              val ny=(y+.5f)/height
              for(x in 0 until width) {
                val nx=(x+.5f)/width
                val axis=if(vertical) ny else nx
                val distance=if(reverse) 1f-axis else axis
                var a=max(0f,1f-distance/band)
                if(a == 0f) continue
                if(shape == "corner") a*=max(0f,1f-kotlin.math.abs((if(vertical) nx else ny)-center)/.4f)
                a=a*a*leak*.9f
                val i=((y-start)*width+x)*3
                rgb[i]+=a;rgb[i+1]+=a*.22f;rgb[i+2]+=a*.06f
              }
            }
        }
        for(mark in print) paint(mark,true)
    }
    companion object {
        const val MODEL="phone_capture_conditions_v3"
        const val PREVIOUS_MODEL="phone_capture_conditions_v2"
        const val LEGACY_MODEL="phone_capture_conditions_v1"
        val SHAPES=listOf("edge","corner","half","perimeter","cone")
        val SIDES=listOf("left","right","top","bottom")
        val PRINT_STYLES=listOf("none","laser_dot","optical_amber","lab_edge")

    }
}

/** Storage degradation belongs to the film: sensitivity loss plus one base fog per frame. */
class PhoneFilmStorageConditions(settings: Map<String,Any?>) {
    private val age=(settings["age"] as? Number)?.toFloat()?.also { require(it.isFinite() && it in 0f..1f) } ?: 0f
    fun apply(rgb: FloatArray) { if(age > 0f) for(i in rgb.indices) rgb[i]=rgb[i]*(1f-age*.55f)+age*.018f }
}
