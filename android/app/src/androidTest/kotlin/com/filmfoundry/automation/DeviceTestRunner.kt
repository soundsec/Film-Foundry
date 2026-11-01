package com.filmfoundry.automation

import android.app.Activity
import android.app.Instrumentation
import android.app.UiAutomation
import android.content.Context
import android.content.ContextWrapper
import android.content.Intent
import android.graphics.BitmapFactory
import android.os.*
import android.opengl.EGL14 as EGL
import android.view.View
import android.view.ViewGroup
import android.widget.Button
import com.filmfoundry.app.session.DevicePerformance
import com.filmfoundry.app.testing.AutomationFixtureActivity
import com.filmfoundry.app.ui.GalleryQuery
import com.filmfoundry.interchange.*
import com.filmfoundry.kernel.*
import com.filmfoundry.platform.*
import com.filmfoundry.workflow.*
import kotlinx.coroutines.runBlocking
import java.io.File
import java.nio.ByteBuffer
import java.nio.file.Files
import java.nio.file.StandardCopyOption
import java.util.Collections
import java.util.concurrent.Executors
import java.util.concurrent.TimeUnit
import java.util.concurrent.CountDownLatch
import java.util.concurrent.atomic.AtomicBoolean

/** No external test libraries: the Android instrumentation process owns only synthetic fixtures. */
class DeviceTestRunner : Instrumentation() {
    private lateinit var args: Bundle
    private lateinit var report: File
    private var fixture: AutomationFixtureActivity? = null
    private val cases = mutableListOf<Map<String, Any?>>()
    private val traces = mutableListOf<Map<String, Any?>>()
    private val gpuProbes = mutableListOf<Map<String,Any?>>()
    private var gpuDriver: Map<String,Any?> = emptyMap()
    private var hardwareSelfTest: Map<String,Any?> = emptyMap()
    private var state = "running"
    private var phase = "starting"
    private val began = SystemClock.elapsedRealtime()

    override fun onCreate(arguments: Bundle?) { super.onCreate(arguments); args = arguments ?: Bundle(); start() }

    override fun onStart() {
        val runId = args.getString("run_id") ?: "run-${System.currentTimeMillis()}"
        require(runId.matches(Regex("[A-Za-z0-9_-]{1,64}")))
        val profile = args.getString("profile") ?: "quick"
        require(profile in listOf("quick", "full", "gpu_probe", "hardware_self_test", "capture_ingest", "viewer_ui", "capture_ui"))
        report = File(targetContext.filesDir, "automation/results/$runId.json")
        val workspace = File(targetContext.cacheDir, "automation/$runId").canonicalFile
        var isolated: FixtureContext? = null
        var wake: PowerManager.WakeLock? = null
        try {
            wake=targetContext.getSystemService(PowerManager::class.java)
                .newWakeLock(PowerManager.PARTIAL_WAKE_LOCK,"FilmFoundry:automation").apply {
                    setReferenceCounted(false)
                    acquire(33*60*1000L) // Bounded even if the host disconnects; normal exit releases immediately.
                }
            check(!workspace.exists()) { "Run ID already used" }
            check(workspace.mkdirs())
            isolated = FixtureContext(targetContext, workspace)
            checkpoint()
            if(profile=="capture_ingest") {
                CaptureIngestProbe.run(context,isolated.filesDir) { name,action -> verify(name,action) }
                state="passed"
                return
            }
            if(profile=="gpu_probe") {
                verify("gpu_numeric_probe") { gpuProbe() }
                state="passed"
                return
            }
            phase="fixture_launch"; checkpoint()
            val automation=getUiAutomation(UiAutomation.FLAG_DONT_SUPPRESS_ACCESSIBILITY_SERVICES)
            val monitor=addMonitor(AutomationFixtureActivity::class.java.name,null,false)
            try {
                automation.adoptShellPermissionIdentity("android.permission.START_ACTIVITIES_FROM_BACKGROUND")
                targetContext.startActivity(Intent(targetContext,AutomationFixtureActivity::class.java)
                    .putExtra("landscape",profile=="capture_ui").addFlags(Intent.FLAG_ACTIVITY_NEW_TASK))
                fixture=waitForMonitorWithTimeout(monitor,15000) as? AutomationFixtureActivity
                    ?: error("Test page did not start within 15 seconds")
            } finally { removeMonitor(monitor); automation.dropShellPermissionIdentity() }
            val laidOut=CountDownLatch(1)
            onMain {
                val root=fixture!!.shell.root
                if(root.width>0) laidOut.countDown()
                else root.viewTreeObserver.addOnGlobalLayoutListener(object: android.view.ViewTreeObserver.OnGlobalLayoutListener {
                    override fun onGlobalLayout() { root.viewTreeObserver.removeOnGlobalLayoutListener(this);laidOut.countDown() }
                })
            }
            check(laidOut.await(10,TimeUnit.SECONDS)) { "Test page layout timed out" }
            check(targetContext.getSystemService(PowerManager::class.java).isInteractive) { "Wake the display before benchmarking" }
            if(profile=="capture_ui") {
                val deadline=SystemClock.elapsedRealtime()+5000
                while(fixture!!.resources.configuration.orientation!=android.content.res.Configuration.ORIENTATION_LANDSCAPE && SystemClock.elapsedRealtime()<deadline) Thread.sleep(50)
                captureUiChecks()
                state="passed"
                return
            }
            if(profile=="viewer_ui") {
                viewerUiChecks()
                state="passed"
                return
            }
            if(profile=="hardware_self_test") {
                hardwareSelfTestUi()
                state="passed"
                return
            }
            uiChecks()
            pipeline(isolated, if(profile == "full") 4096 else 768, if(profile == "full") 2730 else 512)
            state = if(cases.any { it["status"] == "failed" }) "failed" else "passed"
        } catch(error: Throwable) {
            if(cases.lastOrNull()?.let { it["name"]==phase && it["status"]=="failed" } != true)
                cases += mapOf("name" to phase, "status" to "failed", "error" to error.javaClass.simpleName,
                    "detail" to error.message?.take(240))
            state = "failed"
        } finally {
            wake?.let { if(it.isHeld) it.release() }
            fixture?.let { activity -> runCatching { onMain { activity.finish() } } }
            isolated?.clearPreferences()
            // Fixed, canonical descendant generated above; never follows or deletes user media paths.
            val allowed = File(targetContext.cacheDir, "automation").canonicalFile
            check(workspace.parentFile == allowed)
            if(workspace.exists() && !workspace.deleteRecursively()) {
                cases += mapOf("name" to "fixture_cleanup", "status" to "failed"); state = "failed"
            }
            phase = "finished"
            checkpoint()
            finish(if(state == "passed") Activity.RESULT_OK else Activity.RESULT_CANCELED,
                Bundle().apply { putString("stream", "\nFilm Foundry automation: $state; ${cases.size} checks\n") })
        }
    }

