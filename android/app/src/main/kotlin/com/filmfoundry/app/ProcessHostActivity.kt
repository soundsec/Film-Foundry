package com.filmfoundry.app

import android.Manifest
import android.content.pm.PackageManager
import android.content.Context
import android.content.res.Configuration
import android.os.Bundle
import android.app.AlertDialog
import android.provider.OpenableColumns
import android.widget.Button
import android.widget.LinearLayout
import android.widget.ScrollView
import android.widget.TextView
import android.util.Range
import android.view.View
import androidx.activity.ComponentActivity
import androidx.activity.OnBackPressedCallback
import androidx.activity.result.contract.ActivityResultContracts
import com.filmfoundry.platform.CameraXCameraAdapter
import com.filmfoundry.platform.AndroidMovieProbe
import com.filmfoundry.platform.MovieSources
import com.filmfoundry.platform.MovieSource
import androidx.camera.view.PreviewView
import java.util.concurrent.Executors
import java.util.Locale
import com.filmfoundry.app.ui.MobileShell
import com.filmfoundry.app.ui.ViewfinderBaffles
import com.filmfoundry.mobile.R

/**
 * Process host, not a filter camera. CameraX is bound so ImageCapture can
 * fill a roll and ImageAnalysis can light the viewer; this screen does not
 * apply looks to the preview.
 */
