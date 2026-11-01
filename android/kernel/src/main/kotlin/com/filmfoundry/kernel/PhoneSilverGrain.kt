package com.filmfoundry.kernel

import com.filmfoundry.interchange.PhoneMaterialPhenomena
import kotlin.math.*

/** Neutral retained-silver redistribution. Chemical pools and dye amounts are untouched. */
internal class PhoneSilverGrain(recipe: Map<String,Any?>,metadata: Map<String,Any?>,width: Int,height: Int) {
    private val film=recipe["film"] as? Map<*,*> ?: emptyMap<Any,Any>()
    private fun n(key: String,default: Float,range: ClosedFloatingPointRange<Float>): Float {
        val value=film[key] ?: return default
        require(value is Number && value.toFloat().isFinite() && value.toFloat() in range) { "$key must be within $range" };return value.toFloat()
    }
    private val strength=n("silver_grain_strength",0f,0f.. .5f)
    private val radius=n("silver_grain_radius",.0008f,0f.. .1f)
    private val clump=n("silver_grain_clump_mix",.22f,0f..1f)
    private val reference=(metadata["material_geometry"] as? Map<*,*>)?.let {
        (it["reference_short_edge_mm"] as Number).toFloat()*max(width,height)/(it["frame_long_edge_mm"] as Number).toFloat()
    } ?: min(width,height).toFloat()
    private val bath=recipe["phone_bath_grain"] as? Map<*,*>
    private val scale=max(.65f,radius*reference*((bath?.get("radius") as? Number)?.toFloat() ?: 1f))
    private val amount=strength*((bath?.get("strength") as? Number)?.toFloat() ?: 1f)
    private val identity=metadata["frame_identity"] as? Map<*,*>
    private val seed=run {
        var value=(recipe["seed"] as? Number)?.toLong() ?: 31L
        for(ch in "silver:${identity?.get("roll_id")}:${identity?.get("frame_id")}:${(identity?.get("frame_index") as? Number)?.toLong()}") value=value*131+ch.code
        value
    }
    val enabled=recipe[PhoneMaterialPhenomena.DEVELOPMENT_OPTION]==PhoneMaterialPhenomena.CURRENT && recipe["enable_grain"]!=false && strength>0f && radius>0f
    data class Normalization(val gain: Double,val originalTotal: Double,val candidateTotal: Double,val record: Map<String,Any?>)
    private fun random(x: Int,y: Int,stream: Long): Float {
        var value=seed+x.toLong()*104729+y.toLong()*130363+stream
        value=value*6364136223846793005L+1442695040888963407L;value=value xor (value ushr 29)
        value=value*2862933555777941757L+3037000493L
        return ((value ushr 40) and 0xffffff).toFloat()/8388608f-1f
    }
    private fun field(x: Int,y: Int,radius: Float,stream: Long): Float {
        val fx=x/radius;val fy=y/radius;val ix=floor(fx).toInt();val iy=floor(fy).toInt();val tx=fx-ix;val ty=fy-iy
        val a=(1-tx)*(1-ty);val b=tx*(1-ty);val c=(1-tx)*ty;val d=tx*ty
        return (random(ix,iy,stream)*a+random(ix+1,iy,stream)*b+random(ix,iy+1,stream)*c+random(ix+1,iy+1,stream)*d)*sqrt(3f/(a*a+b*b+c*c+d*d))
    }
    fun raw(silver: Float,x: Int,y: Int): Float {
        if(silver<=1e-8f || !enabled) return silver
        val fine=field(x,y,scale,1709)
        val noise=if(clump>0f) fine*sqrt(1-clump*clump)+field(x,y,scale*3.2f,3911)*clump else fine
        return max(silver*.1f,silver+noise*amount*silver/sqrt(silver+.05f))
    }
    fun normalized(silver: Float,x: Int,y: Int,normalization: Normalization)=(raw(silver,x,y)*normalization.gain).toFloat()
    fun normalization(original: Double,candidate: Double)=Normalization(if(candidate>0) original/candidate else 1.0,original,candidate,
        mapOf("model" to MODEL,"stage" to "retained_metallic_silver_optical_amount","applied" to (original>1e-8),
            "reason" to if(original>1e-8) "retained_silver" else "no_retained_silver",
            "strength" to strength,"effective_strength" to amount,"radius_reference_short_edge" to radius,"radius_px" to scale,
            "clump_mix" to clump,"scope" to "frame","coordinates" to "absolute_material_pixels_v1",
            "normalization" to "whole_frame_silver_total_v1","original_total" to original,"candidate_total" to candidate,
            "gain" to if(candidate>0) original/candidate else 1.0,"chemical_pool_consumptions" to 0,"dye_grain" to "separate"))
    companion object { const val MODEL="phone_conservative_silver_cloud_v1" }
}
