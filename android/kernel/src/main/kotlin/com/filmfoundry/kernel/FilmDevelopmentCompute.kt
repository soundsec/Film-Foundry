package com.filmfoundry.kernel

/** Optional numeric port: one upload runs all ordered baths; null requests the CPU reference.
 * Inputs are task-owned copies. No latent, platform or UI ownership crosses this interface. */
interface FilmDevelopmentCompute {
    val minimumSamples: Int get()=0
    fun available(plan: FilmDevelopmentPlan): Boolean = true
    fun develop(input: FloatArray, plan: FilmDevelopmentPlan): FloatArray?
    fun developPrepared(input: FloatArray,plan: FilmDevelopmentPlan,preparationNanos: Long): FloatArray? = develop(input,plan)
}

/** Eight floats per sample: H, activation, silver, coupler, dye, bleached H, local rate, layer.
 * Six output pools, before optical grain and residual-light exposure. Arithmetic stays FP32. */
class FilmDevelopmentPlan internal constructor(private val program: PhoneProcessProgram, val color: Boolean,
    coefficients: FloatArray) {
    private val parameters=coefficients.copyOf()
    fun coefficients()=parameters.copyOf()
    fun reference(input: FloatArray): FloatArray {
        require(input.size%8==0)
        val result=FloatArray(input.size/8*6);val p=PhoneProcessProgram.Pools()
        for(n in 0 until input.size/8) {
            val i=n*8;val o=n*6
            p.halide=input[i];p.activation=input[i+1];p.silver=input[i+2]
            p.coupler=input[i+3];p.dye=input[i+4];p.bleached=input[i+5]
            program.apply(p,color,input[i+6],input[i+7].toInt())
            result[o]=p.halide;result[o+1]=p.activation;result[o+2]=p.silver
            result[o+3]=p.coupler;result[o+4]=p.dye;result[o+5]=p.bleached
        }
        return result
    }
    companion object {
        /** Same prepared current pool operators used by lightweight film rows. */
        fun prepare(develop: Map<String,Any?>,material: Map<String,Any?>,color: Boolean,positive: Boolean=false): FilmDevelopmentPlan =
            requireNotNull(PhoneProcessProgram.from(develop,color,positive,material,true,true,true)).computePlan(color)
    }
}