class ProcessHostActivity : ComponentActivity() {
    private var camera: CameraXCameraAdapter? = null
    private val movieWorker=Executors.newSingleThreadExecutor()
    private lateinit var movies: MovieSources
    private lateinit var status: TextView
    private lateinit var sourceList: TextView
    private lateinit var preview: PreviewView
    private lateinit var importButton: Button
    private lateinit var exportButton: Button
    private var importing=false
    private var sourceRows=emptyList<MovieSource>()
    private var exportSource: MovieSource?=null
    private var masterActions: com.filmfoundry.app.ui.MovieMasterActions?=null
    private var rollActions: com.filmfoundry.app.ui.RollActions?=null
    private var observeProcessing: (()->Unit)?=null
    private var cameraReady=false
    private lateinit var cameraButton: Button
    private lateinit var shell: MobileShell
    private var cameraWanted=false
    private var boundTarget: String?=null
    private var failedTarget: String?=null
    private var naturalLightAfterPermission: Pair<()->Unit,(Throwable)->Unit>?=null
    @Volatile private var sceneSessionSerial=0L
    @Volatile private var samplingLight=false
    private var viewingCamera: CameraXCameraAdapter?=null
    private val lightHandler=android.os.Handler(android.os.Looper.getMainLooper())
    private var lightTimeout: Runnable?=null
    private lateinit var gateView: ViewfinderBaffles
    private lateinit var touchControls: com.filmfoundry.app.ui.CameraTouchControls
    private var cameraChoices=emptyList<com.filmfoundry.platform.NativeCameraCatalog.Lens>()
    private var selectedCamera: com.filmfoundry.platform.NativeCameraCatalog.Lens?=null
    private var nativeSettings=com.filmfoundry.platform.NativeCaptureSettings()
    private val finderExpert=object: com.filmfoundry.app.ui.FinderExpertPort {
        override fun snapshot(): com.filmfoundry.app.ui.FinderExpertPort.State {
            val applied=camera?.takeIf { cameraReady }?.nativeRequests() ?: return com.filmfoundry.app.ui.FinderExpertPort.State()
            val chars=selectedCamera?.characteristics ?: return com.filmfoundry.app.ui.FinderExpertPort.State()
            val motion=rollActions?.captureTarget()?.startsWith("motion")==true
            val manual=(chars.get(android.hardware.camera2.CameraCharacteristics.REQUEST_AVAILABLE_CAPABILITIES) ?: intArrayOf()).contains(android.hardware.camera2.CameraCharacteristics.REQUEST_AVAILABLE_CAPABILITIES_MANUAL_SENSOR) &&
                chars.get(android.hardware.camera2.CameraCharacteristics.SENSOR_INFO_SENSITIVITY_RANGE)!=null && chars.get(android.hardware.camera2.CameraCharacteristics.SENSOR_INFO_EXPOSURE_TIME_RANGE)!=null
            val yes=com.filmfoundry.app.ui.FinderExpertPort.Access.FULL_EDITOR;val no=com.filmfoundry.app.ui.FinderExpertPort.Access.DEVICE_UNSUPPORTED
            val exposureAccess=if(!manual) no else if(motion) com.filmfoundry.app.ui.FinderExpertPort.Access.NOT_CONNECTED else yes
            return com.filmfoundry.app.ui.FinderExpertPort.State(applied.iso,applied.exposureNs,null,exposureAccess,exposureAccess,com.filmfoundry.app.ui.FinderExpertPort.Access.NOT_CONNECTED)
        }
        override fun openEditor(control: com.filmfoundry.app.ui.FinderExpertPort.Control) { if(control in listOf(com.filmfoundry.app.ui.FinderExpertPort.Control.ISO,com.filmfoundry.app.ui.FinderExpertPort.Control.SHUTTER)) openNativeCameraSettings() }
        override val applyNative: ((com.filmfoundry.platform.NativeCaptureSettings) -> Unit)? = { next ->
            applyLiveNative(next,copy("原生控制未应用：","Native control was not applied: "))
        }
        override val setShutter: ((Long?) -> Unit)? = fun(nanos: Long?) {
            if(rollActions?.captureInProgress()==true || rollActions?.captureTarget()?.startsWith("motion")==true) return
            if(camera?.takeIf { cameraReady && !bindingCamera }==null) return
            isoLock.selectRoll(rollActions?.captureRollId())
            val base=nativeSettings
            val next=if(nanos==null) base.copy(iso=null,exposureNs=null) else {
                val chars=selectedCamera?.characteristics
                val isoRange=chars?.get(android.hardware.camera2.CameraCharacteristics.SENSOR_INFO_SENSITIVITY_RANGE)
                val timeRange=chars?.get(android.hardware.camera2.CameraCharacteristics.SENSOR_INFO_EXPOSURE_TIME_RANGE)
                val iso=base.iso?.takeIf { isoRange?.contains(it)==true }
                    ?: getSharedPreferences("mobile_ui",MODE_PRIVATE).getInt("film_iso",0).takeIf { isoRange?.contains(it)==true }
                    ?: isoRange?.lower
                if(iso==null || timeRange?.contains(nanos)!=true) null
                else base.copy(iso=iso,exposureNs=nanos,lockAe=false,flash="off")
            }
            if(next==null) ToastMessage(copy("这颗镜头不能使用这个快门。","This lens cannot use that shutter."))
            else { isoUnlocked=true;applyLiveNative(next,copy("快门未应用：","Shutter was not applied: ")) }
        }
    }
    private var liveNativeRevision=0L
    private fun applyLiveNative(requested: com.filmfoundry.platform.NativeCaptureSettings,failure: String) {
        val next=com.filmfoundry.app.session.CaptureAutomationPolicy.apply(requested)
        val adapter=camera?.takeIf { cameraReady && !bindingCamera } ?: return
        if(rollActions?.captureInProgress()==true || rollActions?.captureTarget()?.startsWith("motion")==true) return
        val revision=++liveNativeRevision
        adapter.applyNativeSettings(next) { error ->
            if(isDestroyed || camera!==adapter || revision!=liveNativeRevision) return@applyNativeSettings
            if(error==null) { nativeSettings=next;touchControls.bind(adapter);rollActions?.refresh() }
            else ToastMessage(failure+(error.message ?: ""))
        }
    }
    private val isoLock=com.filmfoundry.app.session.CaptureIsoLock()
    private var isoUnlocked: Boolean
        get()=isoLock.unlocked
        set(value) { isoLock.setUnlocked(value) }
    private var openingNativeSettings=false
    private var bindingCamera=false
    private var resetCameraWhenIdle=false
    private var readingCameras=false
    private var cameraCatalogReady: (() -> Unit)?=null
    private var cameraRollback: Pair<com.filmfoundry.platform.NativeCameraCatalog.Lens,com.filmfoundry.platform.NativeCaptureSettings>?=null
    override fun attachBaseContext(newBase: Context) {
        val language=newBase.getSharedPreferences("mobile_ui",Context.MODE_PRIVATE).getString("language","system")
        val context=if(language in listOf("zh","en")) {
            // Only override language. Copying the full startup configuration also pins portrait,
            // dimensions, font scale and density after the capture page rotates the Activity.
            val config=Configuration()
            config.setLocale(Locale.forLanguageTag(language!!));config.setLayoutDirection(Locale.forLanguageTag(language))
            newBase.createConfigurationContext(config)
        } else newBase
        super.attachBaseContext(context)
    }
    private fun copy(zh: String,en: String)=if(resources.configuration.locales[0].language == "zh") zh else en
    private fun loadCameraChoices(ready: ()->Unit) {
        if(isDestroyed) return
        cameraCatalogReady=ready
        if(readingCameras) return
        readingCameras=true
        com.filmfoundry.platform.NativeCameraCatalog.load(this) { result ->
            readingCameras=false
            if(isDestroyed) return@load
            result.fold({ snapshot ->
                cameraChoices=snapshot.lenses
                if(cameraChoices.isEmpty()) { status.text=copy("系统没有开放可用相机","No camera is available to this app");return@fold }
                val prefs=getSharedPreferences("native_camera",MODE_PRIVATE)
                val wanted=selectedCamera?.key ?: prefs.getString("lens",null)
                selectedCamera=cameraChoices.firstOrNull { it.key==wanted } ?: cameraChoices.firstOrNull { it.key==snapshot.defaultKey } ?: cameraChoices.first()
                val callback=cameraCatalogReady;cameraCatalogReady=null;callback?.invoke()
            },{ status.text=it.message })
        }
    }
    private fun openNativeCameraSettings() {
        if(importing || samplingLight || bindingCamera || rollActions?.captureInProgress()==true || isDestroyed) return
        shell.closeQuickControls()
        if(checkSelfPermission(Manifest.permission.CAMERA)!=PackageManager.PERMISSION_GRANTED) {
            openingNativeSettings=true;cameraPermission.launch(Manifest.permission.CAMERA);return
        }
        if(cameraChoices.isEmpty()) { loadCameraChoices { openNativeCameraSettings() };return }
        val motionNow=rollActions?.captureTarget()?.startsWith("motion")==true
        isoLock.selectRoll(if(shell.selected==MobileShell.Page.CAPTURE) rollActions?.captureRollId() else null)
        val shown=if(motionNow) nativeSettings.copy(iso=null,exposureNs=null) else if(isoUnlocked) nativeSettings else exposureFromFilm(nativeSettings)
        com.filmfoundry.app.ui.showNativeCameraDialog(this,shell,cameraChoices,selectedCamera ?: cameraChoices.first(),shown,shell.selected==MobileShell.Page.CAPTURE && !motionNow,isoUnlocked && !motionNow) { lens,requested,unlock ->
            applyNativeCameraSelection(lens,requested,unlock)
        }
    }
    /** Both the full editor and the finder shortcut use this same apply/rollback transaction. */
    private fun applyNativeCameraSelection(lens: com.filmfoundry.platform.NativeCameraCatalog.Lens,requested: com.filmfoundry.platform.NativeCaptureSettings,unlock: Boolean) {
            if(importing || bindingCamera || rollActions?.captureInProgress()==true) return
            val motion=rollActions?.captureTarget()?.startsWith("motion")==true
            val settings=com.filmfoundry.app.session.CaptureAutomationPolicy.apply(if(motion) requested.copy(iso=null,exposureNs=null) else if(unlock) requested else exposureFromFilm(requested))
            val size=runCatching { captureSize(lens,settings) }.getOrElse {
                ToastMessage(copy("请先过片，或选择支持当前格尺寸的镜头。","Advance first, or select a lens supporting the current frame size."));return
            }
            val w=size?.first ?: 0;val h=size?.second ?: 0
            val imageFormat=when(settings.format) { "raw"->android.graphics.ImageFormat.RAW_SENSOR;"ultra_hdr"->android.graphics.ImageFormat.JPEG_R;else->android.graphics.ImageFormat.JPEG }
            if(motion && w>0 && h>0 && lens.sizes(imageFormat).none { (it.first==w && it.second==h) || (it.first==h && it.second==w) }) {
                ToastMessage(copy("这颗镜头不支持当前胶卷的拍摄尺寸，当前相机保留","This lens cannot provide the loaded roll's capture size; current camera retained"));return
            }
            if(motion && settings!=com.filmfoundry.platform.NativeCaptureSettings()) {
                ToastMessage(copy("原生手动控制和 HDR 当前仅用于静照，请恢复自动设置或切换静照卷","Manual native controls and HDR currently apply to still rolls; use automatic settings or load a still roll"));return
            }
            val keepUnlock=isoLock.snapshot()
            isoUnlocked=unlock && !motion
            val adapter=camera
            if(cameraReady && adapter!=null && selectedCamera?.key==lens.key && nativeSettings.format==settings.format) {
                val previous=nativeSettings
                bindingCamera=true;cameraReady=false;rollActions?.refresh()
                adapter.applyNativeSettings(settings) { error ->
                    if(camera!==adapter || isDestroyed) return@applyNativeSettings
                    if(error==null) {
                        nativeSettings=settings;bindingCamera=false;cameraReady=true
                        touchControls.bind(adapter)
                        boundTarget=cameraBindingKey(lens,settings,size)
                        rollActions?.refresh();status.text=copy("原生相机参数已应用","Native camera controls applied")
                    } else adapter.applyNativeSettings(previous) { restored ->
                        isoLock.restore(keepUnlock)
                        bindingCamera=false;cameraReady=restored==null
                        touchControls.bind(if(cameraReady) adapter else null)
                        if(restored!=null) { adapter.unbind();camera=null;boundTarget=null;failedTarget=null;cameraButton.visibility=View.VISIBLE }
                        rollActions?.refresh();ToastMessage(copy("参数未应用，已尝试恢复原设置：","Controls not applied; previous settings restored when available: ")+error.message)
                    }
                }
                return
            }
            cameraRollback=if(cameraReady) selectedCamera?.let { it to nativeSettings } else null
            selectedCamera=lens;nativeSettings=settings;failedTarget=null
            if(shell.selected==MobileShell.Page.CAPTURE) bindCamera(force=true)
            else { getSharedPreferences("native_camera",MODE_PRIVATE).edit().putString("lens",lens.key).apply();cameraRollback=null;status.text=copy("已选择：","Selected: ")+lens.label(resources.configuration.locales[0].language=="zh") }
    }
    private fun nativeFlashAvailable()=selectedCamera?.characteristics?.get(android.hardware.camera2.CameraCharacteristics.FLASH_INFO_AVAILABLE)==true && rollActions?.captureTarget()?.startsWith("motion")!=true
    private fun openFinderLens(anchor: View) {
        val adapter=camera ?: return
        if(!cameraReady || importing || samplingLight || bindingCamera || rollActions?.captureInProgress()==true || cameraChoices.isEmpty()) return
        val choices=cameraChoices.toList()
        shell.quickControls.choices("lens",anchor,copy("镜头","Lenses"),copy("系统开放的真实相机；自动镜头由系统调度。","Actual system-accessible cameras; automatic lenses are managed by the system."),
            choices.map { it.label(resources.configuration.locales[0].language=="zh") },choices.indexOfFirst { it.key==selectedCamera?.key }) { index ->
            if(camera===adapter && cameraReady && !bindingCamera && !importing && rollActions?.captureInProgress()!=true && choices[index].key!=selectedCamera?.key)
                applyNativeCameraSelection(choices[index],adapter.nativeRequests(),isoUnlocked)
        }
    }
    private fun openFinderFlash(anchor: View) {
        val adapter=camera ?: return;val lens=selectedCamera ?: return
        if(!cameraReady || importing || samplingLight || bindingCamera || rollActions?.captureInProgress()==true || !nativeFlashAvailable()) return
        val applied=adapter.nativeRequests()
        val choices=applied.illuminationChoices(true)
        val labels=choices.map { when(it) { "off"->copy("闪光关闭","Flash off");"auto"->copy("自动闪光","Auto flash");"on"->copy("强制闪光","Flash on");else->copy("常亮补光","Continuous torch") } }
        shell.quickControls.choices("flash",anchor,copy("原生闪光与补光","Native flash & torch"),copy("闪光与常亮互斥；手动曝光仅关闭或常亮。","Photo flash and torch are exclusive; manual exposure allows off or torch."),labels,choices.indexOf(if(applied.torch) "torch" else applied.flash)) { index ->
                if(camera===adapter && cameraReady && !bindingCamera && !importing && rollActions?.captureInProgress()!=true) {
                    val key=choices[index];applyNativeCameraSelection(lens,applied.withIllumination(key),isoUnlocked)
                }
            }
    }
    private fun ToastMessage(message: String) { status.text=message;android.widget.Toast.makeText(this,message,android.widget.Toast.LENGTH_LONG).show() }
    private val importMovie=registerForActivityResult(ActivityResultContracts.OpenDocument()) { uri ->
        if(uri != null) {
            importing=true; updateMovieControls()
            status.text=copy("正在导入原视频…","Importing original video…")
            movieWorker.execute {
                val result=runCatching {
                    var name="source.video"
                    contentResolver.query(uri,arrayOf(OpenableColumns.DISPLAY_NAME),null,null,null)?.use { cursor ->
                        if(cursor.moveToFirst()) name=cursor.getString(0) ?: name
                    }
                    contentResolver.openInputStream(uri)?.use { movies.import(it,name) }
                        ?: error("Cannot open selected movie")
                }
                runOnUiThread {
                    if(!isDestroyed) {
                        importing=false; updateMovieControls()
                        status.text=result.fold({copy("视频源已入库；尚未曝光冲洗","Video source saved; not exposed/developed")},{it.message})
                        refreshMovies()
                    }
                }
            }
        }
    }
    private val exportMovie=registerForActivityResult(ActivityResultContracts.CreateDocument("application/octet-stream")) { uri ->
        val source=exportSource; exportSource=null
        if(uri != null && source != null) movieWorker.execute {
            val result=runCatching {
                contentResolver.openOutputStream(uri,"w")?.use { output -> source.file.inputStream().use { it.copyTo(output,64*1024) } }
                    ?: error("Cannot open destination")
            }
            runOnUiThread { if(!isDestroyed) status.text=result.fold({copy("原视频文件已导出","Original video source exported")},{it.message}) }
        }
    }
    private val cameraPermission = registerForActivityResult(
        ActivityResultContracts.RequestPermission(),
    ) { granted ->
        if (granted) {
            val waiting=naturalLightAfterPermission;naturalLightAfterPermission=null
            if(waiting != null) waiting.first() else { cameraWanted=true;loadCameraChoices { if(openingNativeSettings) { openingNativeSettings=false;openNativeCameraSettings() } else updateCameraForPage() } }
        } else {
            openingNativeSettings=false
            naturalLightAfterPermission?.second?.invoke(IllegalStateException(copy("未授予相机权限","Camera permission denied")))
            naturalLightAfterPermission=null
            status.text=copy("未授予相机权限","Camera permission denied")
        }
    }

