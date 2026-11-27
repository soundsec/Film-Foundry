package com.filmfoundry.kernel.materials

import com.filmfoundry.interchange.*
import com.filmfoundry.kernel.RgbVolume
import kotlin.math.*

enum class MaterialFamily(val id: String) {
    PEEL("peel_apart_transfer_v1"), INSTANT("integral_instant_v1"), SILVER("daguerreotype_surface_v1"),
    PAPER("photographic_paper_v1"), WET("wet_collodion_v1"), CYANOTYPE("cyanotype_v1"), PLATINUM("platinum_palladium_v1")
}

/** Task-owned immutable state. Stages are explicit; observing never advances time. */
class PhoneMaterialState internal constructor(val family: MaterialFamily,val groupId: String,val seed: Long,
    val width: Int,val height: Int,val stage: String,val elapsedSeconds: Double,
    parameters: Map<String,Any?>,fields: Map<String,FloatNd>,history: List<Map<String,Any?>>,val arithmetic: String) {
    val parameters: Map<String,Any?> = MaterialSnapshot.film(MaterialSnapshot.capture(parameters))
    internal val data=fields.mapValues { it.value.copy() }
    internal val history=history.map { MovieMasterCodec.snapshot(it) }
    fun fields()=data.mapValues { it.value.copy() }
}

/** Independent mobile reduction, not the desktop chemistry executor.
 * v3 keeps each family's recognizable formation and drops generic defects:
 * edge loss, pinholes, scratches, light leaks, contamination, alkali stripping
 * and local wash spots. Stored state may be binary16; the FP32 path remains
 * for comparison. No UI or platform/GPU dependency.
 */
