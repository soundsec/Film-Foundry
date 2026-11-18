package com.filmfoundry.kernel.materials

import com.filmfoundry.interchange.*
import com.filmfoundry.kernel.RgbVolume
import kotlin.math.*

/** Linear optical observation, not display encoding or material processing. */
data class MaterialView(val illuminant: List<Double> = listOf(1.0,1.0,1.0),val exposureEv: Double=0.0,
    val lightDirection: List<Double> = listOf(0.0,0.0,1.0),val viewDirection: List<Double> = listOf(.42,0.0,.91),
    val background: List<Double> = listOf(.02,.02,.02),val borderStyle: String="none")

object PhoneMaterialObserve {
    /** Explicit compatibility list; never interpret unknown desktop fields as a generic RGB filter. */
    fun supported(body: MaterialBody): Boolean=when(body.family) {
        MaterialFamily.PEEL.id -> body.schemaVersion==2 && body.observationModel in setOf("diffuse_paper_v2","diffuse_residue_v2","donor_transmission_v2")
        MaterialFamily.INSTANT.id -> body.schemaVersion==2 && body.observationModel=="integral_reflection_v2"
        MaterialFamily.SILVER.id -> body.schemaVersion==2 && body.observationModel=="metal_particle_reflection_v2"
        MaterialFamily.PAPER.id -> body.schemaVersion==1 && body.observationModel=="paper_reflection_v1" || body.schemaVersion==2 && body.observationModel=="paper_reflection_v2"
        MaterialFamily.WET.id -> body.schemaVersion==1 && body.observationModel=="wet_plate_optics_v1" || body.schemaVersion==2 && body.observationModel=="wet_plate_optics_v2"
        MaterialFamily.CYANOTYPE.id -> body.schemaVersion==1 && body.observationModel=="cyanotype_reflection_v1" || body.schemaVersion==3 && body.observationModel=="cyanotype_reflection_v3"
        MaterialFamily.PLATINUM.id -> body.schemaVersion==1 && body.observationModel=="noble_reflection_v1" || body.schemaVersion==3 && body.observationModel=="noble_reflection_v3"
        else -> false
    }
    fun observe(body: MaterialBody,view: MaterialView=MaterialView(),checkCancel: ()->Unit={}): RgbVolume {
        require(supported(body)) { "Material observation version is not implemented on mobile" }
        fun vector(v: List<Double>,unit: Boolean=false): DoubleArray {
            require(v.size==3 && v.all {it.isFinite()});val a=v.toDoubleArray()
            if(unit) {val length=sqrt(a.sumOf {it*it});require(length.isFinite() && length>0);for(c in 0..2)a[c]/=length}
            else require(a.all {it>=0});return a
        }
        val light=vector(view.illuminant);val background=vector(view.background)
        val direction=vector(view.lightDirection,true);val eye=vector(view.viewDirection,true)
        require(eye[2]>0 && view.exposureEv.isFinite() && view.exposureEv in -20.0..20.0)
        require(view.borderStyle in setOf("none","material","equal","pod_short"))
        val presentation=RecipeJson.objectMap(body.presentationJson)
        val m=(presentation["material"] as? Map<*,*>)?.entries?.associate {it.key.toString() to it.value}.orEmpty()
        fun n(key: String,default: Double,low: Double=0.0,high: Double=100.0)=PhoneMaterialKernel.n(m,key,default,low,high)
        val gain=2.0.pow(view.exposureEv);val w=body.width;val h=body.height
        val style=if(view.borderStyle=="material")presentation["frame"] ?: "none" else view.borderStyle
        val edge=if(style=="none" || body.family==MaterialFamily.SILVER.id)0 else max(1,(min(w,h)*.055).roundToInt())
        val bottom=if(style=="pod" || style=="pod_short" && h>=w)max(edge,(min(w,h)*.20).roundToInt()) else edge
        val right=if(style=="pod_short" && w>h)max(edge,(h*.20).roundToInt()) else edge
        val outW=w+edge+right;val outH=h+edge+bottom
        val out=FloatArray(Math.multiplyExact(Math.multiplyExact(outW,outH),3)) { (light[it%3]*.94*gain).toFloat() }
        val blockRows=max(1,16384/w)
        val maxDensity=n("maximum_density",when(body.family) {MaterialFamily.PAPER.id->2.2;MaterialFamily.WET.id->2.2;else->2.1},.001,20.0)
        val warmth=n("warmth",.05,0.0,1.0)
        for(start in 0 until h step blockRows) {
            checkCancel();val count=min(blockRows,h-start);val fields=body.rows(start,count)
            fun f(key: String,i: Int,c: Int=0): Double {
                val a=fields[key] ?: error("Missing $key in material")
                val channels=if(a.shape.size==3)a.shape[2] else 1
                require(channels==1 || channels==3)
                return a.values[i*channels+if(channels==1)0 else c].toDouble()
            }
            fun mean(key: String,i: Int)=(f(key,i,0)+f(key,i,1)+f(key,i,2))/3
            for(i in 0 until count*w) {
                if(i and 4095==0)checkCancel()
                val target=((start+i/w+edge)*outW+i%w+edge)*3
                if(body.family==MaterialFamily.SILVER.id) {
                    var nx=(f("polish_x",i)-.5)*.10;var ny=(f("polish_y",i)-.5)*.10
                    val len=sqrt(nx*nx+ny*ny+1);nx/=len;ny/=len;val nz=1/len
                    val nl=max(0.0,nx*direction[0]+ny*direction[1]+nz*direction[2])
                    val alignment=((2*nl*nx-direction[0])*eye[0]+(2*nl*ny-direction[1])*eye[1]+(2*nl*nz-direction[2])*eye[2]).coerceIn(0.0,1.0)
                    val damage=f("damage",i);val pit=f("pit_depth",i);val rough=(f("roughness",i)+damage*.12+pit*.16).coerceIn(.02,1.0)
                    val specular=alignment.pow(min(2/(rough*rough)-2,5000.0))*nl
                    val gold=f("gold",i);val coverage=f("particles",i)*(1-damage);val thickness=f("tarnish",i)
                    for(c in 0..2) {
                        val metal=(background[c]+light[c]*specular)*f("metal_reflectance",i)*(1-gold*doubleArrayOf(.02,.08,.22)[c])
                        val damaged=metal*(1-.65*damage)+light[c]*(.018*damage+.012*pit)
                        val particles=light[c]*nl*f("particle_reflectance",i)*(1-gold*.08)*(.96+.08*f("microtexture",i))
                        val phase=4*PI*1.8*thickness*320*max(eye[2],.1)/doubleArrayOf(650.0,535.0,460.0)[c]
                        val interference=1-.62*(-expm1(-thickness*7))*(.5+.5*cos(phase))
                        out[target+c]=((damaged*(1-coverage)+particles*coverage)*interference*exp(-thickness*doubleArrayOf(1.1,1.35,1.7)[c])*gain).toFloat()
                    }
                    continue
                }
                for(c in 0..2) {
                    val radiance=when(body.family) {
                        MaterialFamily.PEEL.id,MaterialFamily.INSTANT.id -> {
                            if(body.role=="silver_negative") f("base_transmission",i)*10.0.pow(-f("density",i,c))
                            else {
                                val amount=when {body.family==MaterialFamily.INSTANT.id->f("received",i,c);body.role=="receiver"->f("transferred",i,c);else->f("immobile",i,c)+f("remaining",i,c)}
                                val density=amount*f("density_per_unit",i)+f("stain_density",i,c)
                                var value=f("paper",i)*10.0.pow(-density)
                                if(body.family==MaterialFamily.INSTANT.id) {
                                    val uncoated=(1-f("coverage",i)/.12).coerceIn(0.0,1.0)
                                    val backing=doubleArrayOf(.14,.085,.045)[c]*(.55+.9*f("backing_texture",i))
                                    value=value*(1-uncoated)+backing*uncoated
                                    val opacity=f("opacifier",i);value=value*(1-opacity)+.12*opacity
                                };value
                            }
                        }
                        MaterialFamily.PAPER.id -> {
                            var density=mean("silver",i)*maxDensity*doubleArrayOf(1.0,1+.12*warmth,1+.35*warmth)[c]+mean("halide",i)*.08+f("residue",i,c)
                            if(m["kind"]=="color") {
                                val matrix=arrayOf(doubleArrayOf(1.0,.06,.03),doubleArrayOf(.04,1.0,.04),doubleArrayOf(.02,.08,1.0))
                                for(k in 0..2)density+=f("dye",i,k)*matrix[k][c]*maxDensity
                            }
                            val diffuse=f("paper",i)*10.0.pow(-density)
                            if(body.schemaVersion<2) diffuse else PhoneMaterialBath.dried(diffuse,f("paper",i),f("paper_moisture",i),
                                n("surface_gloss",PhoneMaterialBath.glossDefault(MaterialFamily.PAPER),0.0,1.0),n("drydown",.06,0.0,.4),
                                n("texture",PhoneMaterialBath.textureDefault(MaterialFamily.PAPER),0.0,1.0),m["paper_base"]!="fiber",direction,eye)
                        }
                        MaterialFamily.WET.id -> {
                            val varnish=f("varnish",i)*doubleArrayOf(.002,.006,.018)[c]
                            val density=f("silver",i)*maxDensity+f("halide",i)*.12+f("residue",i,c)+varnish
                            if(body.usableNegative)n("base_transmission",.94,0.0,1.0)*10.0.pow(-density)
                            else if(body.schemaVersion<2) .012*10.0.pow(-2*density)+(1-exp(-f("silver",i)*2))*n("particle_reflectance",.65,0.0,1.0)*f("surface",i)*doubleArrayOf(1.0,.94,.83)[c]*10.0.pow(-(f("residue",i,c)+varnish))
                            else {
                                val silver=f("silver",i);val glass=m["support"]=="ambrotype"
                                val scatter=(1-exp(-silver*2))*n("particle_reflectance",.65,0.0,1.0)*f("surface",i)
                                val tint=if(glass) doubleArrayOf(1.0,.96,.9) else doubleArrayOf(.95,.98,1.0)
                                val reflection=(if(glass).035 else .012)+.025*f("varnish",i)
                                val glare=reflection*PhoneMaterialBath.viewGloss((f("roughness",i)+.08).coerceIn(0.0,1.0),direction,eye)
                                (if(glass).006 else .022)*10.0.pow(-2*density)+scatter*tint[c]*10.0.pow(-(f("residue",i,c)+varnish))+glare
                            }
                        }
                        MaterialFamily.CYANOTYPE.id,MaterialFamily.PLATINUM.id -> {
                            val ironFamily=if(body.family==MaterialFamily.CYANOTYPE.id) MaterialFamily.CYANOTYPE else MaterialFamily.PLATINUM
                            if(body.schemaVersion>=3) {
                                val scatter=1+n("texture",PhoneMaterialBath.textureDefault(ironFamily),0.0,1.0)*.35*f("fibers",i)
                                var iron=f("ferric",i)+f("ferrous",i)
                                val absorption=if(body.family==MaterialFamily.CYANOTYPE.id)
                                    .5*expm1(f("blue_pigment",i)*maxDensity*ln(10.0))*doubleArrayOf(1.35,.85,.12)[c]+f("reduced_pigment",i)*doubleArrayOf(.15,.1,.03)[c]
                                else {iron+=f("spent_ferric",i);.5*expm1((f("platinum",i)+f("palladium",i)*doubleArrayOf(.98,1.0,1.045)[c])*maxDensity*ln(10.0))}
                                val optical=absorption+iron*doubleArrayOf(.04,.16,.65)[c]+f("residue",i,c)
                                val remitted=PhoneMaterialBath.remit(f("paper",i),optical,scatter)
                                PhoneMaterialBath.dried(remitted,f("paper",i),f("paper_moisture",i),n("surface_gloss",PhoneMaterialBath.glossDefault(ironFamily),0.0,1.0),
                                    n("drydown",.06,0.0,.4),n("texture",PhoneMaterialBath.textureDefault(ironFamily),0.0,1.0),m["paper_base"]=="rc",direction,eye)
                            } else {
                                var iron=f("ferric",i)+f("ferrous",i)
                                val density=if(body.family==MaterialFamily.CYANOTYPE.id)
                                    (f("blue_pigment",i)*doubleArrayOf(1.1,.65,.12)[c]+f("reduced_pigment",i)*.04)*maxDensity
                                else {iron+=f("spent_ferric",i);(f("platinum",i)+f("palladium",i)*doubleArrayOf(.88,1.0,1.14)[c])*maxDensity}
                                f("paper",i)*10.0.pow(-density-iron*doubleArrayOf(.03,.1,.32)[c]-f("residue",i,c))
                            }
                        }
                        else -> error("Unknown material family")
                    }
                    out[target+c]=(radiance*light[c]*gain).toFloat()
                }
            }
        }
        require(out.all {it.isFinite() && it>=0}) { "Invalid observed radiance" }
        return RgbVolume(outW,outH,linearRgb=out)
    }
}