    private fun checkpoint() {
        report.parentFile!!.mkdirs()
        val content = mapOf("schema" to "film_foundry_device_automation_v1", "status" to state,
            "phase" to phase, "profile" to (args.getString("profile") ?: "quick"),
            "focused" to (args.getString("focused")=="true"),
            "gpu_requested" to (args.getString("gpu")=="true"),
            "scan_kind" to if(args.getString("spectral")=="true") "spectral_31" else "rgb",
            "gpu_probes" to gpuProbes.toList(),
            "gpu_driver" to gpuDriver,
            "hardware_self_test" to hardwareSelfTest,
            "build" to targetContext.packageManager.getPackageInfo(targetContext.packageName,0).versionName,
            "wall_ms" to SystemClock.elapsedRealtime()-began, "checks" to cases.toList(), "performance" to traces.toList(),
            "scope" to if(args.getString("profile")=="hardware_self_test") "Production self-test dialog and service lease, synthetic data and isolated preferences; no user media or visual acceptance."
                else "Synthetic component callbacks and real row pipeline; no camera, user library, MediaStore, service lifecycle or visual acceptance. Timings are nested; a single pass is not a benchmark.")
        val pending = File(report.parentFile, "${report.name}.pending")
        pending.writeText(RecipeJson.text(content), Charsets.UTF_8)
        Files.move(pending.toPath(), report.toPath(), StandardCopyOption.ATOMIC_MOVE, StandardCopyOption.REPLACE_EXISTING)
        sendStatus(0, Bundle().apply { putString("stream", "\n$phase ($state)\n") })
    }

