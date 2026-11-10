package com.filmfoundry.kernel

import kotlin.math.*

/** Independent phone creative models. Bounded row scratch, absolute material coordinates. */
internal class PhoneFormationEffects(recipe: Map<String,Any?>, source: Map<String,Any?>,
    private val width: Int, private val height: Int, private val lightweight: Boolean=false) {
    private val film=recipe["film"] as? Map<*,*> ?: emptyMap<Any,Any>()
    private val develop=recipe["develop"] as? Map<*,*> ?: emptyMap<Any,Any>()
    private val identity=source["frame_identity"] as? Map<*,*> ?: emptyMap<Any,Any>()
    private val singleLayer=(source["layer_count"] as? Number)?.toInt()==1
    private fun amount(map: Map<*,*>,key: String,limit: Float=1f): Float {
        val raw=map[key] ?: return 0f
        require(raw is Number && raw.toFloat().isFinite() && raw.toFloat() in 0f..limit) { "$key must be between 0 and $limit" }
        return raw.toFloat()
    }
    private val profile=source[PhoneGrainProfile.KEY] as? Map<*,*>
    private val bathGrain=recipe["phone_bath_grain"] as? Map<*,*>
    private val baseGrain=if(profile != null) (profile["grain_strength"] as Number).toFloat().also { require(it.isFinite() && it in 0f..1f) }
        else if(recipe["enable_grain"] == false) 0f else amount(film,"grain_strength",.25f)
    val grain=(baseGrain*((bathGrain?.get("strength") as? Number)?.toFloat() ?: 1f)).coerceIn(0f,1f)
    val grainModel=if(lightweight) "phone_shared_density_cloud_v1" else profile?.get("model")?.toString() ?: "phone_density_cloud_v1"
    private val uneven=amount(develop,"uneven_development")
    private val stain=amount(develop,"chemical_stain")
    private val reticulation=amount(develop,"reticulation_strength")
    val hasOverlay get()=stain > 0f || reticulation > 0f
    val enabled get()=grain > 0f || uneven > 0f || hasOverlay
    private fun identityNumber(text: String): Long { var n=17L;for(ch in text)n=n*131+ch.code;return n }
    private val batch=(develop["incident_identity_seed"] as? Number)?.toLong() ?: identityNumber(identity["roll_id"]?.toString() ?: "phone")
    private val frame=identityNumber("${identity["roll_id"]}:${identity["frame_id"]}:${(identity["frame_index"] as? Number)?.toLong()}")
    private val scale=max(1f,min(width,height)/720f)
    private val referencePixels=(source["material_geometry"] as? Map<*,*>)?.let {
        (it["reference_short_edge_mm"] as Number).toFloat()*max(width,height)/(it["frame_long_edge_mm"] as Number).toFloat()
    } ?: min(width,height).toFloat()
    private val scales=(profile?.get("scales") as? List<*>)?.map { (it as Number).toFloat().also { n->require(n.isFinite() && n>0f && n<=.1f) } }
    private val weights=(profile?.get("weights") as? List<*>)?.mapIndexed { i,v ->
        (v as Number).toFloat().also { n->require(n.isFinite() && n>=0f) } * if(i==(profile["weights"] as List<*>).lastIndex) ((bathGrain?.get("clump") as? Number)?.toFloat() ?: 1f) else 1f }
    private val sourceGrainSize=((profile?.get("size_multiplier") as? Number)?.toFloat() ?: 1f).also { require(it.isFinite() && it in .1f..8f) }
    private val grainSize=(sourceGrainSize*((bathGrain?.get("radius") as? Number)?.toFloat() ?: 1f)).coerceIn(.1f,8f)
    private val grainMu=(profile?.get("midtone_mu") as? Number)?.toFloat() ?: .48f
    private val grainSigma=(profile?.get("midtone_sigma") as? Number)?.toFloat() ?: .22f
    init {
        if(profile != null) require(profile["model"] in listOf(PhoneGrainProfile.MODEL,PhoneGrainProfile.PREVIOUS_MODEL,com.filmfoundry.interchange.PhoneGrainModels.LAYERED))
        if(profile != null) require(scales != null && scales.size in 1..4 && scales.size == weights?.size && weights!!.sum()>0f)
        require(grainSize.isFinite() && grainSize in .1f..8f && grainMu.isFinite() && grainMu in 0f..1f && grainSigma.isFinite() && grainSigma in .01f..1f)
    }
    private val layered=if(grain>0f && grainModel==com.filmfoundry.interchange.PhoneGrainModels.LAYERED)
        PhoneLayerDyeCloud(width,referencePixels,grainSize,scales!!,weights!!,frame,grainMu,grainSigma) else null
    private val fineRadius=max(.75f,(scales?.first() ?: .0012f)*referencePixels*grainSize*.45f)
    private val coarseRadius=max(fineRadius,(scales?.last() ?: .010f)*referencePixels*grainSize)
    // Fold omitted intermediate scales into fine energy, not into oversized clumps.
    private val coarseMix=((weights?.let {
        if(it.size==1) 0f else it.last()/(sqrt(it.dropLast(1).sumOf { v->(v*v).toDouble() }).toFloat()+it.last())
    } ?: .1f)*
        ((bathGrain?.get("clump") as? Number)?.toFloat() ?: 1f)).coerceIn(0f,.6f)
    private val cloudNorm=sqrt((1f-coarseMix)*(1f-coarseMix)+coarseMix*coarseMix)
    private val densityResponse=if(lightweight && grain>0f) FloatArray(1025) { i ->
        val t=(i/1024f-grainMu)/grainSigma;.06f+.94f*exp(-.5f*t*t)
    } else null
    private fun value(x: Int,y: Int,seed: Long): Float {
        // Coordinate PRNG, independently expressed; no image/file checksum.
        var state=seed+x.toLong()*104729L+y.toLong()*130363L
        state=state*6364136223846793005L+1442695040888963407L
        state=state xor (state ushr 29)
        state=state*2862933555777941757L+3037000493L
        return ((state ushr 40) and 0xffffff).toFloat()/8388608f-1f
    }
    private fun field(x: Float,y: Float,seed: Long,smooth: Boolean=false): Float {
        val ix=floor(x).toInt();val iy=floor(y).toInt()
        val fx=x-ix;val fy=y-iy
        val tx=if(smooth) fx*fx*(3-2*fx) else fx;val ty=if(smooth) fy*fy*(3-2*fy) else fy
        val a=value(ix,iy,seed)*(1-tx)+value(ix+1,iy,seed)*tx
        val b=value(ix,iy+1,seed)*(1-tx)+value(ix+1,iy+1,seed)*tx
        return a*(1-ty)+b*ty
    }
    fun completion(base: Float,x: Int,y: Int): Float = if(uneven == 0f) base else
        (base*(1f+uneven*.7f*field(x*3f/width,y*3f/height,batch+31))).coerceIn(0f,1f)
    /** Signed |x|^1.5: fog collapses, clump bodies stay, and the mean stays near zero. */
    private fun clump(noise: Float): Float { val x=noise.coerceIn(-1f,1f);val mag=abs(x);return sign(x)*mag*sqrt(mag) }
    private fun envelope(amount: Float): Float {
        densityResponse?.let {
            val v=amount.coerceIn(0f,1f)*1024f;val i=v.toInt().coerceAtMost(1023);val t=v-i
            return it[i]*(1-t)+it[i+1]*t
        }
        val t=(amount.coerceIn(0f,1f)-grainMu)/grainSigma
        return .06f+.94f*exp(-.5f*t*t)
    }
    /** Caller-owned pixel scratch; never shared between tasks, blocks or threads. */
    class GrainSamples {
        var x=Int.MIN_VALUE;var y=Int.MIN_VALUE;var mask=0
        val shared=FloatArray(4)
        internal var layeredOwner: PhoneLayerDyeCloud?=null
        internal var layered: PhoneLayerDyeCloud.Samples?=null
    }
    private fun shared(x: Int,y: Int,index: Int,radius: Float,seed: Long,smooth: Boolean,shaped: Boolean,cache: GrainSamples?): Float {
        if(cache!=null) {
            if(cache.x!=x || cache.y!=y) { cache.x=x;cache.y=y;cache.mask=0 }
            if(cache.mask and (1 shl index)!=0) return cache.shared[index]
        }
        val raw=field(x/radius,y/radius,seed,smooth);val value=if(shaped) clump(raw) else raw
        if(cache!=null) { cache.shared[index]=value;cache.mask=cache.mask or (1 shl index) }
        return value
    }
    fun grainFactor(x: Int,y: Int,layer: Int,amount: Float,cache: GrainSamples?=null): Float {
        if(grain == 0f) return 1f
        if(lightweight) {
            val fine=shared(x,y,0,fineRadius,frame+11,true,true,cache)
            val coarse=if(coarseMix>0f) shared(x,y,1,coarseRadius,frame+712,true,true,cache) else 0f
            val cloud=(fine*(1f-coarseMix)+coarse*coarseMix)/cloudNorm
            return 1f+(grain*7f*cloud*envelope(amount)).coerceIn(-.42f,.42f)
        }
        layered?.let { model ->
            val samples=if(cache==null) model.samples(false) else {
                if(cache.layeredOwner!==model) { cache.layeredOwner=model;cache.layered=model.samples() }
                cache.layered!!
            }
            return model.factor(samples,x,y,layer,amount,grain)
        }
        // Archived profiles select their original formula, not merely an accepted file version.
        if(grainModel!=PhoneGrainProfile.MODEL) return legacyGrainFactor(x,y,layer,amount,cache)
        val shaped=if(profile != null) {
            var cloud=0f;var norm=0f
            for(i in scales!!.indices) {
                val radius=max(2.4f,scales[i]*referencePixels*grainSize)
                val shared=shared(x,y,i,radius,frame+11+i*701,true,true,cache)
                val fraction=(radius/referencePixels).coerceIn(0f,.05f)
                val chroma=((fraction-.008f)/.042f).coerceIn(0f,1f)*.22f
                // Fine scales have zero chroma weight; their independent fields cannot affect the result.
                val independent=if(chroma==0f) 0f else clump(field(x/radius,y/radius,frame+1301+i*1009+layer*101,true))
                val weight=weights!![i];cloud+=(shared*(1f-chroma)+independent*chroma)*weight;norm+=weight*weight
            }
            cloud/sqrt(norm)
        } else {
            val radius=max(2.4f,scale*1.6f)
            val shared=shared(x,y,0,radius,frame+11,true,true,cache)
            val independent=clump(field(x/radius,y/radius,frame+701+layer*101,true))
            shared*.94f+independent*.06f
        }
        return 1f+(grain*7f*shaped*envelope(amount)).coerceIn(-.42f,.42f)
    }
    private fun legacyGrainFactor(x: Int,y: Int,layer: Int,amount: Float,cache: GrainSamples?): Float {
        if(profile != null) {
            var cloud=0f;var norm=0f
            for(i in scales!!.indices) {
                val radius=max(.65f,scales[i]*referencePixels*grainSize)
                val shared=shared(x,y,i,radius,frame+11+i*701,true,false,cache)
                val independent=field(x/radius,y/radius,frame+1301+i*1009+layer*101,true)
                val weight=weights!![i];cloud+=(shared*.75f+independent*.25f)*weight;norm+=weight*weight
            }
            val t=(amount.coerceIn(0f,1f)-grainMu)/grainSigma
            val envelope=.3f+.7f*exp(-.5f*t*t)
            return max(.05f,1f+grain*5f*cloud/sqrt(norm)*envelope)
        }
        val shared=shared(x,y,0,scale,frame+11,false,false,cache)
        val independent=field(x/scale,y/scale,frame+701+layer*101)
        return max(.05f,1f+grain*5f*(shared*.75f+independent*.25f))
    }
    fun overlay(x: Int,y: Int,channel: Int): Float {
        var d=0f
        if(stain > 0f) {
            val blot=max(0f,field(x*5f/width,y*5f/height,batch+991))
            d+=stain*blot*blot*when(channel) { 0->.12f;1->.22f;else->.38f }
        }
        if(reticulation > 0f) {
            val n=field(x/(scale*13f),y/(scale*13f),batch+313)
            val line=(1f-abs(n)/.045f).coerceAtLeast(0f)
            d+=reticulation*(line*.13f-.008f)
        }
        return d
    }
    fun record()=mapOf("grain" to if(grain>0f) grainModel else "omitted_v0",
        "grain_strength" to grain,"grain_scope" to "frame",
        "uneven_development" to uneven,"chemical_stain" to stain,"reticulation_strength" to reticulation,
        "incident_scope" to "roll","incident_identity" to batch,
        "coordinates" to "absolute_material_pixels_v1") + (if(profile != null) mapOf("grain_profile" to profile) else emptyMap()) +
        (if(bathGrain!=null) mapOf("bath_grain_character" to bathGrain) else emptyMap()) +
        (if(lightweight && grain>0f) mapOf("density_response" to "material_midtone_envelope_v1",
            "shared_grain_radii_px" to listOf(fineRadius,coarseRadius),"coarse_mix" to coarseMix,
            "independent_layer_noise" to false) else emptyMap()) +
        (if(layered!=null) mapOf("dye_cloud" to if(singleLayer) "omitted_v0" else "layer_density_coarse_cloud_v1",
            "layer_shared_mix" to .32f,"grain_layer_count" to if(singleLayer) 1 else 3) else emptyMap())
}
