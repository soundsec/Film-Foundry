package com.filmfoundry.app.session

import android.content.Context
import android.content.Intent
import android.content.IntentFilter
import android.os.*
import com.filmfoundry.interchange.RecipeJson
import java.io.File
import java.time.Instant
import java.util.UUID
import java.util.concurrent.Executors
import java.util.concurrent.TimeUnit

/** Opt-in, local measurements of actual jobs, independent of Activity and image ownership. */
class DevicePerformance(context: Context) {
    private val app=context.applicationContext
    private val prefs=app.getSharedPreferences("performance_test",0)
    private val store=PerformanceTraceStore(File(app.filesDir,"performance_test"))
    var enabled: Boolean
        get()=prefs.getBoolean("enabled",false)
        set(value) { prefs.edit().putBoolean("enabled",value).apply() }
    var testHandoff: Boolean
        get()=prefs.getBoolean("optical_handoff",true)
        set(value) { prefs.edit().putBoolean("optical_handoff",value).apply() }
    val handoffEnabled get()=!enabled || testHandoff
    @Volatile var lastError: String?=null;private set
    fun records()=store.records(activeId)
    fun clear() { check(activeId==null);store.clear();lastError=null }
    fun export(): String=RecipeJson.text(mapOf("schema" to "film_foundry_performance_export_v1","exported_at" to Instant.now().toString(),
        "notes" to listOf("Timings may be nested; do not sum them as wall time.","material_compute is worker time, row_pipeline_wall is elapsed time.",
            "process_cpu_ms includes other operations in this app.","Resources sampled every 5 seconds; peaks are sampled, not absolute.",
            "cpu_workers is the configured maximum; legacy or small row kernels may use fewer.",
            "scan_output describes rendered pixels before image publication; saved events follow commit.",
            "interrupted records contain only the last checkpoint."),"runs" to records()))
    fun begin(workers: Int,gpu: Boolean,budget: Long): Run? {
        if(!enabled) return null
        lastError=null
        return runCatching {
            val info=app.packageManager.getPackageInfo(app.packageName,0)
            val runtime=Runtime.getRuntime()
            val trace=PerformanceTrace(UUID.randomUUID().toString(),mapOf("started_at" to Instant.now().toString(),
                "build" to info.versionName,"app_version" to info.versionName,"sdk" to Build.VERSION.SDK_INT,
                "device" to "${Build.MANUFACTURER} ${Build.MODEL}","cpu_cores" to runtime.availableProcessors(),
                "cpu_workers" to workers,"gpu_requested" to gpu,"optical_handoff" to handoffEnabled,
                "initial_row_budget_bytes" to budget,"heap_limit_bytes" to runtime.maxMemory()),SystemClock::elapsedRealtime)
            Run(trace).also { it.start();activeId=trace.id }
        }.onFailure { lastError=it.javaClass.simpleName }.getOrNull()
    }
    inner class Run internal constructor(private val trace: PerformanceTrace) {
        private val cpuStart=android.os.Process.getElapsedCpuTime()
        private val persistence=Any()
        @Volatile private var stopped=false
        private val sampler=Executors.newSingleThreadScheduledExecutor { work -> Thread({
            android.os.Process.setThreadPriority(android.os.Process.THREAD_PRIORITY_BACKGROUND);work.run()
        },"FilmFoundry-performance").apply { isDaemon=true } }
        internal fun start() {
            sample();checkpoint()
            sampler.scheduleWithFixedDelay({ if(!stopped) { sample();checkpoint() } },5,5,TimeUnit.SECONDS)
        }
        fun timing(command: String,section: String,nanos: Long)=trace.timing(command,section,nanos)
        fun frame(phase: String,index: Int,total: Int)=trace.event(phase,index,total)
        fun photo(format: String,width: Int,height: Int,bits: Int,quality: Int)=trace.event("${format}_saved",width=width,height=height,bits=bits,quality=quality)
        fun output(format: String,width: Int,height: Int,bits: Int,quality: Int)=trace.event("scan_output",width=width,height=height,bits=bits,quality=quality,format=format)
        private fun sample() { runCatching {
            val runtime=Runtime.getRuntime();val power=app.getSystemService(Context.POWER_SERVICE) as PowerManager
            val metrics=linkedMapOf<String,Number>("java_heap_bytes" to runtime.totalMemory()-runtime.freeMemory(),
                "native_heap_bytes" to Debug.getNativeHeapAllocatedSize(),"process_cpu_ms" to (android.os.Process.getElapsedCpuTime()-cpuStart).coerceAtLeast(0),
                "screen_interactive" to if(power.isInteractive) 1 else 0)
            if(Build.VERSION.SDK_INT>=29) metrics["thermal_status"]=power.currentThermalStatus
            app.registerReceiver(null,IntentFilter(Intent.ACTION_BATTERY_CHANGED))?.let { battery ->
                if(battery.hasExtra(BatteryManager.EXTRA_TEMPERATURE)) metrics["battery_c"]=battery.getIntExtra(BatteryManager.EXTRA_TEMPERATURE,0)/10.0
                val level=battery.getIntExtra(BatteryManager.EXTRA_LEVEL,-1);val scale=battery.getIntExtra(BatteryManager.EXTRA_SCALE,-1)
                if(level>=0 && scale>0) metrics["battery_percent"]=level*100.0/scale
            }
            trace.sample(metrics)
        }.onFailure { lastError=it.javaClass.simpleName };Unit }
        private fun checkpoint() { synchronized(persistence) {
            runCatching { store.save(trace) }.onFailure { lastError=it.javaClass.simpleName }
        } }
        fun finish(status: String,error: Throwable?=null) {
            if(stopped) return
            stopped=true;sampler.shutdownNow()
            sample();trace.finish(status,error?.javaClass?.simpleName);checkpoint()
            if(activeId==trace.id) activeId=null
        }
    }
    companion object { @Volatile private var activeId: String?=null }
}
