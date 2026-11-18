package com.filmfoundry.platform

import com.filmfoundry.kernel.FilmDevelopmentCompute
import com.filmfoundry.kernel.FilmDevelopmentPlan
import java.util.concurrent.Executors
import kotlin.math.abs

/** Worker-owned qualification. Timings include upload/readback and bounded copies. */
class QualifiedFilmDevelopmentCompute(private val backend: SrgbRowCompute,
    private val report: (String)->Unit={},private val selectByTiming: Boolean=true,
    private val clockNs: ()->Long=System::nanoTime): FilmDevelopmentCompute,AutoCloseable {
    private var qualified: FilmDevelopmentPlan?=null
    private var declined: FilmDevelopmentPlan?=null
    private var failed=false
    private var lastReport: String?=null
    override fun available(plan: FilmDevelopmentPlan)=!failed && declined!==plan
    override fun develop(input: FloatArray,plan: FilmDevelopmentPlan)=developPrepared(input,plan,0L)
    override fun developPrepared(input: FloatArray,plan: FilmDevelopmentPlan,preparationNanos: Long): FloatArray? {
        require(preparationNanos>=0)
        require(input.size%8==0 && input.all { it.isFinite() && it>=0f })
        if(!available(plan))return null
        fun run(rows: FloatArray): FloatArray {
            val output=FloatArray(rows.size/8*6)
            var start=0
            while(start<rows.size) {
                val end=minOf(rows.size,start+MAX_SAMPLES*8)
                val part=backend.develop(rows.copyOfRange(start,end),plan) ?: error("Formation compute unavailable")
                require(part.size==(end-start)/8*6 && part.all { it.isFinite() && it>=0f }) { "Invalid GPU pools" }
                // Material mass cannot grow. Dye destruction/removal can decrease its ledger.
                for(n in part.indices step 6) {
                    val i=start+n/6*8
                    require(part[n+1]<=1.00001f && part[n]+part[n+2]+part[n+5]<=rows[i]+rows[i+2]+rows[i+5]+2e-5f &&
                        part[n+3]+part[n+4]<=rows[i+3]+rows[i+4]+2e-5f) { "GPU formation inventory differs" }
                }
                part.copyInto(output,start/8*6);start=end
            }
            return output
        }
        fun verify(expected: FloatArray,actual: FloatArray) {
            require(actual.size==expected.size && actual.indices.all { abs(actual[it]-expected[it])<=3e-6f+abs(expected[it])*3e-5f }) {
                "GPU formation differs from CPU"
            }
        }
        try {
            if(qualified!==plan) {
                val probe=FloatArray(192*8)
                val edges=floatArrayOf(0f,1e-8f,1e-6f,.001f,.05f,.5f,.95f,1f)
                for(n in 0 until 192) {
                    val i=n*8;probe[i]=if(n%4==0) 0f else 1f
                    probe[i+1]=edges[n%8];probe[i+2]=.1f;probe[i+3]=if(n%5==0) 0f else .8f
                    probe[i+4]=.2f;probe[i+5]=.3f;probe[i+6]=if(n<96) 1f else .37f
                    probe[i+7]=(n%3).toFloat()
                }
                verify(plan.reference(probe),run(probe))
                // Verify real rows too; qualify a whole program, not just one transfer function.
                verify(plan.reference(input),run(input))
                if(selectByTiming) {
                    val gpuStart=clockNs();val result=run(input);val gpu=clockNs()-gpuStart+preparationNanos
                    var cpu=Long.MAX_VALUE
                    repeat(2) { val start=clockNs();plan.reference(input);cpu=minOf(cpu,clockNs()-start) }
                    qualified=plan
                    if(gpu>=cpu*.85) {
                        declined=plan;publish("cpu_film_development_fp32 · transfer timing GPU ${gpu/1e6} ms / CPU ${cpu/1e6} ms")
                        return null
                    }
                    publish("gles31_film_development_fp32 · GPU ${gpu/1e6} ms / CPU ${cpu/1e6} ms")
                    return result
                }
                qualified=plan
            }
            return run(input).also { publish("gles31_film_development_fp32") }
        } catch(error: Exception) {
            failed=true;publish("cpu_film_development_fp32 · fallback: ${error.message}");return null
        }
    }
    private fun publish(value: String) { if(lastReport!=value) { lastReport=value;runCatching { report(value) } } }
    override fun close()=backend.close()
    companion object { const val MAX_SAMPLES=8192 }
}

/** One bounded GPU queue shared by the two row producers; EGL creation and destruction stay
 * on the same worker. At most one submitted block per producer is retained. CPU mode opens no EGL. */
class QueuedFilmDevelopmentCompute(private val requested: ()->Boolean,private val report: (String)->Unit={}): AutoCloseable {
    fun workflow(): com.filmfoundry.workflow.WorkflowFacade = com.filmfoundry.workflow.DefaultWorkflowFacade(
        com.filmfoundry.kernel.PhoneReducedKernel(object: FilmDevelopmentCompute {
            override val minimumSamples=4096
            override fun available(plan: FilmDevelopmentPlan)=this@QueuedFilmDevelopmentCompute.available(plan)
            override fun develop(input: FloatArray,plan: FilmDevelopmentPlan)=this@QueuedFilmDevelopmentCompute.develop(input,plan,0L)
            override fun developPrepared(input: FloatArray,plan: FilmDevelopmentPlan,preparationNanos: Long)=this@QueuedFilmDevelopmentCompute.develop(input,plan,preparationNanos)
        }))
    private var worker: java.util.concurrent.ExecutorService?=null
    private var backend: QualifiedFilmDevelopmentCompute?=null // GPU-thread owned
    @Volatile private var declined: FilmDevelopmentPlan?=null
    @Volatile private var failed=false
    private fun available(plan: FilmDevelopmentPlan)=requested() && !failed && declined!==plan
    private fun develop(input: FloatArray,plan: FilmDevelopmentPlan,preparationNanos: Long): FloatArray? {
        if(!available(plan)) return null
        val executor=synchronized(this) { worker ?: Executors.newSingleThreadExecutor { job ->
            Thread(job,"film-formation-gpu").apply { isDaemon=true }
        }.also { worker=it } }
        return executor.submit<FloatArray?> {
            if(!available(plan)) return@submit null
            try {
                val compute=backend ?: QualifiedFilmDevelopmentCompute(GlesSrgbRowCompute(),report).also { backend=it }
                compute.developPrepared(input,plan,preparationNanos).also { if(it==null) declined=plan }
            } catch(error: Exception) { failed=true;runCatching { report("cpu_film_development_fp32 · fallback: ${error.message}") };null }
        }.get()
    }
    /** Caller drains row jobs before releasing the task-owned queue. */
    override fun close() {
        val executor=synchronized(this) { worker.also { worker=null } } ?: return
        try { executor.submit { backend?.close();backend=null }.get() }
        finally { executor.shutdown();declined=null;failed=false }
    }
}