    private fun captureUiChecks() {
        val activity=fixture!!
        val worker=Executors.newSingleThreadExecutor()
        var dialog: android.app.AlertDialog?=null
        var saved: Map<String,Any?>?=null
        val initial=mapOf<String,Any?>("imprint" to "Film Aa-01 @50%!","leak" to .2,"seed" to 17L)
        fun descendants(v: View): List<View> = listOf(v)+if(v is ViewGroup) (0 until v.childCount).flatMap { descendants(v.getChildAt(it)) } else emptyList()
        try {
            verify("landscape_imprint_preview_and_controls_fit_side_by_side") {
                onMain {
                    check(activity.resources.configuration.orientation==android.content.res.Configuration.ORIENTATION_LANDSCAPE)
                    dialog=com.filmfoundry.app.ui.CameraEffectsDialog(activity,activity.shell,worker).open(initial,emptyMap(),mapOf("format" to "135"),imprintOnly=true) { saved=it }
                }
                val deadline=SystemClock.elapsedRealtime()+15000
                var ready=false
                while(!ready && SystemClock.elapsedRealtime()<deadline) {
                    onMain { ready=descendants(dialog!!.window!!.decorView).filterIsInstance<android.widget.ImageView>().any { it.drawable is android.graphics.drawable.BitmapDrawable } }
                    if(!ready) Thread.sleep(50)
                }
                check(ready) { "Synthetic imprint preview did not render" }
                onMain {
                    val decor=dialog!!.window!!.decorView
                    val layout=decor.findViewWithTag<android.widget.LinearLayout>("camera_effects_layout")
                    check(layout.orientation==android.widget.LinearLayout.HORIZONTAL && layout.childCount==2)
                    val left=layout.getChildAt(0);val right=layout.getChildAt(1)
                    check(left.width>100 && right.width>100 && left.right<=right.left && left.height>100)
                    val image=descendants(left).filterIsInstance<android.widget.ImageView>().single()
                    check(image.height>100 && image.drawable!=null)
                    check(dialog!!.getButton(android.app.AlertDialog.BUTTON_POSITIVE).isShown)
                    val bitmap=android.graphics.Bitmap.createBitmap(decor.width,decor.height,android.graphics.Bitmap.Config.ARGB_8888)
                    decor.draw(android.graphics.Canvas(bitmap))
                    File(report.parentFile,"${report.nameWithoutExtension}-imprint.png").outputStream().use { bitmap.compress(android.graphics.Bitmap.CompressFormat.PNG,100,it) };bitmap.recycle()
                }
            }
            verify("imprint_custom_position_brightness_save_and_cancel") {
                onMain { descendants(dialog!!.window!!.decorView).filterIsInstance<Button>().single { it.text==activity.shell.copy("亮度位置","Placement") }.performClick() }
                waitForIdleSync()
                onMain {
                    val decor=dialog!!.window!!.decorView
                    val bitmap=android.graphics.Bitmap.createBitmap(decor.width,decor.height,android.graphics.Bitmap.Config.ARGB_8888)
                    decor.draw(android.graphics.Canvas(bitmap))
                    File(report.parentFile,"${report.nameWithoutExtension}-placement.png").outputStream().use { bitmap.compress(android.graphics.Bitmap.CompressFormat.PNG,100,it) };bitmap.recycle()
                    val controls=descendants(dialog!!.window!!.decorView).filterIsInstance<com.filmfoundry.app.ui.PrintPlacementControls>().single()
                    val bars=descendants(controls).filterIsInstance<android.widget.SeekBar>()
                    check(controls.isShown)
                    bars[0].progress=145;bars[1].progress=29;bars[2].progress=58
                    check(saved==null && initial["imprint_x"]==null)
                    dialog!!.getButton(android.app.AlertDialog.BUTTON_POSITIVE).performClick()
                    check(saved?.get("imprint_brightness")==1.45 && saved?.get("imprint_x")==.29 && saved?.get("imprint_y")==.58)
                    check(saved?.get("leak")==.2 && saved?.get("seed")==17L && saved?.get("imprint")=="Film Aa-01 @50%!")
                    dialog=com.filmfoundry.app.ui.CameraEffectsDialog(activity,activity.shell,worker).open(saved!!,emptyMap(),emptyMap(),imprintOnly=true) { error("Cancel must not save") }
                }
                onMain {
                    val controls=descendants(dialog!!.window!!.decorView).filterIsInstance<com.filmfoundry.app.ui.PrintPlacementControls>().single()
                    check(controls.brightness==1.45 && controls.x==.29 && controls.y==.58)
                    descendants(controls).filterIsInstance<Button>().last().performClick()
                    check(controls.x==1.0 && controls.y==1.0)
                    dialog!!.getButton(android.app.AlertDialog.BUTTON_NEGATIVE).performClick()
                }
            }
            verify("auto_advance_setting_is_draft_until_saved") {
                var auto=false
                onMain {
                    dialog=com.filmfoundry.app.ui.CameraEffectsDialog(activity,activity.shell,worker).open(initial,emptyMap(),emptyMap(),initialAutoAdvance=false,saveAutoAdvance={ auto=it }) { }
                }
                onMain {
                    val toggle=descendants(dialog!!.window!!.decorView).filterIsInstance<android.widget.CheckBox>().single { it.text==activity.shell.copy("自动过片","Auto advance") }
                    toggle.isChecked=true;check(!auto)
                    dialog!!.getButton(android.app.AlertDialog.BUTTON_POSITIVE).performClick();check(auto)
                }
            }
        } finally { onMain { dialog?.dismiss() };worker.shutdownNow() }
    }