    override fun onCreate(savedInstanceState: Bundle?) {
        val preferences=getSharedPreferences("mobile_ui",Context.MODE_PRIVATE)
        val theme=preferences.getString("theme","dark") ?: "dark"
        setTheme(if(theme == "ivory") R.style.Theme_FilmFoundry_Ivory else R.style.Theme_FilmFoundry_Dark)
        super.onCreate(savedInstanceState)
        movies=MovieSources(filesDir,AndroidMovieProbe::read)
        shell=MobileShell(this,theme == "ivory",savedInstanceState?.getString("mobile_page") ?: "start")
        status=shell.status
        status.text=copy("装卷后逐格拍摄","Load a roll to capture frames")
        val cameraPanel=shell.cameraSurface
        preview=PreviewView(this)
        preview.scaleType=PreviewView.ScaleType.FIT_CENTER
        preview.setBackgroundColor(android.graphics.Color.BLACK)
        preview.contentDescription=copy("相机实时取景","Live camera viewfinder")
        cameraPanel.addView(preview,android.widget.FrameLayout.LayoutParams(-1,-1))
        gateView=ViewfinderBaffles(this);cameraPanel.addView(gateView,android.widget.FrameLayout.LayoutParams(-1,-1))
        touchControls=com.filmfoundry.app.ui.CameraTouchControls(preview,shell,
            { cameraReady && !bindingCamera && !importing && rollActions?.captureInProgress() != true && shell.selected == MobileShell.Page.CAPTURE },
            { status.text=it },::openFinderLens,::openFinderFlash,::nativeFlashAvailable,finderExpert)
        cameraPanel.addView(touchControls,android.widget.FrameLayout.LayoutParams(-1,-1))
        cameraButton=Button(this).apply {
            this.text=copy("启用相机预览","Enable viewfinder")
            setOnClickListener {
                if(!importing) {
                    cameraWanted=true
                    if(checkSelfPermission(Manifest.permission.CAMERA) != PackageManager.PERMISSION_GRANTED) cameraPermission.launch(Manifest.permission.CAMERA)
                    else bindCamera(force=true)
                }
            }
        }
        cameraPanel.addView(cameraButton,android.widget.FrameLayout.LayoutParams(-2,-2,android.view.Gravity.CENTER))
        rollActions=runCatching {
            com.filmfoundry.app.ui.RollActions(this,shell,movieWorker,{ if(cameraReady) camera else null },
                { cameraButton.performClick() },{ !importing },
                { active -> importing=active;updateMovieControls(false) },{ status.text=it },{ gateView.closed=it },{
                    if(!importing) {
                        rollActions?.pauseCapture()
                        cameraWanted=false;cameraReady=false;bindingCamera=false;touchControls.bind(null);camera?.unbind();camera=null;boundTarget=null;failedTarget=null
                        preview.visibility=View.INVISIBLE;cameraButton.visibility=View.VISIBLE
                    }
                },{ if(::importButton.isInitialized) updateMovieControls(false) }, ::streamSceneFrames,{ selectedCamera },::openNativeCameraSettings,{ nativeSettings.format },{ format,model -> gateView.filmFormat=format;gateView.gateModel=model },::applyFilmIso,{ gateView.frameStyle=it },finderExpert)
        }.getOrElse { status.text=copy("胶卷库读取失败：","Could not open roll library: ")+it.message;null }
        val appearance=shell.card(MobileShell.Page.SETTINGS,"外观与语言","Appearance & language")
        shell.appearance(appearance,theme,preferences.getString("language","system") ?: "system") { nextTheme,nextLanguage ->
            if(!importing && !com.filmfoundry.app.session.ProcessingWork.busy && rollActions?.captureInProgress() != true) {
                rollActions?.saveDrafts()
                preferences.edit().putString("theme",nextTheme).putString("language",nextLanguage).putString("page",shell.selected.key).apply()
                recreate()
            }
        }
        shell.settingsEntry("后置处理","Processing","计算与加速、内存与存储","Compute & acceleration, memory and storage",MobileShell.Page.SETTINGS_PROCESS)
        shell.settingsEntry("材料与配方","Recipes","材料、药液、扫描配方和分享码","Materials, chemistry, scanner recipes and share codes",MobileShell.Page.SETTINGS_RECIPES)
        shell.settingsEntry("附加工具","Additional tools","视频来源与材料母带","Video sources and material masters",MobileShell.Page.SETTINGS_TOOLS)
        shell.settingsEntry("关于软件","About","版本、作者与源码","Version, author and source",MobileShell.Page.SETTINGS_ABOUT)
        shell.settingsEntry("使用指南","Guide","拍摄、冲洗、观片与资源管理","Capture, develop, view and manage",MobileShell.Page.SETTINGS_GUIDE)
        com.filmfoundry.app.ui.MobileGuide.install(shell)
        val about=shell.card(MobileShell.Page.SETTINGS_ABOUT,"Film Foundry","Film Foundry",
            "拍摄、冲洗、观片与扫描。","Capture, develop, view and scan.")
        about.addView(android.widget.ImageView(this).apply {
            setImageDrawable(applicationInfo.loadIcon(packageManager));contentDescription="Film Foundry"
        },LinearLayout.LayoutParams(shell.dp(64),shell.dp(64)).apply { bottomMargin=shell.dp(12) })
        @Suppress("DEPRECATION")
        val packageInfo=runCatching { packageManager.getPackageInfo(packageName,0) }.getOrNull()
        val version=packageInfo?.versionName.orEmpty().ifBlank { "—" }
        @Suppress("DEPRECATION")
        val build=packageInfo?.let { if(android.os.Build.VERSION.SDK_INT>=28) it.longVersionCode else it.versionCode.toLong() }?.toString() ?: "—"
        shell.note(about,"版本：Android $version","Version: Android $version")
        shell.note(about,"构建编号：$build","Build: $build")
        shell.note(about,"作者：soundsec","Author: soundsec")
        shell.action(about,"开源许可与第三方声明","Open-source licenses") {
            val notice = assets.open("third_party_notices.txt").bufferedReader().use { it.readText() }
            val content = TextView(this).apply {
                text = notice; textSize = 12f; setTextIsSelectable(true)
                setPadding(shell.dp(16),shell.dp(16),shell.dp(16),shell.dp(16))
            }
            val scroll = android.widget.ScrollView(this).apply { addView(content) }
            android.app.AlertDialog.Builder(this).setTitle(copy("开源许可","Open-source licenses"))
                .setView(scroll).setPositiveButton(copy("关闭","Close"),null).show()
        }
        runCatching {
            val collection=com.filmfoundry.app.session.SeasonalUnlock(this)
            about.addView(TextView(this).apply {
                text="ID ${collection.identity}\nXOR · HEX ${collection.cipher()}"
                textSize=11f;setTextIsSelectable(true)
                setOnLongClickListener {
                    android.app.AlertDialog.Builder(this@ProcessHostActivity).setMessage(copy(
                        "以编号的 ASCII 字节循环异或，十六进制还原文字。","Repeat the ID's ASCII bytes as the XOR key; decode the hex into text."))
                        .setPositiveButton(copy("知道了","Got it"),null).show();true
                }
            })
        }
        shell.action(about,"GitHub · soundsec/Film-Foundry","GitHub · soundsec/Film-Foundry") {
            runCatching { startActivity(android.content.Intent(android.content.Intent.ACTION_VIEW,
                android.net.Uri.parse("https://github.com/soundsec/Film-Foundry"))) }.onFailure {
                android.widget.Toast.makeText(this,copy("无法打开链接，请检查浏览器","Could not open the link. Check your browser."),android.widget.Toast.LENGTH_SHORT).show()
            }
        }
        val tools=shell.card(MobileShell.Page.SETTINGS_TOOLS,"附加工具","Additional tools",
            "视频来源与材料母带独立存放，不占用静照胶卷的格位。","Video sources and material masters are separate from still-photo rolls.")
        tools.addView(TextView(this).apply { this.text=copy(
            "已有视频可分帧导入。连续拍摄将采用逐格曝光与有限卷长；普通无限录像入口已撤下。",
            "Existing video can be imported frame by frame. Continuous capture will use per-frame exposure and finite roll length.") })
        importButton=Button(this).apply { this.text=copy("导入视频文件","Import video file");setOnClickListener { importMovie.launch(arrayOf("video/*")) } }
        tools.addView(importButton)
        exportButton=Button(this).apply { this.text=copy("导出原视频到文件…","Export original source…");setOnClickListener {
            if(sourceRows.isEmpty()) status.text=copy("尚无视频源","No movie sources yet")
            else {
                val rows=sourceRows
                AlertDialog.Builder(this@ProcessHostActivity).setTitle(copy("选择视频源","Choose source"))
                    .setItems(rows.map { it.displayName }.toTypedArray()) { _, index ->
                        exportSource=rows[index];exportMovie.launch(rows[index].displayName)
                    }.show()
            }
        } }
        tools.addView(exportButton)
        sourceList=TextView(this)
        tools.addView(sourceList)
        masterActions=com.filmfoundry.app.ui.MovieMasterActions(this,tools,movieWorker,{sourceRows.toList()},
            { active -> importing=active;updateMovieControls();if(active) { cameraReady=false;camera?.unbind();camera=null } },
            { message -> status.text=message },{ !importing && !com.filmfoundry.app.session.ProcessingWork.busy && rollActions?.captureInProgress() != true })
        shell.pause.setOnClickListener { com.filmfoundry.app.session.ProcessingWork.pauseCurrent();rollActions?.cancel();masterActions?.cancel();shell.pause.isEnabled=false }
        val back=object: OnBackPressedCallback(shell.selected != MobileShell.Page.START) {
            override fun handleOnBackPressed() { shell.back() }
        }
        onBackPressedDispatcher.addCallback(this,back)
        shell.onPageChanged={ page ->
            if(page!=MobileShell.Page.CAPTURE) isoLock.leave()
            rollActions?.pageSelected(page)
            back.isEnabled=page != MobileShell.Page.START
            preferences.edit().putString("page",page.key).apply()
            applyPageOrientation();updateCameraForPage()
        }
        shell.decorate()
        setContentView(shell.root)
        applyPageOrientation()
        shell.root.requestApplyInsets()
        cameraWanted=checkSelfPermission(Manifest.permission.CAMERA) == PackageManager.PERMISSION_GRANTED
        if (cameraWanted) {
            loadCameraChoices { updateCameraForPage() }
        } else {
            status.text=copy("装卷后启用相机；导入不需要相机权限","Load a roll and enable the camera; import needs no camera permission")
        }
        refreshMovies()
    }

