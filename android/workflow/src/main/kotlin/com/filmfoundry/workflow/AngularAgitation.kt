package com.filmfoundry.workflow

import kotlin.math.*

/** O(1) rotation sampling; no Android types, raw history, or invented samples during gaps. */
class AngularAgitation {
    data class Summary(val elapsedSeconds: Double,val activeSeconds: Double,val strength: Double,val samples: Int)
    private var first=0L;private var previous=0L;private var last=0L
    private var previousRate=0.0;private var active=0.0;private var energy=0.0;private var samples=0
    fun add(timestampNs: Long,x: Double,y: Double,z: Double) {
        if(timestampNs<=0 || !x.isFinite() || !y.isFinite() || !z.isFinite()) return
        if(samples>0 && timestampNs<=previous) return
        val rate=sqrt(x*x+y*y+z*z).coerceAtMost(30.0)
        if(samples==0) first=timestampNs else {
            val dt=(timestampNs-previous)/1e9
            if(dt<=.25 && rate>.25 && previousRate>.25) { active+=dt;energy+=(rate*rate+previousRate*previousRate)*.5*dt }
        }
        samples++;previous=timestampNs;last=timestampNs;previousRate=rate
    }
    fun summary()=Summary(if(samples<2) 0.0 else (last-first)/1e9,active,if(active>0) (sqrt(energy/active)/2).coerceIn(0.0,3.5) else 0.0,samples)
}