object PhoneMaterialKernel {
    const val MODEL="phone_material_reduced_v4"
    const val ARITHMETIC_FULL="float32"
    const val ARITHMETIC_HALF="float16"
    /** Imported desktop definitions retain unimplemented parameters, explicitly reported below. */
    fun supportedMaterialFields(family: MaterialFamily): Set<String> {
        val sheets=setOf("half_exposure","response_contrast","maximum_density","texture")
        val paperSupport=setOf("paper_base","water_uptake_seconds","drying_seconds","surface_gloss","drydown")
        return when(family) {
            MaterialFamily.PEEL,MaterialFamily.INSTANT -> setOf("transfer","half_exposure","reaction_seconds","maximum_density","paper_reflectance",
                "batch_variation","batch_seed","border_style","light_sensitivity","developer_capacity","texture",
                "temperature_sensitivity")+
                if(family==MaterialFamily.PEEL)setOf("donor_mode","peel_travel_fraction","material_long_edge_mm") else setOf("stabilization_seconds")
            MaterialFamily.SILVER -> setOf("half_exposure","response_contrast","reaction_seconds","roughness","texture","metal_reflectance","particle_reflectance",
                "fixing_seconds","gilding_seconds","gold_capacity","iodized_response")
            MaterialFamily.PAPER -> sheets+paperSupport+setOf("kind","paper_reflectance","warmth","sensitivity_remaining","wash_seconds","preflash_dose")
            MaterialFamily.WET -> sheets+setOf("support","exposure_delay_s","drying_seconds","particle_reflectance","base_transmission","wash_seconds",
                "humidity","pour_direction","flow_ridges","edge_pooling")
            MaterialFamily.CYANOTYPE,MaterialFamily.PLATINUM -> sheets+paperSupport+setOf("paper_reflectance","green_sensitivity","actinic_gain","humidity","sensitizer_remaining")+
                if(family==MaterialFamily.CYANOTYPE) setOf("oxidation_seconds","brush_direction","brush_overlap","pigment_binding")
                else setOf("platinum_fraction","clearing_seconds","coating_method","paper_sizing","printout_strength")
        }
    }
    /** Addressable pixel count. This is an array bound, not a quality or megapixel cap. */
    fun addressablePixels(width: Int,height: Int): Long {
        require(width>0 && height>0) { "Working image dimensions must be positive" }
        val pixels=width.toLong()*height.toLong()
        require(pixels<=Int.MAX_VALUE.toLong()/3) { "Working image exceeds an addressable array" }
        return pixels
    }
    fun expose(family: MaterialFamily,image: RgbVolume,parameters: Map<String,Any?> = emptyMap(),
        groupId: String,seed: Long=0,checkCancel: ()->Unit={},arithmetic: String=ARITHMETIC_HALF): PhoneMaterialState {
        require(arithmetic==ARITHMETIC_FULL || arithmetic==ARITHMETIC_HALF) { "Unsupported material arithmetic" }
        require(groupId.isNotBlank() && seed>=0)
        val defaults=when(family) {
            MaterialFamily.WET -> mapOf("support" to "glass_negative","half_exposure" to .15,"response_contrast" to 1.5,"maximum_density" to 2.5,"drying_seconds" to 300.0,"base_transmission" to .94,"particle_reflectance" to .65)
            MaterialFamily.PAPER -> mapOf("kind" to "variable_contrast","half_exposure" to .18,"response_contrast" to 2.2,"maximum_density" to 2.2)
            else -> emptyMap()
        }
        val m=MovieMasterCodec.snapshot(defaults+parameters);validateKinds(family,m)
        val w=image.width;val h=image.height;val size=addressablePixels(w,h).toInt()
        require(image.linearRgb.all { it.isFinite() && it>=0 }) { "Material input must be finite nonnegative linear RGB" }
        val f=linkedMapOf<String,FloatNd>()
        fun field(key: String,channels: Int=0,value: Float=0f): FloatArray {
            val a=FloatArray(size*max(1,channels)) { value }
            f[key]=FloatNd(if(channels==0) intArrayOf(h,w) else intArrayOf(h,w,channels),a);return a
        }
        val half=n(m,"half_exposure",if(family in listOf(MaterialFamily.CYANOTYPE,MaterialFamily.PLATINUM)) .3 else .18,.001,20.0)
        val contrast=n(m,"response_contrast",when(family) { MaterialFamily.CYANOTYPE->2.0;MaterialFamily.PLATINUM->.95;MaterialFamily.PAPER->2.2;MaterialFamily.SILVER->1.65;else->1.0 },.2,8.0)
        val texture=n(m,"texture",.2,0.0,1.0)
        fun reflectance(i: Int)=n(m,"paper_reflectance",.93,.01,1.0)*(1-texture*.04*(.5+.5*noise((i%w)*.15,(i/w)*.15,seed+19)))
        fun response(d: Double)=(d/half).pow(contrast).let { if(it.isInfinite())1.0 else it/(1+it) }
        fun sample(i: Int,c: Int)=image.linearRgb[i*3+c].toDouble()
        if(family in listOf(MaterialFamily.PEEL,MaterialFamily.INSTANT)) {
            val channels=if(m["transfer"]=="silver_compound")1 else 3
            val a=field("exposure_response",channels);val immobile=field("immobile",channels);val remaining=field("remaining",channels)
            field("received",channels);field("developed_donor",channels)
            for(key in listOf("coverage","contact","arrival_seconds","reagent","escaped_reagent"))field(key)
            field("stain_density",3);field("channel_activity",channels,1f);field("transfer_yield",channels,1f)
            val paper=field("paper_texture");val backing=field("backing_texture",value=.5f)
            field("phone_developer_available_mean");field("phone_developer_used_mean")
            if(family==MaterialFamily.INSTANT) { field("opacifier",value=1f);field("stabilization") }
            for(i in 0 until size) {
                if(i and 4095==0)checkCancel();paper[i]=reflectance(i).toFloat()
                backing[i]=(.5+.1*noise(i%w*.03,i/w*.03,seed+7)).toFloat()
                for(c in 0 until channels) {
                    val dose=if(channels==1)(sample(i,0)+sample(i,1)+sample(i,2))/3 else sample(i,c)
                    val v=(dose/(dose+half)).toFloat();val k=i*channels+c
                    a[k]=v;immobile[k]=v;remaining[k]=1-v
                }
            }
        } else if(family==MaterialFamily.SILVER) {
            field("sensitivity",value=1f);val latent=field("latent");field("particles")
            val rough=field("roughness",value=n(m,"roughness",.12,.02,1.0).toFloat())
            val px=field("polish_x");val py=field("polish_y");val grain=field("microtexture")
            field("damage");field("pit_depth");field("tarnish");field("gold")
            val iodized = n(m,"iodized_response",1.0,0.0,1.0)
            for(i in 0 until size) {
                if(i and 4095==0)checkCancel()
                val blue = sample(i,2) + .06*sample(i,1)
                val dose = if(iodized == 1.0) blue else iodized*blue + (1-iodized)*(.2126*sample(i,0)+.7152*sample(i,1)+.0722*sample(i,2))
                latent[i]=response(dose).toFloat()
                px[i]=(.5+.25*noise(i%w*.01,i/w*.7,seed)).toFloat();py[i]=(.5+.05*noise(i%w*.2,i/w*.03,seed+1)).toFloat()
                grain[i]=(.5+.5*noise(i%w*.8,i/w*.8,seed+2)).toFloat();rough[i]=(rough[i]+texture*.025*grain[i]).coerceIn(.02,1.0).toFloat()
            }
        } else if(family==MaterialFamily.PAPER) {
            val dose=field("dose",3);image.linearRgb.copyInto(dose)
            val flash = n(m,"preflash_dose",0.0,0.0,2.0).toFloat()
            if(flash > 0) for(k in dose.indices) { if(k and 8191==0)checkCancel(); dose[k] += flash }
            val halide=field("halide",3);field("silver",3);field("dye",3);field("removed_silver",3)
            val coating=field("coating");val paper=field("paper");field("residue",3)
            field("paper_moisture",value=PhoneMaterialBath.initialMoisture(family,m).toFloat())
            for(i in 0 until size) { if(i and 4095==0)checkCancel();coating[i]=1f;paper[i]=reflectance(i).toFloat();for(c in 0..2)halide[i*3+c]=coating[i] }
        } else if(family==MaterialFamily.WET) {
            val latent=field("latent");val halide=field("halide");field("silver");field("removed_silver")
            val coat=field("coating");val surface=field("surface");field("varnish");field("residue",3)
            val flow=field("flow");val rough=field("roughness");val dose=field("dose");val moisture=field("moisture",value=1f)
            val paint=PhoneMaterialBath.wetPainter(w,h,seed,m)
            for(i in 0 until size) {
                if(i and 4095==0)checkCancel();val sampleCoat=paint(i)
                coat[i]=sampleCoat.coating;halide[i]=sampleCoat.coating;flow[i]=sampleCoat.flow;rough[i]=sampleCoat.roughness;surface[i]=sampleCoat.surface
                dose[i]=(sample(i,2)+sample(i,1)*.06).toFloat()
            }
            val delay=n(m,"exposure_delay_s",0.0,0.0,86400.0)
            if(delay>0) PhoneMaterialBath.dryWet(moisture,coat,delay,20.0,n(m,"drying_seconds",300.0,1.0,86400.0),n(m,"humidity",.6,0.0,1.0),checkCancel)
            for(i in 0 until size) latent[i]=response(dose[i].toDouble()*moisture[i]).toFloat()
        } else {
            val cyan=family==MaterialFamily.CYANOTYPE
            val ferric=field("ferric");val ferrous=field("ferrous");field("iron_removed")
            val coating=field("coating");val paper=field("paper");field("residue",3)
            val fibers=field("fibers");field("paper_moisture",value=PhoneMaterialBath.initialMoisture(family,m).toFloat())
            val binding=if(cyan) field("binding") else null
            val absorption=if(cyan) null else field("absorption")
            val access=if(cyan) null else field("clear_access")
            if(cyan) {field("reduced_pigment");field("blue_pigment")}
            else {field("spent_ferric");field("platinum_salt");field("palladium_salt");field("platinum");field("palladium");field("metal_removed")}
            val humidity=exp(-3*(n(m,"humidity",.6,0.0,1.0)-.6).pow(2))
            val cyanPaint=if(cyan) PhoneMaterialBath.cyanPainter(w,h,seed,m) else null
            val noblePaint=if(cyan) null else PhoneMaterialBath.noblePainter(w,h,seed,m)
            val platinum=if(cyan) 0.0 else n(m,"platinum_fraction",.4,0.0,1.0)
            val printout = if(cyan) 0.0 else n(m,"printout_strength",0.0,0.0,1.0)*n(m,"humidity",.6,0.0,1.0)
            for(i in 0 until size) {
                if(i and 4095==0)checkCancel()
                if(cyan) {val coat=cyanPaint!!(i);coating[i]=coat.coating;paper[i]=coat.paper;binding!![i]=coat.binding;fibers[i]=coat.fibers}
                else {val coat=noblePaint!!(i);coating[i]=coat.coating;paper[i]=coat.paper;absorption!![i]=coat.absorption;access!![i]=coat.clearAccess;fibers[i]=coat.fibers
                    f.getValue("platinum_salt").values[i]=(coating[i]*platinum).toFloat();f.getValue("palladium_salt").values[i]=(coating[i]*(1-platinum)).toFloat()}
                val capacity=coating[i]*(if(cyan)1 else 2)
                val active=response((sample(i,2)+sample(i,1)*n(m,"green_sensitivity",.05,0.0,1.0))*n(m,"actinic_gain",1.0,.001,20.0)*humidity)*n(m,"sensitizer_remaining",1.0,0.0,1.0)
                ferric[i]=(capacity*(1-active)).toFloat();ferrous[i]=(capacity*active).toFloat()
                if(printout > 0) {
                    val pt = (f.getValue("platinum_salt").values[i]*active*printout).toFloat()
                    val pd = (f.getValue("palladium_salt").values[i]*active*printout).toFloat()
                    f.getValue("platinum").values[i]=pt; f.getValue("platinum_salt").values[i]-=pt
                    f.getValue("palladium").values[i]=pd; f.getValue("palladium_salt").values[i]-=pd
                    val spent=2*(pt+pd)
                    f.getValue("spent_ferric").values[i]=spent; ferrous[i]=(ferrous[i]-spent).coerceAtLeast(0f)
                }
            }
        }
        quantize(f,arithmetic)
        return PhoneMaterialState(family,groupId,seed,w,h,"exposed",0.0,m,f,listOf(mapOf("operation" to "expose","model" to MODEL)),arithmetic)
    }

