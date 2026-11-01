package com.filmfoundry.app.ui

/** Common lamp range in reciprocal kelvin; numeric entry retains the wider valid range. */
internal object TemperatureScale {
    const val STEPS=1000
    fun kelvin(progress: Int): Double {
        val p=progress.coerceIn(0,STEPS).toDouble()/STEPS
        val k=1.0/(1.0/2000+(1.0/10000-1.0/2000)*p)
        return kotlin.math.round(k/50)*50
    }
    fun progress(kelvin: Double)=kotlin.math.round((1.0/kelvin.coerceIn(2000.0,10000.0)-1.0/2000)/(1.0/10000-1.0/2000)*STEPS).toInt().coerceIn(0,STEPS)
}