    private fun viewerUiChecks() {
        fun onUi(name: String,work: ()->Unit)=verify(name) { onMain(work) }
        onUi("viewer_preview_hd_and_cold_page_never_clear_pixels") {
            val view=com.filmfoundry.app.ui.TouchPhotoView(fixture!!)
            val preview=android.graphics.Bitmap.createBitmap(20,20,android.graphics.Bitmap.Config.ARGB_8888).apply { eraseColor(android.graphics.Color.RED) }
            val detail=android.graphics.Bitmap.createBitmap(80,80,android.graphics.Bitmap.Config.ARGB_8888).apply { eraseColor(android.graphics.Color.GREEN) }
            val rendered=android.graphics.Bitmap.createBitmap(100,100,android.graphics.Bitmap.Config.ARGB_8888)
            fun pixel(): Int { rendered.eraseColor(android.graphics.Color.BLACK);view.draw(android.graphics.Canvas(rendered));return rendered.getPixel(50,50) }
            try {
                view.layout(0,0,100,100)
                val first=view.requestPage("photo:first");check(view.previewPage(first,preview));check(pixel()==android.graphics.Color.RED)
                check(view.presentPage(first,detail));check(pixel()==android.graphics.Color.GREEN)
                view.neighbors(first,null,preview,false,true)
                val pinned=view.neighborPreview(1);check(pinned===preview)
                val second=view.requestPage("photo:second");check(view.previewPage(second,pinned));check(pixel()==android.graphics.Color.RED)
                check(!view.presentPage(first,detail));check(pixel()==android.graphics.Color.RED)
                check(view.presentPage(second,detail))
                val cold=view.requestPage("photo:cold");check(view.previewPage(cold,null))
                check(view.displayedPage==second && view.pageLoading && pixel()==android.graphics.Color.GREEN)
                check(view.previewPage(cold,preview));check(pixel()==android.graphics.Color.RED)
                check(view.presentPage(cold,detail));check(pixel()==android.graphics.Color.GREEN)
            } finally { view.clearPage();view.dispose();preview.recycle();detail.recycle();rendered.recycle() }
        }
        onUi("oversized_hd_does_not_evict_next_thumbnail") {
            val cache=com.filmfoundry.app.ui.DisplayThumbnailCache()
            val preview=android.graphics.Bitmap.createBitmap(20,20,android.graphics.Bitmap.Config.ARGB_8888)
            val huge=android.graphics.Bitmap.createBitmap(1024,cache.budget/4096+1,android.graphics.Bitmap.Config.ARGB_8888)
            try {
                cache.put("photo:next",preview);cache.put("display:photo:current",huge)
                check(cache.get("photo:next")===preview && cache.get("display:photo:current")==null)
            } finally { cache.clear();preview.recycle();huge.recycle() }
        }
        onUi("ivory_viewer_safe_area_black_and_theme_restored") {
            val shell=com.filmfoundry.app.ui.MobileShell(fixture!!,true,"observe")
            val original=(shell.root.background as android.graphics.drawable.ColorDrawable).color
            check(original!=android.graphics.Color.BLACK)
            shell.select(com.filmfoundry.app.ui.MobileShell.Page.VIEWER)
            check((shell.root.background as android.graphics.drawable.ColorDrawable).color==android.graphics.Color.BLACK)
            shell.select(com.filmfoundry.app.ui.MobileShell.Page.OBSERVE)
            check((shell.root.background as android.graphics.drawable.ColorDrawable).color==original)
        }
    }

    private fun hardwareSelfTestUi() {
        val activity=fixture!!
        val preferences=activity.getSharedPreferences("hardware_self_test",Context.MODE_PRIVATE)
        val capture=activity.getSharedPreferences("capture_conditions",Context.MODE_PRIVATE)
        val previousCapture=capture.all.toMap()
        var ui: com.filmfoundry.app.ui.HardwareSelfTestUi?=null
        lateinit var dialog: android.app.AlertDialog
        fun awaitIdle() {
            val deadline=SystemClock.elapsedRealtime()+75_000
            while(com.filmfoundry.app.session.ProcessingWork.busy && SystemClock.elapsedRealtime()<deadline) Thread.sleep(50)
            onMain {} // Complete callbacks and their result commit precede this barrier.
            check(!com.filmfoundry.app.session.ProcessingWork.busy) { "Self-test service did not release its lease" }
        }
        try {
            verify("hardware_self_test_dialog_service") {
                onMain {
                    ui=com.filmfoundry.app.ui.HardwareSelfTestUi(activity,activity.shell) { true }
                    dialog=ui!!.open()!!
                }
                // Dialog dispatches OnShow on the next main-loop turn before installing its click handler.
                onMain {
                    dialog.getButton(android.app.AlertDialog.BUTTON_POSITIVE).performClick()
                    check(com.filmfoundry.app.session.ProcessingWork.busy) { "Self-test did not acquire processing lease" }
                    check(!com.filmfoundry.app.session.ProcessingWork.availableTo(Any())) { "Concurrent work was not blocked" }
                }
                awaitIdle()
                val saved=preferences.getString("last_result",null) ?: error("Self-test did not save its result")
                val parsed=RecipeJson.parse(saved) as Map<*,*>
                @Suppress("UNCHECKED_CAST")
                hardwareSelfTest=parsed["result"] as Map<String,Any?>
                val measurements=hardwareSelfTest["measurements"] as List<*>
                check(measurements.size==5)
                check(capture.all==previousCapture) { "Self-test changed acceleration preferences" }
                onMain { check(dialog.getButton(android.app.AlertDialog.BUTTON_POSITIVE).isEnabled) }
            }
            verify("hardware_self_test_cancel_preserves_result") {
                val saved=preferences.getString("last_result",null)
                onMain {
                    dialog.getButton(android.app.AlertDialog.BUTTON_POSITIVE).performClick()
                    dialog.dismiss()
                }
                awaitIdle()
                check(preferences.getString("last_result",null)==saved)
            }
        } finally {
            onMain { ui?.close() }
            awaitIdle()
            preferences.edit().clear().commit()
        }
    }