    fun process(state: PhoneMaterialState,operation: String,parameters: Map<String,Any?> = emptyMap(),checkCancel: ()->Unit={}): PhoneMaterialState {
        val f=state.fields();val m=state.parameters;val p=parameters;val family=state.family
        fun a(key: String)=f.getValue(key).values
        fun requireStage(vararg stages: String)=require(state.stage in stages) { "$operation is unavailable at ${state.stage}" }
        var seconds=if(operation=="stop" && family==MaterialFamily.PAPER) 0.0 else n(p,"seconds",when(operation) { "spread","eject","separate","finish","clear_donor"->0.0;"display_fade"->3600.0;else->60.0 },0.0,86400.0)
        val temperature=n(p,"temperature_c",20.0,-10.0,50.0)
        val thermal=2.0.pow((temperature-20)/10)
        fun rate(tau: Double)=(-expm1(-seconds/tau*thermal*n(p,"activity",1.0,0.0,100.0))).toFloat()
        var stage=state.stage
        val size=state.width*state.height
        fun each(block: (Int)->Unit) { for(i in 0 until size) { if(i and 4095==0)checkCancel();block(i) } }
        rejectReducedDefect(p)
        if(family in listOf(MaterialFamily.PEEL,MaterialFamily.INSTANT)) {
            val instant=family==MaterialFamily.INSTANT;val channels=f.getValue("received").shape[2]
            val progressive = !instant && operation=="separate" && n(m,"peel_travel_fraction",0.0,0.0,1.0)>0
            when(if(progressive) "peel_front" else operation) {
                "spread","eject" -> {
                    requireStage("exposed");require(operation==if(instant)"eject" else "spread")
                    val coverage=n(p,"coverage",1.0,0.0,1.0);val contact=n(p,"contact",1.0,0.0,1.0)
                    val capacity=n(m,"developer_capacity",1.25,0.0,10.0)
                    each { i ->
                        a("coverage")[i]=coverage.toFloat();a("contact")[i]=contact.toFloat();a("reagent")[i]=coverage.toFloat()
                        a("phone_developer_available_mean")[i]=(coverage*capacity).toFloat()
                    };stage="contact"
                }
                "advance","peel_front" -> {
                    requireStage("contact")
                    val peeling = operation=="separate"
                    val direction = if(peeling) state.history.lastOrNull { it["operation"]=="spread" }?.get("direction") ?: "down" else "down"
                    val vertical = direction=="down" || direction=="up"
                    val length = if(vertical) state.height else state.width
                    if(peeling) {
                        require(direction in listOf("up","down","left","right"))
                        val fraction=n(m,"peel_travel_fraction",0.0,0.0,1.0)
                        val travel=n(m,"material_long_edge_mm",100.0,10.0,1000.0)*length/max(state.width,state.height)
                        seconds=fraction*travel/n(p,"speed_mm_s",25.0,.1,1000.0)
                    }
                    val tau=n(m,"reaction_seconds",60.0,.1,86400.0)
                    val stable=n(m,"stabilization_seconds",300.0,.1,86400.0)
                    val activeSeconds=if(instant)min(seconds,max(0.0,stable-state.elapsedSeconds)) else seconds
                    val daylight=n(p,"light_exposure",0.0,0.0,20.0)*n(m,"light_sensitivity",1.0,0.0,2.0)
                    val batch=n(m,"batch_variation",0.0,0.0,1.0)
                    val thermalTransfer=2.0.pow((temperature-20)/10*n(m,"temperature_sensitivity",1.0,0.0,2.0))
                    val colorRates=DoubleArray(channels) { c ->
                        val seed=(m["batch_seed"] as? Number)?.toLong() ?: state.seed
                        (1+batch*.35*noise(c.toDouble(),0.0,seed)).coerceAtLeast(.1)
                    }
                    val remaining=a("remaining");val immobile=a("immobile");val donor=a("developed_donor")
                    val response=a("exposure_response");val received=a("received");val supply=a("phone_developer_available_mean");val used=a("phone_developer_used_mean")
                    val demand=DoubleArray(channels);val transfer=DoubleArray(channels);val reacted=DoubleArray(channels)
                    // On integral film the opaque cover clears as (1-t/stable)^2.
                    // Integrate its transmission over this contact interval.
                    val startOpacity=(1-state.elapsedSeconds/stable).coerceIn(0.0,1.0)
                    val endOpacity=(1-(state.elapsedSeconds+activeSeconds)/stable).coerceIn(0.0,1.0)
                    val transmission=if(instant) 1-(startOpacity*startOpacity+startOpacity*endOpacity+endOpacity*endOpacity)/3 else 1.0
                    val illuminatedSeconds=activeSeconds*transmission
                    val lightDose=daylight*.025*illuminatedSeconds
                    val darkSurvival=exp(-lightDose)
                    val stainTint=doubleArrayOf(.025,.09,.24)
                    each { i ->
                        val local=a("coverage")[i]*a("contact")[i]*thermalTransfer.toFloat()
                        val contactTime=if(peeling) {
                            val coord=if(vertical) i/state.width else i%state.width
                            val along=if(direction=="up" || direction=="left") length-1-coord else coord
                            seconds*(along+.5)/length
                        } else activeSeconds
                        val localLight=if(peeling) daylight*.025*contactTime else lightDose
                        val survival=if(peeling && localLight>0) exp(-localLight) else darkSurvival
                        var total=0.0
                        for(c in 0 until channels) {
                            val k=i*channels+c
                            val dose=contactTime/tau*local*colorRates[c]
                            val mobile=remaining[k].toDouble()
                            reacted[c]=-expm1(-dose)
                            demand[c]=(mobile+max(0f,immobile[k]-donor[k]))*reacted[c]
                            // Development and incident light compete for the same mobile material.
                            transfer[c]=if(localLight==0.0) mobile*reacted[c] else
                                mobile*(dose/(dose+localLight))*(-expm1(-(dose+localLight)))
                            total+=demand[c]
                        }
                        // A single mean reagent field serves all layers. Allocate it in proportion
                        // to demand, so channel order cannot turn a neutral exposure into a colour.
                        val budget=supply[i].toDouble()*channels
                        val share=if(total>budget) budget/total else 1.0
                        for(c in 0 until channels) {
                            val k=i*channels+c
                            val moved=min(remaining[k].toDouble(),transfer[c]*share)
                            val left=remaining[k]-moved
                            val fogged=if(localLight==0.0) 0.0 else
                                (left-remaining[k]*(1-reacted[c]*share)*survival).coerceIn(0.0,left)
                            remaining[k]=(left-fogged).toFloat();immobile[k]+=fogged.toFloat()
                            response[k]=immobile[k];received[k]+=moved.toFloat()
                            donor[k]+=max(0.0,demand[c]*share-moved).toFloat()
                        }
                        val consumed=(min(budget,total)/channels).toFloat()
                        supply[i]=(supply[i]-consumed).coerceAtLeast(0f);used[i]+=consumed
                        val litTime=if(peeling) contactTime else illuminatedSeconds
                        for(c in 0..2) a("stain_density")[i*3+c]+=(daylight*litTime/300*local*stainTint[c]).toFloat()
                        if(instant) {
                            val neutral=((state.elapsedSeconds+seconds)/stable).coerceIn(0.0,1.0).toFloat()
                            a("stabilization")[i]=neutral;a("opacifier")[i]=(1-neutral).pow(2)
                        }
                    }
                    if(instant && state.elapsedSeconds+seconds>=stable)stage="stable"
                    if(peeling)stage="separated"
                }
                "separate" -> {require(!instant);requireStage("contact");stage="separated"}
                "clear_donor" -> {require(!instant && m["transfer"]=="silver_compound" && m["donor_mode"]=="silver_negative");requireStage("separated");stage="cleared"}
                "finish" -> {require(instant);requireStage("contact","stable");stage="finished"}
                else -> error("Unsupported reduced transfer operation: $operation")
            }
        } else if(family==MaterialFamily.SILVER) {
            when(operation) {
                "develop" -> {requireStage("exposed","developed");val r=rate(n(m,"reaction_seconds",40.0,.1,86400.0));each { i->a("particles")[i]+=(a("latent")[i]-a("particles")[i])*r };stage="developed"}
                "fix" -> {requireStage("developed","fixed");val r=rate(n(m,"fixing_seconds",12.0,.1,86400.0));each { i->a("sensitivity")[i]*=1-r };stage="fixed"}
                "gild" -> {
                    requireStage("fixed","gilded")
                    val ceiling=(n(p,"amount",1.0,0.0,1.0)*min(1.0,n(m,"gold_capacity",1.0,0.0,10.0))).toFloat()
                    val r=rate(n(m,"gilding_seconds",20.0,.1,86400.0))
                    each { i->a("gold")[i]+=max(0f,ceiling-a("gold")[i])*r };stage="gilded"
                }
                "tarnish" -> {requireStage("fixed","gilded");val amount=n(p,"amount",.2,0.0,1.0);each { i->a("tarnish")[i]=(a("tarnish")[i]+amount*(.6+.4*noise(i%state.width*.02,i/state.width*.02,state.seed+45))).coerceIn(0.0,1.0).toFloat() }}
                else -> error("Unsupported reduced plate operation: $operation")
            }
        } else if(family==MaterialFamily.PAPER) {
            val rc=PhoneMaterialBath.paperBase(family,m)=="rc"
            val bath=PhoneMaterialBath.contact(p)
            val uptake=n(m,"water_uptake_seconds",25.0,.1,7200.0)
            fun wetAccess()=PhoneMaterialBath.hydrate(a("paper_moisture"),state.width,state.height,seconds,temperature,uptake,rc,bath,checkCancel)
            when(operation) {
                "develop" -> {
                    requireStage("exposed","developed")
                    val developer=p["developer"] ?: "material"
                    require(developer in listOf("material","bw","color")) { "Unknown paper developer capability: $developer" }
                    val formDye=m["kind"]=="color" && developer!="bw"
                    wetAccess();val r=rate(20.0)
                    each { i->
                        var developed=0.0
                        for(c in 0..2) {
                            val k=i*3+c
                            val target=a("coating")[i]*paperResponse(a("dose"),i,c,m)
                            val amount=min(a("halide")[k],max(0f,target-a("silver")[k]))*r
                            a("halide")[k]-=amount;a("silver")[k]+=amount;developed+=amount.toDouble()
                            if(formDye)a("dye")[k]=(a("dye")[k]+amount).coerceAtMost(a("coating")[i])
                        }
                        val mean=(developed/3).toFloat()
                        for(c in 0..2)a("residue")[i*3+c]+=mean*floatArrayOf(.002f,.004f,.008f)[c]
                    };stage="developed"
                }
                "stop" -> {requireStage("developed");stage="stopped"}
                "fix" -> {
                    requireStage("exposed","developed","stopped","fixed","washed");wetAccess();val r=rate(20.0)
                    each { i->for(c in 0..2){val k=i*3+c;val h=a("halide")[k]*r;a("halide")[k]-=h;a("removed_silver")[k]+=h} };stage="fixed"
                }
                "bleach_fix" -> {
                    requireStage("developed","stopped","fixed","washed");wetAccess()
                    val bleach=(-expm1(-(seconds/8)/15*thermal*n(p,"bleach_activity",1.0,0.0,100.0))).toFloat()
                    val fix=(-expm1(-(seconds/8)/15*thermal*n(p,"fix_activity",1.0,0.0,100.0))).toFloat()
                    repeat(8) { each { i->for(c in 0..2) {
                        val k=i*3+c;val silver=a("silver")[k]*bleach;a("silver")[k]-=silver;a("halide")[k]+=silver
                        val cleared=a("halide")[k]*fix;a("halide")[k]-=cleared;a("removed_silver")[k]+=cleared
                    } } };stage="fixed"
                }
                "wash" -> {
                    requireStage("fixed","washed")
                    val changes=n(p,"water_changes",3.0,0.0,100.0);val dose=wetAccess()
                    val tau=n(m,"wash_seconds",60.0,1.0,7200.0)*(if(rc)1.0 else 3.0)
                    each { i->val keep=exp(-dose[i].toDouble()*changes/(3*tau)).toFloat();for(c in 0..2)a("residue")[i*3+c]*=keep };stage="washed"
                }
                "dry" -> {
                    requireStage("washed")
                    PhoneMaterialBath.dryPaper(a("paper_moisture"),seconds,temperature,n(m,"drying_seconds",600.0,1.0,86400.0),
                        n(p,"airflow",1.0,0.0,100.0),n(p,"humidity",.5,0.0,1.0),rc,checkCancel)
                }
                else -> error("Unsupported reduced paper operation: $operation")
            }
        } else if(family==MaterialFamily.WET) {
            when(operation) {
                "develop" -> {
                    requireStage("exposed","developed")
                    val pour=n(p,"pour_uniformity",1.0,0.0,1.0).toFloat()
                    val activity=n(p,"activity",1.0,0.0,100.0)
                    each { i->
                        val access=a("moisture")[i]*(1f-(1f-pour)*a("flow")[i])
                        val r=(-expm1(-seconds/12.0*activity*thermal*access.toDouble())).toFloat()
                        val target=a("coating")[i]*a("latent")[i]
                        val amount=min(a("halide")[i],max(0f,target-a("silver")[i]))*r
                        a("halide")[i]-=amount;a("silver")[i]+=amount
                    };stage="developed"
                }
                "fix" -> {
                    requireStage("exposed","developed","fixed");val r=rate(20.0)
                    each { i->val h=a("halide")[i]*r;a("halide")[i]-=h;a("removed_silver")[i]+=h };stage="fixed"
                }
                "wash" -> {
                    requireStage("fixed","washed");val changes=n(p,"water_changes",3.0,0.0,100.0)
                    val r=(-expm1(-seconds/n(m,"wash_seconds",60.0,1.0,7200.0)*thermal*(changes/3.0))).toFloat()
                    each { i->for(c in 0..2)a("residue")[i*3+c]*=1-r;a("moisture")[i]=1f };stage="washed"
                }
                "dry","wait" -> {
                    requireStage("exposed","developed","fixed","washed","dry")
                    if(operation=="dry")requireStage("washed","dry")
                    val humidity=if(p.containsKey("humidity"))n(p,"humidity",0.0,0.0,1.0) else n(m,"humidity",.6,0.0,1.0)
                    PhoneMaterialBath.dryWet(a("moisture"),a("coating"),seconds,temperature,n(m,"drying_seconds",300.0,1.0,86400.0),humidity,checkCancel)
                    if(operation=="dry")stage="dry"
                }
                "varnish" -> {
                    requireStage("dry","varnished");require(a("moisture").all { it<=.1f }) { "Dry the washed plate further before varnishing" }
                    val amount=n(p,"amount",1.0,0.0,1.0).toFloat()
                    each { i->a("varnish")[i]=(a("varnish")[i]+amount).coerceAtMost(1f) };stage="varnished"
                }
                else -> error("Unsupported reduced wet-plate operation: $operation")
            }
        } else {
            val cyan=family==MaterialFamily.CYANOTYPE
            val rc=PhoneMaterialBath.paperBase(family,m)=="rc"
            val bath=PhoneMaterialBath.contact(p)
            val uptake=n(m,"water_uptake_seconds",25.0,.1,7200.0)
            fun wetAccess()=PhoneMaterialBath.hydrate(a("paper_moisture"),state.width,state.height,seconds,temperature,uptake,rc,bath,checkCancel)
            when(operation) {
                "wash" -> {
                    requireStage(*(if(cyan)arrayOf("exposed","washed","oxidized") else arrayOf("cleared","washed")))
                    val changes=n(p,"water_changes",3.0,0.0,100.0);val dose=wetAccess()
                    if(cyan) cyanWash(::a,m,changes,dose,checkCancel) else {
                        nobleClear(::a,m,changes*.1/3,dose,checkCancel)
                        each { i->val keep=exp(-dose[i].toDouble()*changes/180.0).toFloat();for(c in 0..2)a("residue")[i*3+c]*=keep }
                    }
                    if(state.stage!="oxidized")stage="washed"
                }
                "oxidize" -> {require(cyan);requireStage("washed","oxidized");oxidizePigment(::a,m,seconds,n(p,"oxygen",1.0,0.0,100.0),temperature);stage="oxidized"}
                "display_fade" -> {
                    require(cyan); requireStage("washed","oxidized")
                    val fraction=(-expm1(-seconds*n(p,"illumination",1.0,0.0,100.0)/14400)).toFloat()
                    each { i -> val faded=a("blue_pigment")[i]*fraction
                        a("blue_pigment")[i]-=faded; a("reduced_pigment")[i]+=faded }
                }
                "develop" -> {require(!cyan);requireStage("exposed","developed");wetAccess();nobleDevelop(::a,m,seconds,temperature,n(p,"activity",1.0,0.0,100.0));stage="developed"}
                "clear" -> {require(!cyan);requireStage("exposed","developed","cleared","washed");nobleClear(::a,m,n(p,"activity",1.0,0.0,100.0),wetAccess(),checkCancel);stage="cleared"}
                "dry" -> {
                    requireStage(*(if(cyan)arrayOf("washed","oxidized") else arrayOf("cleared","washed")))
                    val airflow=n(p,"airflow",1.0,0.0,100.0)
                    PhoneMaterialBath.dryPaper(a("paper_moisture"),seconds,temperature,n(m,"drying_seconds",600.0,1.0,86400.0),airflow,n(p,"humidity",.5,0.0,1.0),rc,checkCancel)
                    if(cyan && airflow>0){oxidizePigment(::a,m,seconds,airflow,temperature);stage="oxidized"}
                }
                else -> error("Unsupported reduced iron-print operation: $operation")
            }
        }
        quantize(f,state.arithmetic)
        require(f.values.all { v->v.values.all { it.isFinite() && it>=0f } }) { "Invalid material inventory" }
        return PhoneMaterialState(family,state.groupId,state.seed,state.width,state.height,stage,state.elapsedSeconds+seconds,m,f,
            state.history+listOf(parameters+mapOf("operation" to operation,"seconds" to seconds)),state.arithmetic)
    }