    private fun updateMovieControls(refreshRolls: Boolean=true) {
        val blocked=importing || com.filmfoundry.app.session.ProcessingWork.busy || rollActions?.captureInProgress() == true
        importButton.isEnabled=!blocked
        exportButton.isEnabled=!blocked
        cameraButton.isEnabled=!blocked
        shell.setBusy(blocked)
        updateCameraForPage()
        if(refreshRolls) rollActions?.refresh()
    }

    private fun updateCameraForPage() {
        if(::shell.isInitialized && shell.selected!=MobileShell.Page.VIEWER) cancelNaturalLight()
        if(importing || com.filmfoundry.app.session.ProcessingWork.busy || isDestroyed) return
        if(shell.selected != MobileShell.Page.CAPTURE) rollActions?.pauseCapture()
        // Let accepted shutters and queued motion frames finish before releasing their camera.
        if(rollActions?.captureInProgress() == true) return
        if(resetCameraWhenIdle) {
            cameraRollback?.let { selectedCamera=it.first;nativeSettings=it.second };cameraRollback=null
            cameraReady=false;bindingCamera=false;touchControls.bind(null);camera?.unbind();camera=null;boundTarget=null;failedTarget=null
            resetCameraWhenIdle=false
        }
        if(shell.selected == MobileShell.Page.CAPTURE && cameraWanted) bindCamera()
        else if(shell.selected != MobileShell.Page.CAPTURE && camera != null) {
            rollActions?.pauseCapture()
            cameraReady=false;bindingCamera=false;touchControls.bind(null);camera?.unbind();camera=null;boundTarget=null;failedTarget=null
            cameraButton.visibility=View.VISIBLE
        }
    }
    private fun applyPageOrientation() {
        val capture=shell.selected in listOf(MobileShell.Page.CAPTURE,MobileShell.Page.LOADING)
        val wanted=if(capture) android.content.pm.ActivityInfo.SCREEN_ORIENTATION_LANDSCAPE else android.content.pm.ActivityInfo.SCREEN_ORIENTATION_PORTRAIT
        if(requestedOrientation != wanted) requestedOrientation=wanted
        val darkCanvas=capture || shell.selected==MobileShell.Page.VIEWER
        val background=if(darkCanvas) android.graphics.Color.BLACK else if(shell.ivory) 0xFFF2EEE5.toInt() else 0xFF141414.toInt()
        window.decorView.setBackgroundColor(background)
        window.setBackgroundDrawable(android.graphics.drawable.ColorDrawable(background))
        window.clearFlags(android.view.WindowManager.LayoutParams.FLAG_TRANSLUCENT_STATUS or android.view.WindowManager.LayoutParams.FLAG_TRANSLUCENT_NAVIGATION)
        window.addFlags(android.view.WindowManager.LayoutParams.FLAG_DRAWS_SYSTEM_BAR_BACKGROUNDS)
        if(android.os.Build.VERSION.SDK_INT>=29) {
            window.isStatusBarContrastEnforced=false
            window.isNavigationBarContrastEnforced=false
        }
        if(android.os.Build.VERSION.SDK_INT>=28) window.attributes=window.attributes.apply {
            layoutInDisplayCutoutMode=if(capture) android.view.WindowManager.LayoutParams.LAYOUT_IN_DISPLAY_CUTOUT_MODE_SHORT_EDGES
                else android.view.WindowManager.LayoutParams.LAYOUT_IN_DISPLAY_CUTOUT_MODE_DEFAULT
        }
        if(android.os.Build.VERSION.SDK_INT>=30) {
            window.setDecorFitsSystemWindows(false) // MobileShell applies bars/cutout/IME insets once.
            @Suppress("DEPRECATION")
            window.statusBarColor=android.graphics.Color.TRANSPARENT
            @Suppress("DEPRECATION")
            window.navigationBarColor=android.graphics.Color.TRANSPARENT
            window.insetsController?.setSystemBarsAppearance(
                if(!darkCanvas && shell.ivory) android.view.WindowInsetsController.APPEARANCE_LIGHT_STATUS_BARS or android.view.WindowInsetsController.APPEARANCE_LIGHT_NAVIGATION_BARS else 0,
                android.view.WindowInsetsController.APPEARANCE_LIGHT_STATUS_BARS or android.view.WindowInsetsController.APPEARANCE_LIGHT_NAVIGATION_BARS)
        }
        if(android.os.Build.VERSION.SDK_INT >= 30) window.insetsController?.let {
            it.systemBarsBehavior=android.view.WindowInsetsController.BEHAVIOR_SHOW_TRANSIENT_BARS_BY_SWIPE
            if(capture) it.hide(android.view.WindowInsets.Type.systemBars()) else it.show(android.view.WindowInsets.Type.systemBars())
        } else {
            @Suppress("DEPRECATION")
            window.decorView.systemUiVisibility=if(capture) View.SYSTEM_UI_FLAG_FULLSCREEN or View.SYSTEM_UI_FLAG_HIDE_NAVIGATION or View.SYSTEM_UI_FLAG_IMMERSIVE_STICKY else if(!darkCanvas && shell.ivory) View.SYSTEM_UI_FLAG_LIGHT_STATUS_BAR or View.SYSTEM_UI_FLAG_LIGHT_NAVIGATION_BAR else 0
            @Suppress("DEPRECATION")
            window.statusBarColor=background
            @Suppress("DEPRECATION")
            window.navigationBarColor=background
        }
    }
    override fun onWindowFocusChanged(hasFocus: Boolean) {
        super.onWindowFocusChanged(hasFocus)
        if(hasFocus && ::shell.isInitialized) applyPageOrientation()
    }
    override fun onConfigurationChanged(newConfig: Configuration) {
        super.onConfigurationChanged(newConfig)
        shell.root.requestApplyInsets()
        shell.root.post { applyPageOrientation();updateCameraForPage() }
    }