    private fun gpuProbe() {
        GlesSrgbRowCompute().use { gpu ->
            fun record(name: String,input: FloatArray,expected: FloatArray,actual: FloatArray,absolute: Float,relative: Float) {
                check(actual.size==expected.size)
                gpuProbes+=mapOf("operation" to name,"input" to input.toList(),"expected" to expected.toList(),
                    "within_tolerance" to actual.indices.all { actual[it].isFinite() && kotlin.math.abs(actual[it]-expected[it])<=absolute+kotlin.math.abs(expected[it])*relative },
                    "actual" to actual.map { if(it.isFinite()) it else it.toString() },
                    "max_absolute_error" to actual.indices.maxOf { kotlin.math.abs(actual[it]-expected[it]).toDouble() }.let { if(it.isFinite()) it else it.toString() })
            }
            val srgb=floatArrayOf(-.1f,0f,.003f,.04044f,.04045f,.04046f,.18f,.5f,.9f,1f,1.1f)
            record("srgb",srgb,TransferFunctions.srgbToLinear(srgb),gpu.linearize(srgb),2e-6f,2e-5f)
            val density=floatArrayOf(-.1f,0f,.04f,.5f,1f,2f,4f,6f,12f)
            record("density",density,DensityTransmissionCompute.reference(density),gpu.transmit(density),1e-8f,2e-5f)
            val plan=SpectralTransmissionPlan(3,DoubleArray(27) { .03+it%7*.01 },DoubleArray(9) { 1.0/3 },DoubleArray(9) { 1.0/3 })
            val amounts=FloatArray(27) { it%9*.2f };val post=FloatArray(9) { if(it%3==0) -.1f else .2f }
            record("spectral",amounts,plan.reference(amounts,post),gpu.transmitSpectral(amounts,post,plan),1e-8f,5e-5f)
            gpu.linearize(srgb);gpu.transmit(density)
            record("spectral_after_switch",amounts,plan.reference(amounts,post),gpu.transmitSpectral(amounts,post,plan),1e-8f,5e-5f)
            val samples=floatArrayOf(-.1f,0f,.0031308f,.02f,.18f,.5f,1f,4f,8f,.6f,.2f,.7f)
            val curve=listOf(listOf(0.0,.01),listOf(.23,.15),listOf(.66,.8),listOf(1.0,.98))
            for(mode in listOf("off","luma","rgb","flat")) for(bounded in listOf(false,true)) {
                val recipe=mapOf("scanner" to mapOf("scan_normalize" to (mode!="off"),"scan_normalize_mode" to mode,
                    "invert_transmission" to bounded,"print_mapping_mode" to "sigmoid","transmission_light_temperature_k" to 3200,
                    "color_curve_rgb" to List(3) {curve},"tone_curve" to curve),"look" to mapOf("print_exposure_ev" to .7,"print_contrast" to 1.2))
                val row=PhoneRowObservation(recipe)
                row.linear(OpticalMaster(FloatNd(intArrayOf(1,1,3),floatArrayOf(.1f,.2f,.3f)),emptyMap()))
                val statistics=if(mode=="flat") FloatArray(samples.size){.5f} else samples
                val outputPlan=row.outputPlan(samples.size/3) { it(statistics) }
                record("output_${mode}_$bounded",samples,outputPlan.reference(samples),gpu.encodeOutput(samples,outputPlan),2e-6f,2e-5f)
                record("spectral_after_output_${mode}_$bounded",amounts,plan.reference(amounts,post),gpu.transmitSpectral(amounts,post,plan),1e-8f,5e-5f)
                record("output_after_spectral_${mode}_$bounded",samples,outputPlan.reference(samples),gpu.encodeOutput(samples,outputPlan),2e-6f,2e-5f)
            }
            // Test-only inspection of the existing backend, without exposing production hooks.
            fun field(name: String)=gpu.javaClass.getDeclaredField(name).apply { isAccessible=true }.get(gpu)
            val display=field("display") as android.opengl.EGLDisplay
            val surface=field("surface") as android.opengl.EGLSurface
            val context=field("context") as android.opengl.EGLContext
            check(EGL.eglMakeCurrent(display,surface,surface,context))
            try {
                val program=field("program") as Int
                fun uniform(name: String): List<Int> {
                    val location=android.opengl.GLES31.glGetUniformLocation(program,name)
                    val value=IntArray(1);if(location>=0) android.opengl.GLES31.glGetUniformiv(program,location,value,0)
                    return listOf(location,value[0])
                }
                val bufferIds=field("buffers") as IntArray
                val memory=bufferIds.take(2).mapIndexed { index,id ->
                    android.opengl.GLES31.glBindBuffer(android.opengl.GLES31.GL_SHADER_STORAGE_BUFFER,id)
                    val size=IntArray(1);android.opengl.GLES31.glGetBufferParameteriv(android.opengl.GLES31.GL_SHADER_STORAGE_BUFFER,android.opengl.GLES31.GL_BUFFER_SIZE,size,0)
                    val bound=IntArray(1);android.opengl.GLES31.glGetIntegeri_v(android.opengl.GLES31.GL_SHADER_STORAGE_BUFFER_BINDING,index,bound,0)
                    val mapped=android.opengl.GLES31.glMapBufferRange(android.opengl.GLES31.GL_SHADER_STORAGE_BUFFER,0,minOf(size[0],36),android.opengl.GLES31.GL_MAP_READ_BIT) as? ByteBuffer
                    val values=if(mapped!=null) try { FloatArray(minOf(size[0],36)/4).also { mapped.order(java.nio.ByteOrder.nativeOrder()).asFloatBuffer().get(it) }.toList() } finally { android.opengl.GLES31.glUnmapBuffer(android.opengl.GLES31.GL_SHADER_STORAGE_BUFFER) } else emptyList()
                    mapOf("id" to id,"binding" to bound[0],"bytes" to size[0],"values" to values)
                }
                gpuDriver=mapOf("renderer" to android.opengl.GLES31.glGetString(android.opengl.GLES31.GL_RENDERER),
                    "version" to android.opengl.GLES31.glGetString(android.opengl.GLES31.GL_VERSION),
                    "program" to program,"size" to uniform("size"),"operation" to uniform("operation"),"buffers" to memory,
                    "error" to android.opengl.GLES31.glGetError())
            } finally { EGL.eglMakeCurrent(display,EGL.EGL_NO_SURFACE,EGL.EGL_NO_SURFACE,EGL.EGL_NO_CONTEXT) }
            check(gpuProbes.all { it["within_tolerance"]==true }) { "GPU numeric probe exceeds the existing operation tolerance" }
        }
    }