    fun finish(state: PhoneMaterialState): MaterialProducts {
        val f=state.fields();val m=state.parameters;val size=state.width*state.height;val bodies=mutableListOf<MaterialBody>()
        fun constant(value: Double)=FloatNd(intArrayOf(state.height,state.width),FloatArray(size) {value.toFloat()})
        fun body(role: String,model: String,version: Int,fields: Map<String,FloatNd>,negative: Boolean=false,frame: String="none") {
            bodies+=MaterialBody(state.family.id,version,state.groupId,state.groupId+":"+role,role,model,fields,negative,
                RecipeJson.text(mapOf("frame" to frame,"material" to m)))
        }
        when(state.family) {
            MaterialFamily.PEEL -> {
                require(state.stage in listOf("separated","cleared"))
                val common=mapOf("paper" to f.getValue("paper_texture"),"density_per_unit" to constant(n(m,"maximum_density",2.0,.001,20.0)),"stain_density" to f.getValue("stain_density"))
                body("receiver","diffuse_paper_v2",2,common+("transferred" to f.getValue("received")),frame=m["border_style"]?.toString() ?: "equal")
                if(state.stage=="cleared") {
                    val donor=f.getValue("developed_donor");val d=n(m,"maximum_density",2.0,.001,20.0).toFloat()
                    body("silver_negative","donor_transmission_v2",2,mapOf("density" to FloatNd(donor.shape,FloatArray(donor.values.size) {donor.values[it]*d}),"base_transmission" to constant(.86)),true)
                } else body("donor_residue","diffuse_residue_v2",2,common+mapOf("immobile" to f.getValue("immobile"),"remaining" to f.getValue("remaining")))
            }
            MaterialFamily.INSTANT -> {
                require(state.stage in listOf("stable","finished"))
                val fields=f.filterKeys { it !in setOf("phone_developer_available_mean","phone_developer_used_mean","inhibitor") }+
                    mapOf("paper" to f.getValue("paper_texture"),"density_per_unit" to constant(n(m,"maximum_density",2.0,.001,20.0)))
                body("integral_print","integral_reflection_v2",2,fields,frame=m["border_style"]?.toString() ?: "pod")
            }
            MaterialFamily.SILVER -> {
                require(state.stage in listOf("fixed","gilded"))
                body("silver_plate","metal_particle_reflection_v2",2,f+mapOf("metal_reflectance" to constant(n(m,"metal_reflectance",.9,0.0,1.0)),"particle_reflectance" to constant(n(m,"particle_reflectance",.7,0.0,1.0))))
            }
            MaterialFamily.PAPER -> {require(state.stage=="washed");body("paper_print","paper_reflection_v2",2,f.filterKeys { it !in setOf("dose","removed_silver") })}
            MaterialFamily.WET -> {require(state.stage in listOf("washed","dry","varnished"));val negative=(m["support"] ?: "glass_negative")=="glass_negative"
                body(if(negative)"wet_negative" else "wet_positive","wet_plate_optics_v2",2,f.filterKeys { it in setOf("silver","halide","coating","surface","varnish","residue","roughness") },negative)}
            MaterialFamily.CYANOTYPE -> {require(state.stage=="oxidized");body("cyanotype_print","cyanotype_reflection_v3",3,f.filterKeys {it!="iron_removed"})}
            MaterialFamily.PLATINUM -> {require(state.stage=="washed");body("noble_print","noble_reflection_v3",3,f.filterKeys {it !in setOf("iron_removed","metal_removed")})}
        }
        val provenance=mapOf("family" to state.family.id,"material" to m,"group_id" to state.groupId,"seed" to state.seed,
            "members" to bodies.map {it.bodyId},"process_seconds" to state.elapsedSeconds,"process" to mapOf("model" to MODEL,"events" to state.history),
            "execution_implementation" to mapOf("model" to MODEL,"resolution" to "unchanged","precision" to state.arithmetic,
                "preserved_material_fields" to (m.keys-supportedMaterialFields(state.family)).sorted(),
                "omitted" to listOf("generic_defects","millimetre_structure_grid","peel_separation_mechanics","external_silver","finite_gold_inventory","desktop_process_continuation")))
        return MaterialProducts(state.groupId,bodies,RecipeJson.text(provenance))
    }