    private fun refreshMovies() {
        movieWorker.execute {
            val result=runCatching { movies.list() }
            runOnUiThread { if(!isDestroyed) result.fold({ rows ->
                sourceRows=rows
                sourceList.text=rows.joinToString("\n") { "${it.displayName} · ${it.info.width}×${it.info.height} · ${it.info.durationUs/1_000_000.0}s · ${it.info.audioTracks} audio" }
            },{ sourceList.text=it.message }) }
        }
    }

    override fun onSaveInstanceState(outState: Bundle) {
        outState.putString("mobile_page",shell.selected.key)
        super.onSaveInstanceState(outState)
    }

    override fun onStart() {
        super.onStart()
        observeProcessing?.invoke()
        observeProcessing=com.filmfoundry.app.session.ProcessingWork.observe {
            if(!isDestroyed && ::shell.isInitialized) {
                if(rollActions?.needsProcessingReload()==true) recreate()
                else { updateMovieControls(false);rollActions?.showProcessingState() }
            }
        }
        if(::shell.isInitialized) shell.root.post {
            rollActions?.resumeScanPreview()
            if(!readingCameras && !openingNativeSettings) updateCameraForPage()
        }
    }
    override fun onStop() {
        cancelNaturalLight(keepPermission=true)
        if(::shell.isInitialized) shell.closeQuickControls()
        isoLock.leave()
        observeProcessing?.invoke();observeProcessing=null
        rollActions?.saveDrafts();rollActions?.pauseCapture(cancelWaiting=true);rollActions?.cancel(keepDisplayedPreview=true,pauseTask=false)
        resetCameraWhenIdle=true
        super.onStop()
        updateCameraForPage()
    }

