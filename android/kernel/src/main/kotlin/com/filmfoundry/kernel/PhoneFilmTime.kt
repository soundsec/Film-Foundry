package com.filmfoundry.kernel

import kotlin.math.*

/** Explicit clocks, prepared once per task. No wall time or scan-side ageing.
 * Shared film_time / film_exposure fields; phone retains scalar layer kinetics,
 * omitting surface-salt pools and history-dependent grain morphology.
 */
internal class PhoneFilmTime(film: Map<String,Any?>, exposure: Map<String,Any?>,
    develop: Map<String,Any?>, private val layers: Int) {
    val speed=FloatArray(layers) { 1f }
    val storageFog=FloatArray(layers)
    private val retention=FloatArray(layers) { 1f }
    private val latentFog=FloatArray(layers)
    private val printout=FloatArray(layers)
    private val bleachedPrintout=FloatArray(layers)
    val record: Map<String,Any?>
    val hasProcessing: Boolean

    init {
        require(layers==1 || layers==3)
        val response=obj(film["time_response"])
        val storage=history(film["storage_history"],"storage")
        val waiting=history(develop["latent_wait"],"latent")
        val light=history(develop["post_process_light"],"light")
        require(response!=null || storage==null && waiting==null && light==null) { "Film time history requires a material time_response" }
        hasProcessing=waiting!=null || light!=null
        if(response!=null) {
            val vectors=setOf("storage_speed_loss_stops_per_year","storage_fog_rate_per_year","latent_fade_rate_per_day","latent_fog_rate_per_day","printout_rate_per_light_hour")
            val scalars=setOf("temperature_q10","humidity_sensitivity","bleached_printout_ratio","printout_grain_factor","printout_grain_radius_factor","printout_grain_clump_factor")
            require(response["model"] in listOf("film_time_response_v1","film_time_response_v2") && response.keys.all { it=="model" || it in vectors || it in scalars }) { "Unsupported film time response" }
            val q10=number(response["temperature_q10"] ?: 2.0,1.0,4.0)
            val humidity=number(response["humidity_sensitivity"] ?: 1.0,0.0,2.0)
            val bleachRatio=number(response["bleached_printout_ratio"] ?: .3,0.0,2.0)
            number(response["printout_grain_factor"] ?: .35,0.0,4.0)
            number(response["printout_grain_radius_factor"] ?: .7,.05,4.0)
            number(response["printout_grain_clump_factor"] ?: .5,.05,4.0)
            fun environment(row: Map<String,Any?>)=q10.pow((number(row["temperature_c"],-40.0,60.0)-20)/10)*
                max(.1,1+humidity*(number(row["relative_humidity"],0.0,1.0)-.5))
            val years=storage.orEmpty().sumOf { number(it["duration_days"],0.0,36500.0)/365*environment(it) }
            val speedLoss=vector(response["storage_speed_loss_stops_per_year"],listOf(.5,.35,.25))
            val fog=vector(response["storage_fog_rate_per_year"],listOf(.025,.020,.015))
            val fade=vector(response["latent_fade_rate_per_day"],listOf(.006,.004,.003))
            val fogRate=vector(response["latent_fog_rate_per_day"],listOf(.0004,.00035,.0003))
            val lightRate=vector(response["printout_rate_per_light_hour"],listOf(.04,.035,.03))
            for(l in 0 until layers) {
                speed[l]=2.0.pow(-min(100.0,speedLoss[l]*years)).toFloat()
                storageFog[l]=(-expm1(-fog[l]*years)).toFloat()
                var a=1.0;var b=0.0
                for(row in waiting.orEmpty()) {
                    val days=number(row["duration_hours"],0.0,876000.0)/24*environment(row)
                    val sum=fade[l]+fogRate[l];val f=-expm1(-sum*days)
                    a*=1-f;b=b*(1-f)+(if(sum>0) fogRate[l]/sum else 0.0)*f
                }
                retention[l]=a.toFloat();latentFog[l]=b.toFloat()
                var dose=0.0
                for(row in light.orEmpty()) dose+=number(row["duration_hours"],0.0,876000.0)*
                    number(row["relative_illuminance"],0.0,10000.0)*vector(row["light_rgb"],listOf(1.0,1.0,1.0),4.0)[l]
                dose*=lightRate[l]
                printout[l]=(-expm1(-dose)).toFloat();bleachedPrintout[l]=(-expm1(-dose*bleachRatio)).toFloat()
            }
        }
        val duration=exposure["duration_seconds"]?.let { number(it,1e-9,86400.0) }
        val reciprocity=obj(film["reciprocity_response"])
        if(reciprocity!=null) {
            require(reciprocity["model"]=="film_reciprocity_response_v1" && reciprocity.keys.all { it in setOf("model","short_limit_seconds","long_limit_seconds","short_loss_per_stop","long_loss_per_stop") })
            val short=number(reciprocity["short_limit_seconds"] ?: .0001,1e-9,86400.0)
            val long=number(reciprocity["long_limit_seconds"] ?: 1.0,1e-9,86400.0);require(short<=long)
            val shortLoss=vector(reciprocity["short_loss_per_stop"],listOf(.05,.05,.05),.95)
            val longLoss=vector(reciprocity["long_loss_per_stop"],listOf(.15,.20,.25),.95)
            if(duration!=null) {
                val stops=when { duration<short -> log2(short/duration);duration>long -> log2(duration/long);else -> 0.0 }
                val loss=if(duration<short) shortLoss else longLoss
                for(l in 0 until layers) speed[l]*=2.0.pow(-stops*loss[l]).toFloat()
            }
        }
        record=preparedValues(mapOf("model" to "phone_film_time_v1","clock" to "explicit_durations_only",
            "time_response" to response,"storage" to film["storage_history"],"latent_wait" to develop["latent_wait"],
            "post_process_light" to develop["post_process_light"],"duration_seconds" to duration,
            "reciprocity_response" to reciprocity,"exposure_efficiency" to speed.toList(),
            "omitted" to listOf("surface_salt_printout","per_bath_grain_history")))
    }
    fun exposed(activation: Float,layer: Int)=activation+(1-activation)*storageFog[layer]
    fun aged(activation: Float,layer: Int)=(activation*retention[layer]+latentFog[layer]).coerceIn(0f,1f)
    fun finish(p: PhoneProcessProgram.Pools,layer: Int) {
        val h=p.halide*printout[layer];val b=p.bleached*bleachedPrintout[layer]
        p.halide-=h;p.bleached-=b;p.silver+=h+b
        if(p.halide<=0f) p.activation=0f
    }
    private fun vector(value: Any?,fallback: List<Double>,high: Double=10.0): DoubleArray {
        val raw=value ?: fallback;require(raw is List<*> && raw.size==3)
        val values=raw.map { number(it,0.0,high) }
        return if(layers==1) doubleArrayOf(values.average()) else values.toDoubleArray()
    }
    private fun history(value: Any?,kind: String): List<Map<String,Any?>>? {
        val h=obj(value) ?: return null
        val model=when(kind) { "storage"->"film_storage_history_v1";"latent"->"film_latent_wait_v1";else->"film_residual_light_v1" }
        require(h.keys==setOf("model","segments") && h["model"]==model)
        val rows=h["segments"];require(rows is List<*> && rows.size in 1..32)
        return rows.map { row ->
            val r=obj(row) ?: error("Missing time segment")
            val keys=when(kind) { "storage"->setOf("duration_days","temperature_c","relative_humidity");"latent"->setOf("duration_hours","temperature_c","relative_humidity");else->setOf("duration_hours","relative_illuminance","light_rgb") }
            require(r.keys==keys);r
        }
    }
    private fun obj(value: Any?): Map<String,Any?>? {
        if(value==null)return null
        require(value is Map<*,*> && value.keys.all { it is String })
        @Suppress("UNCHECKED_CAST") return value as Map<String,Any?>
    }
    private fun number(value: Any?,low: Double,high: Double): Double {
        require(value is Number && value.toDouble().isFinite() && value.toDouble() in low..high) { "Invalid film time parameter ($low..$high)" }
        return value.toDouble()
    }
}
