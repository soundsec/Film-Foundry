package com.filmfoundry.automation

import android.graphics.Bitmap
import android.graphics.Rect
import android.os.Bundle
import android.os.SystemClock
import android.view.KeyEvent
import android.view.MotionEvent
import android.view.View
import android.view.ViewGroup
import android.view.accessibility.AccessibilityNodeInfo
import android.widget.Button
import android.widget.HorizontalScrollView
import android.widget.SeekBar
import android.widget.TextView
import com.filmfoundry.app.testing.AutomationFixtureActivity
import com.filmfoundry.app.ui.*
import com.filmfoundry.interchange.BuiltinRecipeCatalog
import com.filmfoundry.platform.AndroidStorageAdapter

/** Events enter the attached root, so hit testing, scrolling and parent cancellation are exercised. */
internal class ScanGestureChecks(
    private val activity: AutomationFixtureActivity, private val storage: AndroidStorageAdapter,
    private val main: (() -> Unit) -> Unit, private val idle: () -> Unit, private val saveView: (View,String)->Unit
) {
    private val shell = activity.shell
    private val root = shell.root
    private val handle get() = root.findViewWithTag<View>("scan_drawer_handle")
    private lateinit var comparison: ScanComparison
    private lateinit var stages: StageRecipeControls

    private fun descendants(view: View): Sequence<View> = sequence {
        yield(view)
        if (view is ViewGroup) for (i in 0 until view.childCount) yieldAll(descendants(view.getChildAt(i)))
    }

    private fun select(index: Int) {
        main { shell.scanTools.getChildAt(index).performClick() }
        idle()
    }

    private fun reveal(view: View, rect: Rect = Rect(0, 0, view.width, view.height)) {
        main { view.requestRectangleOnScreen(rect, true) }
        idle()
    }

    private fun drag(view: View, x: Float, y: Float, dx: Float, dy: Float, multi: Boolean = false) {
        main {
            val a = IntArray(2); val b = IntArray(2)
            view.getLocationOnScreen(a); root.getLocationOnScreen(b)
            val startX = a[0]-b[0]+x; val startY = a[1]-b[1]+y
            val down = SystemClock.uptimeMillis()
            fun send(action: Int, fraction: Float, count: Int = 1) {
                val pointers = Array(count) { i -> MotionEvent.PointerProperties().apply { id=i;toolType=MotionEvent.TOOL_TYPE_FINGER } }
                val coords = Array(count) { i -> MotionEvent.PointerCoords().apply {
                    this.x=startX+dx*fraction+i*8;this.y=startY+dy*fraction;pressure=1f;size=1f
                } }
                val event=MotionEvent.obtain(down,SystemClock.uptimeMillis(),action,count,pointers,coords,0,0,1f,1f,0,0,android.view.InputDevice.SOURCE_TOUCHSCREEN,0)
                try { root.dispatchTouchEvent(event) } finally { event.recycle() }
            }
            send(MotionEvent.ACTION_DOWN,0f)
            if (multi) {
                send(MotionEvent.ACTION_POINTER_DOWN or (1 shl MotionEvent.ACTION_POINTER_INDEX_SHIFT),0f,2)
                send(MotionEvent.ACTION_POINTER_UP or (1 shl MotionEvent.ACTION_POINTER_INDEX_SHIFT),0f,2)
            }
            send(MotionEvent.ACTION_MOVE,.4f);send(MotionEvent.ACTION_MOVE,1f);send(MotionEvent.ACTION_UP,1f)
        }
        idle()
    }

    fun run(verify: (String, () -> Unit) -> Unit) {
        val catalog=activity.assets.open("phone_builtin_recipes.json").use { BuiltinRecipeCatalog.read(it.readBytes()) }
        main {
            listOf(activity.rating,activity.tile,activity.selection).forEach { it.visibility=View.GONE }
            activity.getSharedPreferences("mobile_ui",0).edit().remove("scan_slider_gestures_seen").apply()
            activity.getSharedPreferences("stage_controls",0).edit().clear().apply()
            stages=StageRecipeControls(activity,shell,catalog,storage) { emptyMap() }
            comparison=ScanComparison(shell,shell.scanPreviewSurface)
            shell.select(MobileShell.Page.SCAN)
        }
        idle()
        val laidOut=java.util.concurrent.CountDownLatch(1)
        main {
            if(handle.isLaidOut && handle.height>0) laidOut.countDown()
            else handle.addOnLayoutChangeListener(object: View.OnLayoutChangeListener {
                override fun onLayoutChange(v: View,l: Int,t: Int,r: Int,b: Int,ol: Int,ot: Int,or: Int,ob: Int) {
                    if(b>t) { v.removeOnLayoutChangeListener(this); laidOut.countDown() }
                }
            })
        }
        check(laidOut.await(5,java.util.concurrent.TimeUnit.SECONDS)) { "Scan handle never laid out" }
        verify("scan_first_hint_drawer_and_multitouch") {
            val first=handle.contentDescription.toString()
            check(!shell.scanTools.isShown && first.contains(shell.copy("精调","fine"))) { "Initial handle: $first, visible=${shell.scanTools.isShown}" }
            val visible=Rect();check(handle.getGlobalVisibleRect(visible) && visible.height()==handle.height) { "Handle clipped: $visible vs ${handle.height}" }
            val dy=-shell.dp(64).toFloat()
            drag(handle,handle.width/2f,handle.height/2f,0f,dy,true)
            check(!shell.scanTools.isShown) { "Pinch opened drawer" }
            drag(handle,handle.width/2f,handle.height/2f,0f,dy)
            check(shell.scanTools.isShown && handle.contentDescription.toString()!=first) { "Drag left handle at ${(handle.parent as View).height}, label=${handle.contentDescription}" }
            main {
                shell.select(MobileShell.Page.START);shell.select(MobileShell.Page.SCAN)
                check(handle.contentDescription.toString()!=first)
                check(handle.performAccessibilityAction(AccessibilityNodeInfo.ACTION_CLICK,null))
            }
            idle();check(shell.scanTools.isShown) { "Enter did not open drawer" }
        }
        verify("scan_hsl_strip_and_body_paging") {
            select(4)
            val scroll=checkNotNull(shell.scanScroller)
            val strip=descendants(scroll).filterIsInstance<HorizontalScrollView>().first { it.isShown && it.canScrollHorizontally(1) }
            reveal(strip)
            drag(strip,strip.width*.8f,strip.height*.5f,-strip.width*.6f,0f)
            check(strip.scrollX>0 && shell.scanTools.getChildAt(4).isSelected)
            val bar=descendants(scroll).filterIsInstance<SeekBar>().first { it.isShown && it.max==60 }
            reveal(bar)
            val p=bar.progress
            drag(bar,bar.width*.3f,bar.height*.5f,shell.dp(56).toFloat(),0f,true)
            check(bar.progress==p)
            drag(bar,bar.width*.3f,bar.height*.5f,bar.width*.45f,0f)
            check(bar.progress>p && shell.scanTools.getChildAt(4).isSelected)
            val caption=descendants(scroll).filterIsInstance<TextView>().first { it.isShown && it.text.startsWith(shell.copy("色相","Hue")) }
            reveal(caption)
            drag(caption,caption.width*.75f,caption.height*.5f,-caption.width*.55f,0f)
            check(shell.scanTools.getChildAt(5).isSelected)
        }
        verify("scan_curve_drag_and_drawer_ownership") {
            val graph=descendants(root).filterIsInstance<CurveGraph>().first { it.isShown }
            val margin=shell.dp(16)
            val point=graph.curve.points.last()
            val y=graph.height-margin-point.y.toFloat()*(graph.height-2*margin)
            reveal(graph,Rect(graph.width-2*margin,y.toInt()-margin,graph.width,y.toInt()+margin))
            drag(graph,(graph.width-margin).toFloat(),y,0f,shell.dp(28).toFloat(),true)
            check(graph.curve.points.last()==point) { "Second finger moved curve endpoint" }
            drag(graph,(graph.width-margin).toFloat(),y,0f,shell.dp(28).toFloat())
            check(graph.curve.points.last()!=point && shell.scanTools.isShown && shell.scanTools.getChildAt(5).isSelected) { "Single-finger curve edit lost: ${graph.curve.points.last()}, start $point" }
            val edited=graph.curve
            drag(handle,handle.width*.5f,handle.height*.5f,0f,shell.dp(70).toFloat())
            check(shell.scanTools.isShown && graph.curve==edited)
            val parked=handle.top+(handle.parent as View).height
            select(2)
            check(parked==handle.top+(handle.parent as View).height)
            main { handle.performClick() }
            idle()
            main {
                check(handle.requestFocusFromTouch() && handle.isFocused) { "Keyboard focus did not reach handle" }
                root.dispatchKeyEvent(KeyEvent(KeyEvent.ACTION_DOWN,KeyEvent.KEYCODE_ENTER))
                root.dispatchKeyEvent(KeyEvent(KeyEvent.ACTION_UP,KeyEvent.KEYCODE_ENTER))
            }
            idle();check(shell.scanTools.isShown) { "Enter did not open drawer" }
        }
        verify("drawer_leaves_one_fifth_for_preview") {
            val drawer=handle.parent as View
            val workspace=drawer.parent as View
            drag(handle,handle.width*.5f,handle.height*.5f,0f,-workspace.height.toFloat())
            check(drawer.height==(workspace.height*.8f).toInt()) { "Drawer ${drawer.height}/${workspace.height}" }
            check(shell.scanPreviewSurface.height>=workspace.height-drawer.height)
        }
        verify("slider_absolute_and_hold_fine") {
            select(2)
            val bar=descendants(root).filterIsInstance<SeekBar>().first { it.isShown }
            reveal(bar)
            var down=0L
            fun touch(action: Int,x: Float) = main {
                if(action==MotionEvent.ACTION_DOWN) down=SystemClock.uptimeMillis()
                val event=MotionEvent.obtain(down,SystemClock.uptimeMillis(),action,x*bar.width,bar.height*.5f,0)
                try { bar.dispatchTouchEvent(event) } finally { event.recycle() }
            }
            touch(MotionEvent.ACTION_DOWN,.2f);touch(MotionEvent.ACTION_UP,.2f)
            val start=bar.progress
            check(start in 1..bar.max/3)
            touch(MotionEvent.ACTION_DOWN,.7f)
            Thread.sleep(android.view.ViewConfiguration.getLongPressTimeout()+100L)
            check(bar.progress==start)
            touch(MotionEvent.ACTION_MOVE,.95f)
            check(bar.progress in (start+1)..(start+bar.max/10))
            main { saveView(root,"fine") }
            touch(MotionEvent.ACTION_UP,.95f)
        }
        verify("scan_keyboard_range_and_accessibility_comparison") {
            select(2)
            val bar=descendants(root).filterIsInstance<SeekBar>().first { it.isShown }
            reveal(bar)
            main {
                check(bar.createAccessibilityNodeInfo().rangeInfo!=null)
                val args=Bundle().apply { putFloat(AccessibilityNodeInfo.ACTION_ARGUMENT_PROGRESS_VALUE,41f) }
                check(bar.performAccessibilityAction(AccessibilityNodeInfo.AccessibilityAction.ACTION_SET_PROGRESS.id,args) && bar.progress==41)
                check(bar.requestFocusFromTouch());root.dispatchKeyEvent(KeyEvent(KeyEvent.ACTION_DOWN,KeyEvent.KEYCODE_DPAD_RIGHT))
                root.dispatchKeyEvent(KeyEvent(KeyEvent.ACTION_UP,KeyEvent.KEYCODE_DPAD_RIGHT))
                check(bar.progress>41)
                val photo=Bitmap.createBitmap(64,64,Bitmap.Config.ARGB_8888)
                comparison.bind("fixture");comparison.offer(photo)
                var initial=false
                comparison.onHistogramChanged={_,shown -> initial=shown }
                val surface=shell.scanPreviewSurface
                check(surface.performAccessibilityAction(AccessibilityNodeInfo.ACTION_LONG_CLICK,null) && initial)
                check(surface.performAccessibilityAction(AccessibilityNodeInfo.ACTION_CLICK,null) && !initial)
                comparison.trim();comparison.offer(photo);check(!surface.isLongClickable)
                comparison.bind("new-frame");comparison.offer(photo);check(surface.isLongClickable)
                comparison.clear()
                val oversized=Bitmap.createBitmap(961,960,Bitmap.Config.ARGB_8888)
                comparison.bind("oversized");comparison.offer(oversized);check(!surface.isLongClickable)
                comparison.clear();oversized.recycle();photo.recycle()
            }
        }
        verify("scan_rapid_tool_edits_with_reversed_completions") {
            val receipt=ScanPreviewState()
            val requests=mutableListOf<ScanPreviewState.Request>()
            main {
                stages.onObservationChanged={
                    requests+=receipt.begin("frame","fixture.npz",stages.currentScanObservation(emptyMap()))
                }
                for(value in 48..55) {
                    shell.scanTools.getChildAt(2).performClick()
                    val gain=descendants(root).filterIsInstance<SeekBar>().first { it.isShown }
                    val args=Bundle().apply { putFloat(AccessibilityNodeInfo.ACTION_ARGUMENT_PROGRESS_VALUE,value.toFloat()) }
                    check(gain.performAccessibilityAction(AccessibilityNodeInfo.AccessibilityAction.ACTION_SET_PROGRESS.id,args))
                    shell.scanTools.getChildAt(if(value%2==0) 4 else 5).performClick()
                }
                check(requests.size>=8 && requests.first().recipe!=requests.last().recipe)
                check(receipt.complete(requests.last()))
                for(late in requests.dropLast(1).reversed()) check(!receipt.complete(late))
                check(receipt.recipeFor("frame","fixture.npz")==requests.last().recipe)
            }
        }
        val stock=catalog.materials.first().recipe["film"] as Map<String,Any?>
        val cache=com.filmfoundry.workflow.CameraEffectsPreviewCache()
        val sampleWorker=java.util.concurrent.Executors.newSingleThreadExecutor()
        val frozenProcess=mapOf("develop" to mapOf("program_key" to "bw_negative"))
        var lastSample: Map<String,Any?> = emptyMap()
        val panels: SamplePanel={ draft -> ProcessSampleView(shell,sampleWorker,cache) {
            stages.sampleRequest(draft(),stock,emptyMap(),emptyMap(),null,shell.selected==MobileShell.Page.SCAN).also { lastSample=it.selected }
        } }
        fun ready(view: ()->View, previous: android.graphics.drawable.Drawable?=null) {
            val deadline=SystemClock.elapsedRealtime()+15000
            while(SystemClock.elapsedRealtime()<deadline) {
                var drawable: android.graphics.drawable.Drawable?=null
                main { drawable=descendants(view()).filterIsInstance<android.widget.ImageView>()
                    .firstOrNull { it.isShown && it.tag=="process_sample_image" }?.drawable }
                if(drawable!=null && drawable!==previous) return
                Thread.sleep(45)
            }
            error("Process chart did not render")
        }
        try {
            verify("scan_sample_follows_gain_with_frozen_development") {
                main {
                    stages.installSamples(panels) { frozenProcess }
                    stages.onObservationChanged={stages.scanSample?.refresh()}
                }
                select(0)
                main { descendants(root).filterIsInstance<Button>().first { it.isShown && it.text==shell.copy("工艺样张","Process sample") }.performClick() }
                ready({ root })
                select(2)
                val image=descendants(root).filterIsInstance<android.widget.ImageView>().single { it.tag=="process_sample_image" && it.isShown }
                val previous=image.drawable
                main {
                    val bar=descendants(root).filterIsInstance<SeekBar>().first { it.isShown }
                    val args=Bundle().apply { putFloat(AccessibilityNodeInfo.ACTION_ARGUMENT_PROGRESS_VALUE,30f) }
                    check(bar.performAccessibilityAction(AccessibilityNodeInfo.AccessibilityAction.ACTION_SET_PROGRESS.id,args))
                }
                ready({ root },previous)
                val before=(previous as android.graphics.drawable.BitmapDrawable).bitmap
                val after=(image.drawable as android.graphics.drawable.BitmapDrawable).bitmap
                check(!before.sameAs(after)) { "Scan gain changed the recipe but not the sample pixels" }
                check(lastSample["develop"]==frozenProcess["develop"])
                check((lastSample["look"] as Map<*,*>)["print_exposure_ev"]==-.5)
                main { saveView(root,"scan-sample") }
            }
            verify("development_duration_on_preparation_page") {
                main { shell.select(MobileShell.Page.DEVELOP) }
                idle();ready({ root })
                main {
                    val group=root.findViewWithTag<View>(shell.copy("共用药浴时长 · 分钟","Shared bath duration · minutes"))
                    check(group.isShown)
                    saveView(root,"develop")
                }
            }
            if (android.os.Build.VERSION.SDK_INT >= 29) verify("single_bath_edits_return_to_existing_draft") {
                fun window()=android.view.inspector.WindowInspector.getGlobalWindowViews().last { it.isShown }
                fun click(zh: String,en: String) {
                    val label=shell.copy(zh,en)
                    val target=descendants(window()).first { it.isShown && it.isClickable &&
                        ((it as? TextView)?.text==label || it.contentDescription?.toString()?.startsWith(label)==true) }
                    check(target.performClick())
                }
                var applied: Map<String,Any?>?=null
                main { ProcessProgramEditor(activity,shell).apply { samplePanel=panels }.open(mapOf("program_key" to "bw_negative")) { applied=it } }
                idle()
                main { click("编辑这一浴","Edit this bath") }
                idle()
                main {
                    val bar=descendants(window()).filterIsInstance<SeekBar>().first { it.isShown }
                    val args=Bundle().apply { putFloat(AccessibilityNodeInfo.ACTION_ARGUMENT_PROGRESS_VALUE,98f) }
                    check(bar.performAccessibilityAction(AccessibilityNodeInfo.AccessibilityAction.ACTION_SET_PROGRESS.id,args))
                }
                ready(::window)
                val rows=(lastSample["develop"] as Map<*,*>)["process_steps"] as List<*>
                check(((rows.first() as Map<*,*>)["time_min"] as Number).toDouble()==9.9 && applied==null)
                main {
                    saveView(window(),"bath")
                    click("完成","Done")
                }
                idle()
                main { check(applied==null); click("应用配置","Apply draft") }
                val first=(applied?.get("process_steps") as List<*>).first() as Map<*,*>
                check(kotlin.math.abs((first["time_min"] as Number).toDouble()-9.9)<1e-6)
            }
        } finally { sampleWorker.shutdownNow() }
    }
}