    @Suppress("DEPRECATION")
    override fun onTrimMemory(level: Int) {
        super.onTrimMemory(level)
        if(level==android.content.ComponentCallbacks2.TRIM_MEMORY_RUNNING_LOW || level==android.content.ComponentCallbacks2.TRIM_MEMORY_RUNNING_CRITICAL || level>=android.content.ComponentCallbacks2.TRIM_MEMORY_BACKGROUND)
            rollActions?.trimMemory(level==android.content.ComponentCallbacks2.TRIM_MEMORY_RUNNING_CRITICAL || level>=android.content.ComponentCallbacks2.TRIM_MEMORY_MODERATE)
    }
    override fun onLowMemory() { super.onLowMemory();rollActions?.trimMemory(true) }
    override fun onDestroy() {
        cancelNaturalLight()
        rollActions?.close();touchControls.bind(null)
        camera?.unbind()
        camera = null
        movieWorker.shutdown()
        super.onDestroy()
    }

    private fun streamSceneFrames(deliver: (com.filmfoundry.platform.ViewerPreviewFrame)->Unit,failed: (Throwable)->Unit,ready: ()->Boolean): ()->Unit {
        cancelNaturalLight()
        val ticket=++sceneSessionSerial
        fun active()=sceneSessionSerial==ticket && !isDestroyed
        fun fail(error: Throwable) {
            if(!active()) return
            cancelNaturalLight();failed(error);updateCameraForPage()
        }
        fun bind() {
            if(!active()) return
            if((importing && !com.filmfoundry.app.session.ProcessingWork.busy) || camera!=null || shell.selected!=MobileShell.Page.VIEWER || !lifecycle.currentState.isAtLeast(androidx.lifecycle.Lifecycle.State.STARTED)) {
                fail(IllegalStateException(copy("现在不能打开实景观片","Scene viewing cannot open now")));return
            }
            samplingLight=true
            val adapter=CameraXCameraAdapter(applicationContext,this,viewerAnalysisEnabled=true,viewerOnly=true)
            viewingCamera=adapter
            val first=java.util.concurrent.atomic.AtomicBoolean(true)
            adapter.streamViewerFrames({ frame ->
                if(active()) {
                    if(first.compareAndSet(true,false)) runOnUiThread { if(active()) { lightTimeout?.let(lightHandler::removeCallbacks);lightTimeout=null } }
                    runCatching { deliver(frame) }.onFailure { error -> runOnUiThread { fail(error) } }
                }
            },::fail,ready)
            lightTimeout=Runnable { fail(IllegalStateException(copy("相机启动超时，请重试","Viewing camera timed out; retry"))) }.also { lightHandler.postDelayed(it,8000) }
            adapter.bind { error -> if(active() && error!=null) fail(error) }
        }
        if(checkSelfPermission(Manifest.permission.CAMERA)!=PackageManager.PERMISSION_GRANTED) {
            naturalLightAfterPermission=::bind to ::fail;cameraPermission.launch(Manifest.permission.CAMERA)
        } else bind()
        return { if(active()) cancelNaturalLight() }
    }
    private fun cancelNaturalLight(keepPermission: Boolean=false) {
        if(keepPermission && naturalLightAfterPermission!=null && viewingCamera==null) return
        sceneSessionSerial++
        lightTimeout?.let(lightHandler::removeCallbacks);lightTimeout=null
        if(!keepPermission) naturalLightAfterPermission=null
        viewingCamera?.unbind();viewingCamera=null;samplingLight=false
    }