    private fun verify(name: String, work: () -> Unit) {
        phase = name; checkpoint()
        val started = SystemClock.elapsedRealtime()
        try { work(); cases += mapOf("name" to name, "status" to "passed", "wall_ms" to SystemClock.elapsedRealtime()-started) }
        catch(error: Throwable) {
            cases += mapOf("name" to name, "status" to "failed", "error" to error.javaClass.simpleName,
                "detail" to error.message?.take(240))
            throw error
        }
        checkpoint()
    }

    private fun uiChecks() {
        val a = fixture!!
        fun onUi(name: String, work: () -> Unit) = verify(name) {
            onMain(work)
        }
        onUi("rating_toggle_and_favorite_independence") {
            check(a.rating.childCount == 6)
            check((0..5).none { a.rating.getChildAt(it).isSelected })
            a.rating.getChildAt(3).performClick()
            check(a.preferences.rating("fixture-a") == 4)
            a.rating.getChildAt(5).performClick()
            check(a.preferences.favorite("fixture-a"))
            a.rating.getChildAt(3).performClick()
            check(a.preferences.rating("fixture-a") == 0 && a.preferences.favorite("fixture-a"))
            a.rating.getChildAt(5).performClick()
            check(!a.preferences.favorite("fixture-a"))
        }
        onUi("viewer_binding_and_pending_disable") {
            a.rating.getChildAt(1).performClick()
            a.rating.bind("fixture-b", true)
            check((0..5).none { a.rating.getChildAt(it).isSelected })
            a.rating.getChildAt(4).performClick()
            check(a.preferences.rating("fixture-b") == 5 && a.preferences.rating("fixture-a") == 2)
            a.rating.bind(null, false)
            check((0..5).none { a.rating.getChildAt(it).isEnabled })
            a.rating.bind("fixture-a", true)
        }
        onUi("layout_cycle_keeps_annotations") {
            check(a.preferences.columns == 3)
            for(expected in listOf(5,1,3)) { a.preferences.cycleLayout(); check(a.preferences.columns == expected) }
            check(a.preferences.rating("fixture-a") == 2)
            check(a.rating.width > 0)
            for(i in 0..5) {
                val v = a.rating.getChildAt(i)
                check(v.width >= a.shell.dp(44) && v.height >= a.shell.dp(44)) { "Annotation touch target too small" }
                check(v.left >= 0 && v.right <= a.rating.width)
            }
        }
        onUi("long_press_selection_and_export_guard") {
            check(a.selection.visibility == View.GONE)
            a.tile.performClick(); check(a.opened == 1 && !a.selection.state.active)
            a.tile.performLongClick(); check(a.selection.state.active && a.selection.state.count == 1)
            a.tile.performClick(); check(a.opened == 1 && a.selection.state.count == 0)
            a.tile.performClick(); check(a.selection.state.count == 1)
            fun descendants(v: View): List<View> = listOf(v) + if(v is ViewGroup) (0 until v.childCount).flatMap { descendants(v.getChildAt(it)) } else emptyList()
            val export = descendants(a.shell.root).filterIsInstance<Button>().single { it.text == a.shell.copy("导出所选","Export selected") }
            check(!export.isEnabled)
            a.allowExport = true; a.selection.refresh(); check(export.isEnabled)
            a.selection.clear(); check(!a.selection.state.active && a.selection.visibility == View.GONE)
        }
        verify("filter_boundaries_and_sort") {
            val rows = listOf(GalleryQuery.Item("a","A",20,100,2,true),GalleryQuery.Item("b","B",10,300,4,false),GalleryQuery.Item("c","C",30,200,4,true))
            check(GalleryQuery("size",true).ordered(rows).map { it.id } == listOf("b","c","a"))
            check(GalleryQuery("name",false).ordered(rows).map { it.id } == listOf("a","b","c"))
            check(GalleryQuery("time",false,true,"ge",4).ordered(rows).map { it.id } == listOf("c"))
            for((op,expected) in mapOf("gt" to false,"lt" to false,"ge" to true,"le" to true)) check(GalleryQuery(ratingRelation=op,rating=4).accepts(4,true) == expected)
        }
    }

    private fun onMain(work: ()->Unit) {
        val done=CountDownLatch(1);var outcome: Result<Unit>?=null
        Handler(Looper.getMainLooper()).post { outcome=runCatching(work);done.countDown() }
        check(done.await(15,TimeUnit.SECONDS)) { "Main thread did not respond within 15 seconds" }
        outcome!!.getOrThrow()
    }

