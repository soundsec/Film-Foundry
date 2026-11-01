package com.filmfoundry.platform

import android.content.Context
import android.hardware.*
import com.filmfoundry.workflow.AngularAgitation

/** Explicit foreground recording only. Call close on dialog dismissal or activity stop. */
class GyroscopeAgitationRecorder(context: Context): SensorEventListener,AutoCloseable {
    private val manager=context.getSystemService(Context.SENSOR_SERVICE) as? SensorManager
    private val sensor=manager?.getDefaultSensor(Sensor.TYPE_GYROSCOPE)
    val available get()=sensor!=null
    private var accumulator=AngularAgitation()
    var recording=false
        private set
    fun start(): Boolean {
        close();accumulator=AngularAgitation()
        recording=runCatching { manager!=null && sensor!=null && manager.registerListener(this,sensor,SensorManager.SENSOR_DELAY_GAME) }.getOrDefault(false)
        return recording
    }
    fun summary()=accumulator.summary()
    override fun onSensorChanged(event: SensorEvent) {
        if(recording && event.sensor.type==Sensor.TYPE_GYROSCOPE && event.values.size>=3)
            accumulator.add(event.timestamp,event.values[0].toDouble(),event.values[1].toDouble(),event.values[2].toDouble())
    }
    override fun onAccuracyChanged(sensor: Sensor?,accuracy: Int)=Unit
    override fun close() { recording=false;manager?.unregisterListener(this) }
}