    private fun filmIsoChoice(): Pair<Int?,Long?> {
        val prefs=getSharedPreferences("mobile_ui",MODE_PRIVATE)
        if(prefs.getString("film_iso_mode","auto")!="manual") return null to null
        val iso=prefs.getInt("film_iso",0)
        val ms=prefs.getString("film_shutter_ms",null)?.toDoubleOrNull()
        if(iso<=0 || ms==null || !ms.isFinite() || ms<=0) return null to null
        return iso to (ms*1e6).toLong()
    }
    private fun exposureFromFilm(settings: com.filmfoundry.platform.NativeCaptureSettings): com.filmfoundry.platform.NativeCaptureSettings {
        val choice=filmIsoChoice()
        if(choice.first==null) return settings.copy(iso=null,exposureNs=null)
        val lens=selectedCamera
        if(lens!=null) {
            val chars=lens.characteristics
            val manual=(chars.get(android.hardware.camera2.CameraCharacteristics.REQUEST_AVAILABLE_CAPABILITIES) ?: intArrayOf()).contains(android.hardware.camera2.CameraCharacteristics.REQUEST_AVAILABLE_CAPABILITIES_MANUAL_SENSOR)
            val isoOk=chars.get(android.hardware.camera2.CameraCharacteristics.SENSOR_INFO_SENSITIVITY_RANGE)?.contains(choice.first)==true
            val timeOk=chars.get(android.hardware.camera2.CameraCharacteristics.SENSOR_INFO_EXPOSURE_TIME_RANGE)?.contains(choice.second)==true
            if(!manual || !isoOk || !timeOk) return settings.copy(iso=null,exposureNs=null)
        }
        return settings.copy(iso=choice.first,exposureNs=choice.second,lockAe=false,flash="off")
    }
    private fun applyFilmIso() {
        isoLock.reset()
        nativeSettings=com.filmfoundry.app.session.CaptureAutomationPolicy.apply(exposureFromFilm(nativeSettings))
        val iso=nativeSettings.iso
        status.text=if(iso==null) copy("ISO 保持自动，拍摄中锁定","ISO stays automatic and locked while shooting")
            else copy("ISO $iso 已写入胶卷设置，拍摄中锁定","ISO $iso is saved in film settings and locked while shooting")
    }
    private fun captureSize(lens: com.filmfoundry.platform.NativeCameraCatalog.Lens,settings: com.filmfoundry.platform.NativeCaptureSettings): Pair<Int,Int>? {
        val parts=rollActions?.captureTarget()?.split(":").orEmpty()
        if(parts.firstOrNull()=="motion") {
            val w=parts.getOrNull(1)?.toIntOrNull() ?: 0;val h=parts.getOrNull(2)?.toIntOrNull() ?: 0
            return if(w>0 && h>0) w to h else null
        }
        fun format(value: String)=when(value) { "raw"->android.graphics.ImageFormat.RAW_SENSOR;"ultra_hdr"->android.graphics.ImageFormat.JPEG_R;else->android.graphics.ImageFormat.JPEG }
        return rollActions?.captureSizeForLens(lens.sizes(format(settings.format)),selectedCamera?.sizes(format(nativeSettings.format)).orEmpty())
    }
    private fun cameraBindingKey(lens: com.filmfoundry.platform.NativeCameraCatalog.Lens,settings: com.filmfoundry.platform.NativeCaptureSettings,size: Pair<Int,Int>?) =
        (rollActions?.captureTarget() ?: "still:0:0:0:0")+"@"+lens.key+"@"+settings.toString()+"@${size?.first ?: 0}:${size?.second ?: 0}"