    private fun pipeline(context: FixtureContext, width: Int, height: Int) = runBlocking {
        val storage = AndroidStorageAdapter(context)
        val medium = MediumStore(storage)
        val io = medium.rowExecutionIo()!!
        val facade = DefaultWorkflowFacade()
        val recorder = DevicePerformance(context).apply { enabled = true }
        val recipe = syntheticRecipe()
        val spectral=args.getString("spectral")=="true"
        require(!spectral || args.getString("focused")=="true")
        val scanRecipe: Map<String,Any?> = if(spectral) mapOf(SpectralOptics.OBSERVER_KEY to mapOf(
            "model" to "spectral_transmission_observer_v1","profile_id" to "automation_observer_31",
            "wavelengths_nm" to List(31) { 400+it*10 },"illuminant" to List(31) { 1.0 },
            "response_rgb" to List(3) { c -> List(31) { n -> 1.0/(1.0+(n-(24-c*9))*(n-(24-c*9))*.04) } },
            "working_space" to DataKernel.CAPTURE_WORKING_SPACE,"normalization" to "open_gate_per_channel_v1")) else emptyMap()
        val latent = "synthetic${DataKernel.LATENT_SUFFIX}"
        val source = object : RgbRows {
            override val width = width
            override val height = height
            override fun readRows(start: Int, count: Int) = RgbVolume(width,count,3,FloatArray(width*count*3) { n ->
                val x = n/3%width; val y = start+n/(3*width); val c = n%3
                (.02 + .7*x/width + .15*y/height + .1*((x*13+y*7+c*31)%101)/101).toFloat()
            })
        }
        val deadline = SystemClock.elapsedRealtime() + if(width>1000) 30*60_000L else 5*60_000L
        val budget = 64L*1024*1024
        val gpuRequested=args.getString("gpu")=="true"
        require(!gpuRequested || args.getString("focused")=="true") { "GPU timing requires focused mode; CPU/GPU are not byte-identical contracts" }
        val backendMessages=mutableListOf<String>()
        suspend fun measured(name: String, workers: Int, handoff: Boolean, work: suspend (WorkflowControl) -> Unit) {
            phase = name; checkpoint(); recorder.testHandoff = handoff
            backendMessages.clear()
            val trace = recorder.begin(workers,gpuRequested && name!="synthetic_exposure",budget) ?: error("Cannot start diagnostics")
            val uiDelays = Collections.synchronizedList(mutableListOf<Long>())
            val pending = AtomicBoolean(false); val active = AtomicBoolean(true)
            val timer = Executors.newSingleThreadScheduledExecutor()
            val handler = Handler(Looper.getMainLooper())
            timer.scheduleWithFixedDelay({
                if(active.get() && pending.compareAndSet(false,true)) {
                    val sent = SystemClock.uptimeMillis()
                    handler.post {
                        if(active.get()) {
                            uiDelays += SystemClock.uptimeMillis()-sent
                            // Real rating callback runs alongside the numerical pipeline, on fixture data only.
                            fixture!!.rating.getChildAt(5).performClick()
                        }
                        pending.set(false)
                    }
                }
            },0,250,TimeUnit.MILLISECONDS)
            var failure: Throwable? = null
            try {
                work(WorkflowControl(cancelRequested={ SystemClock.elapsedRealtime() >= deadline },onTiming=trace::timing))
            } catch(error: Throwable) { failure=error; throw error }
            finally {
                active.set(false); timer.shutdownNow(); trace.finish(if(failure==null) "completed" else "failed",failure)
                val delays = synchronized(uiDelays) { uiDelays.sorted() }
                traces += mapOf("case" to name,"trace" to recorder.records().first(),
                    "compute_backend" to backendMessages.toList(),
                    "width" to width,"height" to height,
                    "main_queue_samples" to delays.size,"main_queue_max_ms" to delays.lastOrNull(),
                    "main_queue_p95_ms" to delays.getOrNull(((delays.size-1)*.95).toInt()))
                check(recorder.lastError == null) { "Performance record failed" }
                checkpoint()
            }
        }
        measured("synthetic_exposure",2,true) { control ->
            facade.run(WorkflowRequest(Command.EXPOSE,MediumRef("camera"),MediumRef(latent),recipe,
                rgbRows=source,rowIo=io,control=control,rowBudgetBytes=budget,rowWorkers=2))
        }
        val focused=args.getString("focused")=="true"
        val baselineName=if(focused) "workers_2_handoff_true.png" else "workers_1_handoff_true.png"
        val modes = if(focused) listOf(2 to true) else listOf(1 to true,2 to true,2 to false)
        for((workers,direct) in modes) {
            val label = "workers_${workers}_handoff_$direct"
            val optical = "$label${DataKernel.NEGATIVE_SUFFIX}"
            measured(label,workers,direct) { control ->
                RowMediumHandoff().use { lease ->
                    facade.run(WorkflowRequests.developLatent(latent,optical,recipe,rowIo=io,control=control)
                        .copy(rowBudgetBytes=budget,rowWorkers=workers,opticalHandoff=if(direct) lease else null))
                    control.measured("scan","png_commit") {
                        val compute=if(gpuRequested) DeferredPixelCompute(true,report={ message -> if(backendMessages.size<8 && message !in backendMessages) backendMessages+=message }) else null
                        try {
                            storage.writePrivate("$label.png") { out ->
                                val dimensions = medium.scanPng(optical,scanRecipe,out,control,budget,compute=compute,bitDepth=16,rowIo=io,handoff=if(direct) lease else null)
                                check(dimensions == width to height)
                            }
                        } finally { compute?.close() }
                    }
                }
            }
            verify("${label}_png16_and_scratch") {
                val f = File(context.filesDir,"$label.png")
                f.inputStream().use { input ->
                    val header=ByteArray(25); check(input.read(header)==25)
                    check(ByteBuffer.wrap(header,16,8).let { it.int==width && it.int==height } && header[24].toInt()==16)
                }
                check(context.cacheDir.listFiles().orEmpty().isEmpty()) { "Unreleased row scratch" }
                val baseline=File(context.filesDir,baselineName)
                if(f!=baseline) check(sameBytes(baseline,f)) { "Worker/handoff output differs" }
                val timings = ((traces.last()["trace"] as Map<*,*>)["stage_timings"] as Map<*,*>)
                check(("scan.handoff_hit" in timings) == direct)
                check(("scan.archive_read" in timings) != direct)
            }
        }
        verify("jpeg_40_70_100_and_tiff16_encoding") {
            val png=baselineName
            for(q in listOf(40,70,100)) {
                val path="quality-$q.jpg"
                storage.writePrivate(path) { ScanImageExport.write(storage,png,ScanImageFormat.JPEG,0,it,jpegQuality=q) }
                val bounds=BitmapFactory.Options().apply { inJustDecodeBounds=true }
                storage.readPrivate(path) { BitmapFactory.decodeStream(it,null,bounds) }
                check(bounds.outWidth==width && bounds.outHeight==height && bounds.outMimeType=="image/jpeg")
            }
            storage.writePrivate("output.tif") { ScanImageExport.write(storage,png,ScanImageFormat.TIFF,0,it) }
            check(File(context.filesDir,"output.tif").length()>width.toLong()*height*6)
        }
        verify("cancellation_no_partial_output") {
            var cancelled=false
            try { runBlocking { facade.run(WorkflowRequests.developLatent(latent,"cancel${DataKernel.NEGATIVE_SUFFIX}",recipe,rowIo=io,control=WorkflowControl(cancelRequested={ true }))) } }
            catch(e: WorkflowException) { check(e.code=="cancelled"); cancelled=true }
            check(cancelled && storage.existsPrivate(latent) && !storage.existsPrivate("cancel${DataKernel.NEGATIVE_SUFFIX}"))
            check(context.cacheDir.listFiles().orEmpty().isEmpty())
        }
    }

