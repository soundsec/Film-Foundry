package com.filmfoundry.kernel

import com.filmfoundry.interchange.ProcessSteps
import com.filmfoundry.interchange.PhoneProcessModels
import kotlin.math.*

/** Pointwise ordered material-pool operators. Not a scanner look or desktop numerical port. */
internal class PhoneProcessProgram private constructor(val key: String,val steps: List<Map<String,Any?>>,private val recipe: Map<String,Any?>,
    private val material: Map<String,Any?>,private val interactive: Boolean,private val mismatch: Boolean,
    private val activationLedger: Boolean,private val spatialModel: Boolean) {
    val model=if(spatialModel) PhoneProcessModels.CURRENT else if(activationLedger) PhoneProcessModels.LEDGER else if(interactive) PhoneProcessModels.PREVIOUS else MODEL
    private val baths=steps.map { PhoneBathCharacter(recipe+it) }
    private fun stock(key: String,default: Float=1f)=(material[key] as? Number)?.toFloat() ?: default
    private val firstDevelopment=steps.indexOfFirst { it["action"] in listOf("develop_silver","develop_color") }
    private val firstColor=steps.indexOfFirst { it["action"]=="develop_color" }
    val hasDevelopment get()=firstDevelopment>=0
    val firstDevelopmentAgitation get()=baths.getOrNull(firstDevelopment)?.agitation ?: 1.0
    val hasColor get()=firstColor>=0
    val reversal get()=steps.any { it["action"]=="activate_remaining_halide" && ((it["strength"] as? Number)?.toDouble() ?: 1.0)>0 }
    private val balance=(material["cross_process_layer_balance"] as? List<*>)?.map { (it as Number).toFloat() }?.toFloatArray() ?: floatArrayOf(1f,1f,1f)
    private val dyeCoupling=if(interactive && mismatch) stock("cross_process_dye_coupling") else 1f
    private val localDyeDamage=FloatArray(steps.size) { (steps[it]["dye_damage_strength"] as? Number)?.toFloat() ?: 0f }
    /** Response probes classify the image carrier, not the stock's original color family. */
    fun imageComponents(color: Boolean): List<String> {
        var dye=0f;var silver=0f;var halide=0f
        for(a in listOf(.01f,.5f,.99f)) for(l in 0 until if(color) 3 else 1) {
            val p=Pools(1f,a,0f,1f,0f,0f);apply(p,color,1f,l)
            dye=max(dye,p.dye);silver=max(silver,p.silver);halide=max(halide,p.halide+p.bleached)
        }
        return buildList { if(dye>1e-7f) add("dye");if(silver>1e-7f) add("metallic_silver");if(halide>1e-7f) add("retained_halide") }
    }
    fun hasVariableImage(color: Boolean): Boolean {
        for(l in 0 until if(color) 3 else 1) {
            var minD=Float.POSITIVE_INFINITY;var maxD=0f;var minS=Float.POSITIVE_INFINITY;var maxS=0f
            for(a in listOf(.01f,.5f,.99f)) {
                val p=Pools(1f,a,0f,1f,0f,0f);apply(p,color,1f,l)
                minD=min(minD,p.dye);maxD=max(maxD,p.dye);minS=min(minS,p.silver);maxS=max(maxS,p.silver)
            }
            if(maxD-minD>1e-6f || maxS-minS>1e-6f) return true
        }
        return false
    }
    fun positiveFor(color: Boolean,absorption: Array<FloatArray>,retained: FloatArray): Boolean {
        fun density(activation: Float): Float {
            var sum=0f;val layers=if(color) 3 else 1
            for(layer in 0 until layers) {
                val p=Pools(1f,activation,0f,1f,0f,0f);apply(p,color,1f,layer)
                sum+=p.silver*.85f/layers+(p.halide+p.bleached)*.12f*retained.average().toFloat()/layers
                if(color) for(c in 0..2) sum+=absorption[c][layer]*p.dye/3
            }
            return sum
        }
        return density(.01f)>density(.99f)+1e-8f
    }
    private val actions=IntArray(steps.size) { ProcessSteps.actions.indexOf(steps[it]["action"]) }
    private val fractions=FloatArray(steps.size) { i ->
        val step=steps[i]
        val conditions=recipe+step
        val strength=(step["strength"] as? Number)?.toFloat() ?: 1f
        val action=step["action"] as String
        val exposureDevelopment=action in listOf("develop_silver","develop_color")
        // Clearing is normalized to the standard bath; shorter/cooler/quieter baths can be incomplete.
        val bath=PhoneBathCharacter(conditions)
        val basic=completion(conditions)
        val progress=if(interactive && exposureDevelopment) ((1-(1-basic).toDouble().pow(bath.activity))/completion(emptyMap())).toFloat().coerceAtMost(1f) else basic
        val stockFactor=if(!interactive || !mismatch) 1f else stock(when(action) {
            "develop_silver","develop_color"->"cross_process_silver_development";"activate_remaining_halide"->"cross_process_activation"
            "bleach_silver"->"cross_process_silver_bleach";"fix_halide"->"cross_process_halide_fixing";"remove_silver"->"cross_process_silver_removal"
            "remove_auxiliary"->"cross_process_auxiliary_removal";else->"_default"
        })
        val conditionFactor=if(!interactive) 1f else when(action) {
            "fix_halide"->bath.fixing.toFloat();"bleach_silver"->(1-bath.n("silver_retention")).toFloat()
            "bleach_mask_dye"->stock("experimental_mask_bleach_susceptibility");else->1f
        }
        (strength*stockFactor*conditionFactor*(if(exposureDevelopment) progress else (progress/completion(emptyMap())).coerceAtMost(1f))).coerceIn(0f,1f)
    }
    // Immutable bath coefficients: no map lookups or boxed fractions in the pixel loop.
    private val fog=FloatArray(steps.size) { baths[it].fog }
    private val gamma=FloatArray(steps.size) { baths[it].gamma }
    private val gammaExponent=FloatArray(steps.size) { gamma[it]-1f }
    private val compensation=FloatArray(steps.size) { baths[it].compensation }
    private val maskDyeDamage=stock("experimental_mask_bleach_dye_damage",.16f)
    val maskRemaining=steps.indices.filter { steps[it]["action"]=="bleach_mask_dye" }.fold(1f) { n,i->n*(1-fractions[i]) }
    val auxiliaryRemaining=steps.indices.filter { steps[it]["action"]=="remove_auxiliary" }.fold(1f) { n,i->n*(1-fractions[i]) }
    val clearsRetainedSilver get(): Boolean {
        val last=actions.indexOfLast { it==0 || it==1 }
        return actions.indices.any { it>last && actions[it] in listOf(3,5) && fractions[it]==1f }
    }
    class Pools(var halide: Float=0f,var activation: Float=0f,var silver: Float=0f,var coupler: Float=0f,var dye: Float=0f,var bleached: Float=0f)
    class Projection(var firstSilver: Float=0f,var firstColorDye: Float=0f)
    fun apply(p: Pools,hasDye: Boolean,localRate: Float,layer: Int=0,firstRate: Float=1f,colorRate: Float=1f,projection: Projection?=null) {
        projection?.let { it.firstSilver=0f;it.firstColorDye=0f }
        for(i in steps.indices) {
            val action=actions[i]
            var f=(fractions[i]*if(action<=1) localRate else 1f).coerceIn(0f,1f)
            if(i==firstDevelopment) f=(f*firstRate).coerceIn(0f,1f)
            if(i==firstColor) f=(f*colorRate).coerceIn(0f,1f)
            when(action) {
                0,1->{
                    var a=p.activation.coerceIn(0f,1f)
                    var rate=f
                    if(interactive) {
                        if(activationLedger) {
                            // Chemical fog explicitly activates previously inactive halide.
                            a=(a+fog[i]*f*(1-a)).coerceIn(0f,1f)
                            // Contrast changes conversion kinetics, never the reactive inventory.
                            val exponent=gammaExponent[i]
                            if(exponent!=0f) rate*=a.coerceAtLeast(1e-6f).pow(exponent)
                        } else if(i==firstDevelopment) a=(a+fog[i]*(1-a)).coerceIn(0f,1f).pow(gamma[i])
                        rate=(rate*(1-compensation[i]*a)*(if(mismatch) balance[layer.coerceAtMost(2)] else 1f)).coerceIn(0f,1f)
                    }
                    val reactive=p.halide*a
                    val n=reactive*rate;p.halide=max(0f,p.halide-n);p.silver+=n
                    if(i==firstDevelopment) projection?.firstSilver=n
                    if(activationLedger) {
                        val remaining=max(0f,reactive-n)
                        p.activation=if(p.halide>1e-8f) (remaining/p.halide).coerceIn(0f,1f) else 0f
                    }
                    if(action==1 && hasDye) { val c=min(p.coupler,n*dyeCoupling);p.coupler=max(0f,p.coupler-c);p.dye+=c }
                }
                2->p.activation+=(1-p.activation)*f
                3->{ val n=p.silver*f;p.silver=max(0f,p.silver-n);p.bleached+=n }
                5->p.silver*=1-f
                4->{p.halide*=1-f;p.bleached*=1-f;if(activationLedger && p.halide<=1e-8f) p.activation=0f}
                6->p.dye*=1-f
                7->if(interactive) p.dye*=1-f*maskDyeDamage
            }
            if(interactive) p.dye*=1-localDyeDamage[i]
            if(projection!=null && (i==firstColor || firstColor<0 && i==firstDevelopment)) {
                if(i==firstColor) projection.firstColorDye=p.dye
                return
            }
        }
    }
    fun record()=mapOf("model" to model,"program_key" to key,"process_steps" to steps,"effective_fractions" to fractions.toList(),
        "conditions" to recipe.filterKeys { it in listOf("time_min","temperature_c","concentration","agitation","agitation_time_s") },"agitation_time_unit" to "total_seconds_per_bath") +
        (if(interactive) mapOf("material_mismatch" to mismatch,"material_layer_balance" to balance.toList(),"bath_character" to baths.map { it.record() },"development_reference" to "standard_bath_normalized") else emptyMap()) +
        (if(activationLedger) mapOf("activation_ledger" to "remaining_developable_halide_v1") else emptyMap())
    /** Conditions feed formation effects before row execution; explicit incident values take priority. */
    fun effectsRecipe(original: Map<String,Any?>): Map<String,Any?> {
        if(!interactive) return original
        val active=steps.indices.filter { actions[it]<=1 }.map { baths[it] }.ifEmpty { listOf(PhoneBathCharacter(recipe)) }
        val character=active.first()
        val temperatures=baths.indices.filter { steps[it]["label"] !in listOf("material_process_dye_loss","explicit_dye_damage") }.map { baths[it].n("temperature_c",20.0) }
        val shock=temperatures.zipWithNext { a,b->abs(a-b) }.maxOrNull() ?: 0.0
        val conditions=recipe.toMutableMap()
        if("uneven_development" !in conditions) conditions["uneven_development"]=(.35*character.exhaustion+.16*max(0.0,1-character.agitation)).coerceIn(0.0,1.0)
        if("chemical_stain" !in conditions) conditions["chemical_stain"]=(character.exhaustion*.28).coerceIn(0.0,1.0)
        if("reticulation_strength" !in conditions) conditions["reticulation_strength"]=((shock-8)/28).coerceIn(0.0,1.0)
        return original+mapOf("develop" to conditions,"phone_bath_grain" to mapOf("strength" to active.map { it.grain }.average(),
            "radius" to active.map { it.radius }.average(),"clump" to active.map { it.clump }.average(),"model" to model))
    }
    companion object {
        const val MODEL="phone_ordered_pool_process_v1"
        fun completion(conditions: Map<String,Any?>): Float {
            ProcessSteps.validateConditions(conditions)
            fun n(key: String,fallback: Double)=(conditions[key] as? Number)?.toDouble() ?: fallback
            val minutes=n("time_min",8.0);val seconds=(conditions["agitation_time_s"] as? Number)?.toDouble()
            require(seconds==null || seconds<=minutes*60) { "agitation time exceeds bath duration" }
            val agitation=n("agitation",1.0)*(if(seconds==null) 1.0 else seconds/(minutes*60))
            val activity=2.05.pow((n("temperature_c",20.0)-20)/10)*n("concentration",1.0).pow(.92)*(.62+.38*agitation)
            return (1-exp(-minutes/8*activity)).toFloat().coerceIn(0f,1f)
        }
        fun from(develop: Map<String,Any?>,color: Boolean,sourcePositive: Boolean,material: Map<String,Any?> = emptyMap(),interactive: Boolean=false,
            activationLedger: Boolean=interactive,spatialModel: Boolean=false): PhoneProcessProgram? {
            if("program_key" !in develop && develop["process_steps"]==null && develop["mask_bleach_completion"]==null && !activationLedger) return null
            ProcessSteps.validate(develop)
            if(interactive) ProcessSteps.validateMaterial(material)
            val requested=develop["program_key"]?.toString() ?: "auto"
            val key=if(develop["process_steps"]!=null) "custom" else if(requested=="auto") {
                val positive=if(interactive && develop["chemistry"]=="cross") !sourcePositive else sourcePositive || color && develop["chemistry"]=="cross"
                if(color) { if(positive) "color_reversal" else "color_negative" } else if(positive) "bw_reversal" else "bw_negative"
            } else requested
            val steps=(ProcessSteps.read(develop["process_steps"]) ?: ProcessSteps.template(key)).toMutableList()
            (develop["mask_bleach_completion"] as? Number)?.toDouble()?.takeIf { it>0.0 }?.let { strength ->
                steps.add(mapOf("action" to "bleach_mask_dye","strength" to strength,"label" to "additional_mask_bleach"))
            }
            val positive=steps.any { it["action"]=="activate_remaining_halide" && ((it["strength"] as? Number)?.toDouble() ?: 1.0)>0 }
            val colorProgram=steps.any { it["action"]=="develop_color" }
            val mismatch=positive!=sourcePositive || colorProgram!=color
            if(interactive) {
                fun damage(value: Double,label: String) { if(value>0 && colorProgram) {
                    val index=steps.indexOfLast { it["action"]=="develop_color" }+1
                    steps.add(index,mapOf("action" to "destroy_dye","strength" to value.coerceIn(0.0,1.0),"label" to label))
                } }
                if(mismatch) damage(1-((material["cross_process_dye_stability"] as? Number)?.toDouble() ?: 1.0),"material_process_dye_loss")
                damage((develop["dye_damage_strength"] as? Number)?.toDouble() ?: 0.0,"explicit_dye_damage")
            }
            return PhoneProcessProgram(key,steps,develop,material,interactive,mismatch,activationLedger,spatialModel)
        }
    }
}