    internal fun n(map: Map<String,Any?>,key: String,default: Double,low: Double=0.0,high: Double=100.0): Double {
        val value=map[key] ?: default
        require(value is Number && value.toDouble().isFinite() && value.toDouble() in low..high) { "Invalid $key" };return value.toDouble()
    }
    private fun validateKinds(f: MaterialFamily,m: Map<String,Any?>) {
        if(f in listOf(MaterialFamily.PEEL,MaterialFamily.INSTANT)) require(m["transfer"] in listOf(null,"dye","silver_compound"))
        if(f==MaterialFamily.PEEL) require(m["donor_mode"] in listOf(null,"residue","silver_negative") && (m["donor_mode"]!="silver_negative" || m["transfer"]=="silver_compound"))
        if(f==MaterialFamily.PAPER)require(m["kind"] in listOf(null,"graded","variable_contrast","direct_positive","color"))
        if(f==MaterialFamily.WET)require(m["support"] in listOf(null,"glass_negative","ambrotype","tintype") && m["pour_direction"] in listOf(null,"down","up","left","right"))
        if(f in listOf(MaterialFamily.PAPER,MaterialFamily.CYANOTYPE,MaterialFamily.PLATINUM)) require(m["paper_base"] in listOf(null,"rc","fiber"))
        if(f==MaterialFamily.CYANOTYPE) require(m["brush_direction"] in listOf(null,"down","up","left","right"))
        if(f==MaterialFamily.PLATINUM) require(m["coating_method"] in listOf(null,"rod","brush"))
        require(m["border_style"] in listOf(null,"none","equal","pod"))
    }
    private fun rejectReducedDefect(parameters: Map<String,Any?>) {
        for(key in listOf("unevenness","reagent_leak","scratches","pitting","edge_tarnish","strength")) {
            val value=parameters[key] ?: continue
            require(value is Number && value.toDouble()==0.0) { "Reduced materials do not apply $key" }
        }
        require(parameters["region"]==null || parameters["region"]=="all") { "Reduced materials do not apply local bath regions" }
    }
    /** Geometry and appearance only. Conserved chemical pools stay Float32 so pool identities remain exact. */
    private val halfFields=setOf("paper","paper_texture","backing_texture","paper_moisture","fibers","binding","absorption",
        "clear_access","flow","roughness","surface","polish_x","polish_y","microtexture","stain_density","varnish")
    private fun quantize(fields: Map<String,FloatNd>,arithmetic: String) {
        if(arithmetic!=ARITHMETIC_HALF) return
        for((name,field) in fields) if(name in halfFields) {
            val values=field.values
            for(i in values.indices) {
                var rounded=NpyCodec.roundBinary16(values[i])
                if(rounded<0f) rounded=0f
                if(name!="stain_density" && rounded>1f) rounded=1f
                values[i]=rounded
            }
        }
    }
    private fun cyanWash(a: (String)->FloatArray,m: Map<String,Any?>,changes: Double,dose: FloatArray,check: ()->Unit) {
        val flow=changes/3
        for(i in dose.indices) {
            if(i and 4095==0)check()
            val reaction=flow/4;val total=reaction+flow/120
            val loss=flow*(.0004+.0018*(1-a("binding")[i].toDouble()));val d=dose[i].toDouble()
            val ea=exp(-total*d);val ep=exp(-loss*d)
            val gain=if(abs(loss-total)>1e-8)(ea-ep)/(loss-total) else d*ep
            val ferrous=a("ferrous")[i].toDouble();val retained=ferrous*reaction*gain;val converted=ferrous*(1-ea)
            val ferricLoss=a("ferric")[i].toDouble()*(-expm1(-d*flow/35.0))
            val lost=(a("reduced_pigment")[i].toDouble()+a("blue_pigment")[i].toDouble())*(1-ep)+max(0.0,converted-retained)
            a("ferrous")[i]=(ferrous*ea).toFloat();a("ferric")[i]=(a("ferric")[i].toDouble()-ferricLoss).toFloat()
            a("reduced_pigment")[i]=(a("reduced_pigment")[i].toDouble()*ep+retained).toFloat();a("blue_pigment")[i]=(a("blue_pigment")[i].toDouble()*ep).toFloat()
            a("iron_removed")[i]=(a("iron_removed")[i].toDouble()+ferricLoss+lost).toFloat()
            val keep=exp(-d*flow/60).toFloat();for(c in 0..2)a("residue")[i*3+c]*=keep
        }
    }
    private fun nobleClear(a: (String)->FloatArray,m: Map<String,Any?>,activity: Double,dose: FloatArray,check: ()->Unit) {
        val tau=n(m,"clearing_seconds",45.0,1.0,7200.0)
        for(i in dose.indices) {
            if(i and 4095==0)check()
            val fraction=(-expm1(-dose[i].toDouble()*activity*a("clear_access")[i].toDouble()/tau)).toFloat().coerceIn(0f,1f)
            for(key in listOf("ferric","ferrous","spent_ferric")) {val lost=a(key)[i]*fraction;a(key)[i]-=lost;a("iron_removed")[i]+=lost}
            for(key in listOf("platinum_salt","palladium_salt")) {val lost=a(key)[i]*fraction;a(key)[i]-=lost;a("metal_removed")[i]+=lost}
        }
    }
    private fun nobleDevelop(a: (String)->FloatArray,m: Map<String,Any?>,seconds: Double,temperature: Double,activity: Double) {
        val heat=2.0.pow((temperature-20)/10)
        val platinumRate=(-expm1(-seconds/25*activity*heat)).toFloat();val palladiumRate=(-expm1(-seconds/15*activity*heat)).toFloat()
        for(i in a("coating").indices) {
            val coating=a("coating")[i]
            val activated=if(coating>0f)(a("ferrous")[i]+a("spent_ferric")[i])/(2*coating) else 0f
            var platinum=min(a("platinum_salt")[i],max(0f,(a("platinum_salt")[i]+a("platinum")[i])*activated-a("platinum")[i])*platinumRate)
            var palladium=min(a("palladium_salt")[i],max(0f,(a("palladium_salt")[i]+a("palladium")[i])*activated-a("palladium")[i])*palladiumRate)
            val wanted=2*(platinum+palladium);val ferrous=a("ferrous")[i]
            if(wanted>ferrous && wanted>0f){val scale=ferrous/wanted;platinum*=scale;palladium*=scale}
            a("platinum_salt")[i]-=platinum;a("palladium_salt")[i]-=palladium;a("platinum")[i]+=platinum;a("palladium")[i]+=palladium
            a("ferrous")[i]=ferrous-2*(platinum+palladium);a("spent_ferric")[i]+=2*(platinum+palladium)
        }
    }
    private fun oxidizePigment(a: (String)->FloatArray,m: Map<String,Any?>,seconds: Double,activity: Double,temperature: Double) {
        val tau=n(m,"oxidation_seconds",240.0,1.0,7200.0);val heat=2.0.pow((temperature-20)/10)
        for(i in a("reduced_pigment").indices) {
            val rate=(-expm1(-seconds/tau*activity*heat*(.7+.3*(1-a("binding")[i].toDouble())))).toFloat()
            val amount=a("reduced_pigment")[i]*rate;a("reduced_pigment")[i]-=amount;a("blue_pigment")[i]+=amount
        }
    }
    private fun paperResponse(d: FloatArray,i: Int,c: Int,m: Map<String,Any?>): Float {
        val half=n(m,"half_exposure",.18,.001,20.0);val contrast=n(m,"response_contrast",2.2,.2,8.0)
        val kind=m["kind"] ?: "variable_contrast"
        val dose=when(kind) {"color"->d[i*3+c].toDouble();"variable_contrast"->d[i*3+2]+d[i*3+1]*doubleArrayOf(.025,.2,1.0)[c];else->d[i*3+2]+.08*d[i*3+1]}
        val e=(max(0.0,dose)*n(m,"sensitivity_remaining",1.0,0.0,1.0)/half).pow(contrast);val active=if(e.isInfinite())1.0 else e/(1+e)
        val response=if(kind=="direct_positive")1-active else active
        return response.toFloat()
    }
    /** Coordinate PRNG with smooth interpolation; no file hashing or fixed texture template. */
    internal fun noise(x: Double,y: Double,seed: Long): Double {
        fun value(a: Long,b: Long): Double {var v=seed+a*104729+b*130363;v=v*6364136223846793005L+1442695040888963407L;v=v xor(v ushr 29);return ((v ushr 40) and 0xffffff)/8388608.0-1}
        val ix=floor(x).toLong();val iy=floor(y).toLong();val fx=x-ix;val fy=y-iy;val tx=fx*fx*(3-2*fx);val ty=fy*fy*(3-2*fy)
        return (value(ix,iy)*(1-tx)+value(ix+1,iy)*tx)*(1-ty)+(value(ix,iy+1)*(1-tx)+value(ix+1,iy+1)*tx)*ty
    }
}