    private fun sameBytes(a: File,b: File): Boolean {
        if(a.length()!=b.length()) return false
        return a.inputStream().buffered(65536).use { x -> b.inputStream().buffered(65536).use { y ->
            val xb=ByteArray(65536);val yb=ByteArray(65536)
            while(true) { val n=x.read(xb);if(n<0) break;var offset=0
                while(offset<n) { val size=y.read(yb,offset,n-offset);if(size<0) return false;offset+=size }
                for(i in 0 until n) if(xb[i]!=yb[i]) return false
            };true
        } }
    }

    private class FixtureContext(base: Context, private val root: File): ContextWrapper(base) {
        override fun getApplicationContext(): Context = this
        override fun getFilesDir() = File(root,"files").apply { mkdirs() }
        override fun getCacheDir() = File(root,"scratch").apply { mkdirs() }
        override fun getSharedPreferences(name: String,mode: Int) = super.getSharedPreferences("automation_runner_$name",mode)
        fun clearPreferences() { getSharedPreferences("performance_test",0).edit().clear().commit() }
    }

    private fun syntheticRecipe(): Map<String,Any?> {
        val spectral=args.getString("spectral")=="true"
        val wave=if(spectral) List(31) { 400+it*10 } else listOf(400,550,700)
        val material=mapOf("model" to "normalized_pool_absorbance_v1","profile_id" to "automation_fixture",
            "coefficient_unit" to "normalized_final_pool_v1","component_names" to SpectralOptics.components,
            "wavelengths_nm" to wave,"component_absorbance" to List(9) { k -> List(wave.size) { n -> .05+k*.02+n*(if(spectral) .002 else .03) } })
        val exposure=mapOf("model" to DataKernel.SPECTRAL_EXPOSURE_MODEL_ID,"profile_id" to "automation_exposure",
            "working_space" to DataKernel.CAPTURE_WORKING_SPACE,"wavelengths_nm" to wave,
            "rgb_basis" to List(3) { List(wave.size) { .003 } },"layer_sensitivity" to List(3) { List(wave.size) { 1.0 } },"layer_gain" to listOf(1,1.2,.8))
        val film=PhoneMaterialDefaults.withEffects(mapOf("image_polarity" to "positive","spatial_reference_short_edge_mm" to 24,
            SpectralOptics.MATERIAL_KEY to material,DataKernel.SPECTRAL_EXPOSURE_PROFILE_KEY to exposure),.3,.4,1.0)
        return mapOf("film" to film,"develop" to mapOf("program_key" to "color_negative","uneven_development" to .2),
            PhoneProcessModels.OPTION to PhoneProcessModels.CURRENT,PhoneGrainModels.EXPOSURE_OPTION to PhoneGrainModels.LAYERED,
            PhoneMaterialPhenomena.DEVELOPMENT_OPTION to PhoneMaterialPhenomena.CURRENT)
    }
}
