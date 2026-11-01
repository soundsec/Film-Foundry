package com.filmfoundry.kernel

import kotlin.math.*

/** Prepared low-dimensional bath character, independent of desktop kinetics. No pixel arrays. */
internal class PhoneBathCharacter(val values: Map<String,Any?>) {
    fun n(key: String,fallback: Double=0.0)=(values[key] as? Number)?.toDouble() ?: fallback
    val requested=values["developer_type"]?.toString() ?: "standard"
    val developer=requested.takeIf { it in profiles } ?: "standard"
    private val profile=profiles.getValue(developer)
    val fixer=values["fixer_type"]?.toString()?.takeIf { it in listOf("standard","rapid","hardening","monobath") } ?: "standard"
    val exhaustion=max(n("developer_exhaustion"),if(developer=="exhausted") .65 else 0.0)
    val agitation=n("agitation",1.0)*(n("agitation_time_s",n("time_min",8.0)*60)/(n("time_min",8.0)*60))
    val push=n("push_stops")
    val activity=profile[0]*(1-.7*exhaustion)*2.0.pow(push*.18)
    val gamma=(profile[1]*(1+push*.08)).coerceIn(.35,2.2).toFloat()
    val compensation=(profile[5]+n("compensation")*.6).coerceIn(0.0,.9).toFloat()
    val grain=(profile[2]*(1+.45*exhaustion+.16*max(push,0.0)+.012*max(n("temperature_c",20.0)-20,0.0))).toFloat()
    val radius=profile[3].toFloat()
    val clump=profile[4].toFloat()
    val fixing=(1-n("fixer_exhaustion")*(if(fixer=="rapid") .65 else .95))*(if(fixer=="monobath" || developer=="monobath") .76 else if(fixer=="hardening") .9 else 1.0)
    val fog=(.035*exhaustion+.008*max(push,0.0)).toFloat()
    fun record()=mapOf("requested_developer" to requested,"developer" to developer,"fixer" to fixer,"activity" to activity,"gamma" to gamma,
        "compensation" to compensation,"grain_multiplier" to grain,"grain_radius_multiplier" to radius,"grain_clump_multiplier" to clump,"fixing" to fixing,"fog_activation" to fog)
    companion object {
        // activity, activation contrast, grain, radius, clumping, compensation; creative coefficients.
        private val profiles=mapOf("standard" to listOf(1.0,1.0,1.0,1.0,1.0,0.0),
            "fine_grain" to listOf(.9,.94,.65,.8,.65,0.0),"compensating" to listOf(.84,.86,.88,1.0,.86,.55),
            "high_acutance" to listOf(.94,1.04,1.1,.94,1.4,.12),"high_contrast" to listOf(1.1,1.28,1.2,1.0,1.15,0.0),
            "push" to listOf(1.2,1.34,1.5,1.15,1.3,0.0),"exhausted" to listOf(.85,.72,1.45,1.18,1.38,.2),"monobath" to listOf(.78,.86,1.18,1.0,1.16,.1))
    }
}