    private fun bindCamera(force: Boolean=false) {
        if(importing || bindingCamera || samplingLight || rollActions?.captureInProgress() == true || isDestroyed || shell.selected != MobileShell.Page.CAPTURE) return
        if(!lifecycle.currentState.isAtLeast(androidx.lifecycle.Lifecycle.State.STARTED)) return
        if(selectedCamera==null) { loadCameraChoices { bindCamera(force) };return }
        if(resources.configuration.orientation != Configuration.ORIENTATION_LANDSCAPE) {
            android.util.Log.d("FilmFoundryCamera","Waiting for landscape resources; displayRotation=${preview.display?.rotation}")
            return
        }
        isoLock.selectRoll(rollActions?.captureRollId())
        if(!isoUnlocked) nativeSettings=exposureFromFilm(nativeSettings)
        nativeSettings=com.filmfoundry.app.session.CaptureAutomationPolicy.apply(nativeSettings)
        val captureTarget=rollActions?.captureTarget() ?: "still:0:0:0:0"
        val lens=selectedCamera!!
        val size=runCatching { captureSize(lens,nativeSettings) }.getOrElse {
            status.text=copy("当前格需要原拍摄尺寸，请先过片或换回原镜头。","The current frame needs its original capture size. Advance or restore the previous lens.");return
        }
        val target=cameraBindingKey(lens,nativeSettings,size)
        if(camera != null && boundTarget == target) return
        if(!force && camera == null && failedTarget == target) return
        val parts=captureTarget.split(":")
        val motion=parts[0] == "motion"
        val width=size?.first ?: 0
        val height=size?.second ?: 0
        val low=parts.getOrNull(3)?.toIntOrNull() ?: 0
        val high=parts.getOrNull(4)?.toIntOrNull() ?: 0
        val captureImageFormat=when(nativeSettings.format) { "raw"->android.graphics.ImageFormat.RAW_SENSOR;"ultra_hdr"->android.graphics.ImageFormat.JPEG_R;else->android.graphics.ImageFormat.JPEG }
        if(motion && width>0 && height>0 && lens.sizes(captureImageFormat).none { (it.first==width && it.second==height) || (it.first==height && it.second==width) }) {
            status.text=copy("这颗镜头不支持本卷的拍摄尺寸，请换回兼容镜头","This lens cannot provide this roll's capture size; choose a compatible lens");return
        }
        bindingCamera=true
        rollActions?.pauseCapture()
        touchControls.bind(null);camera?.unbind();camera=null;cameraReady=false;boundTarget=null
        preview.visibility=View.VISIBLE
        status.text=copy("正在启用相机…","Starting camera…")
        val adapter = CameraXCameraAdapter(applicationContext, this,
            previewSurfaceProvider=preview.surfaceProvider,viewerAnalysisEnabled=false,
            targetRotation=preview.display?.rotation ?: android.view.Surface.ROTATION_90,
            frameWidth=width,frameHeight=height,motionFrames=motion,
            nativeLens=lens,nativeSettings=if(motion) com.filmfoundry.platform.NativeCaptureSettings() else nativeSettings,
            motionFps=if(motion && low > 0 && high >= low) Range(low,high) else null)
        camera = adapter
        adapter.meterWatch={ meter -> runOnUiThread { if(!isDestroyed && camera===adapter) rollActions?.showEvaluativeMeter(meter.state,meter.ev100) } }
        boundTarget=target
        failedTarget=null
        android.util.Log.d("FilmFoundryCamera","Binding target=$target orientation=${resources.configuration.orientation} rotation=${preview.display?.rotation}")
        adapter.bind(onBound={ bindError ->
            if(!isDestroyed && camera === adapter) {
                val error=bindError ?: runCatching {
                    val actual=adapter.plannedDimensions() ?: if(width>0 && height>0) width to height else adapter.stillGeometry()?.let { it[0] to it[1] }
                    if(actual!=null) rollActions?.validateCaptureGeometry(actual.first,actual.second)
                }.exceptionOrNull()
                bindingCamera=false
                if(error == null) android.util.Log.d("FilmFoundryCamera","Camera bound: $target")
                else android.util.Log.e("FilmFoundryCamera","Camera bind failed: $target",error)
                cameraReady=error == null
                touchControls.bind(if(cameraReady) adapter else null)
                cameraButton.visibility=if(cameraReady) View.GONE else View.VISIBLE
                val actual=adapter.plannedDimensions() ?: adapter.stillGeometry()?.let { it[0] to it[1] }
                actual?.let { gateView.geometry(it.first,it.second) }
                if(error != null) {
                    adapter.unbind();camera=null;boundTarget=null;failedTarget=target
                    ToastMessage(copy("相机未能启动；本卷已保留，可返回后重试。","Camera could not start. Your roll is retained; return and retry."))
                }
                else getSharedPreferences("native_camera",MODE_PRIVATE).edit().putString("lens",lens.key).apply()
                val rollback=cameraRollback;cameraRollback=null
                if(error!=null && rollback!=null) {
                    selectedCamera=rollback.first;nativeSettings=rollback.second;failedTarget=null
                    ToastMessage(copy("切换未完成，正在恢复原相机：","Switch failed; restoring the previous camera: ")+error.message)
                    bindCamera(force=true);return@bind
                }
                rollActions?.refresh()
                status.text=error?.message ?: if(motion) copy("过片相机就绪","Motion camera ready") else copy("相机就绪，可拍一格","Camera ready; expose a frame")
            }
        })
    }

}
