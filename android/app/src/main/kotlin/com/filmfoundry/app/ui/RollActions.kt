package com.filmfoundry.app.ui

import android.app.AlertDialog
import android.graphics.Bitmap
import android.widget.*
import androidx.activity.ComponentActivity
import com.filmfoundry.app.session.RollDevelopmentSession
import com.filmfoundry.app.session.DevelopmentPhotoPlan
import com.filmfoundry.app.session.DeferredShootSession
import com.filmfoundry.app.session.StillCaptureQueue
import com.filmfoundry.app.session.RollLoadingSession
import com.filmfoundry.app.session.LibraryResourceSession
import com.filmfoundry.app.session.NegativeDisplayStore
import com.filmfoundry.app.session.NegativeViewCache
import com.filmfoundry.app.session.BatchScanSession
import com.filmfoundry.app.session.PrivateScanTaskStore
import com.filmfoundry.app.session.ScanTask
import com.filmfoundry.app.session.RollLibraryContents
import com.filmfoundry.app.session.ViewerFrameRows
import com.filmfoundry.app.session.ViewerRowScratch
import com.filmfoundry.app.session.LibraryExportSession
import com.filmfoundry.app.session.LibraryExportPlanner
import com.filmfoundry.app.session.LibraryExportRequest
import com.filmfoundry.app.session.LibraryFacts
import com.filmfoundry.app.session.LibraryInformation
import com.filmfoundry.app.session.LibraryProvenance
import com.filmfoundry.app.session.LibraryOrigin
import com.filmfoundry.interchange.*
import com.filmfoundry.platform.*
import com.filmfoundry.workflow.*
import java.util.UUID
import java.util.concurrent.ArrayBlockingQueue
import java.util.concurrent.Executor
import java.util.concurrent.TimeUnit
import java.util.concurrent.atomic.AtomicBoolean
import kotlinx.coroutines.runBlocking

/** Controls only: source/formation/observation formulas remain in the existing sessions and facade. */
class RollActions(private val activity: ComponentActivity, private val shell: MobileShell, private val worker: Executor,
    private val camera: () -> CameraAdapter?, private val requestCamera: () -> Unit,
    private val canRun: () -> Boolean, private val busy: (Boolean) -> Unit, private val statusChanged: (String) -> Unit,
    private val baffles: (Boolean)->Unit={},private val stopCamera: ()->Unit={},private val syncCamera: ()->Unit={},
    private val streamScene: ((ViewerPreviewFrame)->Unit,(Throwable)->Unit,()->Boolean)->(()->Unit) = { _,failed,_ -> failed(IllegalStateException("camera viewing is not connected"));({}) },
    private val nativeLens: ()->NativeCameraCatalog.Lens?={ null },private val nativeCameraSettings: ()->Unit={},
    private val nativeFormat: ()->String={ "jpeg" },private val frameFormat: (String,String)->Unit={_,_->},
    private val onFilmExposure: ()->Unit={},private val onFinderStyle: (String)->Unit={},private val expert: FinderExpertPort?=null) {
    private val taskOwner=Any()
    private val openedRevision=com.filmfoundry.app.session.ProcessingWork.snapshot.revision
    private var pendingCompletion: (() -> Unit)?=null
    private fun canBrowse()=!activity.isDestroyed && !captureInProgress() && (com.filmfoundry.app.session.ProcessingWork.browsingAllowed || available())
    private fun available()=canRun() && com.filmfoundry.app.session.ProcessingWork.availableTo(taskOwner)
    private val status: (String)->Unit={ message ->
        com.filmfoundry.app.session.ProcessingWork.progress(taskOwner,message)
        if(!activity.isDestroyed) statusChanged(message)
    }
    private fun taskStatus(message: String) {
        com.filmfoundry.app.session.ProcessingWork.progress(taskOwner,message)
        activity.runOnUiThread { if(!activity.isDestroyed) statusChanged(message) }
    }
    fun needsProcessingReload()=com.filmfoundry.app.session.ProcessingWork.needsReload(taskOwner,openedRevision)
    fun showProcessingState() {
        if(activity.lifecycle.currentState.isAtLeast(androidx.lifecycle.Lifecycle.State.STARTED)) {
            val pending=pendingCompletion;pendingCompletion=null;pending?.invoke()
        }
        val task=com.filmfoundry.app.session.ProcessingWork.snapshot
        if(task.active && task.owner!==taskOwner) statusChanged(task.message)
        refresh()
    }
    private val storage = AndroidStorageAdapter(activity.applicationContext)
    private val seasonal=com.filmfoundry.app.session.SeasonalUnlock(activity)
    private val baseBuiltin=activity.assets.open("phone_builtin_recipes.json").use { BuiltinRecipeCatalog.read(it.readBytes()) }
    private val builtin get()=seasonal.catalog(baseBuiltin)
    private val basePairings=activity.assets.open("phone_creative_pairings.json").use { PhoneCreativePairings.read(it.readBytes()) }
    private val seasonalPairings by lazy { seasonal.pairings() }
    private val pairings get()=basePairings+(if(seasonal.unlocked) seasonalPairings else emptyList())
    private val library = FilmLibrary(PrivateLibraryIndexStore(storage))
    private val store = MediumStore(storage)
    private val opticalProxies=com.filmfoundry.app.session.OpticalProxyStore(activity.filesDir,activity.cacheDir)
    private val facade = DefaultWorkflowFacade()
    private val displayObservation=OpticalPreviewSession(facade,retainPrepared=true)
    private val captureStore = CaptureReceiptStore(activity.filesDir)
    private val preferences=activity.getSharedPreferences("capture_conditions",android.content.Context.MODE_PRIVATE)
    private val gpuEnabled get()=preferences.getBoolean("gpu_source_linearization",true)
    private val rowBudget: ()->Long={ minOf(AndroidProcessingMemory.snapshot(activity).budgetBytes,Runtime.getRuntime().maxMemory()/5) }
    private var jobGpuEnabled=false
    private var jobRowWorkers=1
    private var jobOpticalHandoff=true
    private val performance=com.filmfoundry.app.session.DevicePerformance(activity.applicationContext)
    private val performanceUi=PerformanceTestUi(activity,shell,performance,::idle,status)
    private val hardwareSelfTestUi=HardwareSelfTestUi(activity,shell,::idle)
    private var activePerformance: com.filmfoundry.app.session.DevicePerformance.Run?=null
    private val pixelCompute=RetainedPixelCompute({ jobGpuEnabled },
        { backend -> preferences.edit().putString("last_decode_backend",backend).apply() })
    private val deferred = DeferredShootSession(library,store,captureStore,activity.cacheDir,facade,{ file ->
        if(file.name.endsWith(".dng")) DngStoredExposurePixels(file)
        else AndroidStoredExposurePixels(file,pixelCompute.open(),
            memory={ AndroidProcessingMemory.snapshot(activity) },bounded=false,releaseCompute=false,
            report={ backend -> preferences.edit().putString("last_source_backend",backend).apply() })
    },rowBudget)
    private val rollTasks=RollDevelopmentSession(library,store,PrivateDevelopmentTaskStore(storage),deferred,facade,rowBudget,{ jobRowWorkers })
    private val cancelled = AtomicBoolean(false)
    private var running = false
    private var loadingView: FilmLoadingView?=null
    private var loadingSession: RollLoadingSession?=null
    private val stillQueue=StillCaptureQueue<StillShot>()
    @Volatile private var drainingStill=false
    @Volatile private var motionJob=false
    @Volatile private var motionDraining=false
    private val motionLock=Any()
    private var motionRemaining=0
    private var motionCount=0
    private var motionStarted=0L
    private val motionQueue=ArrayBlockingQueue<MotionFrame>(2)
    private data class StillShot(val id: String,val hold: Boolean,val advance: String,val wide: Boolean,val settings: Map<String,Any?>)
    private data class MotionFrame(val jpeg: ByteArray,val width: Int,val height: Int)
    private var current = library.shootingRolls().firstOrNull { it.rollId == preferences.getString("current_roll",null) }?.rollId
        ?: library.shootingRolls().lastOrNull()?.rollId
    private val recipe = EditText(activity)
    private val recipeInput=com.filmfoundry.workflow.RecipeInput()
    private var lastValidRecipeText=""
    private val counter = TextView(activity)
    private val frameCount = TextView(activity)
    private val readout=CaptureReadout(shell)
    private val image = TouchPhotoView(activity)
    private val scanImage=ImageView(activity)
    private lateinit var scanComparison: ScanComparison
    private lateinit var scanFeedback: ScanPreviewFeedback
    private val developmentImage=ImageView(activity)
    private val controls = mutableListOf<android.view.View>()
    private val multiple: Button
    private var holdFirst=false
    private var exposureTarget: Int?=null
    private var exposureBusy=false
    private var exposureOwner: CameraXCameraAdapter?=null
    private var exposureEpoch=0L
    private val autoAdvance=CheckBox(activity)
    private val panorama=CheckBox(activity)
    private val wideGate: Button
    private val advanceMode: Button
    private val startSummary=TextView(activity)
    private lateinit var startShooting: Button
    private val references=com.filmfoundry.app.session.RollDirectReferenceStore(activity.filesDir)
    private val rollMarkCache=linkedMapOf<String,Pair<Bitmap,Long>>()
    private val menuPause=Button(activity)
    private val autoFirstPreview=CheckBox(activity)
    private val advance: Button
    private val shoot: Button
    private val seal: Button
    private val rollRows: LinearLayout
    private val resultRows: LinearLayout
    private val rollDetailRows: LinearLayout
    private val batchRows: LinearLayout
    private var detailRollId: String?=null
    private var detailBatchId: String?=null
    private var batchPage=0
    private val scanRows: LinearLayout
    private val scans=PrivateScanStore(storage)
    private val information=LibraryInformation(activity.filesDir)
    private val informationDialogs=LibraryInfoDialog(activity,shell,worker)
    private val provenance=LibraryProvenance(library,storage::existsPrivate)
    private val sourceDialogs=LibrarySourceDialog(activity,shell,worker)
    private val libraryExports=LibraryExportSession(storage,store,{ ScanImageFormat.valueOf(preferences.getString("confirmed_export_format","PNG")!!) },
        { preferences.getInt("confirmed_export_jpeg_quality",95) },{ negativeRotation(it.resultId) })
    private val exportPlanner=LibraryExportPlanner(library,scans,captureStore,deferred,libraryExports,rowBudget)
    private val exportUi=LibraryExportUi(activity,shell,exportPlanner,libraryExports,::idle,status) { message,work -> job(message,work=work) }
    private val scanTasks=PrivateScanTaskStore(storage)
    private val batchScans=BatchScanSession(library,storage,scans,scanTasks)
    private val resourceSession=LibraryResourceSession(library,storage,scans,store,PrivateDevelopmentTaskStore(storage))
    // One 960px spectral proxy can contain 15 float channels (about 53 MiB).
    // Retain it within a bounded heap share instead of reopening the full NPZ per edit.
    private val previewCache=OpticalPreviewCache((Runtime.getRuntime().maxMemory()/6).coerceIn(16L*1048576,56L*1048576))
    private val sampleCache=CameraEffectsPreviewCache()
    private val sampleWorker=java.util.concurrent.Executors.newSingleThreadExecutor { task ->
        Thread({ android.os.Process.setThreadPriority(android.os.Process.THREAD_PRIORITY_BACKGROUND);task.run() },"FilmFoundry-samples")
    }
    private val displayCacheGeneration=java.util.concurrent.atomic.AtomicInteger()
    private val viewerWorker=java.util.concurrent.Executors.newSingleThreadExecutor()
    private val viewerWork=LatestViewWork(viewerWorker) { activity.runOnUiThread(it) }
    private var negativeViewerRecipe: Map<String,Any?> = emptyMap()
    private val negativeViews=NegativeViewCache(storage)
    private val negativeSizes=object: LinkedHashMap<String,Pair<Int,Int>>(16,.75f,true) {
        override fun removeEldestEntry(eldest: MutableMap.MutableEntry<String,Pair<Int,Int>>?)=size>64
    }
    private val styleSampleWork=LatestViewWork(sampleWorker) { activity.runOnUiThread(it) }
    private val scanWork=LatestViewWork(viewerWorker) { activity.runOnUiThread(it) }
    private lateinit var stages: StageRecipeControls
    private var scanResult: DevelopedResult?=null
    private var libraryResult: DevelopedResult?=null
    private val scanCaption=TextView(activity)
    private lateinit var savePhoto: Button
    private lateinit var scanBatchWithSettings: Button
    private val scanExportAfterSave=CheckBox(activity)
    private var scanBatchFrames=emptyList<DevelopedResult>()
    private val scanPreview=ScanPreviewState()
    private val viewerCaption=TextView(activity)
    private val viewerHint=TextView(activity)
    private val galleryGuide=TextView(activity)
    private val viewerTitle=TextView(activity)
    private val viewerPrimary=Button(activity)
    private val viewerRotate=Button(activity)
    private val viewerMore=Button(activity)
    private val viewerMode=Button(activity)
    private val viewerLight=Button(activity)
    private var viewerDisplayMode="viewer_light_table"
    private var viewerSceneLight=false
    @Volatile private var sceneEpoch=0L
    private var closeScene: (()->Unit)?=null
    private var viewingEv=0.0
    private var viewingKelvin=5500.0
    private val previousImage=Button(activity)
    private val nextImage=Button(activity)
    private var showingSaved: SavedScan?=null
    private var photoSequence=emptyList<SavedScan>()
    private var negativeSequence=emptyList<DevelopedResult>()
    @Volatile private var viewerRevision=0
    private var neighborKey: String?=null
    private var galleryRoll: String?=null
    private var negativesSelected=preferences.getBoolean("gallery_negatives",false)
    private lateinit var photoTab: Button
    private lateinit var negativeTab: Button
    private val thumbnails=DisplayThumbnailCache()
    private val galleryDisplay=GalleryPreferences(shell)
    private lateinit var galleryDisplayButton: ImageButton
    private lateinit var viewerRating: ViewerRatingBar
    private val negativeCatalogSerial=java.util.concurrent.atomic.AtomicInteger()
    private var metadataBusy=false
    private val photoSelection=LibrarySelectionBar<SavedScan>(shell,::canManage,{ it.id },{ selectAllPhotos() },{ removeSelectedPhotos(it) },::exportSelectedPhotos,::canBrowse,::idle)
    private val negativeSelection=LibrarySelectionBar<DevelopedResult>(shell,::canManage,{ it.resultId },{ negativeSelectionAll(false) },{ removeSelectedNegatives(it) },::exportSelectedNegatives,::canBrowse,::idle)
    private val batchSelection=LibrarySelectionBar<DevelopedResult>(shell,::canManage,{ it.resultId },{ negativeSelectionAll(true) },{ removeSelectedNegatives(it) },::exportSelectedNegatives,::canBrowse,::idle)
    private val rollSelection=LibrarySelectionBar<Roll>(shell,::idle,{ it.rollId },{ selectAllRolls() },{ removeSelectedRolls(it) },::exportSelectedRolls,::canBrowse)
    private val photoGrid=ThumbnailGrid(shell,thumbnails) { shell.selected == MobileShell.Page.OBSERVE && !negativesSelected && canBrowse() }
    private val negativeGrid=ThumbnailGrid(shell,thumbnails) { shell.selected == MobileShell.Page.OBSERVE && negativesSelected && canBrowse() }
    private val batchGrid=ThumbnailGrid(shell,thumbnails) { shell.selected == MobileShell.Page.ROLL_BATCH && canBrowse() }
    private val previewHandler=android.os.Handler(android.os.Looper.getMainLooper())
    private var pendingScanPreview=false
    private var listsDirty=true
    private val refreshScanPreview=Runnable {
        if(!activity.isDestroyed && shell.selected == MobileShell.Page.SCAN && stages.autoPreviewEnabled && pendingScanPreview && !running && !captureInProgress() && available()) {
            pendingScanPreview=false;scanResult?.let { openScanner(it,null,null,true) }
        }
    }
    private var scanPage=0
    private data class PhotoQuery(val serial: Int,val roll: String?,val page: Int,val continuous: Boolean,val scrollY: Int,val settings: GalleryQuery)
    private val catalogSerial=java.util.concurrent.atomic.AtomicInteger()
    private val pendingCatalog=java.util.concurrent.atomic.AtomicReference<PhotoQuery?>()
    private var catalogLoading=false
    private val exportDocument=activity.registerForActivityResult(androidx.activity.result.contract.ActivityResultContracts.CreateDocument("application/octet-stream")) { uri ->
        val path=preferences.getString("export_medium_path",null)
        preferences.edit().remove("export_medium_path").apply()
        if(uri != null && path != null) job(copy("正在导出材料文件…","Exporting material file…")) { control ->
            activity.contentResolver.openOutputStream(uri,"w")?.use { output -> storage.readPrivate(path) { input ->
                val buffer=ByteArray(64*1024)
                while(true) { control.checkCancel();val n=input.read(buffer);if(n < 0) break;output.write(buffer,0,n) }
            } } ?: error("Cannot open destination")
            copy("文件已导出，软件里的原件还在","File exported. The original in the app stays.")
        }
    }
    private val selectedRoll=TextView(activity)
    private var developmentRoll: String?=null
    private var completedDevelopmentRoll: String?=null
    private var rollPage=0
    private var resultPage=0
    private fun copy(zh: String, en: String) = if(activity.resources.configuration.locales[0].language == "zh") zh else en
    private fun button(column: LinearLayout, zh: String, en: String, action: () -> Unit): Button = Button(activity).also {
        it.text = copy(zh,en); it.setOnClickListener { if(available() && !running) action() }; controls.add(it); column.addView(it);shell.styleButton(it)
    }
    private fun shortcut(parent: LinearLayout,zh: String,en: String,detailZh: String="",detailEn: String="",action: ()->Unit)=
        shell.shortcut(parent,zh,en,detailZh,detailEn) { if(available() && !running) action() }.also { controls.add(it) }
    init {
        shell.onBeforeBack={
            val selection=when(shell.selected) {
                MobileShell.Page.ROLLS->rollSelection
                MobileShell.Page.ROLL_BATCH->batchSelection
                MobileShell.Page.OBSERVE->if(negativesSelected) negativeSelection else photoSelection
                else->null
            }
            if(selection?.state?.active==true) { selection.clear();true }
            else shell.selected==MobileShell.Page.DEVELOP && stages.backDevelopment()
        }
        buildViewer()
        val start=shell.card(MobileShell.Page.START,"胶卷与规格","Film & format")
        start.addView(startSummary)
        shell.note(start,"装卷开始，未拍完可随时继续。","Load film to start. Resume unfinished rolls anytime.")
        val skipLoading=CheckBox(activity).apply {
            text=copy("跳过装填过程","Skip the loading sequence")
            isChecked=preferences.getBoolean("skip_loading",false)
            setOnCheckedChangeListener { _,checked -> preferences.edit().putBoolean("skip_loading",checked).apply() }
        }
        start.addView(skipLoading)
        button(start,"装填／切换胶卷与规格","Load / switch film & format") { loadDialog() }
        autoAdvance.text=copy("自动过片","Auto advance")
        autoAdvance.isChecked=preferences.getBoolean("auto_advance",true)
        startShooting=button(start,"启动／继续拍摄","Start / continue shooting") {
            if(current == null) loadDialog(continueSelected=true) else continueShooting(current!!)
        }.also { it.tag="primary";shell.styleButton(it,true) }
        val column=shell.card(MobileShell.Page.CAPTURE,"拍摄设置","Camera settings")
        fun menuButton(zh: String,en: String,action: ()->Unit)=button(column,zh,en) {
            shell.closeCaptureMenu();action()
        }.apply { tag="capture_option";shell.styleButton(this) }
        menuButton("镜头与相机","Lens & camera",nativeCameraSettings)
        menuButton("文字打印","Text imprint",::imprintDialog)
        val processing=shell.card(MobileShell.Page.SETTINGS_PROCESS,"计算与加速","Compute & acceleration")
        shell.note(processing,"CPU 参考处理 · 按内存自动分段","CPU reference processing · automatic memory-based blocks")
        shortcut(processing,"计算方式与内存","Compute method & memory","查看 GPU、原片读取与内存状态","GPU, source reading and memory status",::computeDialog)
        button(processing,"回收失主计算暂存","Reclaim abandoned computation files") {
            job(copy("正在检查计算暂存…","Checking scratch workspaces…")) { _ ->
                val r=OwnedScratch.recover(activity.cacheDir)
                copy("已回收 ${r.reclaimed} 个暂存；运行中 ${r.busy}；未完成 ${r.failed}。原卷、底片和照片未动。",
                    "Reclaimed ${r.reclaimed}; active ${r.busy}; deferred ${r.failed}. Rolls, negatives and photos retained.")
            }
        }
        shell.captureHud.orientation=LinearLayout.VERTICAL
        shell.captureHud.addView(readout,LinearLayout.LayoutParams(-1,-1))
        readout.onControl("iso") { showExpertReadout(it,FinderExpertPort.Control.ISO) }
        readout.onControl("shutter",::quickShutter)
        readout.onReset("shutter") { expert?.setShutter?.invoke(null) }
        readout.onControl("ev",::quickExposure)
        readout.onReset("ev") { setNativeExposure(0) }
        readout.onControl("wb",::quickWhiteBalance)
        readout.onControl("lock",::quickLocks)
        readout.onReset("lock") { pushNative(liveNative()?.copy(lockAe=false,lockAwb=false)) }
        readout.onControl("frame") { source -> shell.quickControls.info("frame",source,copy("胶卷进度","Roll progress"),counter.text.toString()+copy("\n过片和多曝使用右侧按钮；这里不会改变格位。","\nUse the right-side Advance and Multi controls; inspecting progress keeps the current frame.")) }
        readout.onControl("print",::quickImprint)
        readout.onReset("print") { setQuickImprint(false) }
        frameCount.textSize=18f;frameCount.typeface=android.graphics.Typeface.MONOSPACE;frameCount.setTextColor(android.graphics.Color.WHITE);shell.mountCaptureDetails(frameCount)
        counter.textSize=12f;counter.setTextColor(android.graphics.Color.LTGRAY);shell.mountCaptureDetails(counter)
        onFinderStyle(preferences.getString("viewfinder_style","line").takeIf { it in OpticalFinderGeometry.styles } ?: OpticalFinderGeometry.styles.first())
        menuButton("取景框样式","Finder frame") {
            val selected=OpticalFinderGeometry.styles.indexOf(preferences.getString("viewfinder_style","line")).coerceAtLeast(0)
            AlertDialog.Builder(activity).setTitle(copy("取景框样式","Finder frame"))
                .setSingleChoiceItems(arrayOf(copy("简洁线框","Simple frame"),copy("中心对焦框","Center focus frame")),selected) { dialog,which ->
                    val key=OpticalFinderGeometry.styles[which];preferences.edit().putString("viewfinder_style",key).apply();onFinderStyle(key);dialog.dismiss()
                }.setNegativeButton(copy("取消","Cancel"),null).show()
        }
        val shotChoices=LinearLayout(activity).apply { orientation=LinearLayout.HORIZONTAL }
        wideGate=button(shotChoices,"宽幅","Wide") { if(panorama.isEnabled) panorama.isChecked=!panorama.isChecked }
        wideGate.tag="capture_rail";wideGate.contentDescription=copy("135 宽幅挡片，上下不曝光","135 panorama baffles, top and bottom stay unexposed")
        wideGate.layoutParams=LinearLayout.LayoutParams(shell.dp(72),shell.dp(40)).apply { rightMargin=shell.dp(8) };shell.styleButton(wideGate)
        advanceMode=button(shotChoices,"连拍","Run") { autoAdvance.isChecked=!autoAdvance.isChecked }
        advanceMode.tag="capture_rail"
        advanceMode.layoutParams=LinearLayout.LayoutParams(shell.dp(72),shell.dp(40));shell.styleButton(advanceMode)
        column.addView(shotChoices,LinearLayout.LayoutParams(-2,-2).apply { topMargin=shell.dp(8) })
        panorama.text=copy("135 宽幅挡片（上下不曝光）","135 panorama baffles (unexposed top / bottom)")
        panorama.isChecked=preferences.getBoolean("panorama",false)
        multiple=button(column,"多重曝光","Multiple exposure") {
            val id=current ?: return@button
            val open=library.roll(id).frames.firstOrNull { it.state == FrameState.CAPTURING }
            if(open == null) { holdFirst=!holdFirst;refresh() }
            else job(copy("正在为当前格上弦…","Cocking the current frame…")) { control ->
                control.checkCancel();library.rearmCurrentFrame(id)
                copy("当前格可再曝光一次；没有过片","Current frame cocked for one more exposure; film not advanced")
            }
        }
        shell.mountAdvance(multiple)
        shoot = button(column,"快门","Shutter",::expose)
        shell.mountShutter(shoot)
        advance=button(column,"完成本格并过片","Finish this frame and advance") {
            val id=current ?: return@button
            job(copy("正在过片并上弦…","Advancing and cocking…"),onSuccess={ holdFirst=false;refresh() }) { control ->
                val frame=deferred.finishFrame(id,control)
                copy("第 ${frame.frameIndex} 格多重曝光完成，已过片","Frame ${frame.frameIndex} multiple exposure complete; advanced")
            }
        }
        shell.mountAdvance(advance,true)
        seal = menuButton("封卷并退出","Seal roll & leave") {
            val id = current ?: return@menuButton
            val roll=library.roll(id)
            confirm(copy("封存这卷胶片？","Seal this roll?"),copy(
                "已曝光 ${roll.exposedCount()}/${roll.capacity} 格。封卷后不能继续拍摄，未使用格位将留空；原曝光会保留。",
                "Exposed ${roll.exposedCount()}/${roll.capacity} frames. Sealing ends shooting; unused frames stay empty. Original exposures are retained."),copy("封卷","Seal")) {
            job(copy("正在封卷…","Sealing roll…"),onSuccess={ shell.select(MobileShell.Page.START,false) }) { control ->
                deferred.seal(id,control); current = null
                copy("已封卷，拍摄结束。稍后可从胶卷库进入冲洗；原曝光保留。","Roll sealed. Shooting has ended. Develop later from Rolls; original exposures retained.")
            }
            }
        }
        shell.mountCaptureDetails(button(column,"暂停取景","Pause viewfinder") { shell.closeCaptureMenu();stopCamera() }.apply { tag="capture_option";shell.styleButton(this) })
        menuPause.text=copy("暂停当前任务","Pause task");menuPause.tag="capture_option";shell.styleButton(menuPause)
        menuPause.setOnClickListener { cancel() };column.addView(menuPause)
        val cameraPanel=shell.card(MobileShell.Page.START,"相机设置","Camera settings")
        shortcut(cameraPanel,"相机设置","Camera settings","镜头、采集格式、漏光与文字打印","Lens, capture format, light leaks and date imprint",::cameraDialog)
        shortcut(cameraPanel,"胶卷设置","Film settings","ISO、颗粒、老化与片边","ISO, grain, aging and film edges",::filmDialog)
        val rollsPanel=shell.card(MobileShell.Page.ROLLS,"胶卷库","Roll library")
        rollsPanel.addView(rollSelection)
        button(rollsPanel,"存储","Storage",::resourceRolls)
        rollRows=LinearLayout(activity).apply { orientation=LinearLayout.VERTICAL };rollsPanel.addView(rollRows)
        rollDetailRows=shell.card(MobileShell.Page.ROLL_DETAIL,"本卷曝光与冲洗结果","This roll & its developments")
        batchRows=shell.card(MobileShell.Page.ROLL_BATCH,"本次冲洗的底片","Film from this development")
        val developPanel=shell.card(MobileShell.Page.DEVELOP,"冲洗后自动扫描","Automatic scanning")
        selectedRoll.textSize=14f;selectedRoll.maxLines=3;selectedRoll.setTextColor(if(shell.ivory) 0xFF292721.toInt() else 0xFFEFEAE1.toInt());shell.developHeader.addView(selectedRoll)
        button(shell.developActions,"开始冲洗","Develop roll") {
            val id=developmentRoll
            if(id == null) developDialog() else startDevelopment(id)
        }.also { it.tag="primary";shell.styleButton(it,true);it.layoutParams=LinearLayout.LayoutParams(-1,-2) }
        shell.note(developPanel,"冲洗得到的底片保存在胶卷库。可同时扫描并存入软件图库，也可之后再扫描。","Developed film is kept in Rolls. Scan it into the app Library automatically, or scan later.")
        autoFirstPreview.text=copy("自动扫描并保存照片","Scan and save photos automatically")
        autoFirstPreview.isChecked=preferences.getBoolean("auto_preview",true)
        autoFirstPreview.setOnCheckedChangeListener { _,_->if(::stages.isInitialized) { saveDrafts();updateDevelopmentHeader() } }
        val initialAutoScan=autoFirstPreview.isChecked
        shell.resetSection(developPanel,copy("扫描与保存","Scan & save")) { autoFirstPreview.isChecked=initialAutoScan;saveDrafts() }
        developmentImage.visibility=android.view.View.GONE;developmentImage.scaleType=ImageView.ScaleType.FIT_CENTER
        developmentImage.contentDescription=copy("本次冲洗的首张照片；其余格的进度见任务状态","First photo from this development; task status shows progress for the remaining frames")
        developPanel.addView(developmentImage,LinearLayout.LayoutParams(-1,shell.dp(200)))
        button(developPanel,"查看本卷照片","View this roll's photos") {
            val id=developmentRoll ?: return@button
            galleryRoll=id;negativesSelected=false;scanPage=0;resultPage=0;listsDirty=true;shell.select(MobileShell.Page.OBSERVE)
        }
        val scanPanel=shell.card(MobileShell.Page.SCAN,"底片与批次","Film & batch")
        scanImage.scaleType=ImageView.ScaleType.FIT_CENTER
        shell.scanPreviewSurface.addView(scanImage,FrameLayout.LayoutParams(-1,-1))
        scanCaption.apply { textSize=12f;maxLines=2;ellipsize=android.text.TextUtils.TruncateAt.END;setTextColor(android.graphics.Color.WHITE);setBackgroundColor(0xB0161616.toInt());setPadding(shell.dp(12),shell.dp(6),shell.dp(12),shell.dp(6)) }
        shell.scanPreviewSurface.addView(scanCaption,FrameLayout.LayoutParams(-1,-2,android.view.Gravity.BOTTOM))
        scanFeedback=ScanPreviewFeedback(shell,shell.scanPreviewSurface)
        scanComparison=ScanComparison(shell,shell.scanPreviewSurface)
        shell.scanOverlaySurface.bringToFront()
        val scanRotate=Button(activity).apply {
            text="90° ↻";tag="camera_action";setTextColor(android.graphics.Color.WHITE);setBackgroundColor(0xD0222222.toInt());textSize=14f;setPadding(0,0,0,0)
            contentDescription=copy("顺时针旋转90°，保存和导出沿用此方向","Rotate clockwise 90 degrees; saved and exported photos follow")
            tooltipText=contentDescription
            setOnClickListener { if(!running && available()) { stages.rotateOutput();orientScanImage() } }
        }
        shell.scanOverlaySurface.addView(scanRotate,android.widget.FrameLayout.LayoutParams(shell.dp(60),shell.dp(48),android.view.Gravity.TOP or android.view.Gravity.START).apply { topMargin=shell.dp(8);marginStart=shell.dp(64) })
        scanImage.addOnLayoutChangeListener { _,_,_,_,_,_,_,_,_ -> orientScanImage() }
        shell.scanFocusButton.bringToFront()
        val scanRefresh=button(scanPanel,"刷新扫描预览","Refresh scan preview") { scanResult?.let { openScanner(it,null,null,true) } ?: status(copy("请先选择底片","Choose a developed frame first")) }.apply {
            contentDescription=copy("刷新扫描预览","Refresh scan preview");tooltipText=contentDescription
            minWidth=0;minimumWidth=0;setPadding(0,shell.dp(8),0,shell.dp(8))
            layoutParams=LinearLayout.LayoutParams(shell.dp(48),-2).apply { marginEnd=shell.dp(8) }
        }
        savePhoto=button(shell.scanActions,"保存","Save") { scanResult?.let { result ->
            val frozen=scanPreview.recipeFor(result.resultId,result.opticalPath) ?: return@button
            exportUi.confirmExecution(copy("确认扫描保存","Confirm scan"),scanExecutionInfo(1,frozen),true,scanExportAfterSave.isChecked,outputRecipe=frozen) { album ->
                scanExportAfterSave.isChecked=album;saveScan(result)
            }
        } ?: status(copy("请先选择底片","Choose a developed frame first")) }.also {
            it.tag="primary";shell.styleButton(it,true);it.layoutParams=LinearLayout.LayoutParams(shell.dp(72),-2)
            it.contentDescription=copy("扫描并保存到软件图库","Scan and save to the app Library");it.minWidth=0;it.setPadding(shell.dp(6),shell.dp(8),shell.dp(6),shell.dp(8))
        }
        scanBatchWithSettings=button(shell.scanActions,"扫描本批","Scan batch") {
            val result=scanResult ?: return@button
            val shown=scanPreview.recipeFor(result.resultId,result.opticalPath) ?: run { status(copy("请先刷新扫描预览","Refresh the scan preview first"));return@button }
            val frames=scanBatchFrames
            if(frames.isNotEmpty()) scanBatchDialog(library.roll(frames.first().sourceRollId),frames,shown)
        }.apply { visibility=android.view.View.GONE;layoutParams=LinearLayout.LayoutParams(0,-2,1f) }
        button(scanPanel,"查看底片","View film") { scanResult?.let(::viewNegative) ?: resultsDialog() }
        button(scanPanel,"来源记录","Source history") { scanResult?.let { originDialog(it.sourceRollId,it.sourceFrameIndex,it.resultId) } ?: resultsDialog() }
        shell.promote(scanPanel);shell.promote(developPanel)
        resultRows=shell.card(MobileShell.Page.OBSERVE,"底片","Film")
        scanRows=shell.card(MobileShell.Page.OBSERVE,"照片","Photos")
        (resultRows.getChildAt(0) as? TextView)?.visibility=android.view.View.GONE
        (scanRows.getChildAt(0) as? TextView)?.visibility=android.view.View.GONE
        shell.promote(scanRows)
        val gallery=shell.card(MobileShell.Page.OBSERVE,"图库","Library")
        val tabs=LinearLayout(activity);gallery.addView(tabs)
        photoTab=button(tabs,"照片","Photos") { selectGallery(false) }.apply { layoutParams=LinearLayout.LayoutParams(0,-2,1f) }
        negativeTab=button(tabs,"底片","Film") { selectGallery(true) }.apply { layoutParams=LinearLayout.LayoutParams(0,-2,1f) }
        val libraryTools=LinearLayout(activity);gallery.addView(libraryTools)
        galleryDisplayButton=ImageButton(activity).apply {
            setPadding(shell.dp(12),shell.dp(12),shell.dp(12),shell.dp(12))
            setOnClickListener { if(canBrowse()) { galleryDisplay.cycleLayout();paintGalleryLayout();refreshLists() } }
        }
        libraryTools.addView(galleryDisplayButton,LinearLayout.LayoutParams(shell.dp(52),shell.dp(52)));paintGalleryLayout()
        shell.action(libraryTools,"内容筛选与排序 ▾","Filter & sort ▾") {
            if(canBrowse()) galleryDisplay.open(library.visibleRolls().map { it.rollId to rollName(it.rollId) },galleryRoll) { selected ->
                galleryRoll=selected;scanPage=0;resultPage=0;batchPage=0;photoSelection.clear();negativeSelection.clear();galleryScroll()?.scrollTo(0,0);refreshLists();shell.decorate()
            }
        }.layoutParams=LinearLayout.LayoutParams(0,-2,1f)

        photoTab.setOnClickListener { if(canBrowse()) selectGallery(false) };negativeTab.setOnClickListener { if(canBrowse()) selectGallery(true) }
        galleryGuide.textSize=13f;galleryGuide.setTextColor(if(shell.ivory) 0xFF686257.toInt() else 0xFFB5AEA1.toInt());galleryGuide.setPadding(0,shell.dp(8),0,shell.dp(4));galleryGuide.text=galleryLead();gallery.addView(galleryGuide)
        gallery.addView(photoSelection);gallery.addView(negativeSelection)
        shell.promote(gallery)
        val configuration=shell.card(MobileShell.Page.SETTINGS_RECIPES,"材料、药液与扫描配方","Material, chemistry & scanner recipes",
            "编辑并另存预设，拍摄、冲洗和扫描时分别选用。",
            "Edit a copy, then choose it when loading, developing or scanning.")
        recipe.hint=copy("FFC2 分享码；留空使用默认配置","FFC2 share code; empty uses defaults")
        shortcut(configuration,"胶卷材料","Film materials","装卷时使用；编辑副本不会更改已装胶卷","Choose when loading; editing a copy keeps loaded rolls intact") {
            MaterialConfiguration(activity,shell,builtin,storage,::readRecipe).apply { onPreviewRecipe=::previewRecipe }.open()
        }
        shortcut(configuration,"药液与冲洗工艺","Baths & processes","保存药液和工艺，冲洗时组合使用","Save baths and processes for development") {
            ProcessConfiguration(activity,shell,storage,{ stages.development(readRecipe(),(developmentRoll ?: current)?.let { library.roll(it).developRecipe }.orEmpty()) }) { stages.reloadCatalog() }.apply { onPreviewRecipe=::previewRecipe }.open()
        }
        shortcut(configuration,"扫描风格","Scan styles","设置光源、色彩与影调，不改变底片","Set light, color and tone while preserving the negative") {
            ScannerConfiguration(activity,shell,builtin,storage,{ stages.observation(readRecipe(),(scanResult?.sourceRollId ?: current)?.let { library.roll(it).scannerRecipe }.orEmpty()) }) { stages.reloadCatalog() }.apply { onPreviewRecipe=::previewRecipe }.open()
        }
        button(configuration,"管理自存材料／扫描配置","Manage saved film / scanners",::managePresets)
        val maintenance=shell.card(MobileShell.Page.SETTINGS_PROCESS,"存储与缓存","Storage & cache")
        shortcut(maintenance,"胶卷存储管理","Roll storage","曝光、底片和照片分别管理","Manage exposures, negatives and photos separately",::resourceRolls)
        val cacheTools=shell.disclosure(maintenance,"显示缓存","Display cache","按需清理；拍摄、底片和照片都会保留","Clear when needed; captures, negatives and photos are retained")
        button(cacheTools,"释放显示缓存","Release display cache") {
            displayCacheGeneration.incrementAndGet()
            previewCache.clear();displayObservation.clear();thumbnails.clear();listOf(photoGrid,negativeGrid,batchGrid).forEach { it.trim() }
            NegativeDisplayStore.clear(storage)
            negativeViews.clear();opticalProxies.clear()
            status(copy("显示缓存已释放","Display cache cleared"))
        }
        val sharing=shell.disclosure(configuration,"导入与分享配方","Import & share recipes","粘贴或复制 FFC2 分享码","Paste or copy an FFC2 share code",expanded=!preferences.getString("recipe_draft","").isNullOrBlank())
        recipe.setSingleLine(true);recipe.setText(preferences.getString("recipe_draft",""));shell.field(sharing,recipe,copy("FFC2 配方分享码","FFC2 recipe share code"),copy("留空使用默认配方；不会覆盖已装卷或已冻结任务。","Blank uses defaults; loaded rolls and frozen tasks stay unchanged."))
        button(sharing,"应用分享码到当前配置","Apply share code") {
            runCatching {
                if(seasonal.tryUnlock(recipe.text.toString())) {
                    recipe.setText(lastValidRecipeText)
                    stages.updateBuiltins(builtin);saveDrafts()
                    status(copy("四季合集已解锁：8 款材料与专属扫描","Seasons unlocked: 8 materials and their matching scans"))
                } else {
                    stages.useSharedRecipe(readRecipe());saveDrafts();status(copy("分享码已应用；已装卷材料和原任务保持不变","Share code applied; loaded materials and existing tasks retained"))
                }
            }.onFailure { status(it.message.orEmpty()) }
        }
        button(sharing,"复制当前配置分享码","Copy current configuration share code") {
            runCatching {
                val roll=(current ?: developmentRoll ?: scanResult?.sourceRollId)?.let(library::roll)
                var values=readRecipe();if(roll != null) values=values+("film" to roll.filmRecipe)
                values=stages.development(values,roll?.developRecipe.orEmpty());values=stages.observation(values,roll?.scannerRecipe.orEmpty())
                val code=RecipeShareCodec.encode(values)
                (activity.getSystemService(android.content.Context.CLIPBOARD_SERVICE) as android.content.ClipboardManager).setPrimaryClip(android.content.ClipData.newPlainText("Film Foundry FFC2",code))
                status(copy("配置分享码已复制","Configuration share code copied"))
            }.onFailure { status(it.message.orEmpty()) }
        }
        val recipeHelp=shell.disclosure(configuration,"配方支持与操作说明","Recipe support & help")
        button(recipeHelp,"查看本配方在手机版的支持情况","Phone support for this recipe") {
            runCatching {
                val roll=(developmentRoll ?: current)?.let(library::roll)
                val base=readRecipe()+("film" to (roll?.filmRecipe ?: section(readRecipe(),"film")))
                val process=stages.development(base,roll?.developRecipe.orEmpty())
                val scan=stages.observation(base,roll?.scannerRecipe.orEmpty())
                val profile=if(roll==null) defaultFormationProfile() else roll.captureSettings[PhoneFormationProfile.ROLL_SETTING]
                val entries=PhoneRecipeCapabilities.inspect(process,PhoneRecipeCapabilities.Stage.DEVELOP,profile).entries+
                    PhoneRecipeCapabilities.inspect(scan,PhoneRecipeCapabilities.Stage.SCAN).entries
                AlertDialog.Builder(activity).setTitle(copy("手机版配方能力","Phone recipe capabilities"))
                    .setMessage(capabilityText(shell,PhoneRecipeCapabilities.Report(entries.distinct()))+"\n\n"+
                        entries.joinToString("\n") { it.path+" · "+when(it.status) {
                            PhoneRecipeCapabilities.Status.IMPLEMENTED -> copy("已执行","implemented")
                            PhoneRecipeCapabilities.Status.REDUCED -> copy("手机轻量模型","phone reduced model")
                            PhoneRecipeCapabilities.Status.PRESERVED -> copy("保留但未执行","retained, not executed")
                            PhoneRecipeCapabilities.Status.UNSUPPORTED -> copy("不支持","unsupported")
                            PhoneRecipeCapabilities.Status.OMITTED -> copy("轻量形成中省略","omitted in lightweight formation")
                        } })
                    .setPositiveButton(copy("知道了","Got it"),null).show()
            }.onFailure { status(it.message.orEmpty()) }
        }
        button(recipeHelp,"照片、底片和相册","Photos, negatives and the phone gallery") {
            AlertDialog.Builder(activity).setTitle(copy("怎么看、怎么导出","Viewing and export")).setMessage(copy(
                "胶卷保存拍摄原片与潜影；冲洗生成底片；扫描底片生成照片。三层资源独立管理。\n\n图像页点「来源」可查看对应胶卷、冲洗批次和扫描记录。\n\n照片与观片图可导出到系统相册或文件夹；NPZ 是供电脑版继续处理的材料文件。导出不删除软件内资源。",
                "Rolls retain captures and latents; development creates film; scanning creates photos. Each layer is managed independently.\n\nTap Source in the viewer to find the roll, development batch and scan record.\n\nExport photos or film-view images to the system gallery or a folder. NPZ files are materials for desktop processing. Export keeps the app's resources."))
                .setPositiveButton(copy("知道了","Got it"),null).show()
        }
        stages=StageRecipeControls(activity,shell,builtin,storage,::readRecipe)
        shell.scanUndo.setOnClickListener { stages.undoScan() };shell.scanRedo.setOnClickListener { stages.redoScan() }
        stages.onHistoryChanged={ undo,redo -> shell.scanUndo.isEnabled=undo;shell.scanRedo.isEnabled=redo }
        (scanBatchWithSettings.parent as? android.view.ViewGroup)?.removeView(scanBatchWithSettings)
        stages.scanExtras.addView(scanBatchWithSettings,LinearLayout.LayoutParams(-1,-2))
        shell.action(stages.scanExtras,"扫描使用指南","Scanner guide") { shell.openGuide(MobileShell.Page.SCAN) }
        shell.note(stages.scanOutput,"按选定格式与处理质量保存到图库，原底片保留。","Save to the app Library; the source film stays.")
        scanExportAfterSave.text=copy("保存后同时导出到系统相册","Also export to system gallery after saving")
        stages.scanOutput.addView(scanExportAfterSave)
        (scanRefresh.parent as? android.view.ViewGroup)?.removeView(scanRefresh)
        stages.scanOutput.addView(scanRefresh,LinearLayout.LayoutParams(-1,-2))
        (scanBatchWithSettings.parent as? android.view.ViewGroup)?.removeView(scanBatchWithSettings)
        stages.scanOutput.addView(scanBatchWithSettings,LinearLayout.LayoutParams(-1,-2))
        scanComparison.onHistogramChanged=stages::showScanHistogram
        stages.onOpenComputeSettings=::computeDialog
        stages.onPreviewRecipe=::previewRecipe
        stages.onStyleReference={ scanResult?.let { references.stats(it.sourceRollId) } }
        stages.onStyleSamples={ recipes,deliver ->
            val source=scanResult
            if(source==null) ({}) else {
                styleSampleWork.submit({ stale ->
                    val control=WorkflowControl(cancelRequested=stale)
                    val preview=previewCache.get(source.resultId+":"+source.opticalPath) { opticalProxies.get(source.resultId,source.opticalPath,960,control::checkCancel) }
                    val observer=OpticalPreviewSession()
                    for((key,values) in recipes) {
                        control.checkCancel()
                        val bitmap=runCatching { runBlocking { observationBitmap(observer.observe(preview,values,false,control),control) } }.getOrElse { control.checkCancel();null }
                        activity.runOnUiThread { if(stale() || activity.isDestroyed) bitmap?.recycle() else deliver(key,bitmap) }
                    }
                },{ _->recipes.forEach { deliver(it.first,null) } })
                ({ styleSampleWork.cancel() })
            }
        }
        stages.onResumeDevelopment=::resumeDevelopment
        stages.developmentQuickOptions.addView(autoFirstPreview,0)
        stages.onAutomaticOutputChanged=::updateDevelopmentHeader
        stages.onOpenProcessCatalog={ ProcessConfiguration(activity,shell,storage,{ stages.development(readRecipe(),(developmentRoll ?: current)?.let { library.roll(it).developRecipe }.orEmpty()) }) { stages.reloadCatalog() }.apply { onPreviewRecipe=::previewRecipe }.open() }
        fun placeBefore(card: LinearLayout,anchor: LinearLayout) {
            val page=anchor.parent as LinearLayout;page.removeView(card);page.addView(card,page.indexOfChild(anchor))
        }
        placeBefore(stages.processCard,developPanel);placeBefore(stages.finishCard,scanPanel)
        (developPanel.parent as? android.view.ViewGroup)?.removeView(developPanel);stages.developmentExtras.addView(developPanel)
        (scanPanel.parent as? android.view.ViewGroup)?.removeView(scanPanel);stages.scanExtras.addView(scanPanel)
        stages.onObservationChanged={
            orientScanImage()
            scanPreview.invalidate();scanWork.cancel();pendingScanPreview=true;savePhoto.isEnabled=false
            stages.histogramNeedsRefresh()
            if(!stages.autoPreviewEnabled) scanFeedback.finish()
            scanBatchWithSettings.isEnabled=false
            scanCaption.text=scanResult?.let(::resultLabel).orEmpty()+copy(" · 预览待刷新"," · Preview needs refresh")
            scheduleScanPreview()
        }
        panorama.setOnCheckedChangeListener { _,_ -> baffles(panorama.isChecked);preferences.edit().putBoolean("panorama",panorama.isChecked).apply();refresh() }
        autoAdvance.setOnCheckedChangeListener { _,checked -> preferences.edit().putBoolean("auto_advance",checked).apply();refresh() }
        refresh()
        if(resourceSession.needsRecovery) job(copy("正在恢复资源管理事务…","Recovering resource changes…")) { _ ->
            resourceSession.recover();copy("资源管理状态已恢复，仍被引用的材料已保留","Resource state recovered; referenced materials retained")
        }
        worker.execute { runCatching { OwnedScratch.recover(activity.cacheDir) } }
    }
    fun saveDrafts() {
        stages.save()
        preferences.edit().putString("recipe_draft",recipe.text.toString()).putBoolean("auto_preview",autoFirstPreview.isChecked)
            .putBoolean("auto_advance",autoAdvance.isChecked).putBoolean("panorama",panorama.isChecked)
            .putString("current_roll",current).apply()
    }
    private fun readRecipe(): Map<String,Any?> = recipeInput.read(recipe.text.toString()).also { lastValidRecipeText=recipe.text.toString() }
    private fun scanRecipe(rollId: String)=stages.observation(readRecipe(),library.roll(rollId).scannerRecipe,references.stats(rollId))
    private fun confirm(title: String,message: String,positive: String,action: ()->Unit) {
        AlertDialog.Builder(activity).setTitle(title).setMessage(message).setNegativeButton(copy("取消","Cancel"),null)
            .setPositiveButton(positive) { _,_->if(available() && !running && !captureInProgress()) action() }.show()
    }
    private fun idle()=available() && !running && !captureInProgress() && !motionJob && !metadataBusy
    private fun canManage()=canBrowse() && !metadataBusy && (!com.filmfoundry.app.session.ProcessingWork.busy || com.filmfoundry.app.session.ProcessingWork.snapshot.owner===taskOwner)
    private fun guide(title: String,fill: LinearLayout.(close: ()->Unit)->Unit) {
        val panel=LinearLayout(activity).apply { orientation=LinearLayout.VERTICAL;setPadding(shell.dp(20),shell.dp(8),shell.dp(20),shell.dp(8)) }
        val dialog=AlertDialog.Builder(activity).setTitle(title).setView(ScrollView(activity).apply { addView(panel,android.view.ViewGroup.LayoutParams(-1,-2)) })
            .setNegativeButton(copy("关闭","Close"),null).create()
        panel.fill { dialog.dismiss() }
        shell.decorate(panel);dialog.show()
    }
    private fun galleryLead(): String {
        val name=galleryRoll?.let { rollName(it) }
        val head=if(name==null) "" else copy("$name。","$name. ")
        return head+if(negativesSelected) copy("点按观片 · 长按多选","Tap to view · hold to select")
            else copy("点按查看 · 长按多选","Tap to view · hold to select")
    }
    private fun buildViewer() {
        val column=LinearLayout(activity).apply { orientation=LinearLayout.VERTICAL;setBackgroundColor(android.graphics.Color.rgb(16,16,16)) }
        shell.viewerSurface.addView(column,android.widget.FrameLayout.LayoutParams(-1,-1))
        val bar=LinearLayout(activity).apply { gravity=android.view.Gravity.CENTER_VERTICAL;setPadding(shell.dp(8),shell.dp(4),shell.dp(8),shell.dp(4)) };column.addView(bar)
        Button(activity).apply { tag="camera_action";shell.navigationIcon(this,NavigationIcon.BACK,true);contentDescription=copy("返回上一页","Go back");setOnClickListener { shell.back() };bar.addView(this,LinearLayout.LayoutParams(shell.dp(52),shell.dp(52))) }
        viewerTitle.apply { textSize=16f;setTextColor(android.graphics.Color.WHITE);typeface=android.graphics.Typeface.DEFAULT_BOLD };bar.addView(viewerTitle,LinearLayout.LayoutParams(0,-2,1f))
        viewerTitle.maxLines=2;viewerTitle.ellipsize=android.text.TextUtils.TruncateAt.END
        Button(activity).apply { text=copy("来源","Source");tag="camera_action";contentDescription=copy("查看当前图像的来源记录","View source history for this image");setOnClickListener {
            if(!idle()) return@setOnClickListener
            showingSaved?.let { originDialog(it.rollId,it.frameIndex,it.resultId,it);return@setOnClickListener }
            libraryResult?.let { originDialog(it.sourceRollId,it.sourceFrameIndex,it.resultId) }
        };bar.addView(this,LinearLayout.LayoutParams(-2,shell.dp(52))) }
        viewerMore.apply { text="•••";tag="camera_action";contentDescription=copy("当前图像的更多操作","More actions for this image");setOnClickListener {
            if(!canBrowse()) return@setOnClickListener
            val saved=showingSaved
            if(saved == null) libraryResult?.let(::chooseResultAdvanced) else photoOptions(saved)
        } };bar.addView(viewerMore,LinearLayout.LayoutParams(shell.dp(56),shell.dp(52)))
        viewerMode.apply { text=copy("观片方式","Viewing mode");tag="camera_action"
            setOnClickListener { if(canBrowse()) libraryResult?.let(::viewingModes) } }
        val modeRow=LinearLayout(activity);column.addView(modeRow)
        modeRow.addView(viewerMode,LinearLayout.LayoutParams(0,-2,1f))
        viewerLight.apply { tag="camera_action";setOnClickListener { if(canBrowse()) libraryResult?.let { if(viewerSceneLight) openCameraView(it) else viewingLight(it) } } }
        modeRow.addView(viewerLight,LinearLayout.LayoutParams(-2,-2))
        viewerHint.apply { textSize=13f;setTextColor(android.graphics.Color.LTGRAY);setPadding(shell.dp(16),0,shell.dp(16),shell.dp(8));visibility=android.view.View.GONE }
        column.addView(viewerHint)
        image.contentDescription=copy("图像，双指或双击缩放，左右滑动切换","Image; pinch or double-tap to zoom, swipe to browse")
        image.onPrevious={ browseImage(-1) };image.onNext={ browseImage(1) }
        image.onDetailRequested=::loadNegativeDetail
        column.addView(image,LinearLayout.LayoutParams(-1,0,1f))
        viewerRating=ViewerRatingBar(shell,galleryDisplay) { listsDirty=true };column.addView(viewerRating)
        viewerCaption.apply { textSize=12f;minLines=2;maxLines=3;ellipsize=android.text.TextUtils.TruncateAt.END;setTextColor(android.graphics.Color.LTGRAY);setPadding(shell.dp(16),shell.dp(8),shell.dp(16),shell.dp(8)) };column.addView(viewerCaption)
        val row=LinearLayout(activity).apply { setPadding(shell.dp(8),0,shell.dp(8),shell.dp(8)) };column.addView(row)
        previousImage.text="‹";previousImage.contentDescription=copy("上一张","Previous image");previousImage.setOnClickListener { if(canBrowse()) { if(viewerSceneLight) browseImage(-1) else image.navigate(-1) } }
        nextImage.text="›";nextImage.contentDescription=copy("下一张","Next image");nextImage.setOnClickListener { if(canBrowse()) { if(viewerSceneLight) browseImage(1) else image.navigate(1) } }
        shell.navigationIcon(previousImage,NavigationIcon.BACK,true);shell.navigationIcon(nextImage,NavigationIcon.NEXT,true)
        viewerPrimary.tag="primary";viewerPrimary.setOnClickListener {
            if(!available() || running || captureInProgress()) return@setOnClickListener
            val saved=showingSaved
            if(saved != null) exportSavedPhoto(saved) else libraryResult?.let(::openScanner)
        }
        row.addView(previousImage,LinearLayout.LayoutParams(shell.dp(52),shell.dp(52)))
        shell.navigationIcon(viewerRotate,NavigationIcon.ROTATE,true)
        viewerRotate.contentDescription=copy("顺时针旋转90°，记住方向并用于图片导出","Rotate clockwise 90 degrees; remember orientation for image exports")
        viewerRotate.tooltipText=viewerRotate.contentDescription
        viewerRotate.setOnClickListener { rotateViewedImage() }
        row.addView(viewerRotate,LinearLayout.LayoutParams(shell.dp(48),shell.dp(48)))
        row.addView(viewerPrimary,LinearLayout.LayoutParams(0,-2,1f).apply { marginStart=shell.dp(6);marginEnd=shell.dp(6) })
        row.addView(nextImage,LinearLayout.LayoutParams(shell.dp(52),shell.dp(52)))
    }
    private fun updateViewerNavigation() {
        val ratingKey=showingSaved?.let { "photo:${it.id}" } ?: libraryResult?.let { "negative:${it.resultId}" }
        if(::viewerRating.isInitialized) viewerRating.bind(ratingKey,canBrowse() && !image.pageLoading && image.displayedPage?.key==ratingKey)
        val saved=showingSaved
        val index=if(saved != null) photoSequence.indexOfFirst { it.id==saved.id } else negativeSequence.indexOfFirst { it.resultId==libraryResult?.resultId }
        val size=if(saved != null) photoSequence.size else negativeSequence.size
        previousImage.isEnabled=canBrowse() && index>0;nextImage.isEnabled=canBrowse() && index>=0 && index<size-1
        viewerPrimary.isEnabled=idle();viewerMore.isEnabled=canBrowse()
        viewerRotate.visibility=if(viewerSceneLight) android.view.View.GONE else android.view.View.VISIBLE
        viewerRotate.isEnabled=canBrowse() && !metadataBusy && !image.pageLoading && image.drawable!=null && !image.isTurning
        viewerMode.visibility=if(saved==null) android.view.View.VISIBLE else android.view.View.GONE
        viewerHint.visibility=viewerMode.visibility
        viewerMode.isEnabled=canBrowse()
        viewerMode.text=when {
            saved!=null -> copy("观片方式","Viewing mode")
            viewerDisplayMode=="viewer_projection" -> copy("正像投射 ▾","Positive projection ▾")
            viewerSceneLight -> copy("实景透射 ▾","Scene transmission ▾")
            else -> copy("灯台 ▾","Light table ▾")
        }
        viewerLight.visibility=if(saved==null && viewerDisplayMode=="viewer_light_table") android.view.View.VISIBLE else android.view.View.GONE
        viewerLight.text=if(viewerSceneLight) copy("重启实景","Restart camera") else copy("灯光","Light")
        viewerLight.isEnabled=canBrowse()
        prepareNeighbors(index,size)
    }
    private fun prepareNeighbors(index: Int,size: Int) {
        if(viewerSceneLight) return
        if(shell.selected!=MobileShell.Page.VIEWER || image.isTurning) return
        val owner=image.displayedPage ?: return
        val expected=showingSaved?.let { "photo:${it.id}" } ?: libraryResult?.let { "negative:${it.resultId}" }
        if(owner.key!=expected) return
        val key="${owner.generation}:$expected:$index:$size"
        if(neighborKey==key) return
        neighborKey=key
        val before=index>0;val after=index>=0 && index<size-1
        val angles=if(showingSaved!=null) listOf(photoSequence.getOrNull(index-1)?.viewRotationDegrees ?: 0,photoSequence.getOrNull(index+1)?.viewRotationDegrees ?: 0)
            else listOf(negativeSequence.getOrNull(index-1)?.let { negativeRotation(it.resultId) } ?: 0,negativeSequence.getOrNull(index+1)?.let { negativeRotation(it.resultId) } ?: 0)
        val entries=if(showingSaved!=null) listOf(photoSequence.getOrNull(index-1),photoSequence.getOrNull(index+1)).map { saved ->
            saved?.let { "photo:${it.id}" to { check: ()->Unit -> decodePhotoThumbnail(it,check) } }
        } else listOf(negativeSequence.getOrNull(index-1),negativeSequence.getOrNull(index+1)).map { result ->
            result?.let { negativeEntry(it,negativeSequence).let { entry -> entry.key to entry.load } }
        }
        image.neighbors(owner,entries[0]?.let { thumbnails.get(it.first) },entries[1]?.let { thumbnails.get(it.first) },before,after,angles[0],angles[1])
        // A cold negative still means unzipping its optical file. Only a tile already on disk may be prepared ahead.
        val loads=if(showingSaved!=null) entries else entries.map { entry ->
            entry?.takeIf { (key,_) -> thumbnails.get(key)!=null || storage.existsPrivate(NegativeDisplayStore.relative(key.removePrefix("negative:"))) }
        }
        if(loads.all { it==null }) return
        val token=viewerRevision
        worker.execute {
            val bitmaps=loads.map { entry -> entry?.let { (key,load) -> thumbnails.get(key) ?: runCatching {
                load { if(activity.isDestroyed || token!=viewerRevision) throw InterruptedException("Viewer changed") }
            }.getOrNull()?.also { thumbnails.put(key,it) } } }
            activity.runOnUiThread { if(!activity.isDestroyed && token==viewerRevision && shell.selected==MobileShell.Page.VIEWER) image.neighbors(owner,bitmaps[0],bitmaps[1],before,after,angles[0],angles[1]) }
        }
    }
    private fun browseImage(delta: Int) {
        if(!canBrowse()) { image.cancelTurn();return }
        showingSaved?.let { saved ->
            val i=photoSequence.indexOfFirst { it.id==saved.id };val target=photoSequence.getOrNull(i+delta)
            if(target!=null) openSavedPhoto(target,photoSequence,image.neighborPreview(delta)) else image.cancelTurn();return
        }
        val i=negativeSequence.indexOfFirst { it.resultId==libraryResult?.resultId }
        val target=negativeSequence.getOrNull(i+delta)
        if(target!=null) { if(viewerSceneLight) openCameraView(target) else viewNegative(target,negativeSequence) } else image.cancelTurn()
    }
    private fun exportSavedPhoto(saved: SavedScan) {
        if(running || !available() || captureInProgress()) return
        exportSelectedPhotos(listOf(saved))
    }
    private fun exportSelectedPhotos(items: List<SavedScan>) = exportUi.images(LibraryExportRequest("photos",items.map { it.id }))
    private fun exportSelectedNegatives(items: List<DevelopedResult>) {
        if(!idle() || items.isEmpty()) return
        AlertDialog.Builder(activity).setTitle(copy("导出 ${items.size} 张底片","Export ${items.size} film images"))
            .setItems(arrayOf(copy("NPZ · 光学底片（给电脑版）","NPZ · optical film (for desktop)"),copy("底片图片 · TIFF / PNG / JPG","Film images · TIFF / PNG / JPG"))) { _,which ->
                if(which==0) exportUi.files(LibraryExportRequest("optical",items.map { it.resultId }))
                else AlertDialog.Builder(activity).setTitle(copy("底片图片的观看方式","Film image rendering"))
                    .setItems(arrayOf(copy("灯台 · 保留底片极性","Light table · preserve film polarity"),copy("正像投射","Positive projection"))) { _,mode ->
                        exportUi.images(LibraryExportRequest(if(mode==0) "light_table" else "projection",items.map { it.resultId },
                            mapOf("scanner" to mapOf("transmission_light_ev" to viewingEv,"transmission_light_temperature_k" to viewingKelvin))))
                    }.setNegativeButton(copy("取消","Cancel"),null).show()
            }.setNegativeButton(copy("取消","Cancel"),null).show()
    }
    private fun exportSelectedRolls(items: List<Roll>) {
        if(!idle() || items.isEmpty()) return
        AlertDialog.Builder(activity).setTitle(copy("整卷导出 · ${items.size} 卷","Export ${items.size} rolls"))
            .setItems(arrayOf(copy("处理材料 · 整卷潜影 NPZ（需已封卷）","Material · all latent NPZ files (sealed rolls)"),
                copy("拍摄原片 · JPEG / DNG 文件","Camera originals · JPEG / DNG files"))) { _,which ->
                if(which==0 && items.any { it.lifecycle!=RollLifecycle.SEALED }) { status(copy("请先封好所选胶卷，再导出潜影材料","Seal all selected rolls before exporting latents"));return@setItems }
                exportUi.files(LibraryExportRequest(if(which==0) "latents" else "originals",items.map { it.rollId }))
            }.setNegativeButton(copy("取消","Cancel"),null).show()
    }
    private fun scanExecutionInfo(count: Int,recipe: Map<String,Any?>): String {
        val edge=section(recipe,"scanner")["scan_grid_long_edge"] ?: recipe["scan_grid_long_edge"]
        return copy("数量：$count 张\n输出：","Photos: $count\nOutput: ")+(edge?.let { copy("长边 $it 像素","$it pixels long edge") } ?: copy("按底片尺寸","Native film dimensions"))+
            "\n${ScanDelivery.format(recipe).extension.uppercase()} · ${ScanDelivery.bits(recipe)} bit"+(if(ScanDelivery.format(recipe)==ScanImageFormat.JPEG) " · Q${ScanDelivery.jpegQuality(recipe)}" else "")+
            copy("\n按选定格式与处理质量保存到图库，原底片保留。","\nSave the selected format and quality to Library; original film stays.")
    }
    private fun developmentExecutionInfo(id: String,recipe: Map<String,Any?>,photos: DevelopmentPhotoPlan): String {
        val develop=section(recipe,"develop")
        val roll=library.roll(id)
        val inspection=runCatching { RecipeDrafts.inspectProcess(roll.filmRecipe,develop) }.getOrNull()
        val process=ProcessPresentation.program(inspection?.program ?: develop["program_key"] as? String,copy("zh","en")=="en")
        return rollName(id)+copy(" · ${roll.exposedCount()} 格\n"," · ${roll.exposedCount()} frames\n")+materialLabel(roll)+"\n"+process+
            (inspection?.let { "\n"+copy("预期底片：","Expected film: ")+ProcessPresentation.result(it,copy("zh","en")=="en") } ?: "")+
            copy("\n共用药浴时长：${develop["time_min"] ?: 8} 分钟","\nShared bath duration: ${develop["time_min"] ?: 8} minutes")+
            (develop["temperature_c"]?.let { " · $it °C" } ?: "")+
            if(photos.enabled) "\n"+scanExecutionInfo(roll.exposedCount(),photos.recipe.orEmpty()) else copy("\n只保存冲洗底片，之后可另行扫描。","\nSave developed film only; scan later.")
    }
    private fun informationText(facts: LibraryFacts,photo: Boolean=false,material: Boolean=false)=
        LibraryInfoPresentation.brief(facts,copy("zh","en")=="en",photo,material)
    private fun tileInformationText(facts: LibraryFacts,photo: Boolean=false)=LibraryInfoPresentation.tile(facts,copy("zh","en")=="en",photo)
    private fun frameLabel(index: Int)=LibraryInfoPresentation.frame(index,copy("zh","en")=="en")
    private fun batchLabel(id: String)=LibraryInfoPresentation.batch(id,copy("zh","en")=="en")
    private fun scanLabel(id: String)=LibraryInfoPresentation.scan(id,copy("zh","en")=="en")
    private fun sourceSection(origin: LibraryOrigin)=LibraryInfoDialog.Section(copy("来源","Source"),description=LibraryInfoPresentation.origin(origin,copy("zh","en")=="en"))
    private fun identitySection(origin: LibraryOrigin)=LibraryInfoDialog.Section(copy("资源标识","Resource IDs"),mapOf(
        "roll_id" to origin.rollId,"frame_index" to origin.frameIndex,"development_batch_id" to origin.batchId,
        "developed_result_id" to origin.resultId,"scan_id" to origin.scanId))
    private fun negativeCaption(result: DevelopedResult)=information.peek(result.resultId)?.let { informationText(it) }
        ?: copy("正在读取底片信息…","Reading film information…")
    private fun photoCaption(saved: SavedScan)=informationText(LibraryFacts(saved.displayWidth,saved.displayHeight,
        saved.sourcePolarity ?: information.peek(saved.resultId)?.polarity,saved.createdAt),photo=true)+" · ${saved.format.extension.uppercase()} ${saved.precisionBits} bit"
    private fun updateViewerInformation(result: DevelopedResult?=libraryResult,saved: SavedScan?=showingSaved) {
        if(saved==null && result==null) return
        worker.execute {
            val facts=runCatching { if(saved!=null) information.photo(saved,result) else information.negative(checkNotNull(result)) }.getOrNull() ?: LibraryFacts()
            val source=runCatching { if(saved!=null) provenance.resolve(saved.rollId,saved.frameIndex,saved.resultId,saved)
                else provenance.resolve(result!!.sourceRollId,result.sourceFrameIndex,result.resultId) }.getOrNull()
            val missing=source?.let {
                when {
                    saved!=null && it.developed==ResourceAvailability.MISSING_FILE -> copy("来源底片文件缺失 · 记录可查","Source film file missing · history retained")
                    saved!=null && it.developed==ResourceAvailability.REMOVED -> copy("来源底片已删除或未收录","Source film removed or not recorded")
                    it.exposure==ResourceAvailability.MISSING_FILE -> copy("来源曝光文件缺失","Source exposure files missing")
                    !it.rollListed -> copy("来源胶卷已移除","Source roll removed")
                    it.exposure==ResourceAvailability.REMOVED -> copy("来源曝光已释放","Source exposures removed")
                    else -> null
                }
            }
            activity.runOnUiThread {
                if(activity.isDestroyed || shell.selected!=MobileShell.Page.VIEWER) return@runOnUiThread
                if(saved!=null && showingSaved?.id!=saved.id || saved==null && (showingSaved!=null || libraryResult?.resultId!=result?.resultId)) return@runOnUiThread
                val loading=copy(" · 加载预览…"," · Loading preview…").takeIf { viewerCaption.text.contains(it) }.orEmpty()
                viewerCaption.text=informationText(facts,photo=saved!=null)+loading+missing?.let { "\n$it" }.orEmpty()
                viewerCaption.contentDescription=null
            }
        }
    }
    private fun showRollInformation(roll: Roll) {
        informationDialogs.show(copy("胶卷信息","Roll information")) {
            LibraryInfoDialog.Content(information.roll(roll),listOf(
                LibraryInfoDialog.Section(copy("材料","Material"),roll.filmRecipe),
                LibraryInfoDialog.Section(copy("形成方式","Formation"),description=
                    if(roll.captureSettings[PhoneFormationProfile.ROLL_SETTING]==PhoneFormationProfile.LIGHTWEIGHT)
                        copy("轻量形成：保留颗粒的明暗响应、材料与药浴差异；简化分层颗粒，省略邻接效应和 DIR 抑制。药痕、网纹采用参数触发的密度附加。像素尺寸与扫描流程不变。",
                            "Lightweight formation: preserves density-dependent grain, stock and bath differences. Simplifies layered grain and omits adjacency and DIR inhibition. Stains and reticulation use parameter-triggered density overlays. Pixel dimensions and scanning are unchanged.")
                    else copy("沿用原有手机形成：包含分层颗粒，以及配方开启的邻接效应和 DIR 抑制。装卷后形成方式固定。",
                        "Original phone formation, including layered grain and recipe-enabled adjacency and DIR inhibition. The choice is fixed when loading.")),
                LibraryInfoDialog.Section(copy("拍摄","Capture"),roll.captureSettings),
                LibraryInfoDialog.Section(copy("冲洗默认","Develop defaults"),roll.developRecipe),
                LibraryInfoDialog.Section(copy("扫描默认","Scan defaults"),roll.scannerRecipe)),material=true,
                note=copy("装卷时的配置。实际冲洗与扫描参数见对应结果的信息。","Loading configuration. See each result for its actual development and scan settings."))
        }
    }
    private fun showNegativeInformation(result: DevelopedResult) {
        informationDialogs.show(copy("底片信息","Film information")) {
            val roll=runCatching { library.roll(result.sourceRollId) }.getOrNull()
            val facts=runCatching { information.negative(result) }.getOrDefault(LibraryFacts())
            val record=runCatching { information.opticalRecord(result) }.getOrDefault(emptyMap())
            val capture=roll?.let { runCatching { information.captureRecord(it,result.sourceFrameIndex) }.getOrDefault(emptyMap()) }.orEmpty()
            val origin=provenance.resolve(result.sourceRollId,result.sourceFrameIndex,result.resultId)
            LibraryInfoDialog.Content(facts,listOf(
                sourceSection(origin),
                LibraryInfoDialog.Section(copy("材料","Material"),section(result.developRecipe,"film").ifEmpty { roll?.filmRecipe.orEmpty() }),
                LibraryInfoDialog.Section(copy("拍摄","Capture"),capture),
                LibraryInfoDialog.Section(copy("冲洗配方","Development recipe"),result.developRecipe),
                LibraryInfoDialog.Section(copy("介质记录","Medium record"),record),identitySection(origin)),
                note=if(!storage.existsPrivate(result.opticalPath)) copy("底片文件缺失，保留的配置记录仍可查看。","Film file missing; recorded settings remain available.")
                    else if(roll==null || roll.catalogRemoved) copy("来源胶卷已删除，底片与冲洗记录保留。","Source roll deleted; film and development records remain.") else null)
        }
    }
    private fun showPhotoInformation(saved: SavedScan) {
        informationDialogs.show(copy("照片信息","Photo information")) {
            val source=library.developedResults().firstOrNull { it.resultId==saved.resultId }
            val recipe=RecipeShareCodec.decode(saved.recipeShare)
            val origin=provenance.resolve(saved.rollId,saved.frameIndex,saved.resultId,saved)
            val sourceFacts=source?.let { runCatching { information.negative(it) }.getOrNull() }
            val width=saved.sourceWidth ?: sourceFacts?.width;val height=saved.sourceHeight ?: sourceFacts?.height
            val notes=listOfNotNull(
                if(width!=null && height!=null) copy("来源底片尺寸：${width}×${height}","Source film dimensions: ${width}×${height}") else null,
                if(source==null) copy("来源底片未收录，照片与已保存配置保留。","Source film is not in the catalog; photo and saved settings remain.")
                else if(!storage.existsPrivate(source.opticalPath)) copy("来源底片文件缺失，冲洗记录仍可查看。","Source film file is missing; development records remain.") else null,
                if(origin.sourceRecipe.isEmpty()) copy("原冲洗配置未记录。","Original development settings were not recorded.") else null)
            LibraryInfoDialog.Content(information.photo(saved,source),listOf(
                sourceSection(origin),
                LibraryInfoDialog.Section(copy("材料","Material"),section(origin.sourceRecipe,"film")),
                LibraryInfoDialog.Section(copy("冲洗","Development"),section(origin.sourceRecipe,"develop")),
                LibraryInfoDialog.Section(copy("扫描","Scan"),section(recipe,"scanner")),
                LibraryInfoDialog.Section(copy("保存时扫描配方","Saved scan recipe"),recipe),identitySection(origin)),photo=true,
                note=notes.takeIf { it.isNotEmpty() }?.joinToString("\n"))
        }
    }
    private fun bindRollInformation(view: TextView,roll: Roll,prefix: String="") {
        val key=roll.rollId;view.tag=key
        worker.execute {
            val facts=runCatching { information.roll(roll) }.getOrDefault(LibraryFacts())
            activity.runOnUiThread { if(!activity.isDestroyed && view.tag==key)
                view.text=prefix+informationText(facts,material=true) }
        }
    }
    private fun photoOptions(saved: SavedScan) {
        val original=library.developedResults().firstOrNull { it.resultId==saved.resultId }
        guide(copy("这张照片","This photo")) { close ->
            shell.action(this,"详细信息","Information") { close();showPhotoInformation(saved) }
            shell.action(this,"导出","Export") { if(!idle()) return@action;close();exportSavedPhoto(saved) }
            if(original != null && storage.existsPrivate(original.opticalPath)) {
                shell.action(this,"查看来源底片","View source film") { if(!canBrowse()) return@action;close();viewNegative(original) }
                shell.action(this,"继续调整这张照片","Continue editing this photo") { if(!idle()) return@action;close();editSavedPhoto(original,saved) }
                shell.action(this,"从来源底片开始扫描","Scan from source film") { if(!idle()) return@action;close();openScanner(original) }
                shell.action(this,"按原参数重新扫描","Rescan with saved settings") {
                    if(!idle()) return@action
                    close()
                    val recipe=saved.editingRecipe()
                    exportUi.confirmExecution(copy("按原参数重新扫描？","Rescan with saved settings?"),scanExecutionInfo(1,recipe),outputRecipe=recipe) { album ->
                        job(copy("正在重新扫描…","Rescanning…"),allowBrowsing=true) { control ->
                            val duplicate=scanAndSave(original,recipe,control)
                            val note=if(album) "\n"+exportUi.summary(libraryExports.gallery(listOf(libraryExports.photo(duplicate)),control)) else ""
                            // Saved output stays in Library; completion does not replace a different image being viewed.
                            copy("重新扫描完成，原结果保留","Rescan saved; previous scan retained")+note
                        }
                    }
                }
            } else shell.note(this,if(original==null) "来源底片未收录，照片可正常查看和导出。" else "来源底片文件缺失，照片可正常查看和导出。",
                if(original==null) "Source film is not in the catalog. This photo can still be viewed and exported." else "Source film file is missing. This photo can still be viewed and exported.")
            shell.action(this,"删除照片","Delete photo") { if(!canManage()) return@action;close();removeSelectedPhotos(listOf(saved)) }
            shell.action(this,"来源记录","Source history") { if(!canBrowse()) return@action;close();originDialog(saved.rollId,saved.frameIndex,saved.resultId,saved) }
        }
    }
    private fun openSavedPhoto(saved: SavedScan,sequence: List<SavedScan>,transitionPreview: Bitmap?=null) {
        stopScene();viewerSceneLight=false
        if(!canBrowse()) return
        photoSequence=sequence;showingSaved=saved;libraryResult=library.developedResults().firstOrNull { it.resultId==saved.resultId };image.orientationDegrees=saved.viewRotationDegrees
        viewerTitle.text=frameLabel(saved.frameIndex)+" · "+scanLabel(saved.id);viewerCaption.text=photoCaption(saved)
        viewerPrimary.text=copy("导出","Export");shell.select(MobileShell.Page.VIEWER)
        val revision=++viewerRevision;val page=beginViewerPage("photo:${saved.id}",transitionPreview=transitionPreview)
        updateViewerNavigation()
        val needsPreview=image.displayedPage!=page
        runViewer { control ->
            if(needsPreview) {
                val preview=decodePhotoThumbnail(saved,control::checkCancel)
                activity.runOnUiThread {
                    if(activity.isDestroyed || revision!=viewerRevision || shell.selected!=MobileShell.Page.VIEWER || !image.previewPage(page,preview)) preview.recycle()
                    else { thumbnails.put("photo:${saved.id}",preview);updateViewerNavigation() }
                }
            }
            showSavedScan(saved,control,revision,page)
        }
    }
    private fun viewNegative(result: DevelopedResult,sequence: List<DevelopedResult> = library.developedResults().asReversed()) {
        stopScene()
        viewerDisplayMode="viewer_light_table";viewerSceneLight=false
        if(!canBrowse()) return
        image.orientationDegrees=negativeRotation(result.resultId)
        negativeSequence=sequence;showingSaved=null;libraryResult=result;viewerTitle.text=frameLabel(result.sourceFrameIndex)+copy(" · 灯台"," · Light table")
        viewerCaption.text=negativeCaption(result);viewerHint.text=viewingHint("viewer_light_table",null);viewerPrimary.text=copy("扫描","Scan")
        updateViewerInformation(result,null)
        shell.select(MobileShell.Page.VIEWER);val revision=++viewerRevision;val page=beginViewerPage("negative:${result.resultId}")
        val frozen=runCatching { stages.observation(readRecipe(),library.roll(result.sourceRollId).scannerRecipe) }.getOrElse { clearViewerImage();status(it.message.orEmpty());return }
        negativeViewerRecipe=frozen
        if(defaultViewer(frozen,"viewer_light_table")) thumbnails.get("display:negative:${result.resultId}")?.let {
            image.presentPage(page,it);updateViewerNavigation();return
        }
        updateViewerNavigation()
        runViewer { control -> renderPreview(result,frozen,"viewer_light_table",control,viewerToken=revision,pageToken=page) }
    }
    private fun beginViewerPage(key: String,preserveCurrent: Boolean=false,transitionPreview: Bitmap?=null): ViewerPageIdentity.Token {
        viewerWork.cancel()
        neighborKey=null
        val page=image.requestPage(key)
        if(image.displayedPage?.key==key && image.drawable!=null) image.retainPage(page)
        else if(!preserveCurrent) image.previewPage(page,transitionPreview ?: thumbnails.get("display:$key") ?: thumbnails.get(key))
        return page
    }
    private fun negativeRotation(id: String)=OutputOrientation.normalized(preferences.getInt("view_rotation_negative_$id",0))
    private fun orientScanImage() {
        if(!::stages.isInitialized) return
        val bitmap=(scanImage.drawable as? android.graphics.drawable.BitmapDrawable)?.bitmap ?: return
        val size=OutputOrientation.size(bitmap.width,bitmap.height,stages.rotationDegrees)
        shell.scanImageSize(size.first,size.second);orientOutputView(scanImage,stages.rotationDegrees)
    }
    private fun rotateViewedImage() {
        if(!canBrowse() || metadataBusy || image.pageLoading || image.drawable==null || viewerSceneLight) return
        val saved=showingSaved;val negative=libraryResult
        if(saved!=null) {
            metadataBusy=true;updateViewerNavigation()
            worker.execute {
                val result=runCatching { scans.rotateClockwise(saved.id) }
                activity.runOnUiThread {
                    metadataBusy=false
                    if(!activity.isDestroyed) {
                        result.fold({ updated ->
                            listsDirty=true;photoSequence=photoSequence.map { if(it.id==updated.id) updated else it }
                            if(showingSaved?.id==updated.id) { showingSaved=updated;image.orientationDegrees=updated.viewRotationDegrees;viewerCaption.text=photoCaption(updated);neighborKey=null }
                        },{ statusChanged(it.message.orEmpty()) });updateViewerNavigation()
                    }
                }
            }
        } else if(negative!=null) {
            val angle=OutputOrientation.normalized(negativeRotation(negative.resultId)+90)
            preferences.edit().putInt("view_rotation_negative_${negative.resultId}",angle).apply()
            image.orientationDegrees=angle;listsDirty=true;neighborKey=null;updateViewerNavigation()
        }
    }
    private fun clearViewerImage() {
        neighborKey=null
        image.clearPage()
    }
    private fun runViewer(work: suspend (WorkflowControl)->Unit) {
        if(!canBrowse()) return
        viewerCaption.append(copy(" · 加载预览…"," · Loading preview…"))
        viewerWork.submit({ stale -> runBlocking { work(WorkflowControl(cancelRequested=stale)) } },{ error ->
            if(!activity.isDestroyed && shell.selected==MobileShell.Page.VIEWER) { viewerCaption.text=viewerCaption.text.toString().substringBefore(copy(" · 加载预览…"," · Loading preview…"))+copy(" · 读取失败，可继续切换"," · Unable to load; browsing remains available");viewerCaption.contentDescription=error.message;updateViewerNavigation() }
        })
    }
    private fun loadPhotoDetail(saved: SavedScan): Boolean {
        if(!canBrowse() || image.pageLoading || shell.selected!=MobileShell.Page.VIEWER) return false
        val displayed=(image.drawable as? android.graphics.drawable.BitmapDrawable)?.bitmap ?: return false
        val sample=PhotoDisplayPolicy.sample(saved.width,saved.height,AndroidProcessingMemory.snapshot(activity).budgetBytes)
        if(saved.width/sample<=displayed.width) return false
        val token=image.requestPage("photo:${saved.id}");image.retainPage(token)
        viewerWork.submit({ stale ->
            val bitmap=decodeDisplayPhoto(saved,sample)
            activity.runOnUiThread {
                if(activity.isDestroyed || stale() || showingSaved?.id!=saved.id) bitmap.recycle()
                else { image.presentPage(token,bitmap);viewerCaption.text=photoCaption(saved)+copy(" · 预览 "," · Preview ")+"${bitmap.width}×${bitmap.height}";updateViewerNavigation() }
            }
        },{ _ ->
            (image.drawable as? android.graphics.drawable.BitmapDrawable)?.bitmap?.let { image.presentPage(token,it) }
            image.retryDetail();updateViewerNavigation()
        })
        return true
    }
    private fun loadNegativeDetail(): Boolean {
        showingSaved?.let { return loadPhotoDetail(it) }
        if(viewerSceneLight || image.pageLoading || !canBrowse() || shell.selected!=MobileShell.Page.VIEWER) return false
        val result=libraryResult ?: return false
        val mode=viewerDisplayMode
        if(mode !in listOf("viewer_light_table","viewer_projection")) return false
        val revision=++viewerRevision
        val token=image.requestPage("negative:${result.resultId}");image.retainPage(token)
        val recipe=negativeViewerRecipe
        val ev=viewingEv;val kelvin=viewingKelvin
        viewerCaption.text=negativeCaption(result)+copy(" · 加载清晰底片…"," · Loading film detail…")
        viewerWork.submit({ stale ->
            val control=WorkflowControl(cancelRequested=stale)
            val size=synchronized(negativeSizes) { negativeSizes[result.resultId] } ?: previewCache.get(result.resultId+":"+result.opticalPath) {
                opticalProxies.get(result.resultId,result.opticalPath,960,control::checkCancel)
            }.let { it.sourceWidth to it.sourceHeight }
            control.checkCancel()
            val budget=observationHeapHeadroom()
            val sample=PhotoDisplayPolicy.sample(size.first,size.second,budget)
            val edge=((maxOf(size.first,size.second).toLong()+sample-1)/sample).toInt().coerceIn(2,8192)
            val viewing=(recipe-"scan_grid_long_edge")+mapOf("observe_mode" to mode,"scanner" to (section(recipe,"scanner")+
                mapOf("scan_grid_long_edge" to edge,"transmission_light_ev" to ev,"transmission_light_temperature_k" to kelvin)))
            val bitmap=scanObservationBitmap(MediumStore(storage),result.opticalPath,viewing,control,budget)
            activity.runOnUiThread {
                if(activity.isDestroyed || stale() || revision!=viewerRevision || shell.selected!=MobileShell.Page.VIEWER) bitmap.recycle()
                else { replaceImage(image,bitmap,"negative:${result.resultId}",token);viewerCaption.text=negativeCaption(result);updateViewerInformation(result,null);updateViewerNavigation() }
            }
        },{ error ->
            if(!activity.isDestroyed && revision==viewerRevision) {
                // Complete the pending identity with the existing image; keep browsing and retry available.
                (image.drawable as? android.graphics.drawable.BitmapDrawable)?.bitmap?.let { image.presentPage(token,it) }
                image.retryDetail();viewerCaption.text=negativeCaption(result)+copy(" · 细节读取失败，可重试"," · Detail unavailable; retry zooming")
                viewerCaption.contentDescription=error.message;updateViewerNavigation()
            }
        })
        return true
    }
    private fun rollName(id: String)=runCatching { library.roll(id).label.ifBlank { id.take(8) } }.getOrDefault(id.take(8))
    private fun rollMark(roll: Roll): Bitmap {
        val file=references.imageFile(roll.rollId)
        val token=if(file.isFile) file.length() else -1L
        val cached=rollMarkCache[roll.rollId]
        if(cached != null && cached.second==token && !cached.first.isRecycled) return cached.first
        cached?.first?.let { if(!it.isRecycled) it.recycle() }
        val decoded=if(file.isFile) android.graphics.BitmapFactory.decodeFile(file.path) else null
        val mark=decoded ?: FilmFace.swatch(roll.filmRecipe,roll.label)
        rollMarkCache[roll.rollId]=mark to token
        return mark
    }
    private fun selectGallery(negative: Boolean) {
        photoSelection.clear();negativeSelection.clear()
        negativesSelected=negative;preferences.edit().putBoolean("gallery_negatives",negative).apply()
        photoGrid.suspendLoading();negativeGrid.suspendLoading();refreshLists();shell.decorate()
    }
    private fun chooseGalleryRoll() {
        val rolls=library.allRolls().asReversed()
        AlertDialog.Builder(activity).setTitle(copy("按胶卷查看","Browse by roll"))
            .setItems((listOf(copy("全部胶卷","All rolls"))+rolls.map { rollName(it.rollId)+if(it.catalogRemoved) copy(" · 原卷已删除"," · Source roll deleted") else "" }).toTypedArray()) { _,which ->
                photoSelection.clear();negativeSelection.clear()
                galleryRoll=if(which==0) null else rolls[which-1].rollId;resultPage=0;scanPage=0;refreshLists();shell.decorate()
            }.show()
    }
    fun pageSelected(page: MobileShell.Page) {
        if(page!=MobileShell.Page.VIEWER) stopScene()
        if(page!=MobileShell.Page.ROLLS) rollSelection.clear()
        if(page!=MobileShell.Page.ROLL_BATCH) batchSelection.clear()
        if(page!=MobileShell.Page.OBSERVE) { photoSelection.clear();negativeSelection.clear() }
        if(page != MobileShell.Page.ROLL_BATCH) batchGrid.suspendLoading() else batchGrid.schedule()
        if(page != MobileShell.Page.VIEWER) { viewerRevision++;viewerWork.cancel();image.cancelTurn() }
        if(page != MobileShell.Page.OBSERVE) { photoGrid.suspendLoading();negativeGrid.suspendLoading() }
        else { photoGrid.schedule();negativeGrid.schedule() }
        if(page != MobileShell.Page.LOADING) releaseLoading()
        if(page == MobileShell.Page.SCAN) scheduleScanPreview() else {
            scanComparison.clear()
            scanFeedback.finish()
            previewHandler.removeCallbacks(refreshScanPreview);scanWork.cancel()
            if(scanResult?.let { scanPreview.recipeFor(it.resultId,it.opticalPath) }==null) pendingScanPreview=true
        }
        val refreshed=page in listOf(MobileShell.Page.ROLLS,MobileShell.Page.ROLL_DETAIL,MobileShell.Page.ROLL_BATCH,MobileShell.Page.OBSERVE) && listsDirty && canBrowse()
        if(refreshed) { refreshLists();listsDirty=false;shell.decorate() }
        else if(page==MobileShell.Page.ROLL_DETAIL) renderRollDetail()
        else if(page==MobileShell.Page.ROLL_BATCH) renderBatch()
    }
    private fun computeDialog() {
        val budget=rowBudget()
        fun mib(n: Long)="${n/1048576} MiB"
        val roll=current?.let { runCatching { library.roll(it) }.getOrNull() }
        val width=(roll?.captureSettings?.get("precision_width") as? Number)?.toInt() ?: 0
        val height=(roll?.captureSettings?.get("precision_height") as? Number)?.toInt() ?: 0
        val planZh: String;val planEn: String
        if(width>0 && height>0) {
            val pixels=PhoneComputeSchedule.blockPixels(width,height,budget);val rows=pixels/width
            val size=mib(PhoneComputeSchedule.workingBytes(pixels))
            planZh="这一卷每次形成 $rows 行，工作区大约 $size。画面尺寸不变。"
            planEn="This roll forms $rows rows at a time, about $size. The picture size stays the same."
        } else {
            planZh="选定拍摄精度后，这里会显示每次形成多少行。"
            planEn="After a capture size is chosen, this shows how many rows each pass forms."
        }
        val panel=LinearLayout(activity).apply { orientation=LinearLayout.VERTICAL;setPadding(shell.dp(20),shell.dp(8),shell.dp(20),shell.dp(8)) }
        shell.action(panel,"实机性能测试…","Device performance tests…") { performanceUi.open() }
        shell.action(panel,"硬件加速自测…","Hardware acceleration self-test…") { hardwareSelfTestUi.open() }
        if(Runtime.getRuntime().availableProcessors()>=4) panel.addView(CheckBox(activity).apply {
            text=copy("CPU 并行冲洗","Parallel CPU development");isChecked=preferences.getBoolean("cpu_parallel_development",true)
            setOnCheckedChangeListener { _,enabled -> preferences.edit().putBoolean("cpu_parallel_development",enabled).apply() }
        }) else shell.note(panel,"显影计算：CPU 单工作线程","Development: one CPU worker")
        val gpu=CheckBox(activity).apply {
            text=copy("GPU 协同计算（自动择优）","GPU assistance (automatic selection)")
            isChecked=gpuEnabled
            setOnCheckedChangeListener { _,enabled -> preferences.edit().putBoolean("gpu_source_linearization",enabled).apply() }
        };panel.addView(gpu)
        shell.note(panel,"正式扫描可用GPU完成光谱积分，以及归一化应用、曝光、对比度、颜色编码与曲线的联合计算。各步骤含传输测速后择优；全图统计、显影与预览仍用CPU。校验失败自动回退，下次任务生效。","Saved scans can use GPU for spectral integration and combined normalization application, exposure, contrast, color encoding and curves. Operations select using transfer-inclusive timing. Global statistics, development and previews stay on CPU. Failed qualification falls back; applies to the next job.")
        shell.note(panel,"原片读取：整幅参考解码；JPEG 分区读取未启用","Source reading: full reference decode; JPEG region reading is disabled")
        shell.note(panel,"自动分段已启用 · 当前工作预算 ${mib(budget)}。分段不降低成片尺寸。","Automatic processing blocks are active · working budget ${mib(budget)}. Blocking keeps the output dimensions.")
        shell.note(panel,planZh,planEn)
        shell.explanation(panel,"最近运行记录","Recent execution records") { details ->
            shell.note(details,"计算记录：${preferences.getString("last_decode_backend","尚未处理")}","Recorded computation: ${preferences.getString("last_decode_backend","not run")}")
            shell.note(details,"原片读取记录：${preferences.getString("last_source_backend","尚未处理")}","Recorded source read: ${preferences.getString("last_source_backend","not run")}")
            preferences.getString("last_formation_timing",null)?.let { raw ->
                runCatching { org.json.JSONObject(raw) }.getOrNull()?.let { times ->
                    shell.note(details,"最近任务累计耗时（含已执行部分；计算含行读取）","Latest job totals (including partial work; computation includes row reads)")
                    for(key in times.keys().asSequence().sorted()) {
                        val command=when(key.substringBefore('.')) { "expose"->copy("潜影","Exposure");"scan"->copy("扫描","Scan");else->copy("显影","Development") }
                        val section=when(key.substringAfter('.')) {
                            "archive_read"->copy("归档解压读取","Archive read")
                            "archive_handoff"->copy("工作区交接","Row handoff")
                            "handoff_hit"->copy("复用已提交工作区","Committed rows reused")
                            "optical_read"->copy("光学字段读取","Optical row read")
                            "linear_compute"->copy("透射与扫描计算","Transmission & scan")
                            "normalization"->copy("全图归一化","Global normalization")
                            "encode_output"->copy("扫描编码输出","Scan encoding")
                            "render_and_png_commit"->copy("扫描及PNG提交（含子阶段）","Scan & PNG commit (includes sub-stages)")
                            "jpg_encode_commit"->copy("JPG编码与提交","JPEG encode & commit")
                            "tif_encode_commit"->copy("TIFF编码与提交","TIFF encode & commit")
                            "setup"->copy("参数准备","Setup")
                            "spatial_prepare"->copy("空间与银粒准备","Spatial & silver preparation")
                            "material_compute"->copy("材料计算（各工作线程累计）","Material computation (worker totals)")
                            "row_pipeline_wall"->copy("行块计算与顺序写回","Row computation & ordered writing")
                            "archive_spool"->copy("中间数据写入","Scratch writing")
                            "archive_pack"->copy("压缩与提交","Compression & commit")
                            "border_prepare"->copy("片边准备","Border preparation")
                            "border_compute"->copy("片边形成","Border formation")
                            else->key.substringAfter('.')
                        }
                        val line="$command · $section: ${times.getLong(key)} ms"
                        shell.note(details,line,line)
                    }
                }
            }
        }
        val dialog=AlertDialog.Builder(activity).setTitle(copy("计算与加速","Compute & acceleration")).setView(ScrollView(activity).apply { addView(panel,android.view.ViewGroup.LayoutParams(-1,-2)) })
            .setNegativeButton(copy("关闭","Close"),null).create()
        shell.decorate(panel);dialog.show()
    }
    fun close() { hardwareSelfTestUi.close();stopScene();scanComparison.clear();scanFeedback.finish();displayObservation.clear();shell.closeQuickControls();viewerRevision++;viewerWork.cancel();scanWork.cancel();viewerWorker.shutdownNow();styleSampleWork.cancel();sampleWorker.shutdownNow();sampleCache.clear();catalogSerial.incrementAndGet();pendingCatalog.set(null);photoGrid.close();negativeGrid.close();batchGrid.close();thumbnails.clear();image.dispose();previewHandler.removeCallbacksAndMessages(null);previewCache.clear();clearViewerImage() }
    fun trimMemory(critical: Boolean) {
        thumbnails.trim(critical);listOf(photoGrid,negativeGrid,batchGrid).forEach { it.trim() }
        if(critical) { previewCache.clear();displayObservation.clear() }
    }
    private fun scheduleScanPreview() {
        previewHandler.removeCallbacks(refreshScanPreview)
        if(!activity.isDestroyed && activity.lifecycle.currentState.isAtLeast(androidx.lifecycle.Lifecycle.State.STARTED) && shell.selected == MobileShell.Page.SCAN && stages.autoPreviewEnabled && pendingScanPreview && scanResult != null)
            previewHandler.postDelayed(refreshScanPreview,220)
    }
    /** Foreground resumes only read-only display work, respecting the auto-preview switch. */
    fun resumeScanPreview() {
        if(activity.isDestroyed || shell.selected!=MobileShell.Page.SCAN) return
        val result=scanResult ?: return
        val ready=scanPreview.recipeFor(result.resultId,result.opticalPath)!=null
        savePhoto.isEnabled=ready && !running && available()
        scanBatchWithSettings.isEnabled=savePhoto.isEnabled && scanBatchFrames.isNotEmpty()
        if(!ready) {
            pendingScanPreview=true
            scanCaption.text=resultLabel(result)+if(stages.autoPreviewEnabled) copy(" · 等待更新预览"," · Waiting to update")
                else copy(" · 点刷新更新预览"," · Tap Refresh to update")
            scheduleScanPreview()
        }
    }
    private fun managePresets() {
        val catalog=RecipeCatalog(PrivateRecipeCatalogStore(storage));val entries=catalog.films()+catalog.scanners()
        if(entries.isEmpty()) { status(copy("尚未保存自定义配置","No saved configurations"));return }
        AlertDialog.Builder(activity).setTitle(copy("独立配置","Saved configurations"))
            .setItems(entries.map { (if(it.kind == "film") copy("材料 · ","Film · ") else copy("扫描 · ","Scanner · "))+it.label }.toTypedArray()) { _,which ->
                val entry=entries[which]
                val actions=mutableListOf(copy("复制分享码","Copy share code"),copy("删除配置（已装卷保留）","Delete definition (loaded rolls retained)"),copy("重命名配置","Rename definition"),copy("查看完整参数","View complete definition"))
                if(entry.kind=="film") actions+=copy("编辑并另存材料副本","Edit and save a material copy")
                AlertDialog.Builder(activity).setTitle(entry.label).setItems(actions.toTypedArray()) { _,action ->
                    runCatching {
                        if(action==2) {
                            val name=EditText(activity).apply { setText(entry.label);setSingleLine(true) }
                            val dialog=AlertDialog.Builder(activity).setTitle(copy("配置名称","Definition name")).setView(name)
                                .setNegativeButton(copy("取消","Cancel"),null).setPositiveButton(copy("保存","Save"),null).create()
                            dialog.setOnShowListener { dialog.getButton(AlertDialog.BUTTON_POSITIVE).setOnClickListener {
                                runCatching { catalog.rename(entry.kind,entry.id,name.text.toString());stages.reloadCatalog() }
                                    .onSuccess { dialog.dismiss();status(copy("名称已保存，材料参数和已装卷保持不变","Name saved; material parameters and loaded rolls unchanged")) }.onFailure { name.error=it.message }
                            } };dialog.show();return@runCatching
                        }
                        if(action==3) {
                            val text=TextView(activity).apply { setPadding(shell.dp(20),shell.dp(12),shell.dp(20),shell.dp(12));setTextIsSelectable(true);text=RecipeJson.text(entry.recipe) }
                            AlertDialog.Builder(activity).setTitle(entry.label).setView(ScrollView(activity).apply { addView(text) }).setPositiveButton(copy("关闭","Close"),null).show();return@runCatching
                        }
                        if(action==4) { MaterialConfiguration(activity,shell,builtin,storage,::readRecipe).apply { onPreviewRecipe=::previewRecipe }.editCopy(entry.recipe,entry.label);return@runCatching }
                        if(action == 0) {
                            val code=RecipeShareCodec.encode(mapOf(entry.kind to entry.recipe))
                            (activity.getSystemService(android.content.Context.CLIPBOARD_SERVICE) as android.content.ClipboardManager).setPrimaryClip(android.content.ClipData.newPlainText("Film Foundry FFC2",code))
                            status(copy("分享码已复制","Share code copied"))
                        } else confirm(copy("删除这份配置？","Delete this definition?"),copy("${entry.label} 将从自存配置中移除，已装卷和照片保留。","Remove ${entry.label} from saved definitions. Loaded rolls and photos remain."),copy("删除配置","Delete definition")) {
                            runCatching { catalog.remove(entry.kind,entry.id);stages.reloadCatalog();status(copy("已删除配置，已装卷不变","Definition removed; loaded rolls retained")) }.onFailure { status(it.message.orEmpty()) }
                        }
                    }.onFailure { status(it.message.orEmpty()) }
                }.show()
            }.show()
    }
    private fun section(value: Map<String,Any?>, key: String): Map<String,Any?> =
        (value[key] as? Map<*,*>)?.entries?.associate { it.key.toString() to it.value } ?: emptyMap()
    private fun cameraSettings(): Map<String,Any?> {
        preferences.getString("camera_effects_v3",null)?.let { return RecipeJson.objectMap(it) }
        return mapOf(
        "seed" to preferences.getLong("camera_seed",14023L),
        "gate_seed" to preferences.getLong("gate_seed",preferences.getLong("camera_seed",14023L)),
        "leak" to preferences.getInt("leak",0)/100.0,
        "leak_shape" to preferences.getString("leak_shape","edge"),
        "leak_side" to preferences.getString("leak_side","left"),
        "dust" to preferences.getBoolean("dust",false),"debris" to preferences.getBoolean("debris",false),
        "hair" to preferences.getBoolean("hair",false),"insect" to preferences.getBoolean("insect",false),
        "imprint" to preferences.getString("imprint",""))
    }
    private fun previewRecipe(value: Map<String,Any?>) {
        runCatching {
            val active=(when(shell.selected) { MobileShell.Page.DEVELOP->developmentRoll ?: current;MobileShell.Page.SCAN,MobileShell.Page.VIEWER->scanResult?.sourceRollId ?: current;else->current })?.let(library::roll)
            val base=readRecipe()+value
            val film=section(value,"film").ifEmpty { active?.filmRecipe ?: section(base,"film").ifEmpty { section(builtin.materials.first().recipe,"film") } }
            val settings=active?.captureSettings ?: mapOf("format" to "135","spatial_effects_revision" to PhoneMaterialDefaults.REVISION,PhoneGrainModels.ROLL_SETTING to PhoneGrainModels.LAYERED,
                "material_model" to PhoneProcessModels.CURRENT,PhoneMaterialPhenomena.ROLL_SETTING to PhoneMaterialPhenomena.CURRENT,
                PhoneFormationProfile.ROLL_SETTING to defaultFormationProfile())
            CameraEffectsDialog(activity,shell,sampleWorker,sampleCache).open(cameraSettings(),film,settings,selected=frozenRecipe(base),previewOnly=true) { }
        }.onFailure { Toast.makeText(activity,it.message,Toast.LENGTH_LONG).show() }
    }
    private fun imprintDialog() = cameraDialog(imprintOnly=true)
    private fun cameraDialog(imprintOnly: Boolean=false) {
        if(shell.selected==MobileShell.Page.CAPTURE && !imprintOnly) { imprintDialog();return }
        if(!available() || running || captureInProgress()) return
        runCatching {
            val roll=current?.let(library::roll)
            val fallback=section(readRecipe(),"film")
            val film=roll?.filmRecipe ?: (fallback+("spatial_reference_short_edge_mm" to (fallback["spatial_reference_short_edge_mm"] ?: 24.0)))
            val settings=roll?.captureSettings ?: mapOf("format" to "135","spatial_effects_revision" to PhoneMaterialDefaults.REVISION,PhoneGrainModels.ROLL_SETTING to PhoneGrainModels.LAYERED,
                "material_model" to PhoneProcessModels.CURRENT,PhoneMaterialPhenomena.ROLL_SETTING to PhoneMaterialPhenomena.CURRENT,
                PhoneFormationProfile.ROLL_SETTING to defaultFormationProfile())
            CameraEffectsDialog(activity,shell,sampleWorker,sampleCache).open(cameraSettings(),film,settings,imprintOnly=imprintOnly,
                initialAutoAdvance=if(imprintOnly) null else autoAdvance.isChecked,
                saveAutoAdvance={ autoAdvance.isChecked=it }) { camera ->
                check(available() && !running && !captureInProgress()) { copy("请先结束当前拍摄或任务","Finish the active capture or task first") }
                val remembered=imprintStyle(camera["imprint_style"]) ?: imprintStyle(cameraSettings()["imprint_style"])
                preferences.edit().apply {
                    putString("camera_effects_v3",RecipeJson.text(camera))
                    if(remembered!=null) putString("quick_imprint_style",remembered)
                }.apply();refresh()
            }
        }.onFailure { status(it.message.orEmpty()) }
    }
    private fun newFilmBorder(format: String,materialLabel: String)=com.filmfoundry.workflow.FilmEdgeSettings.create(format,
        preferences.getBoolean("film_rebate",true),materialLabel,preferences.getString("film_edge_text","").orEmpty(),preferences.getBoolean("film_factory_marks",true),
        RecipeJson.objectMap(preferences.getString("film_factory_pattern_v1","{}").orEmpty()))
    private fun filmDialog() {
        val panel=LinearLayout(activity).apply { orientation=LinearLayout.VERTICAL;setPadding(shell.dp(20),shell.dp(8),shell.dp(20),shell.dp(12)) }
        panel.addView(TextView(activity).apply { text=copy("片边齿孔和厂印跟胶卷走，不跟漏光、边字放在一起。","Sprockets and factory marks belong to the roll, separate from light leak and edge imprint.") })
        val rebate=CheckBox(activity).apply { text=copy("新胶卷保留齿孔与厂印边字","Keep sprockets & factory marks on new rolls");isChecked=preferences.getBoolean("film_rebate",true) };panel.addView(rebate)
        shell.action(panel,"齿孔与厂印边字：预览…","Sprockets & factory marks: preview…") {
            showFilmBorderPreview(activity,shell,current?.let(library::roll)?.captureSettings?.get("format")?.toString() ?: preferences.getString("load_format","135").orEmpty(),
                custom=preferences.getString("film_edge_text","").orEmpty(),marks=preferences.getBoolean("film_factory_marks",true),previewSample=::previewRecipe,
                factorySettings=RecipeJson.objectMap(preferences.getString("film_factory_pattern_v1","{}").orEmpty()),
                save={ text,marks,patterns -> preferences.edit().putString("film_edge_text",text).putBoolean("film_factory_marks",marks)
                    .putString("film_factory_pattern_v1",RecipeJson.text(patterns)).apply() })
        }
        panel.addView(TextView(activity).apply { text=copy("相机 ISO 在这里指定，拍摄中默认锁定。只有拍摄页里强制手动解锁，才能临时改 ISO 和快门。过片模式仍由相机自动曝光。已拍的格子不变。","Set the camera ISO here. It stays locked while shooting unless the capture page is force-unlocked. Motion capture stays on automatic exposure. Saved frames are unchanged.") })
        val lens=nativeLens()
        val chars=lens?.characteristics
        val isoRange=chars?.get(android.hardware.camera2.CameraCharacteristics.SENSOR_INFO_SENSITIVITY_RANGE)
        val timeRange=chars?.get(android.hardware.camera2.CameraCharacteristics.SENSOR_INFO_EXPOSURE_TIME_RANGE)
        val manualSensor=(chars?.get(android.hardware.camera2.CameraCharacteristics.REQUEST_AVAILABLE_CAPABILITIES) ?: intArrayOf()).contains(android.hardware.camera2.CameraCharacteristics.REQUEST_AVAILABLE_CAPABILITIES_MANUAL_SENSOR)
        val isoMode=Spinner(activity)
        val isoInput=EditText(activity).apply { inputType=android.text.InputType.TYPE_CLASS_NUMBER;setSingleLine(true);hint=copy("ISO ${isoRange ?: ""}","ISO ${isoRange ?: ""}");setText(preferences.getInt("film_iso",isoRange?.lower ?: 100).toString()) }
        val shutterInput=EditText(activity).apply { inputType=android.text.InputType.TYPE_CLASS_NUMBER or android.text.InputType.TYPE_NUMBER_FLAG_DECIMAL;setSingleLine(true);hint=copy("快门（毫秒）","Shutter (milliseconds)");setText(preferences.getString("film_shutter_ms","10")) }
        if(!manualSensor) panel.addView(TextView(activity).apply { text=copy("当前镜头不能手动指定 ISO，保持自动并锁定。","This lens cannot set ISO manually. It stays automatic and locked.");setPadding(0,shell.dp(8),0,shell.dp(8)) })
        else {
            isoMode.adapter=ArrayAdapter(activity,android.R.layout.simple_spinner_dropdown_item,listOf(copy("自动 ISO（锁定）","Automatic ISO (locked)"),copy("指定 ISO 与快门","Set ISO and shutter")))
            isoMode.setSelection(if(preferences.getString("film_iso_mode","auto")=="manual") 1 else 0)
            panel.addView(isoMode)
            val isoField=shell.field(panel,isoInput,copy("感光度 · ISO ${isoRange ?: ""}","Sensitivity · ISO ${isoRange ?: ""}"),resettable=false)
            val shutterField=shell.field(panel,shutterInput,copy("快门时间 · 毫秒","Shutter time · milliseconds"),resettable=false)
            fun showIso() { val manual=isoMode.selectedItemPosition==1;isoField.visibility=if(manual) android.view.View.VISIBLE else android.view.View.GONE;shutterField.visibility=isoField.visibility }
            isoMode.onItemSelectedListener=object: android.widget.AdapterView.OnItemSelectedListener {
                override fun onNothingSelected(parent: android.widget.AdapterView<*>?)=Unit
                override fun onItemSelected(parent: android.widget.AdapterView<*>?,view: android.view.View?,position: Int,id: Long)=showIso()
            }
            showIso()
        }
        val label=TextView(activity);panel.addView(label)
        val age=SeekBar(activity).apply { max=100;progress=preferences.getInt("film_age",0) };panel.addView(age)
        panel.addView(TextView(activity).apply { text=copy("自然沿用材料的颗粒强度，细腻或明显相应减弱或加强；关闭同时停用染料颗粒与银粒。仅对新装胶卷生效，扫描不会重新生成颗粒。","Natural keeps the stock's grain strength; Fine and Pronounced reduce or increase it. Off disables dye and silver grain. Applies to new rolls; scanning does not regenerate grain.") })
        val grain=Spinner(activity).apply {
            adapter=ArrayAdapter(activity,android.R.layout.simple_spinner_dropdown_item,listOf(copy("关闭","Off"),copy("细腻","Fine"),copy("自然（推荐）","Natural (recommended)"),copy("明显颗粒","Pronounced")))
            setSelection(preferences.getInt("film_grain",2).coerceIn(0,3))
        };panel.addView(grain)
        fun choice(zh: String,en: String,labels: List<String>,key: String,initial: Int): Spinner {
            panel.addView(TextView(activity).apply { text=copy(zh,en) })
            return Spinner(activity).also { it.adapter=ArrayAdapter(activity,android.R.layout.simple_spinner_dropdown_item,labels);it.setSelection(preferences.getInt(key,initial).coerceIn(0,labels.lastIndex));panel.addView(it) }
        }
        val grainSize=choice("颗粒大小","Grain size",listOf(copy("细","Small"),copy("标准","Standard"),copy("大","Large")),"film_grain_size",1)
        val mtf=choice("乳剂柔化（MTF）","Emulsion softness (MTF)",listOf(copy("关闭","Off"),copy("自然（推荐）","Natural (recommended)"),copy("柔和","Soft")),"film_mtf",1)
        val halo=choice("高光光晕（halation）","Highlight halation",listOf(copy("关闭","Off"),copy("自然（推荐）","Natural (recommended)"),copy("明显","Pronounced")),"film_halation",1)
        fun caption() { label.text=copy("过期／保存退化：${age.progress}%（仅新装胶卷；整卷冻结）","Age / storage degradation: ${age.progress}% (new rolls only; frozen per roll)") }
        caption();age.setOnSeekBarChangeListener(object: SeekBar.OnSeekBarChangeListener {
            override fun onProgressChanged(s: SeekBar?,p: Int,user: Boolean)=caption()
            override fun onStartTrackingTouch(s: SeekBar?)=Unit
            override fun onStopTrackingTouch(s: SeekBar?)=Unit
        })
        val dialog=AlertDialog.Builder(activity).setTitle(copy("胶卷设置","Film settings")).setView(ScrollView(activity).apply { addView(panel) })
            .setNegativeButton(copy("取消","Cancel"),null).setPositiveButton(copy("保存","Save"),null).create()
        dialog.setOnShowListener { dialog.getButton(AlertDialog.BUTTON_POSITIVE).setOnClickListener {
            if(!available() || running || captureInProgress()) return@setOnClickListener
            val edit=preferences.edit().putBoolean("film_rebate",rebate.isChecked).putInt("film_age",age.progress).putInt("film_grain",grain.selectedItemPosition)
                .putInt("film_grain_size",grainSize.selectedItemPosition).putInt("film_mtf",mtf.selectedItemPosition).putInt("film_halation",halo.selectedItemPosition)
            if(manualSensor && isoMode.selectedItemPosition==1) {
                if(lens==null) { status(copy("先选择可用镜头，再指定 ISO","Choose an available lens before setting ISO"));return@setOnClickListener }
                val iso=isoInput.text.toString().toIntOrNull()
                val millis=shutterInput.text.toString().toDoubleOrNull()
                if(iso==null || isoRange?.contains(iso)!=true) { isoInput.error=copy("ISO 超出当前镜头范围","ISO is outside this lens range");return@setOnClickListener }
                val ns=if(millis!=null && millis.isFinite() && millis>0) (millis*1e6).toLong() else null
                if(ns==null || timeRange?.contains(ns)!=true) { shutterInput.error=copy("快门超出当前镜头范围","Shutter time is outside this lens range");return@setOnClickListener }
                edit.putString("film_iso_mode","manual").putInt("film_iso",iso).putString("film_shutter_ms",shutterInput.text.toString().trim())
            } else edit.putString("film_iso_mode","auto")
            edit.apply();dialog.dismiss();onFilmExposure()
        } }
        shell.decorate(panel);dialog.show()
    }
    private fun continueShooting(id: String) {
        if(!available() || running || captureInProgress()) return
        current=id;refresh();saveDrafts();shell.select(MobileShell.Page.CAPTURE);requestCamera()
    }
    private fun rollProgress(roll: Roll): String {
        val open=roll.frames.firstOrNull { it.state == FrameState.CAPTURING }
        if(open != null) return copy("当前格 ${open.captureExposures} 次曝光；","${open.captureExposures} exposures on current frame; ")+
            if(open.shutterArmed) copy("已上弦，可继续多曝","cocked for another exposure") else copy("快门锁定，待过片或多曝上弦","shutter locked; advance or cock for multiple exposure")
        return when {
            roll.exposedCount() == 0 -> copy("已装填，尚未拍摄","Loaded · no exposures yet")
            roll.exposedCount() == roll.capacity -> copy("已满卷，待封卷","Roll full · ready to seal")
            else -> copy("未拍完，下次拍第 ${roll.exposedCount()+1} 格","Unfinished · next frame ${roll.exposedCount()+1}")
        }
    }
    private fun loadDialog(continueSelected: Boolean=false) {
        val active = library.shootingRolls().sortedBy { if(it.rollId == current) 0 else 1 }
        if(active.isEmpty()) { newRollDialog(animateLoading=!preferences.getBoolean("skip_loading",false));return }
        val labels = active.map {
            (if(it.rollId == current) copy("当前卷 · ","Current · ") else "")+
                "${it.label.ifBlank { it.rollId.take(8) }} · ${it.exposedCount()}/${it.capacity}\n${rollProgress(it)}"
        } + copy("装入另一卷（原卷与进度保留）","Load another roll (keep existing rolls and progress)")
        AlertDialog.Builder(activity).setTitle(copy("已有装填胶卷，选择续拍或换卷","Film already loaded · continue or switch")).setItems(labels.toTypedArray()) { _, choice ->
            if(!available() || running) return@setItems
            if(choice < active.size) {
                if(continueSelected) continueShooting(active[choice].rollId)
                else { current=active[choice].rollId;refresh();saveDrafts() }
            } else newRollDialog(animateLoading=!preferences.getBoolean("skip_loading",false))
        }.show()
    }
    private fun defaultFormationProfile()=if(preferences.getBoolean("load_lightweight_formation",true)) PhoneFormationProfile.LIGHTWEIGHT else PhoneFormationProfile.DETAILED
    private fun newRollDialog(animateLoading: Boolean=false) {
        val pairings=this.pairings
        val catalog=runCatching { RecipeCatalog(PrivateRecipeCatalogStore(storage)).films() }.getOrElse { status(it.message.orEmpty());return }
        val panel=LinearLayout(activity).apply { orientation=LinearLayout.VERTICAL;setPadding(shell.dp(16),0,shell.dp(16),0) }
        val details=LinearLayout(activity).apply { orientation=LinearLayout.VERTICAL;visibility=android.view.View.GONE }
        val detailsSummary=TextView(activity).apply { textSize=12f;maxLines=3;ellipsize=android.text.TextUtils.TruncateAt.END }
        var refreshDetails: ()->Unit={}
        fun choice(title: String,labels: List<String>,parent: LinearLayout=panel): Spinner {
            parent.addView(TextView(activity).apply { text=title })
            return Spinner(activity).also { it.adapter=ArrayAdapter(activity,android.R.layout.simple_spinner_dropdown_item,labels);parent.addView(it) }
        }
        val material=MaterialPicker(activity,shell,builtin,catalog,runCatching { RecipeDrafts.filmFromRecipe(readRecipe()) }.getOrDefault(emptyMap()))
        panel.addView(material)
        val lightweight=CheckBox(activity).apply {
            text=copy("轻量形成（推荐）","Lightweight formation (recommended)")
            isChecked=defaultFormationProfile()==PhoneFormationProfile.LIGHTWEIGHT
            val explanation=copy("简化分层颗粒，省略邻接效应与 DIR 抑制。保留颗粒的明暗响应及基础事故，尺寸与扫描不变；装卷后固定。",
                "Simplifies layered grain and omits adjacency and DIR inhibition. Keeps density-dependent grain and basic incidents; dimensions and scanning stay unchanged. Fixed when loading.")
            tooltipText=explanation
            setOnLongClickListener { Toast.makeText(activity,explanation,Toast.LENGTH_LONG).show();true }
        }
        panel.addView(lightweight)
        fun selectedFormationProfile()=if(lightweight.isChecked) PhoneFormationProfile.LIGHTWEIGHT else PhoneFormationProfile.DETAILED
        val pairing=choice(copy("材料／冲洗／扫描搭配","Material / process / scan pairing"),listOf(copy("自行选择","Choose independently"))+pairings.map { copy(it.labelZh,it.labelEn) },details)
        val pairingInfo=TextView(activity);details.addView(pairingInfo)
        pairing.onItemSelectedListener=object: AdapterView.OnItemSelectedListener {
            override fun onNothingSelected(parent: AdapterView<*>?)=Unit
            override fun onItemSelected(parent: AdapterView<*>?,view: android.view.View?,position: Int,id: Long) {
                val selected=pairings.getOrNull(position-1)
                if(selected!=null) material.selectBuiltIn(selected.materialId)
                refreshDetails();pairingInfo.text=selected?.let { copy(it.summaryZh,it.summaryEn)+copy("\n药浴和扫描仍可另选。","\nBaths and scanner remain independent.") }.orEmpty()
            }
        }
        val timeName=CheckBox(activity).apply { text=copy("以装卷时间编号","Use loading time as roll number");isChecked=preferences.getBoolean("time_roll_names",true) };details.addView(timeName)
        val rollName=EditText(activity).apply { setSingleLine(true);isEnabled=!timeName.isChecked };shell.field(details,rollName,copy("自定义胶卷名称（最多64字）","Custom roll name (up to 64 characters)"),copy("启用时间编号时，这一栏不使用。","This field is unused when time numbering is enabled."),resettable=false)
        timeName.setOnCheckedChangeListener { _,checked -> rollName.isEnabled=!checked;refreshDetails() }
        val formats=listOf("135","half","6x4.5","6x6","6x7","6x9")
        val format=choice(copy("画幅规格（尺寸和张数联动）","Format (aperture and roll capacity)"),listOf("135 · 3:2",copy("半格 · 3:4（18×24）","Half frame · 3:4 (18×24)"),"120 · 6×4.5 · 16","120 · 6×6 · 12","120 · 6×7 · 10","120 · 6×9 · 8"))
        format.setSelection(formats.indexOf(preferences.getString("load_format","135")).coerceAtLeast(0))
        val length=choice(copy("135 胶卷长度","135 roll length"),listOf("12","24","36"))
        length.setSelection(listOf(12,24,36).indexOf(preferences.getInt("load_length",24)).coerceAtLeast(0))
        val count=TextView(activity);panel.addView(count)
        var updatePrecision: ()->Unit={}
        fun caption() {
            val f=formats[format.selectedItemPosition];length.isEnabled=!StillFrameFormat.is120(f)
            val n=StillFrameFormat.capacity(f,if(StillFrameFormat.is120(f)) 120 else listOf(12,24,36)[length.selectedItemPosition])
            count.text=copy("本卷 $n 格；规格装卷后冻结","$n frames; format freezes when loading")
            updatePrecision()
        }
        val listener=object: AdapterView.OnItemSelectedListener {
            override fun onNothingSelected(parent: AdapterView<*>?)=Unit
            override fun onItemSelected(parent: AdapterView<*>?,view: android.view.View?,position: Int,id: Long)=caption()
        }
        format.onItemSelectedListener=listener;length.onItemSelectedListener=listener;caption()
        val selectedCamera=nativeLens()
        if(selectedCamera==null) { nativeCameraSettings();status(copy("先选择可用镜头，再装填胶卷","Select an available camera before loading film"));return }
        val captureFormat=nativeFormat()
        val imageFormat=when(captureFormat) { "raw"->android.graphics.ImageFormat.RAW_SENSOR;"ultra_hdr"->android.graphics.ImageFormat.JPEG_R;else->android.graphics.ImageFormat.JPEG }
        val sizes=runCatching { CameraOutputCatalog.jpegSizes(activity,selectedCamera,imageFormat) }.getOrDefault(emptyList())
        val ranges=runCatching { CameraOutputCatalog.fpsRanges(activity,selectedCamera) }.getOrDefault(emptyList())
        val motionSize=ShootingBudget.motion(sizes)
        val motionRange=ShootingBudget.motionFps(ranges)
        val stillSizes=sizes.filter { it.first>0 && it.second>0 }.distinct()
        val motionLabel=if(sizes.isEmpty()) copy("过片 · 长边不超过 ${ShootingBudget.MOTION_MAX_LONG_EDGE}（未读到相机尺寸）","Motion · long edge at most ${ShootingBudget.MOTION_MAX_LONG_EDGE} (camera sizes unread)")
            else copy("过片 ${motionSize.first}×${motionSize.second} · 请求 ${if(motionRange.second==0) "设备帧率" else "${motionRange.first}–${motionRange.second} fps"}","Motion ${motionSize.first}×${motionSize.second} · request ${if(motionRange.second==0) "device rate" else "${motionRange.first}–${motionRange.second} fps"}")
        val precision=choice(copy("采集精度（原片 → 有效片门）","Capture precision (source → film aperture)"),listOf(motionLabel),details)
        val precisionInfo=TextView(activity).apply { setPadding(0,shell.dp(6),0,shell.dp(8)) };details.addView(precisionInfo)
        val materialInfo=TextView(activity);panel.addView(materialInfo)
        fun selectedStock(base: Map<String,Any?>): Map<String,Any?> {
            val selected=material.selected ?: error(copy("请选择匹配的材料","Choose a matching material"))
            return if(selected.id=="current") RecipeDrafts.filmFromRecipe(base) else selected.film
        }
        fun configuredStock(base: Map<String,Any?>): Map<String,Any?> {
            val chosen=selectedStock(base);val shared=material.selected?.id=="current"
            var film=PhoneMaterialDefaults.withLevels(chosen,preferences.getInt("film_grain",2).coerceIn(0,3),
                preferences.getInt("film_mtf",1).coerceIn(0,2),preferences.getInt("film_halation",1).coerceIn(0,2),
                listOf(.65,1.0,1.7)[preferences.getInt("film_grain_size",1).coerceIn(0,2)])
            if(shared && base["enable_grain"]==false) film=film+mapOf("grain_strength" to 0.0,"silver_grain_strength" to 0.0)
            if(shared && base["enable_mtf"]==false) film=film+("emulsion_mtf_strength" to 0.0)
            if(shared && base["enable_halation"]==false) film=film+("halation_strength" to 0.0)
            return film
        }
        fun materialCaption() {
            runCatching { val base=readRecipe();PhoneRecipeCapabilities.inspect(base+("film" to selectedStock(base)),PhoneRecipeCapabilities.Stage.CAPTURE,selectedFormationProfile()) }
                .onSuccess { report->materialInfo.text=capabilityText(shell,report);materialInfo.visibility=if(report.warnings.isEmpty()) android.view.View.GONE else android.view.View.VISIBLE }
                .onFailure { materialInfo.text=it.message;materialInfo.visibility=android.view.View.VISIBLE }
        }
        lightweight.setOnCheckedChangeListener { _,_->materialCaption() }
        material.onChanged={ item ->
            pairings.getOrNull(pairing.selectedItemPosition-1)?.let { selected -> if(item?.id!="builtin:"+selected.materialId) pairing.setSelection(0) }
            if(item?.id?.startsWith("builtin:season_")==true) {
                val index=pairings.indexOfFirst { it.materialId==item.id.removePrefix("builtin:") }
                if(index>=0 && pairing.selectedItemPosition != index+1) pairing.setSelection(index+1)
            }
            materialCaption()
        }
        shell.action(panel,"预览所选材料与搭配…","Preview material & pairing…") {
            runCatching {
                val base=readRecipe();val selected=pairings.getOrNull(pairing.selectedItemPosition-1)?.recipe ?: base
                val f=formats[format.selectedItemPosition]
                val capture=mapOf("format" to f,"spatial_effects_revision" to PhoneMaterialDefaults.REVISION,PhoneGrainModels.ROLL_SETTING to PhoneGrainModels.LAYERED,"material_model" to PhoneProcessModels.CURRENT,
                    PhoneMaterialPhenomena.ROLL_SETTING to PhoneMaterialPhenomena.CURRENT,PhoneFormationProfile.ROLL_SETTING to selectedFormationProfile())
                val border=newFilmBorder(f,material.selected?.let { it.id.removePrefix("builtin:") } ?: "FILM FOUNDRY")
                CameraEffectsDialog(activity,shell,sampleWorker,sampleCache).open(cameraSettings(),configuredStock(base),capture,selected=selected+("film_border" to border),previewOnly=true) { }
            }.onFailure { Toast.makeText(activity,it.message,Toast.LENGTH_LONG).show() }
        }
        materialCaption()
        fun recommendation()=StillResolutionPlan.ranked(stillSizes,formats[format.selectedItemPosition]).firstOrNull()?.source
        fun selectedSize(): Pair<Int,Int>?=when(val picked=precision.selectedItemPosition) {
            0 -> motionSize
            1 -> recommendation()
            in 2..stillSizes.size+1 -> stillSizes[picked-2]
            else -> null
        }
        fun sizeLabel(size: Pair<Int,Int>): String {
            val estimate=StillResolutionPlan.estimate(size,formats[format.selectedItemPosition])
            return "${size.first}×${size.second} → ${estimate.gate.width}×${estimate.gate.height}"
        }
        fun precisionCaption() {
            val estimate=selectedSize()?.let { StillResolutionPlan.estimate(it,formats[format.selectedItemPosition]) }
            precisionInfo.text=if(estimate==null) copy("实际像素待相机绑定后确定；不会自动放大补像素。","Actual dimensions are determined when the camera binds; no automatic upscaling.")
                else {
                    val mp=String.format(java.util.Locale.ROOT,"%.2f",estimate.imagePixels/1_000_000.0)
                    val percent=String.format(java.util.Locale.ROOT,"%.1f",estimate.retainedFraction*100)
                    copy("有效片门 $mp 百万像素，保留原片 $percent%。原片完整留存；高像素会增加保存与冲洗成本。宽幅挡片另遮上下，扫描尺寸不增加原始细节。",
                        "Film aperture: $mp MP, $percent% of source pixels. Full source retained. More pixels cost storage and processing. Panorama masks additional rows; scan size adds no captured detail.")
                }
            refreshDetails()
        }
        updatePrecision={
            val selected=precision.selectedItemPosition.coerceAtLeast(0)
            val recommended=recommendation()
            val labels=listOf(motionLabel,copy("最大有效尺寸 · 随镜头 · ","Maximum useful size · follows lens · ")+(recommended?.let(::sizeLabel) ?: copy("尺寸未知","size unavailable")))+
                stillSizes.map { copy("静照 ","Still ")+sizeLabel(it) }+copy("静照 · 相机默认尺寸","Still · camera default size")
            precision.adapter=ArrayAdapter(activity,android.R.layout.simple_spinner_dropdown_item,labels)
            precision.setSelection(selected.coerceAtMost(labels.lastIndex));precisionCaption()
        }
        updatePrecision()
        val lastPrecision=preferences.getString("load_precision","recommended")
        precision.setSelection(when(lastPrecision) {
            "motion" -> 0
            "default" -> stillSizes.size+2
            else -> stillSizes.indexOfFirst { "${it.first}x${it.second}" == lastPrecision }.let { if(it>=0) it+2 else 1 }
        })
        precision.onItemSelectedListener=object: AdapterView.OnItemSelectedListener {
            override fun onNothingSelected(parent: AdapterView<*>?)=Unit
            override fun onItemSelected(parent: AdapterView<*>?,view: android.view.View?,position: Int,id: Long)=precisionCaption()
        }
        precisionCaption()
        val rewindModes=listOf("auto","crank")
        val rewind=choice(copy("倒片","Rewind"),listOf(copy("自动倒片","Automatic rewind"),copy("摇把倒片","Crank rewind")),details)
        rewind.setSelection(rewindModes.indexOf(preferences.getString("rewind","auto")).coerceAtLeast(0))
        refreshDetails={
            val size=selectedSize()?.let { "${it.first}×${it.second}" } ?: copy("相机默认","Camera default")
            detailsSummary.text=(if(timeName.isChecked) copy("时间编号","Time-based name") else copy("自定义名称","Custom name"))+" · "+size+" · "+rewind.selectedItem.toString()+
                (pairings.getOrNull(pairing.selectedItemPosition-1)?.let { "\n"+copy("搭配：","Pairing: ")+copy(it.labelZh,it.labelEn) } ?: "")
        }
        refreshDetails();panel.addView(detailsSummary)
        val more=shell.action(panel,"更多装卷设置 ▾","More loading settings ▾") { }
        more.setOnClickListener { refreshDetails();val open=details.visibility!=android.view.View.VISIBLE;details.visibility=if(open) android.view.View.VISIBLE else android.view.View.GONE;more.text=if(open) copy("收起更多设置 ▴","Hide more settings ▴") else copy("更多装卷设置 ▾","More loading settings ▾") }
        panel.addView(details)
        val dialog=AlertDialog.Builder(activity).setTitle(copy("装填新胶卷","Load new film")).setView(ScrollView(activity).apply { addView(panel) })
            .setNegativeButton(copy("取消","Cancel"),null).setPositiveButton(if(animateLoading) copy("开始装填","Begin loading") else copy("装填","Load"),null).create()
        dialog.setOnShowListener { dialog.getButton(AlertDialog.BUTTON_POSITIVE).setOnClickListener {
            if(!available() || running) return@setOnClickListener
            val frozen=runCatching { readRecipe() }.getOrElse { status(it.message.orEmpty());return@setOnClickListener }
            val f=formats[format.selectedItemPosition];val len=if(StillFrameFormat.is120(f)) 120 else listOf(12,24,36)[length.selectedItemPosition]
            val capacity=StillFrameFormat.capacity(f,len)
            val selectedMaterial=material.selected ?: run { status(copy("没有匹配材料，请调整筛选","No matching material; change filters"));return@setOnClickListener }
            var film=configuredStock(frozen)
            val label=runCatching { if(timeName.isChecked) RollNaming.atTime(java.time.LocalDateTime.now(),(library.shootingRolls()+library.sealedRolls()).map { it.label }) else RollNaming.validate(rollName.text.toString()) }
                .getOrElse { details.visibility=android.view.View.VISIBLE;more.text=copy("收起更多设置 ▴","Hide more settings ▴");rollName.error=it.message;rollName.requestFocus();Toast.makeText(activity,it.message,Toast.LENGTH_LONG).show();return@setOnClickListener }
            val settings=linkedMapOf<String,Any?>("age" to preferences.getInt("film_age",0)/100.0,"format" to f,"stock_length" to len)
            val chosenPairing=pairings.getOrNull(pairing.selectedItemPosition-1)
            if(chosenPairing!=null) settings["creative_pairing_id"]=chosenPairing.id
            settings["material_preset_id"]=selectedMaterial.id
            settings["spatial_effects_revision"]=PhoneMaterialDefaults.REVISION
            settings[PhoneGrainModels.ROLL_SETTING]=PhoneGrainModels.LAYERED
            settings[PhoneFormationProfile.ROLL_SETTING]=selectedFormationProfile()
            settings["gate_revision"]=StillFrameFormat.MODEL
            settings["film_border"]=newFilmBorder(f,selectedMaterial.id.takeIf { it.startsWith("builtin:") }?.removePrefix("builtin:") ?: selectedMaterial.name)
            settings["material_model"]=com.filmfoundry.interchange.PhoneProcessModels.CURRENT
            settings[com.filmfoundry.interchange.PhoneMaterialPhenomena.ROLL_SETTING]=com.filmfoundry.interchange.PhoneMaterialPhenomena.CURRENT
            film=film+("spatial_reference_short_edge_mm" to (film["spatial_reference_short_edge_mm"] ?: 24.0))
            val picked=precision.selectedItemPosition
            if(picked == 0) {
                settings["pace"]="motion";settings["precision_width"]=motionSize.first;settings["precision_height"]=motionSize.second
                if(motionRange.second > 0) { settings["motion_fps_min"]=motionRange.first;settings["motion_fps_max"]=motionRange.second }
            } else if(picked == 1 || picked in 2..stillSizes.size+1) {
                val size=selectedSize() ?: run { status(copy("未读到可推荐的拍摄尺寸，请选择相机默认尺寸或重选镜头","No advertised size available; choose camera default or another lens"));return@setOnClickListener }
                settings["pace"]="still";settings["precision_width"]=size.first;settings["precision_height"]=size.second
                settings["precision_policy"]=if(picked==1 || StillResolutionPlan.isMaximum(size,stillSizes,f)) "maximum" else "fixed"
            } else settings["pace"]="still"
            val plannedWidth=(settings["precision_width"] as? Number)?.toInt()
            val plannedHeight=(settings["precision_height"] as? Number)?.toInt()
            if(plannedWidth!=null && plannedHeight!=null) {
                runCatching { RecipeDrafts.validateCaptureSize(film,settings,plannedWidth,plannedHeight) }
                    .onFailure { precisionInfo.text=copy("这份材料与采集精度不能搭配：","Material cannot run at this precision: ")+it.message }
                    .getOrElse { return@setOnClickListener }
            }
            settings["rewind"]=rewindModes[rewind.selectedItemPosition]
            preferences.edit().putString("load_precision",when { picked == 0 -> "motion";picked == 1 -> "recommended";picked in 2..stillSizes.size+1 -> "${stillSizes[picked-2].first}x${stillSizes[picked-2].second}";else -> "default" }).apply()
            preferences.edit().putString("rewind",settings["rewind"] as String).apply()
            preferences.edit().putString("load_format",f).putInt("load_length",listOf(12,24,36)[length.selectedItemPosition]).apply()
            preferences.edit().putBoolean("time_roll_names",timeName.isChecked).apply()
            preferences.edit().putBoolean("load_lightweight_formation",lightweight.isChecked).apply()
            val draft=RollLoadingSession(library,UUID.randomUUID().toString(),capacity,film,label,
                section(chosenPairing?.recipe ?: frozen,"develop"),section(chosenPairing?.recipe ?: frozen,"scanner"),settings)
            val loaded={ if(chosenPairing!=null) stages.useLoadedPairing() }
            dialog.dismiss()
            if(animateLoading) {
                showLoading(draft,label,material.selectedLabel,f,capacity,film,loaded)
            } else job(copy("正在装填胶卷…","Loading film…"),onSuccess=loaded) { control ->
                control.checkCancel();current=draft.commit()
                copy("已装填 $capacity 格；规格与材料已冻结","Loaded $capacity frames; format and material frozen")
            }
        } };dialog.show()
    }
    private fun showLoading(draft: RollLoadingSession,label: String,material: String,format: String,capacity: Int,film: Map<String,Any?>,onLoaded: ()->Unit={}) {
        releaseLoading();loadingSession=draft
        lateinit var view: FilmLoadingView
        view=FilmLoadingView(activity,::copy,label,material,format,film,capacity,onCommit={
            if(loadingView === view && available() && !running) {
                job(copy("正在装填胶卷…","Loading film…"),onSuccess={
                    onLoaded()
                    if(loadingView === view && shell.selected == MobileShell.Page.LOADING) view.loaded()
                },onFailure={ error ->
                    if(loadingView === view) view.failed(copy("装填失败，可重试：","Loading failed; retry: ")+error.message)
                }) { control ->
                    control.checkCancel();current=draft.commit()
                    copy("已装填 $capacity 格；规格与材料已冻结","Loaded $capacity frames; format and material frozen")
                }
            } else view.failed(copy("当前任务尚未结束，请稍后重试","Wait for the current task, then retry"))
        },onExit={ shell.back() },onEnter={
            if(loadingView === view && shell.selected == MobileShell.Page.LOADING) {
                shell.enterLoadedCamera();requestCamera()
            }
        })
        loadingView=view;shell.loadingSurface.addView(view,android.widget.FrameLayout.LayoutParams(-1,-1))
        shell.select(MobileShell.Page.LOADING)
    }
    private fun releaseLoading() {
        loadingSession?.cancel();loadingSession=null
        loadingView?.dispose();loadingView=null;shell.loadingSurface.removeAllViews()
    }
    private fun finderCanAdjust()=shell.selected==MobileShell.Page.CAPTURE && !running && !captureInProgress() && available()
    private fun shutterCaption(ns: Long): String=if(ns<1_000_000_000L) "1/${(1_000_000_000.0/ns).toInt().coerceAtLeast(1)}" else String.format(java.util.Locale.ROOT,"%.1fs",ns/1e9)
    private fun showExpertReadout(source: android.view.View,control: FinderExpertPort.Control) {
        val state=expert?.snapshot() ?: FinderExpertPort.State()
        val access=if(control==FinderExpertPort.Control.ISO) state.isoAccess else state.shutterAccess
        val title=if(control==FinderExpertPort.Control.ISO) "ISO" else copy("快门速度","Shutter speed")
        val value=if(control==FinderExpertPort.Control.ISO) state.iso?.toString() ?: "AUTO" else state.shutterNs?.let(::shutterCaption) ?: "AUTO"
        val note=when(access) {
            FinderExpertPort.Access.FULL_EDITOR->copy("当前 $value，可在原生控制中调整。","Current: $value. Adjust in native controls.")
            FinderExpertPort.Access.DEVICE_UNSUPPORTED->copy("这颗镜头未开放手动曝光，保持原生自动。","This lens does not expose manual exposure; native auto remains active.")
            FinderExpertPort.Access.NOT_READY->copy("相机尚未就绪。","Camera is not ready.")
            else->copy("暂不支持此项快捷调整。","This quick control is currently unavailable.")
        }
        shell.quickControls.info(control.name,source,title,note,
            if(access==FinderExpertPort.Access.FULL_EDITOR) copy("详细原生控制…","Full native controls…") else null,
            if(access==FinderExpertPort.Access.FULL_EDITOR) ({ if(finderCanAdjust()) expert?.openEditor(control) }) else null)
    }
    private fun quickExposure(source: android.view.View) {
        if(!finderCanAdjust()) return
        val adapter=camera() as? CameraXCameraAdapter ?: return
        val native=adapter.nativeExposure()
        if(!native.supported) { shell.quickControls.info("ev",source,"EV",copy("手动曝光，补偿不参与。评价测光：${meterPhrase()}。","Manual exposure; compensation is inactive. Evaluative meter: ${meterPhrase()}."));return }
        shell.quickControls.dial("ev",source,copy("曝光补偿 EV","Exposure compensation EV"),copy("上下拖动；每格使用相机原生步长。这不是评价测光标尺。","Drag vertically; each tick uses the camera's native step. This is not the evaluative meter."),
            native.lower,native.upper,exposureTarget ?: native.index,
            { String.format(java.util.Locale.ROOT,"%+.2f EV",it*native.stepEv) },
            { if(camera()===adapter) setNativeExposure(it) },
            { val confirmed=adapter.nativeExposure()
                copy("相机确认的补偿 ","Camera-confirmed compensation ")+String.format(java.util.Locale.ROOT,"%+.2f EV",confirmed.index*confirmed.stepEv)+copy("\n评价测光 ","\nEvaluative meter ")+meterPhrase() })
    }
    private var meterState="inactive"
    private var meterEv: Double?=null
    fun showEvaluativeMeter(state: String,ev: Double?) {
        if(activity.isDestroyed || (state==meterState && ev==meterEv)) return
        meterState=state;meterEv=ev
        if(shell.selected==MobileShell.Page.CAPTURE) paintExposureReadout()
    }
    private fun liveNative()=(camera() as? CameraXCameraAdapter)?.nativeRequests()
    private fun pushNative(next: NativeCaptureSettings?) {
        if(next==null || !finderCanAdjust()) return
        val current=liveNative() ?: return
        if(next==current) return
        expert?.applyNative?.invoke(next)
    }
    private fun meterPhrase(): String {
        if(camera()==null) return copy("已停止","Off")
        val number=meterEv?.let { String.format(java.util.Locale.ROOT,"%+.1f",it) }
        return when(meterState) {
            "searching"->number?.let { copy("测光 $it","Seeking $it") } ?: copy("测光中","Seeking")
            "converged"->number ?: copy("已收敛","Converged")
            "locked"->number?.let { copy("锁定 $it","Locked $it") } ?: copy("已锁定","Locked")
            "flash"->copy("需要闪光","Flash required")
            "precapture"->copy("预闪","Precapture")
            else->copy("已停止","Off")
        }
    }
    private fun paintExposureReadout() {
        val native=(camera() as? CameraXCameraAdapter)?.nativeExposure()
        val applied=liveNative()
        val manual=applied?.iso!=null
        val compensation=if(native?.supported==true) String.format(java.util.Locale.ROOT,"%+.1f",native.index*native.stepEv) else "--"
        val meter=meterPhrase()
        val shown=if(manual) "M·"+when(meterState) {
            "searching"->copy("测","Seek")
            "flash"->copy("闪","Flash")
            "precapture"->copy("预","Pre")
            "inactive"->copy("停","Off")
            else->meterEv?.let { String.format(java.util.Locale.ROOT,"%+.1f",it) } ?: copy("测","Meter")
        } else compensation
        val meaning=when {
            manual->copy("手动曝光，补偿不参与。评价测光：$meter。","Manual exposure; compensation is inactive. Evaluative meter: $meter.")
            applied?.lockAe==true->copy("自动曝光已锁定。补偿 $compensation。评价测光：$meter。","Auto exposure is locked. Compensation $compensation. Evaluative meter: $meter.")
            else->copy("曝光补偿 $compensation。评价测光：$meter。","Exposure compensation $compensation. Evaluative meter: $meter.")
        }
        readout.value("ev",shown,meaning,applied!=null && (native?.supported==true || manual))
        readout.value("wb",if(applied==null) "--" else "AWB",copy("原生自动白平衡；手动白平衡暂不可用。","Native automatic white balance; manual white balance temporarily unavailable."),false)
        val lock=when {
            applied==null->"--"
            manual->copy("手动","M")
            applied.lockAe->copy("已锁","ON")
            else->copy("自动","OFF")
        }
        readout.value("lock",lock,copy("AE 锁：$lock。按住上下拨动，双击解锁。","AE lock: $lock. Hold and slide vertically; double-tap to unlock."),applied?.lockAe==true)
    }
    private fun quickWhiteBalance(source: android.view.View) {
        if(!finderCanAdjust()) return
        shell.quickControls.info("wb",source,"AWB",copy("使用原生自动白平衡。手动白平衡暂不可用。","Native automatic white balance is active. Manual white balance is temporarily unavailable."))
    }
    private fun quickLocks(source: android.view.View) {
        if(!finderCanAdjust()) return
        val native=liveNative() ?: return
        if(current?.let { library.roll(it).captureSettings["pace"]?.toString() }=="motion") {
            shell.quickControls.info("lock",source,copy("AE 锁","AE lock"),copy("连续过片暂不支持 AE 锁。","AE lock is unavailable during motion capture."));return
        }
        val manual=native.iso!=null
        if(manual) { shell.quickControls.info("lock",source,copy("AE 锁","AE lock"),copy("当前为手动曝光，无需锁定自动曝光。","Manual exposure is active; AE lock is unavailable."));return }
        shell.quickControls.switch("lock",source,copy("自动曝光锁","AE lock"),native.lockAe,{ liveNative()?.lockAe==true }) { enabled ->
            val now=liveNative() ?: return@switch
            if(now.iso==null) pushNative(now.copy(lockAe=enabled))
        }
    }
    private fun shutterLadder(): LongArray {
        val range=nativeLens()?.characteristics?.get(android.hardware.camera2.CameraCharacteristics.SENSOR_INFO_EXPOSURE_TIME_RANGE) ?: return LongArray(0)
        val stops=longArrayOf(125_000L,250_000L,500_000L,1_000_000L,2_000_000L,4_000_000L,8_000_000L,16_666_667L,33_333_333L,66_666_667L,125_000_000L,250_000_000L,500_000_000L,1_000_000_000L,2_000_000_000L,4_000_000_000L,8_000_000_000L,15_000_000_000L,30_000_000_000L)
        return stops.filter { range.contains(it) }.toLongArray()
    }
    private fun quickShutter(source: android.view.View) {
        if(!finderCanAdjust()) return
        val state=expert?.snapshot() ?: FinderExpertPort.State()
        val stops=shutterLadder()
        val apply=expert?.setShutter
        if(state.shutterAccess!=FinderExpertPort.Access.FULL_EDITOR || stops.isEmpty() || apply==null) {
            val note=when(state.shutterAccess) {
                FinderExpertPort.Access.DEVICE_UNSUPPORTED->copy("这颗镜头未开放手动快门，保持自动。","This lens has no manual shutter; automatic exposure remains.")
                FinderExpertPort.Access.NOT_CONNECTED->copy("过片不使用手动快门。","Motion capture does not use a manual shutter.")
                else->copy("相机尚未就绪。","Camera is not ready.")
            }
            shell.quickControls.info("shutter",source,copy("快门速度","Shutter speed"),note);return
        }
        val current=liveNative()?.exposureNs ?: 8_000_000L
        val home=stops.indices.minBy { kotlin.math.abs(stops[it]-current) }
        shell.quickControls.dial("shutter",source,copy("快门速度","Shutter speed"),
            copy("上下拖动为整档快门。双击回到自动曝光，下次从 1/125 或最接近的整档起。选定快门时会带上一个 ISO，并关闭 AE 锁和闪光。本机是否听从尚未确认。","Drag vertically through whole stops. Double-tap returns to automatic exposure; the next dial starts at 1/125 or the nearest stop. Choosing a speed also sets an ISO and turns off AE lock and flash. Whether this phone obeys it is not confirmed."),
            0,stops.lastIndex,home,{ shutterCaption(stops[it]) },
            { if(finderCanAdjust()) apply(stops[it]) },
            { liveNative()?.exposureNs?.let(::shutterCaption) ?: "AUTO" })
    }
    private fun quickImprint(source: android.view.View) {
        if(!finderCanAdjust()) return
        val settings=cameraSettings();val text=settings["imprint"]?.toString().orEmpty()
        if(text.isBlank()) { shell.closeQuickControls();status(copy("请先设置打印内容","Set imprint text first"));imprintDialog();return }
        shell.quickControls.switch("print",source,copy("文字打印","Text imprint"),quickImprintEnabled(),::quickImprintEnabled,::setQuickImprint)
    }
    private fun imprintStyle(value: Any?)=value?.toString()?.takeIf { it in listOf("laser_dot","optical_amber","lab_edge") }
    private fun quickImprintEnabled()=cameraSettings().let { it["imprint"]?.toString().orEmpty().isNotBlank() && it["imprint_style"]!="none" }
    private fun setQuickImprint(enabled: Boolean) {
        if(!finderCanAdjust()) return
        val settings=cameraSettings()
        if(enabled && settings["imprint"]?.toString().orEmpty().isBlank()) { shell.closeQuickControls();imprintDialog();return }
        val previous=imprintStyle(settings["imprint_style"])
        val style=if(!enabled) "none" else previous ?: imprintStyle(preferences.getString("quick_imprint_style",null)) ?: "optical_amber"
        preferences.edit().apply {
            if(previous!=null) putString("quick_imprint_style",previous)
            putString("camera_effects_v3",RecipeJson.text(settings+("imprint_style" to style)))
        }.apply();refresh()
    }
    private fun setNativeExposure(requested: Int) {
        if(!finderCanAdjust()) return
        val adapter=camera() as? CameraXCameraAdapter ?: return
        val native=adapter.nativeExposure();if(!native.supported) return
        val index=requested.coerceIn(native.lower,native.upper)
        exposureTarget=index
        shoot.isEnabled=false
        if(!exposureBusy) sendExposure(adapter,index)
    }
    private fun sendExposure(adapter: CameraXCameraAdapter,index: Int) {
        exposureBusy=true;exposureOwner=adapter;val epoch=exposureEpoch
        adapter.adjustExposureCompensation(index) { error ->
            activity.runOnUiThread {
                if(activity.isDestroyed) return@runOnUiThread
                if(epoch!=exposureEpoch || exposureOwner!==adapter) return@runOnUiThread
                if(camera()!==adapter) { exposureEpoch++;exposureOwner=null;exposureBusy=false;exposureTarget=null;shell.closeQuickControls();refresh();return@runOnUiThread }
                exposureBusy=false
                val wanted=exposureTarget
                if(error != null) {
                    exposureTarget=null
                    exposureOwner=null;shell.closeQuickControl("ev")
                    if(shell.selected == MobileShell.Page.CAPTURE) android.widget.Toast.makeText(activity,error.message,android.widget.Toast.LENGTH_SHORT).show()
                    refresh()
                } else if(wanted != null && wanted != index && finderCanAdjust()) sendExposure(adapter,wanted)
                else { exposureTarget=null;exposureOwner=null;refresh() }
            }
        }
    }
    private fun updateDevelopmentHeader() {
        shell.developResultBar.visibility=if(completedDevelopmentRoll!=null && completedDevelopmentRoll==developmentRoll && shell.developResultBar.childCount>0) android.view.View.VISIBLE else android.view.View.GONE
        val roll=developmentRoll?.let { runCatching { library.roll(it) }.getOrNull() }
        selectedRoll.text=if(roll==null) copy("选择已封卷的胶卷后准备冲洗","Choose a sealed roll to prepare development") else
            rollName(roll.rollId)+copy(" · ${roll.exposedCount()} 张"," · ${roll.exposedCount()} frames")+"\n"+
                if(autoFirstPreview.isChecked) copy("保存：底片＋图库照片","Save: film + Library photos")+"\n"+stages.automaticOutputLabel() else copy("保存：底片 · 可稍后扫描","Save: film · scan later")
        selectedRoll.contentDescription=selectedRoll.text.toString()+roll?.let { " · "+materialLabel(it) }.orEmpty()
    }
    private fun resumeDevelopment() {
        val id=developmentRoll
        if(id==null) { resumeDialog();return }
        job(copy("正在读取本卷任务…","Loading this roll's task…")) { _ ->
            val task=rollTasks.latest(id)?.takeIf { it.status !in listOf("completed","abandoned") }
            if(task!=null) activity.runOnUiThread { if(!activity.isDestroyed) continuationDialog(task) }
            if(task==null) copy("本卷没有未完成冲洗任务","This roll has no unfinished development") else copy("已读取本卷任务","This roll's task is ready")
        }
    }
    private fun developDialog() {
        val rolls=library.sealedRolls()
        if(rolls.isEmpty()) { status(copy("请先拍摄并封卷","Capture and seal a roll first"));return }
        AlertDialog.Builder(activity).setTitle(copy("选择冲洗胶卷","Choose a roll to develop"))
            .setItems(rolls.map { rollName(it.rollId)+copy(" · ${it.exposedCount()} 张"," · ${it.exposedCount()} frames") }.toTypedArray()) { _,index ->
                developmentRoll=rolls[index].rollId;refresh();shell.select(MobileShell.Page.DEVELOP)
            }.show()
    }
    private fun startDevelopment(id: String,selectedOutput: ScanOutputSettings?=null) {
                val old=rollTasks.latest(id)
                if(old!=null && old.status !in listOf("completed","abandoned")) { continuationDialog(old);return }
                if(autoFirstPreview.isChecked && selectedOutput==null) {
                    OutputQualityControls.scan(activity,shell,stages.automaticOutputSettings(),false) { value -> if(idle()) startDevelopment(id,value) }
                    return
                }
                val nextRecipe=runCatching { stages.development(readRecipe(),library.roll(id).developRecipe) }.getOrElse { status(it.message.orEmpty());return }
                val photos=if(selectedOutput==null) developmentPhotos(id) else DevelopmentPhotoPlan.prepare(true) { scanRecipe(id)+mapOf("output_format" to selectedOutput.format,"output_bits" to selectedOutput.actualBits,"jpeg_quality" to selectedOutput.jpegQuality) }
                exportUi.confirmExecution(copy("确认冲洗","Confirm development"),developmentExecutionInfo(id,nextRecipe,photos),photos.enabled,outputRecipe=photos.recipe) { album ->
                job(copy("正在整卷冲洗…","Developing roll…"),allowBrowsing=true) { control ->
                    val previous=rollTasks.latest(id)
                    if(previous != null && previous.status !in listOf("completed","abandoned")) {
                        activity.runOnUiThread { if(!activity.isDestroyed) continuationDialog(previous) }
                        copy("已有未完成任务；继续时沿用原冻结配方","Unfinished task found; resume keeps its frozen recipe")
                    } else {
                        val task=rollTasks.create(id,UUID.randomUUID().toString(),nextRecipe)
                        preferences.edit().putBoolean("album_task_${task.id}",album).apply()
                        runDevelopment(task,photos,control)
                    }
                }
                }
    }
    private fun resumeDialog() {
        job(copy("正在读取任务…","Loading tasks…")) { control ->
            val tasks=library.sealedRolls().mapNotNull { control.checkCancel();rollTasks.latest(it.rollId) }
                .filter { it.status !in listOf("completed","abandoned") }
            if(tasks.isEmpty()) copy("没有未完成冲洗任务","No unfinished development tasks") else {
                activity.runOnUiThread { if(!activity.isDestroyed) AlertDialog.Builder(activity)
                    .setTitle(copy("选择继续的任务","Resume a task"))
                    .setItems(tasks.map { "${it.rollId.take(8)} · ${it.frames.count { f -> f.phase == "registered" }}/${it.frames.size}" }.toTypedArray()) { _,index ->
                        if(available() && !running) continuationDialog(tasks[index])
                    }.show() }
                copy("选择任务后继续；相机曝光条件与原冲洗配方保留","Choose a task; captured conditions and process recipe are retained")
            }
        }
    }
    private fun continuationDialog(task: DevelopmentTask) {
        AlertDialog.Builder(activity).setTitle(copy("已有未完成冲洗任务","Unfinished development task"))
            .setItems(arrayOf(copy("继续原任务（原配方）","Resume original task (frozen recipe)"),copy("结束旧任务，以当前配方另开一批","End this task and start a new batch"))) { _,choice ->
                if(!available() || running) return@setItems
                val recorded=if(choice==0) preferences.getString("development_scan_recipe_${task.id}",null) else null
                val photos=if(recorded==null) developmentPhotos(task.rollId)
                    else DevelopmentPhotoPlan.prepare(recorded.isNotBlank()) { RecipeShareCodec.decode(recorded) }
                if(choice == 0) exportUi.confirmExecution(copy("继续原冲洗任务","Resume development"),developmentExecutionInfo(task.rollId,task.recipe,photos),photos.enabled,
                    preferences.getBoolean("album_task_${task.id}",false),outputRecipe=photos.recipe) { album ->
                    preferences.edit().putBoolean("album_task_${task.id}",album).apply()
                    job(copy("正在继续原任务…","Resuming task…"),allowBrowsing=true) { control -> runDevelopment(task,photos,control) }
                }
                else AlertDialog.Builder(activity).setMessage(copy("已完成结果与原片保留；旧任务将结束，新批次采用当前配方。","Completed results and sources remain. End this task and use the current recipe for a new batch."))
                    .setNegativeButton(copy("返回","Back"),null).setPositiveButton(copy("另开一批","Start new batch")) { _,_ ->
                        val nextRecipe=runCatching { stages.development(readRecipe(),library.roll(task.rollId).developRecipe) }.getOrElse { status(it.message.orEmpty());return@setPositiveButton }
                        if(available() && !running) exportUi.confirmExecution(copy("确认新批次","Confirm new development"),developmentExecutionInfo(task.rollId,nextRecipe,photos),photos.enabled,outputRecipe=photos.recipe) { album ->
                          job(copy("正在新建冲洗任务…","Starting development task…"),allowBrowsing=true) { control ->
                            control.checkCancel();rollTasks.abandon(task.rollId,task.id)
                            val next=rollTasks.create(task.rollId,UUID.randomUUID().toString(),nextRecipe)
                            preferences.edit().putBoolean("album_task_${next.id}",album).apply();runDevelopment(next,photos,control)
                          }
                        }
                    }.show()
            }.show()
    }
    private fun developmentPhotos(id: String)=DevelopmentPhotoPlan.prepare(autoFirstPreview.isChecked) { scanRecipe(id) }.also { plan ->
        plan.error?.let { Toast.makeText(activity,copy("自动扫描参数有误，本次只冲洗，底片保留：","Automatic scan settings are invalid. This run will develop negatives only: ")+it,Toast.LENGTH_LONG).show() }
    }
    private suspend fun runDevelopment(task: DevelopmentTask,photos: DevelopmentPhotoPlan,control: WorkflowControl): String {
        activity.runOnUiThread { if(!activity.isDestroyed && developmentRoll==task.rollId) { completedDevelopmentRoll=null;shell.developResultBar.visibility=android.view.View.GONE } }
        var firstShown=false;var firstMs: Long?=null;var previewFailed=false;val started=System.nanoTime()
        val planKey="development_scan_recipe_${task.id}"
        val recorded=preferences.getString(planKey,null)
        val frozenShare=recorded ?: (photos.share ?: "").also {
            check(preferences.edit().putString(planKey,it).commit()) { "Cannot freeze automatic scan settings" }
        }
        val frozenPhotos=DevelopmentPhotoPlan.prepare(frozenShare.isNotBlank()) { RecipeShareCodec.decode(frozenShare) }
        val showFirst=frozenPhotos.enabled
        val observation=frozenPhotos.recipe.orEmpty()
        val share=frozenPhotos.share.orEmpty()
        val observationModel=frozenPhotos.model.orEmpty()
        val delivered=if(showFirst) runCatching { (0 until scans.count() step 20).flatMap { scans.listNewest(it,20) }
            .filter { it.recipeShare == share && ObservationImplementation.matches(it.observationModel,observationModel) }.map { it.resultId }.toSet() }.getOrDefault(emptySet()) else emptySet()
        val delivery=PhotoDeliverySession(delivered,control)
        val results=rollTasks.runWithRows(task.rollId,task.id,control,{ progress ->
            activePerformance?.frame(progress.phase,progress.frameIndex,progress.total)
            val stage=when(progress.phase) { "forming" -> copy("生成潜影","Forming latent");"developing" -> copy("冲洗","Developing");else -> copy("已登记","Registered") }
            taskStatus(copy("已完成 ${progress.done}/${progress.total}","Completed ${progress.done}/${progress.total}")+" · "+frameLabel(progress.frameIndex)+" · "+stage)
        },retainRows=showFirst && jobOpticalHandoff) { result,rowIo,handoff ->
            var firstPhoto: SavedScan?=null
            if(showFirst) {
                val deliveredNow=delivery.deliver(result.resultId) { firstPhoto=scanAndSave(result,observation,control,rowIo,handoff.takeIf { jobOpticalHandoff }) }
                if(!deliveredNow) activePerformance?.frame("scan_failed",result.sourceFrameIndex,0)
                else if(firstPhoto==null) activePerformance?.frame("scan_reused",result.sourceFrameIndex,0)
            }
            if(showFirst && !firstShown) {
                firstShown=true
                try {
                    val saved=firstPhoto ?: scans.findNewest(result.resultId,share,control::checkCancel)
                    if(saved != null && activity.lifecycle.currentState.isAtLeast(androidx.lifecycle.Lifecycle.State.STARTED)) {
                        val bitmap=decodeSavedPhoto(saved,640)
                        activity.runOnUiThread { if(!activity.isDestroyed) {
                            developmentImage.visibility=android.view.View.VISIBLE;replaceImage(developmentImage,bitmap)
                        } else bitmap.recycle() }
                    }
                    firstMs=((System.nanoTime()-started)/1_000_000).coerceAtLeast(0)
                } catch(error: Exception) { control.checkCancel();previewFailed=true }
            }
        }
        val timing=firstMs?.let { copy(" · 本次首张可看片 $it ms"," · First preview this run: $it ms") }.orEmpty()
        val saved=if(showFirst) results.mapNotNull { scans.findNewest(it.resultId,share,control::checkCancel) } else emptyList()
        val albumNote=if(showFirst && preferences.getBoolean("album_task_${task.id}",false)) {
            if(saved.isEmpty()) copy("\n没有可导出的扫描照片，冲洗底片保留","\nNo scanned photos to export; developed film retained") else
                "\n"+exportUi.summary(libraryExports.gallery(saved.map(libraryExports::photo),control))
        } else ""
        val savedNote=when {
            frozenPhotos.error!=null -> copy("自动扫描参数需修正，可之后从底片扫描：","Fix automatic scan settings and scan the negatives later: ")+frozenPhotos.error+" "
            showFirst && delivery.failed==0 -> copy("照片在图库里。","Photos are in the Library. ")
            delivery.failed>0 -> copy("有 ${delivery.failed} 张没存上，可以之后从底片再做。","${delivery.failed} photos were not saved. Make them later from the negatives. ")
            else -> copy("这次未启用自动扫描。","Automatic scanning was off. ")
        }
        control.checkCancel()
        activity.runOnUiThread { if(!activity.isDestroyed && developmentRoll==task.rollId) {
            completedDevelopmentRoll=task.rollId
            shell.developResultBar.apply { removeAllViews();visibility=android.view.View.VISIBLE }
            shell.note(shell.developResultBar,"已保留 ${results.size} 张底片 · ${saved.size} 张照片","${results.size} film images retained · ${saved.size} photos")
            if(albumNote.isNotBlank()) shell.note(shell.developResultBar,albumNote.trim(),albumNote.trim())
            else shell.note(shell.developResultBar,"本次未自动导出到系统相册","No automatic system-gallery export this run")
            val actions=LinearLayout(activity);shell.developResultBar.addView(actions)
            shell.action(actions,"查看本批底片","View batch film") { if(idle() && results.isNotEmpty()) {
                detailRollId=task.rollId;detailBatchId=RollLibraryContents.batchId(results.first());batchPage=0;shell.select(MobileShell.Page.ROLL_BATCH)
            } }.layoutParams=LinearLayout.LayoutParams(0,-2,1f)
            shell.action(actions,if(saved.isEmpty()) "扫描本批" else "查看照片",if(saved.isEmpty()) "Scan batch" else "View photos") { if(idle()) {
                if(saved.isNotEmpty()) openSavedPhoto(saved.first(),saved) else if(results.isNotEmpty()) { scanBatchFrames=results;openScanner(results.first()) }
            } }.layoutParams=LinearLayout.LayoutParams(0,-2,1f)
            if(saved.isNotEmpty()) shell.action(actions,"导出照片","Export photos") { if(idle()) exportUi.images(LibraryExportRequest("photos",saved.map { it.id })) }.layoutParams=LinearLayout.LayoutParams(0,-2,1f)
        } }
        return copy("冲洗完成：已保存 ${results.size} 张底片。","Development complete: ${results.size} film images saved. ")+savedNote+timing+albumNote+
            if(previewFailed) copy("预览没显示出来，可以到图库里看。","The preview did not appear. Look in the Library.") else ""
    }
    private fun resultsDialog() {
        val rows = library.developedResults()
        if(rows.isEmpty()) { status(copy("还没有底片","No negatives yet")); return }
        AlertDialog.Builder(activity).setTitle(copy("选择一张底片","Choose a negative")).setItems(rows.map {
            resultLabel(it)
        }.toTypedArray()) { _, index ->
            openScanner(rows[index])
        }.show()
    }
    private fun chooseResult(result: DevelopedResult) = viewNegative(result)
    private fun viewingHint(mode: String,illuminant: Any?)=when {
        mode=="viewer_projection" -> copy("投射预览","Projection preview")
        illuminant=="scene_camera" -> copy("透明底片覆盖实时相机","Transparent film over the live camera")
        else -> copy("灯台透射","Light-table transmission")
    }
    private fun openLightTable(result: DevelopedResult,mode: String) {
        stopScene()
        viewerDisplayMode=mode;viewerSceneLight=false
        val frozen=runCatching { stages.observation(readRecipe(),library.roll(result.sourceRollId).scannerRecipe) }.getOrElse { status(it.message.orEmpty());return }
        val revision=++viewerRevision;val page=beginViewerPage("negative:${result.resultId}")
        updateViewerNavigation()
        runViewer { control -> renderPreview(result,frozen,mode,control,viewerToken=revision,pageToken=page) }
    }
    private fun viewingModes(result: DevelopedResult) {
        guide(copy("观片方式","Viewing mode")) { close ->
            shell.action(this,"灯台","Light table") { if(!canBrowse()) return@action;close();openLightTable(result,"viewer_light_table") }
            shell.action(this,"实景透射","Scene transmission") { if(!canBrowse()) return@action;close();openCameraView(result) }
            shell.action(this,"正像投射","Positive projection") { if(!canBrowse()) return@action;close();openLightTable(result,"viewer_projection") }
        }
    }
    private fun viewingLight(result: DevelopedResult) {
        val panel=LinearLayout(activity).apply { orientation=LinearLayout.VERTICAL;setPadding(shell.dp(20),shell.dp(8),shell.dp(20),shell.dp(8)) }
        shell.note(panel,"只改这次看片的亮度和颜色。底片和照片配方不变。","Changes the light for this viewing only. The negative and the photo finish stay as they are.")
        fun number(label: String,value: Double,low: Double,high: Double,step: Double,default: Double,temperature: Boolean=false): EditText {
            shell.note(panel,label,label)
            val row=LinearLayout(activity).apply { gravity=android.view.Gravity.CENTER_VERTICAL };panel.addView(row)
            val slider=SeekBar(activity).apply { max=if(temperature) TemperatureScale.STEPS else ((high-low)/step).toInt();progress=if(temperature) TemperatureScale.progress(value) else ((value-low)/step).toInt().coerceIn(0,max) }
            val input=EditText(activity).apply {
                contentDescription=label;setSingleLine(true)
                inputType=android.text.InputType.TYPE_CLASS_NUMBER or android.text.InputType.TYPE_NUMBER_FLAG_DECIMAL or android.text.InputType.TYPE_NUMBER_FLAG_SIGNED
                setText(if(step>=1) value.toInt().toString() else "%.1f".format(java.util.Locale.ROOT,value))
            }
            row.addView(slider,LinearLayout.LayoutParams(0,shell.dp(52),1f));row.addView(input,LinearLayout.LayoutParams(shell.dp(100),shell.dp(52)))
            row.addView(shell.resetButton(label) { input.error=null;input.setText(default.toString()) },LinearLayout.LayoutParams(shell.dp(48),shell.dp(48)))
            slider.setOnSeekBarChangeListener(object: SeekBar.OnSeekBarChangeListener {
                override fun onProgressChanged(bar: SeekBar?,n: Int,user: Boolean) { if(user) {
                    val v=if(temperature) TemperatureScale.kelvin(n) else low+n*step;input.setText(if(step>=1) v.toInt().toString() else "%.1f".format(java.util.Locale.ROOT,v));input.setSelection(input.text.length)
                } }
                override fun onStartTrackingTouch(bar: SeekBar?)=Unit
                override fun onStopTrackingTouch(bar: SeekBar?)=Unit
            })
            input.addTextChangedListener(object: android.text.TextWatcher {
                override fun beforeTextChanged(s: CharSequence?,start: Int,count: Int,after: Int)=Unit
                override fun onTextChanged(s: CharSequence?,start: Int,before: Int,count: Int) {
                    val v=s?.toString()?.toDoubleOrNull();if(v!=null && v.isFinite() && v in low..high) slider.progress=if(temperature) TemperatureScale.progress(v) else ((v-low)/step).toInt().coerceIn(0,slider.max)
                }
                override fun afterTextChanged(s: android.text.Editable?)=Unit
            })
            return input
        }
        val ev=number(copy("亮度 · EV（-8 至 +8）","Brightness · EV (-8 to +8)"),viewingEv,-8.0,8.0,.1,0.0)
        val kelvin=number(copy("色温 · K","Temperature · K"),viewingKelvin,1000.0,20000.0,100.0,5500.0,true)
        shell.resetSection(panel,copy("灯台光源","Light-table lamp")) { ev.error=null;kelvin.error=null;ev.setText("0");kelvin.setText("5500") }
        val dialog=AlertDialog.Builder(activity).setTitle(copy("观片灯光","Viewing light")).setView(panel)
            .setNegativeButton(copy("取消","Cancel"),null).setPositiveButton(copy("应用并看灯台","Apply to light table"),null).create()
        dialog.setOnShowListener { dialog.getButton(AlertDialog.BUTTON_POSITIVE).setOnClickListener {
            val e=ev.text.toString().toDoubleOrNull();val k=kelvin.text.toString().toDoubleOrNull()
            if(e==null || !e.isFinite() || e !in -8.0..8.0) { ev.error=copy("请输入 -8 至 8","Enter -8 to 8");return@setOnClickListener }
            if(k==null || !k.isFinite() || k !in 1000.0..20000.0) { kelvin.error=copy("请输入 1000 至 20000","Enter 1000 to 20000");return@setOnClickListener }
            viewingEv=e;viewingKelvin=k;dialog.dismiss();viewNegative(result,negativeSequence)
        } };dialog.show()
    }
    private fun chooseResultAdvanced(result: DevelopedResult) {
        libraryResult=result
        guide(copy("底片操作","Film actions")) { close ->
            shell.action(this,"详细信息","Information") { close();showNegativeInformation(result) }
            shell.action(this,"导出图片／处理材料…","Export image / processing material…") { if(!idle()) return@action;close();exportSelectedNegatives(listOf(result)) }
            shell.note(this,"图片用于观看；光学 NPZ 可交给电脑版继续扫描。原底片保留。","Images are for viewing; optical NPZ continues scanning on desktop. The original film stays.")
            shell.note(this,"仅删除当前底片，保留胶卷与已保存照片。","Deletes this developed film only. The roll and saved photos are retained.")
            shell.action(this,"删除底片","Delete negative") { if(!canManage()) return@action;close();removeSelectedNegatives(listOf(result)) }
            shell.action(this,"来源记录","Source history") { if(!canBrowse()) return@action;close();originDialog(result.sourceRollId,result.sourceFrameIndex,result.resultId) }
        }
    }
    private fun rollDetail(roll: Roll) {
        detailRollId=roll.rollId;detailBatchId=null;batchPage=0
        shell.select(MobileShell.Page.ROLL_DETAIL)
    }
    private fun detailAction(parent: LinearLayout,zh: String,en: String,primary: Boolean=false,readOnly: Boolean=false,action: ()->Unit) {
        fun allowed()=if(readOnly) canBrowse() else idle()
        shell.action(parent,zh,en,primary) { if(allowed()) action() }.isEnabled=allowed()
    }
    private fun renderRollDetail() {
        val id=detailRollId ?: return;val roll=library.roll(id)
        val batches=RollLibraryContents.batches(id,library.developedResults())
        rollDetailRows.removeAllViews();shell.pageTitle(rollName(id))
        val state=when(roll.lifecycle) {
            RollLifecycle.SEALED->copy("已封卷","Sealed")
            RollLifecycle.DISCARDED->copy("曝光已释放 · 底片与照片独立保留","Exposures removed · negatives and photos retained")
            else->copy("正在拍摄 · 尚未封卷","Shooting · not sealed")
        }
        shell.note(rollDetailRows,if(roll.lifecycle==RollLifecycle.DISCARDED) state else "$state · ${roll.exposedCount()}/${roll.capacity}",if(roll.lifecycle==RollLifecycle.DISCARDED) state else "$state · ${roll.exposedCount()}/${roll.capacity}")
        val facts=shell.note(rollDetailRows,copy("正在读取信息…","Reading information…"),copy("正在读取信息…","Reading information…"))
        bindRollInformation(facts,roll,"${roll.captureSettings["format"] ?: "135"} · ")
        detailAction(rollDetailRows,"详细信息","Information",readOnly=true) { showRollInformation(roll) }
        if(roll.lifecycle==RollLifecycle.SEALED) {
            detailAction(rollDetailRows,if(batches.isEmpty()) "冲洗本卷" else "用本卷潜影重新冲洗",if(batches.isEmpty()) "Develop this roll" else "Develop this roll again",true) {
                developmentRoll=id;refresh();shell.select(MobileShell.Page.DEVELOP)
            }
            rollTasks.latest(id)?.takeIf { it.status !in listOf("completed","abandoned") }?.let { task ->
                detailAction(rollDetailRows,"继续／结束未完成冲洗","Resume / end unfinished development") { continuationDialog(task) }
            }
        } else if(roll.lifecycle!=RollLifecycle.DISCARDED) shell.note(rollDetailRows,"封卷后即可冲洗。","Seal this roll before development.")
        shell.note(rollDetailRows,"冲洗结果 · ${batches.size} 批","${batches.size} retained development batches")
        if(batches.isEmpty()) shell.note(rollDetailRows,"暂无保留的冲洗结果。","No retained development results.")
        for(batch in batches.asReversed()) { val tile=shell.listTile(rollDetailRows,
            batchLabel(batch.id),
            if(roll.lifecycle==RollLifecycle.DISCARDED) copy("保留 ${batch.frames.size} 张底片 · 原曝光已释放","${batch.frames.size} negatives retained · exposures removed") else copy("保留 ${batch.frames.size}/${roll.exposedCount()} 张底片 · 观片与扫描","${batch.frames.size}/${roll.exposedCount()} negatives retained · view and scan"),"›") {
                if(canBrowse()) { detailBatchId=batch.id;batchPage=0;shell.select(MobileShell.Page.ROLL_BATCH) }
            }
            tile.tooltipText=copy("长按管理本批冲洗结果","Hold to manage this batch")
            tile.setOnLongClickListener {
                if(canBrowse()) { tile.performHapticFeedback(android.view.HapticFeedbackConstants.LONG_PRESS);manageBatch(batch.id,batch.frames) };true
            }
        }
        if(scanTasks.latest(id)?.unfinished==true) detailAction(rollDetailRows,"继续／结束未完成扫描","Resume / end unfinished scans") { unfinishedScanDialog(roll) }
        detailAction(rollDetailRows,"本卷已保存的照片","Saved photos from this roll",readOnly=true) { openRollGallery(id,false) }
        detailAction(rollDetailRows,"导出、命名与存储管理…","Export, naming & storage…") { rollDetailAdvanced(roll) }
        detailAction(rollDetailRows,"删除胶卷…","Delete roll…") { deleteRoll(roll) }
        shell.decorate(rollDetailRows)
    }
    private fun manageBatch(id: String,frames: List<DevelopedResult>) {
        guide(batchLabel(id)) { close ->
            shell.action(this,"选择本批底片","Select negatives in batch") {
                if(canBrowse()) { close();detailBatchId=id;batchPage=0;batchSelection.selectAll(frames);shell.select(MobileShell.Page.ROLL_BATCH) }
            }
            shell.action(this,"导出本批…","Export batch…") { if(idle()) { close();exportSelectedNegatives(frames) } }.isEnabled=idle()
            shell.action(this,"删除本批底片…","Delete batch negatives…") { if(canManage()) { close();removeSelectedNegatives(frames) } }.isEnabled=canManage()
            shell.note(this,"删除底片会保留胶卷潜影与已有照片。","Deleting negatives retains latent exposures and saved photos.")
        }
    }
    private fun renderBatch() {
        val id=detailRollId ?: return;val batches=RollLibraryContents.batches(id,library.developedResults())
        val index=batches.indexOfFirst { it.id==detailBatchId };val batch=batches.getOrNull(index)
        batchGrid.suspendLoading();(batchGrid.parent as? android.view.ViewGroup)?.removeView(batchGrid);batchRows.removeAllViews()
        shell.pageTitle(if(batch==null) copy("冲洗结果已移除","Development results removed") else batchLabel(batch.id))
        if(batch==null) { shell.note(batchRows,"本批已无底片记录，已保存的照片仍可在图库查看。","No film records remain in this batch. Saved photos are still available in Library.");return }
        shell.note(batchRows,rollName(id)+copy(" · ${batch.frames.size} 张底片"," · ${batch.frames.size} negatives"),rollName(id)+" · ${batch.frames.size} negatives")
        detailAction(batchRows,"冲洗配方详情","Development recipe details",readOnly=true) { showNegativeInformation(batch.frames.first()) }
        batchSelection.clearBindings();(batchSelection.parent as? android.view.ViewGroup)?.removeView(batchSelection);batchRows.addView(batchSelection)
        detailAction(batchRows,"调整扫描参数…","Adjust scan settings…",true) {
            AlertDialog.Builder(activity).setTitle(copy("选择预览底片","Choose a preview frame"))
                .setItems(batch.frames.map { frameLabel(it.sourceFrameIndex) }.toTypedArray()) { _,which ->
                    scanBatchFrames=batch.frames;openScanner(batch.frames[which])
                }.show()
        }
        detailAction(batchRows,"按当前参数扫描本批","Scan batch with current settings") { scanBatchDialog(library.roll(id),batch.frames) }
        batchPage=batchPage.coerceIn(0,(batch.frames.size-1).coerceAtLeast(0)/20)
        val visible=batch.frames.drop(batchPage*20).take(20)
        batchGrid.submit(visible.map { negativeEntry(it,batch.frames,batchSelection) },galleryDisplay.columns);batchRows.addView(batchGrid)
        pagination(batchRows,batch.frames.size,batchPage) { batchPage=it;renderBatch() }
        shell.decorate(batchRows);batchGrid.schedule()
    }
    private fun negativeEntry(result: DevelopedResult,sequence: List<DevelopedResult>,selection: LibrarySelectionBar<DevelopedResult> = negativeSelection)=ThumbnailGrid.Entry(
        "negative:${result.resultId}",LibraryInfoPresentation.filmTitle(rollName(result.sourceRollId),result.sourceFrameIndex,result.resultId,copy("zh","en")=="en")+" "+galleryDisplay.badge("negative:${result.resultId}"),copy("正在读取信息…","Reading information…"),true,
        load={ check -> negativeThumbnail(result,check) },open={ if(canBrowse()) viewNegative(result,sequence) },bindSelection={ tile,check ->
            selection.bind(tile,result,{ viewNegative(result,sequence) }) { active,selected -> check.visibility=if(active) android.view.View.VISIBLE else android.view.View.GONE;check.isChecked=selected }
        },information={ check -> check();information.negative(result).let { facts -> if(galleryDisplay.columns!=1) rollName(result.sourceRollId)+" · "+(facts.time?.take(16) ?: "—")+" "+galleryDisplay.badge("negative:${result.resultId}") else tileInformationText(facts) }.also { check() } },rotationDegrees=negativeRotation(result.resultId),compact=rollName(result.sourceRollId)+" · "+(information.peek(result.resultId)?.time?.take(16) ?: "—")+" "+galleryDisplay.badge("negative:${result.resultId}"))
    /** Standard light-table tile. The PNG is a private display cache, never a medium. */
    private fun negativeThumbnail(result: DevelopedResult,check: ()->Unit): Bitmap {
        check()
        val generation=displayCacheGeneration.get()
        val relative=NegativeDisplayStore.relative(result.resultId)
        if(generation==displayCacheGeneration.get()) storedThumbnail(relative)?.let { return it }
        check()
        val preview=previewCache.get("192:${result.resultId}:${result.opticalPath}") {
            opticalProxies.get(result.resultId,result.opticalPath,192,check)
        }
        check()
        val control=WorkflowControl()
        val observation=runBlocking { OpticalPreviewSession(facade).observe(preview,emptyMap(),true,control,"viewer_light_table") }
        check()
        val bitmap=observationBitmap(observation,control)
        runCatching {
            if(generation!=displayCacheGeneration.get()) return@runCatching
            storage.writePrivate(relative) { out ->
                if(!bitmap.compress(Bitmap.CompressFormat.PNG,100,out)) error("display thumbnail was not written")
            }
            if(generation!=displayCacheGeneration.get()) NegativeDisplayStore.delete(storage,result.resultId)
        }
        return bitmap
    }
    private fun storedThumbnail(relative: String): Bitmap? {
        if(!storage.existsPrivate(relative)) return null
        val bitmap=runCatching {
            val bytes=storage.readPrivate(relative)
            val options=android.graphics.BitmapFactory.Options().apply { inPreferredConfig=Bitmap.Config.ARGB_8888 }
            android.graphics.BitmapFactory.decodeByteArray(bytes,0,bytes.size,options)
        }.getOrNull()
        if(bitmap!=null) return bitmap
        runCatching { storage.deletePrivate(relative) }
        return null
    }
    private fun openRollGallery(rollId: String,negative: Boolean) {
        photoSelection.clear();negativeSelection.clear()
        galleryRoll=rollId;negativesSelected=negative;preferences.edit().putBoolean("gallery_negatives",negative).apply()
        scanPage=0;resultPage=0;listsDirty=true;shell.select(MobileShell.Page.OBSERVE)
    }
    private fun rollDetailAdvanced(roll: Roll) {
        guide(copy("导出和存储","Export and storage")) { close ->
            shell.action(this,"改名","Rename") { if(!idle()) return@action;close();renameDialog(roll) }
            if(roll.frames.any { it.capturePath!=null }) {
                shell.note(this,"导出相机保存的原始 JPEG／DNG，卷内原片保留。","Export original camera JPEG / DNG files. Captures stay on the roll.")
                shell.action(this,"导出整卷原片 · JPEG / DNG","Export all camera originals · JPEG / DNG") { if(!idle()) return@action;close();exportUi.files(LibraryExportRequest("originals",listOf(roll.rollId))) }
                shell.action(this,"按画面导出原片…","Export originals by frame…") { if(!idle()) return@action;close();exportOriginalDialog(roll) }
            }
            if(roll.lifecycle==RollLifecycle.SEALED) {
                shell.action(this,"去冲洗","Develop") { if(!idle()) return@action;close();developmentRoll=roll.rollId;refresh();shell.select(MobileShell.Page.DEVELOP) }
                shell.note(this,"导出潜影 NPZ，可在电脑版继续冲洗。卷内潜影保留。","Export latent NPZ files for development on desktop. Latents stay on the roll.")
                shell.action(this,"导出整卷潜影材料 · NPZ","Export all latent materials · NPZ") { if(!idle()) return@action;close();exportUi.files(LibraryExportRequest("latents",listOf(roll.rollId))) }
                shell.action(this,"按画面导出潜影…","Export a latent by frame…") { if(!idle()) return@action;close();exportLatentDialog(roll) }
            }
            shell.action(this,"管理存储","Manage storage") { if(!idle()) return@action;close();resourceDetail(roll.rollId) }
        }
    }
    private fun deleteRoll(roll: Roll) {
        confirm(copy("删除胶卷“${rollName(roll.rollId)}”？","Delete roll “${rollName(roll.rollId)}”?"),
            copy("胶卷将从列表移除，整卷拍摄原片和潜影一并删除。已有底片、照片和导出文件保留。此操作无法撤销。",
                "Remove the roll and all of its captures and latents. Negatives, photos and exports remain. This cannot be undone."),copy("删除胶卷","Delete roll")) {
            job(copy("正在删除胶卷…","Deleting roll…"),onSuccess={ clearRemovedRollNavigation();shell.select(MobileShell.Page.ROLLS) }) { _->cleanupMessage(resourceSession.removeRoll(roll.rollId)) }
        }
    }
    private fun clearRemovedRollNavigation() {
        if(current?.let { library.roll(it).catalogRemoved }==true) { current=null;stopCamera() }
        if(developmentRoll?.let { library.roll(it).catalogRemoved }==true) developmentRoll=null
        if(detailRollId?.let { library.roll(it).catalogRemoved }==true) { detailRollId=null;detailBatchId=null }
    }
    private fun resourceRolls() {
        val rolls=library.visibleRolls()
        if(rolls.isEmpty()) { status(copy("还没有胶卷","No rolls yet"));return }
        AlertDialog.Builder(activity).setTitle(copy("选择一卷","Choose a roll"))
            .setItems(rolls.map { rollName(it.rollId) }.toTypedArray()) { _,i->resourceDetail(rolls[i].rollId) }.show()
    }
    private fun resourceDetail(rollId: String) {
        job(copy("正在查看这卷的存储…","Checking this roll's storage…")) { control ->
            control.checkCancel();val info=resourceSession.inventory(rollId);val roll=library.roll(rollId)
            fun size(n: Long)=String.format(java.util.Locale.ROOT,"%.1f MiB",n/1048576.0)
            activity.runOnUiThread { if(!activity.isDestroyed) {
                val panel=LinearLayout(activity).apply { orientation=LinearLayout.VERTICAL;setPadding(shell.dp(20),shell.dp(8),shell.dp(20),shell.dp(12)) }
                val dialog=AlertDialog.Builder(activity).setTitle(rollName(rollId)).setView(ScrollView(activity).apply { addView(panel,android.view.ViewGroup.LayoutParams(-1,-2)) }).setNegativeButton(copy("关闭","Close"),null).create()
                shell.note(panel,"曝光 · ${size(info.captureBytes+info.latentBytes)}\n原片 ${info.captures} 份／潜影 ${info.latents} 份 · 仅支持整卷删除",
                    "Exposure · ${size(info.captureBytes+info.latentBytes)}\n${info.captures} capture bundles / ${info.latents} latents · whole-roll deletion only")
                shell.note(panel,"底片 · ${info.negatives} 张 · ${size(info.negativeBytes)}","Developed film · ${info.negatives} images · ${size(info.negativeBytes)}")
                shell.note(panel,"照片 · ${info.photos} 张 · ${size(info.photoBytes)}","Photos · ${info.photos} images · ${size(info.photoBytes)}")
                shell.note(panel,"底片和照片可单张或批量管理。三层独立，已导出文件不受影响。","Manage film and photos individually or in batches. The three layers and exported files are independent.")
                shell.action(panel,"查看本卷照片","View photos from this roll") { dialog.dismiss();openRollGallery(rollId,false) }
                shell.action(panel,"查看本卷底片","View developed film from this roll") { dialog.dismiss();openRollGallery(rollId,true) }
                if(roll.lifecycle==RollLifecycle.SEALED) shell.action(panel,"删除这卷曝光…","Delete this roll's exposures…") {
                    dialog.dismiss()
                    confirm(copy("删除这卷曝光？","Delete this roll's exposures?"),copy("删除整卷拍摄原片和潜影，之后无法重新冲洗。已有底片、照片和导出文件保留。","Delete all captures and latents on this roll; further development will be unavailable. Developed film, photos and exports are retained."),copy("删除这卷曝光","Delete exposures")) {
                        job(copy("正在删除这卷曝光…","Deleting this roll's exposures…")) { _->cleanupMessage(resourceSession.discardRoll(rollId)) }
                    }
                }
                shell.decorate(panel);dialog.show()
            } }
            copy("存储信息已更新","Storage information updated")
        }
    }
    private fun cleanupMessage(done: Boolean)=if(done) copy("删除完成，其他资源保留。","Deleted. Other resources retained.")
        else copy("已从列表移除，剩余文件待清理。其他资源保留。","Removed from the catalog; file cleanup is pending. Other resources retained.")
    private fun originDialog(rollId: String,index: Int,resultId: String,saved: SavedScan?=null) {
        sourceDialogs.show({ provenance.resolve(rollId,index,resultId,saved) },
            openRoll={ if(idle()) { rollDetail(library.roll(rollId));refresh() } },
            openBatch={ if(idle()) {
                detailRollId=rollId;detailBatchId=RollLibraryContents.batchId(resultId);batchPage=0
                shell.select(MobileShell.Page.ROLL_BATCH);renderBatch()
            } },
            openFilm={ if(idle()) library.developedResults().firstOrNull { it.resultId==resultId }?.let(::viewNegative) })
    }
    private fun removePhoto(saved: SavedScan) {
        confirm(copy("删除这张照片？","Delete this photo?"),copy("仅删除软件内的当前照片。来源底片、胶卷及已导出文件保留。","Delete this app photo only. Source film, the roll and exported files are retained."),copy("删除照片","Delete photo")) {
            job(copy("正在删除照片…","Removing photo…"),onSuccess={ showingSaved=null;photoSequence=emptyList();clearViewerImage();scanPage=0;shell.select(MobileShell.Page.OBSERVE) }) { _->cleanupMessage(resourceSession.removePhoto(saved)) }
        }
    }
    private fun selectAllRolls() { rollSelection.selectAll(library.visibleRolls()) }
    private fun negativeSelectionAll(batch: Boolean) {
        val items=if(batch) detailRollId?.let { id -> RollLibraryContents.batches(id,library.developedResults()).firstOrNull { it.id==detailBatchId }?.frames }.orEmpty()
            else library.developedResults().filter { galleryRoll==null || it.sourceRollId==galleryRoll }
        (if(batch) batchSelection else negativeSelection).selectAll(items.filter { galleryDisplay.accepts("negative:${it.resultId}") })
    }
    private fun selectAllPhotos() {
        val filter=galleryRoll
        catalogWork { control ->
            val items=scans.matching(filter,control::checkCancel).filter { galleryDisplay.accepts("photo:${it.id}") }
            activity.runOnUiThread { if(!activity.isDestroyed && !negativesSelected && filter==galleryRoll) photoSelection.selectAll(items) }
            copy("已选 ${items.size} 张","${items.size} selected")
        }
    }
    private fun removeSelectedPhotos(items: List<SavedScan>)=removeSelection(items.size,copy("照片","photos"),copy("只删除所选成片。胶卷、底片和已导出的文件保留。","Only selected photos are deleted. Rolls, negatives and exports remain.")) { control ->
        resourceSession.removeSelected(photos=items,control=control)
    }
    private fun removeSelectedNegatives(items: List<DevelopedResult>)=removeSelection(items.size,copy("底片","negatives"),copy("只删除所选冲洗底片。胶卷曝光和已有照片保留；删除后不能再扫描这些底片。","Only selected negatives are deleted. Roll exposures and photos remain; these negatives cannot be scanned again.")) { control ->
        val outcome=resourceSession.removeSelected(negatives=items,control=control)
        items.take(outcome.completed).forEach { previewCache.dropResult(it.resultId);opticalProxies.dropResult(it.resultId) }
        outcome
    }
    private fun removeSelectedRolls(items: List<Roll>)=removeSelection(items.size,copy("卷胶卷","rolls"),copy("所选胶卷将从列表移除，整卷拍摄原片和潜影一并删除。已有底片、照片和导出文件保留。","Remove the selected rolls and all of their captures and latents. Negatives, photos and exports remain.")) { control ->
        resourceSession.removeSelected(rolls=items.map { it.rollId },control=control,removeRollCatalog=true)
    }
    private fun catalogConfirm(title: String,message: String,positive: String,action: ()->Unit) {
        AlertDialog.Builder(activity).setTitle(title).setMessage(message).setNegativeButton(copy("取消","Cancel"),null)
            .setPositiveButton(positive) { _,_->if(canManage()) action() }.show()
    }
    private fun catalogWork(onSuccess: ()->Unit={},work: (WorkflowControl)->String) {
        if(!canManage()) return
        metadataBusy=true;refresh()
        worker.execute {
            val result=runCatching { work(WorkflowControl()) }
            activity.runOnUiThread {
                metadataBusy=false
                if(!activity.isDestroyed) { result.fold({ statusChanged(it);onSuccess() },{ statusChanged(it.message.orEmpty()) });refresh() }
            }
        }
    }
    private fun removeSelection(count: Int,kind: String,detail: String,remove: (WorkflowControl)->LibraryResourceSession.RemovalBatch) {
        if(!canManage() || count==0) return
        catalogConfirm(copy("删除 $count 项$kind？","Delete $count $kind?"),detail+copy("\n此操作无法撤销。","\nThis cannot be undone."),copy("删除所选","Delete selected")) {
            catalogWork(onSuccess={
                listOf(photoSelection,negativeSelection,batchSelection,rollSelection).forEach { it.clear() }
                clearRemovedRollNavigation()
                if(showingSaved?.let { !storage.existsPrivate(it.imagePath) }==true || (showingSaved==null && libraryResult?.let { !storage.existsPrivate(it.opticalPath) }==true)) {
                    showingSaved=null;libraryResult=null;clearViewerImage();shell.select(MobileShell.Page.OBSERVE)
                }
                thumbnails.clear();refreshLists();shell.decorate()
            }) { control ->
                val outcome=remove(control)
                copy("已删除 ${outcome.completed}/${outcome.total}","Deleted ${outcome.completed}/${outcome.total}")+
                    (if(outcome.cleanupPending) copy("；文件清理待重试","; file cleanup pending") else "")+
                    (outcome.error?.let { copy("。其余保留：",". Remaining items kept: ")+it.message } ?: "")
            }
        }
    }
    private fun removeNegative(result: DevelopedResult) {
        confirm(copy("删除这张底片？","Delete this negative?"),copy("删除后无法再观看或扫描这张底片。来源胶卷、已保存照片及导出文件保留。","This film can no longer be viewed or scanned. The source roll, saved photos and exports are retained."),copy("删除底片","Delete negative")) {
            job(copy("正在删除冲洗底片…","Removing developed film…"),onSuccess={
                previewCache.dropResult(result.resultId)
                opticalProxies.dropResult(result.resultId)
                thumbnails.remove("negative:${result.resultId}");thumbnails.remove("display:negative:${result.resultId}")
                libraryResult=null;negativeSequence=emptyList();clearViewerImage()
                if(scanResult?.resultId==result.resultId) { scanResult=null;scanPreview.invalidate();pendingScanPreview=false }
                resultPage=0;shell.select(MobileShell.Page.OBSERVE)
            }) { _->cleanupMessage(resourceSession.removeNegative(result)) }
        }
    }
    private fun exportOriginalDialog(roll: Roll) {
        val frames=roll.frames.filter { it.capturePath!=null }
        AlertDialog.Builder(activity).setTitle(copy("导出哪一格","Which frame"))
            .setMessage(copy("导出相机原片文件，卷内资源保留。","Export camera originals as files; keep captures on the roll."))
            .setItems(frames.map { frameLabel(it.frameIndex)+copy(" · ${it.captureExposures} 次曝光"," · ${it.captureExposures} exposures") }.toTypedArray()) { _,n ->
                val frame=frames[n]
                job(copy("正在读取原片记录…","Reading original records…")) { control ->
                    control.checkCancel();val sources=captureStore.read(frame.capturePath!!).sources
                    activity.runOnUiThread { if(!activity.isDestroyed) AlertDialog.Builder(activity).setTitle(copy("这一格里的哪一张","Which file in this frame"))
                        .setMessage(copy("JPEG 为相机处理图像，DNG 为 RAW 原始文件。导出后原片保留。","JPEG is camera-processed; DNG is RAW. Originals remain after export."))
                        .setItems(sources.mapIndexed { i,source -> "${i+1} · ${if(source.path.endsWith(".dng")) "RAW / DNG" else "JPEG"}" }.toTypedArray()) { _,index ->
                            if(available() && !running && !captureInProgress()) {
                                val source=sources[index]
                                preferences.edit().putString("export_medium_path",frame.capturePath!!.substringBeforeLast('/')+"/"+source.path).apply()
                                exportDocument.launch("original-${roll.rollId.take(8)}-${frame.frameIndex}-${index+1}.${if(source.path.endsWith(".dng")) "dng" else "jpg"}")
                            }
                        }.setNegativeButton(copy("取消","Cancel"),null).show() }
                    copy("已读取原片记录","Original records ready")
                }
            }.setNegativeButton(copy("取消","Cancel"),null).show()
    }
    private fun renameDialog(roll: Roll) {
        val panel=LinearLayout(activity).apply { orientation=LinearLayout.VERTICAL;setPadding(shell.dp(16),0,shell.dp(16),0) }
        val name=EditText(activity).apply { setText(roll.label);setSingleLine(true) };shell.field(panel,name,copy("胶卷名称（最多64字）","Roll name (up to 64 characters)"))
        val time=CheckBox(activity).apply { text=copy("改用当前时间编号","Use current time as roll number") };panel.addView(time)
        time.setOnCheckedChangeListener { _,checked -> name.isEnabled=!checked }
        val dialog=AlertDialog.Builder(activity).setTitle(copy("胶卷名称","Roll name")).setView(panel)
            .setNegativeButton(copy("取消","Cancel"),null).setPositiveButton(copy("保存","Save"),null).create()
        dialog.setOnShowListener { dialog.getButton(AlertDialog.BUTTON_POSITIVE).setOnClickListener {
            val label=runCatching { if(time.isChecked) RollNaming.atTime(java.time.LocalDateTime.now(),(library.shootingRolls()+library.sealedRolls()).filter { it.rollId != roll.rollId }.map { it.label }) else RollNaming.validate(name.text.toString()) }
                .getOrElse { name.error=it.message;return@setOnClickListener }
            dialog.dismiss();job(copy("正在保存卷名…","Saving roll name…")) { _ -> library.renameRoll(roll.rollId,label);copy("卷名已保存","Roll name saved") }
        } };dialog.show()
    }
    private fun exportMedium(path: String,name: String) {
        if(running || captureInProgress() || !available()) return
        preferences.edit().putString("export_medium_path",path).apply();exportDocument.launch(name)
    }
    private fun exportLatentDialog(roll: Roll) {
        val frames=roll.frames.filter { it.state in listOf(FrameState.CAPTURED,FrameState.LATENT) }
        AlertDialog.Builder(activity).setTitle(copy("导出哪一格的潜影","Which latent"))
            .setMessage(copy("导出潜影 NPZ，供电脑版继续冲洗。卷内潜影保留。","Export latent NPZ for development on desktop. Latents stay on the roll."))
            .setItems(frames.map { frameLabel(it.frameIndex)+copy(" · ${it.captureExposures} 次曝光"," · ${it.captureExposures} exposures") }.toTypedArray()) { _,which ->
                var path: String?=null;val frame=frames[which]
                job(copy("正在准备潜影文件…","Preparing latent file…"),onSuccess={ path?.let { exportMedium(it,"FilmFoundry-${roll.rollId.take(8)}-${frame.frameIndex}${DataKernel.LATENT_SUFFIX}") } }) { control ->
                    val grain=if(roll.captureSettings[PhoneGrainModels.ROLL_SETTING]==PhoneGrainModels.LAYERED) PhoneGrainModels.LAYERED else PhoneGrainModels.CURRENT
                    path=deferred.prepareFrame(roll.rollId,frame.frameIndex,control,grain).latentPath
                    copy("潜影已准备，原曝光保留","Latent ready; original exposures retained")
                }
            }.show()
    }
    private suspend fun renderPreview(result: DevelopedResult,frozen: Map<String,Any?>,mode: String,control: WorkflowControl,scanRequest: ScanPreviewState.Request?=null,viewerToken: Int?=null,pageToken: ViewerPageIdentity.Token?=null): String {
                    control.checkCancel()
                    val payload = frozen.toMutableMap()
                    if(!payload.containsKey("scanner")) payload["scanner"] = library.roll(result.sourceRollId).scannerRecipe
                    val scanner=mode == "scanner"
                    if(!scanner) payload["scanner"]=section(payload,"scanner")+mapOf("transmission_light_ev" to viewingEv,"transmission_light_temperature_k" to viewingKelvin)
                    val generation=displayCacheGeneration.get()
                    val cached=if(scanner) null else runCatching { negativeViews.find(result.resultId,result.opticalPath,mode,payload,control::checkCancel) }.getOrNull()
                    val cachedBitmap=cached?.let { storedThumbnail(it.path) }
                    var observation: OpticalPreviewSession.Observation?=null
                    var histogram: DisplayHistogram?=null
                    val bitmap=if(cachedBitmap!=null) {
                        synchronized(negativeSizes) { negativeSizes[result.resultId]=cached!!.sourceWidth to cached.sourceHeight }
                        cachedBitmap
                    } else {
                    val preview = previewCache.get(result.resultId+":"+result.opticalPath) {
                        if(scanRequest!=null) activity.runOnUiThread {
                            if(!activity.isDestroyed && shell.selected==MobileShell.Page.SCAN && scanPreview.current(scanRequest))
                                scanCaption.text=resultLabel(result)+copy(" · 正在读取底片…"," · Reading film…")
                        }
                        opticalProxies.get(result.resultId,result.opticalPath,960,control::checkCancel)
                    }
                    synchronized(negativeSizes) { negativeSizes[result.resultId]=preview.sourceWidth to preview.sourceHeight }
                    control.checkCancel()
                    if(scanRequest!=null) activity.runOnUiThread {
                        if(!activity.isDestroyed && shell.selected==MobileShell.Page.SCAN && scanPreview.current(scanRequest) && scanResult?.resultId==result.resultId) {
                            stages.bindScanSource(result.resultId,result.opticalPath,preview.sourceWidth,preview.sourceHeight)
                            scanCaption.text=resultLabel(result)+copy(" · 正在更新预览…"," · Updating preview…")
                        }
                    }
                    observation = displayObservation.observe(preview,payload,!scanner,control,mode)
                    control.checkCancel()
                    val histogramBuilder=if(scanner) DisplayHistogram.Builder(observation.width,observation.height) else null
                    val rendered=observationBitmap(observation,control,histogramBuilder)
                    histogram=histogramBuilder?.finish()
                    if(!scanner) runCatching { negativeViews.save(result.resultId,result.opticalPath,mode,payload,preview.sourceWidth,preview.sourceHeight,{
                        control.checkCancel();check(generation==displayCacheGeneration.get()) { "Display cache cleared" }
                    }) { output -> check(rendered.compress(Bitmap.CompressFormat.PNG,100,output)) } }
                    rendered
                    }
                    activity.runOnUiThread { if(!activity.isDestroyed) {
                        if(control.cancelRequested()) { bitmap.recycle();return@runOnUiThread }
                        if(scanRequest != null && (!scanPreview.current(scanRequest) || shell.selected != MobileShell.Page.SCAN)) { bitmap.recycle();return@runOnUiThread }
                        if(scanner) {
                            if(scanRequest==null || !scanPreview.complete(scanRequest)) { bitmap.recycle();return@runOnUiThread }
                            scanFeedback.finish()
                            stages.reflectInterpretation(checkNotNull(observation).metadata)
                            scanResult=result;scanCaption.text=resultLabel(result)
                            savePhoto.isEnabled=!running && available();scanBatchWithSettings.isEnabled=savePhoto.isEnabled
                        }
                        else {
                            negativeViewerRecipe=payload
                            viewerDisplayMode=mode;viewerSceneLight=payload["viewer_illuminant"]=="scene_camera"
                            if(viewerToken != null && (viewerToken != viewerRevision || shell.selected != MobileShell.Page.VIEWER)) { bitmap.recycle();return@runOnUiThread }
                            showingSaved=null;libraryResult=result;viewerCaption.text=negativeCaption(result);updateViewerInformation(result,null)
                            viewerHint.text=viewingHint(mode,payload["viewer_illuminant"])
                            viewerTitle.text=if(mode=="viewer_projection") frameLabel(result.sourceFrameIndex)+copy(" · 正像投射"," · Positive projection") else if(payload["viewer_illuminant"]=="scene_camera") frameLabel(result.sourceFrameIndex)+copy(" · 实景透射"," · Live scene") else frameLabel(result.sourceFrameIndex)+copy(" · 灯台"," · Light table")
                            viewerPrimary.text=copy("扫描","Scan")
                            if(negativeSequence.none { it.resultId==result.resultId }) negativeSequence=library.developedResults().asReversed()
                        }
                        val cacheKey=if(!scanner && defaultViewer(frozen,mode) && payload["viewer_illuminant"]!="scene_camera") "display:negative:${result.resultId}" else null
                        if(!replaceImage(if(scanner) scanImage else image,bitmap,"negative:${result.resultId}",pageToken,cacheKey)) return@runOnUiThread
                        if(scanner) { stages.histogramReady();scanComparison.acceptHistogram(checkNotNull(histogram)) }
                        shell.select(if(scanner) MobileShell.Page.SCAN else MobileShell.Page.VIEWER);updateViewerNavigation()
                    } else bitmap.recycle() }
                    return copy("只读预览完成：${bitmap.width}×${bitmap.height}","Read-only preview: ${bitmap.width}×${bitmap.height}")
    }
    private fun defaultViewer(recipe: Map<String,Any?>,mode: String)=mode=="viewer_light_table" && viewingEv==0.0 && viewingKelvin==5500.0 &&
        section(recipe,"scanner")["spectral_observation_profile"]==null && recipe["spectral_observation_profile"]==null &&
        recipe["viewer_illuminant"] in listOf(null,"synthetic_light_table")
    private fun materialLabel(roll: Roll): String {
        val id=roll.captureSettings["material_preset_id"] as? String
        val stock=builtin.materials.firstOrNull { item -> "builtin:"+item.id==id || id==null && (item.recipe["film"] as? Map<*,*>)?.get("name")==roll.filmRecipe["name"] }
        val name=stock?.let { copy(it.labelZh,it.labelEn) } ?: roll.filmRecipe["name"]?.toString().orEmpty()
        return MaterialSelection.displayName(roll.filmRecipe,name,copy("zh","en")=="en")
    }
    private fun resultLabel(result: DevelopedResult)=rollName(result.sourceRollId)+" · "+frameLabel(result.sourceFrameIndex)+" · "+batchLabel(RollLibraryContents.batchId(result))
    private fun editSavedPhoto(result: DevelopedResult,saved: SavedScan) {
        runCatching {
            val recipe=saved.editingRecipe()
            val model=saved.observationModel ?: ObservationImplementation.LEGACY
            if(ObservationImplementation.forRecipe(recipe)==model) recipe else ObservationImplementation.frozenRuntime(recipe,model)
        }
            .onSuccess { openScanner(result,it,saved.id) }
            .onFailure { status(copy("无法读取这张照片的扫描配置：","Cannot read this photo's scan settings: ")+it.message) }
    }
    private fun openScanner(result: DevelopedResult)=openScanner(result,null,null)
    private fun openScanner(result: DevelopedResult,savedRecipe: Map<String,Any?>?,savedId: String?,resumeSession: Boolean=false) {
        stopScene()
        scanWork.cancel();scanPreview.invalidate()
        val availableIds=library.developedResults().map { it.resultId }.toSet()
        scanBatchFrames=scanBatchFrames.filter { it.resultId in availableIds }
        if(scanBatchFrames.none { it.resultId==result.resultId }) scanBatchFrames=emptyList()
        scanBatchWithSettings.visibility=if(scanBatchFrames.isEmpty()) android.view.View.GONE else android.view.View.VISIBLE
        scanBatchWithSettings.text=copy("扫描本批 (${scanBatchFrames.size})","Scan batch (${scanBatchFrames.size})")
        scanBatchWithSettings.contentDescription=copy("按当前参数扫描本批 ${scanBatchFrames.size} 张","Scan ${scanBatchFrames.size} frames with the current settings")
        if(scanResult?.resultId != result.resultId) {
            val old=(scanImage.drawable as? android.graphics.drawable.BitmapDrawable)?.bitmap;scanImage.setImageDrawable(null);old?.recycle()
        }
        if(!resumeSession || scanResult?.resultId!=result.resultId) { scanComparison.clear();shell.scanResultBar.visibility=android.view.View.GONE }
        scanComparison.bind(result.resultId+":"+result.opticalPath)
        stages.beginScanSession(result.resultId,result.opticalPath,library.roll(result.sourceRollId).scannerRecipe,savedRecipe,savedId,resumeSession,negativeRotation(result.resultId))
        pendingScanPreview=false
        scanResult=result;scanCaption.text=resultLabel(result);scanCaption.contentDescription=null;shell.select(MobileShell.Page.SCAN)
        shell.scanTitle.text=frameLabel(result.sourceFrameIndex);shell.scanTitle.contentDescription=resultLabel(result)
        savePhoto.isEnabled=false;scanBatchWithSettings.isEnabled=false
        if(running || captureInProgress() || !available()) { scanFeedback.finish();pendingScanPreview=true;return }
        val frozen=runCatching { stages.currentScanObservation(library.roll(result.sourceRollId).scannerRecipe,references.stats(result.sourceRollId)) }.getOrElse {
            scanFeedback.finish();scanCaption.text=resultLabel(result)+copy(" · 请检查扫描参数"," · Check scan settings")
            scanCaption.contentDescription=it.message;status(it.message.orEmpty());return
        }
        stages.save()
        val exposure=(section(frozen,"look")["print_exposure_ev"] as? Number)?.toDouble()
        val saturation=(section(frozen,"scanner")["scan_saturation"] as? Number)?.toDouble()
        stages.reflectRecommendation(exposure,saturation)
        val request=scanPreview.begin(result.resultId,result.opticalPath,frozen)
        scanFeedback.begin()
        scanCaption.append(copy(" · 刷新中…"," · Updating…"))
        scanWork.submit({ stale -> runBlocking { renderPreview(result,request.recipe,"scanner",WorkflowControl(cancelRequested=stale),request) } },{ error ->
            if(!activity.isDestroyed && shell.selected==MobileShell.Page.SCAN && scanPreview.current(request)) {
                scanFeedback.finish()
                scanCaption.text=resultLabel(result)+copy(" · 预览失败，点刷新重试"," · Preview failed; tap Refresh to retry")
                scanCaption.contentDescription=error.message
            }
        })
    }
    private fun replaceImage(view: ImageView,bitmap: Bitmap,pageKey: String?=null,pageToken: ViewerPageIdentity.Token?=null,displayCacheKey: String?=null): Boolean {
        if(view === image) (view.parent as? android.view.View)?.let { it.visibility=android.view.View.VISIBLE }
        val old=(view.drawable as? android.graphics.drawable.BitmapDrawable)?.bitmap
        if(view===image) {
            val token=pageToken ?: image.requestPage(requireNotNull(pageKey))
            if(!image.presentPage(token,bitmap)) { bitmap.recycle();return false }
            displayCacheKey?.let { thumbnails.put(it,bitmap) }
        } else { view.setImageBitmap(bitmap);if(view===scanImage) { val size=OutputOrientation.size(bitmap.width,bitmap.height,stages.rotationDegrees);shell.scanImageSize(size.first,size.second);orientScanImage();scanComparison.offer(bitmap,stages.rotationDegrees) };if(old !== bitmap) old?.recycle() }
        return true
    }
    private fun saveScan(result: DevelopedResult) {
        val frozen=scanPreview.recipeFor(result.resultId,result.opticalPath) ?: run { status(copy("先刷新照片预览，再保存","Refresh the photo preview before saving"));return }
        val album=scanExportAfterSave.isChecked
        val start={
        var completed: SavedScan?=null;var reused=false
        job(copy("正在全分辨率扫描并保存…","Scanning and saving at full resolution…"),allowBrowsing=true,onSuccess={
            completed?.let { saved ->
                if(shell.selected==MobileShell.Page.SCAN && scanResult?.resultId==result.resultId) {
                    shell.scanResultBar.removeAllViews();shell.scanResultBar.visibility=android.view.View.VISIBLE
                    shell.action(shell.scanResultBar,if(reused) "已有版本 · 查看" else "已存图库 · 查看",if(reused) "Existing version · View" else "Saved · View photo") {
                        if(idle()) openSavedPhoto(saved,listOf(saved))
                    }.layoutParams=LinearLayout.LayoutParams(0,-2,1f)
                    shell.action(shell.scanResultBar,"继续调整","Keep editing") { shell.scanResultBar.visibility=android.view.View.GONE }.layoutParams=LinearLayout.LayoutParams(0,-2,1f)
                    val next=scanBatchFrames.indexOfFirst { it.resultId==result.resultId }.takeIf { it>=0 }?.let { scanBatchFrames.getOrNull(it+1) }
                    if(next!=null) shell.action(shell.scanResultBar,"下一张","Next") { if(idle()) openScanner(next) }.layoutParams=LinearLayout.LayoutParams(0,-2,1f)
                    resumeScanPreview()
                }
            }
        }) { control ->
            val existing=scans.findNewest(result.resultId,RecipeShareCodec.encode(frozen),control::checkCancel,minimumBits=ScanDelivery.bits(frozen))
            val saved=existing ?: scanAndSave(result,frozen,control)
            if(existing!=null) activePerformance?.frame("scan_reused",result.sourceFrameIndex,0)
            val albumNote=if(album) "\n"+exportUi.summary(libraryExports.gallery(listOf(libraryExports.photo(saved)),control)) else ""
            completed=saved;reused=existing!=null
            (if(existing!=null) copy("已复用图库中相同参数的照片","Reused the photo with the same settings") else copy("照片已保存到软件图库","Photo saved to the app Library"))+albumNote
        }
        }
        if(album) exportUi.withGalleryPermission(start) else start()
    }
    private suspend fun scanAndSave(result: DevelopedResult,recipe: Map<String,Any?>,control: WorkflowControl,rowIo: RowMediumIo?=null,handoff: RowMediumHandoff?=null): SavedScan {
        control.checkCancel()
        activePerformance?.frame("scan_start",result.sourceFrameIndex,0)
        return scans.saveStreamed(result,recipe,control) { output ->
            store.scanPng(result.opticalPath,recipe,output,control,observationHeapHeadroom(),if(jobGpuEnabled) pixelCompute.open() else null,
                bitDepth=ScanDelivery.bits(recipe),rowIo=rowIo,handoff=handoff)
        }.also { activePerformance?.photo(it.format.extension,it.width,it.height,it.precisionBits,if(it.format==ScanImageFormat.JPEG) ScanDelivery.jpegQuality(recipe) else 0) }
    }
    private fun scanBatchDialog(roll: Roll,results: List<DevelopedResult>,previewRecipe: Map<String,Any?>?=null) {
        if(scanTasks.latest(roll.rollId)?.unfinished==true) { unfinishedScanDialog(roll);return }
        val groups=results.groupBy { RollLibraryContents.batchId(it) }.values.toList()
        fun start(which: Int) {
                if(!available() || running || captureInProgress()) return
                val frozen=previewRecipe ?: runCatching { scanRecipe(roll.rollId) }.getOrElse { status(it.message.orEmpty());return }
                val frames=groups[which].sortedBy { it.sourceFrameIndex }
                exportUi.confirmExecution(copy("确认扫描本批","Confirm batch scan"),rollName(roll.rollId)+"\n"+scanExecutionInfo(frames.size,frozen),outputRecipe=frozen) { album ->
                job(copy("正在逐格扫描…","Scanning frames…"),allowBrowsing=true) { control ->
                    val task=batchScans.create(roll.rollId,frames,frozen)
                    preferences.edit().putBoolean("album_task_${task.id}",album).apply()
                    runScanTask(task,control)
                }
                }
        }
        if(groups.size == 1) start(0)
        else AlertDialog.Builder(activity).setTitle(copy("选择冲洗批次","Choose development batch"))
            .setItems(groups.map { frames -> batchLabel(RollLibraryContents.batchId(frames.first()))+copy(" · ${frames.size} 张底片"," · ${frames.size} frames") }.toTypedArray()) { _,which -> start(which) }.show()
    }
    private fun unfinishedScanDialog(roll: Roll) {
        val task=scanTasks.latest(roll.rollId)?.takeIf { it.unfinished } ?: return
        AlertDialog.Builder(activity).setTitle(copy("未完成照片任务 ${task.completed}/${task.frames.size}","Unfinished photos ${task.completed}/${task.frames.size}"))
            .setMessage(copy("继续会沿用原扫描配方，跳过已完成照片。若来源或已完成照片被删除，会提示缺失。结束任务保留已经做好的照片。","Resume uses the original scanning recipe and skips finished photos. Removed sources or finished photos are reported as missing. Ending retains saved photos.")+(task.error?.let { "\n\n$it" } ?: ""))
            .setNegativeButton(copy("取消","Cancel"),null)
            .setPositiveButton(copy("继续原任务","Resume original task")) { _,_->if(idle()) exportUi.confirmExecution(copy("确认继续扫描","Resume scans"),scanExecutionInfo(task.frames.size,RecipeShareCodec.decode(task.recipeShare)),true,
                preferences.getBoolean("album_task_${task.id}",false),outputRecipe=RecipeShareCodec.decode(task.recipeShare)) { album ->
                preferences.edit().putBoolean("album_task_${task.id}",album).apply()
                job(copy("正在继续逐格扫描…","Resuming scans…"),allowBrowsing=true) { control -> runScanTask(task,control) }
            } }
            .setNeutralButton(copy("结束任务…","End task…")) { _,_->confirm(copy("结束这次照片任务？","End this photo task?"),copy("已完成照片保留，之后可创建新任务。","Finished photos stay. You can then start a new task."),copy("结束任务","End task")) {
                job(copy("正在结束照片任务…","Ending photo task…")) { _ -> batchScans.abandon(task.rollId,task.id);copy("任务已结束，已有照片保留","Task ended; saved photos retained") }
            } }.show()
    }
    private suspend fun runScanTask(task: ScanTask,control: WorkflowControl): String {
        val outcome=batchScans.run(task.rollId,task.id,control,{ progress ->
            activePerformance?.frame("scan",progress.completed,progress.frames.size)
            taskStatus(copy("已扫描 ${progress.completed}/${progress.frames.size}","Scanned ${progress.completed}/${progress.frames.size}"))
        }) { frame,settings,cancel,output ->
            activePerformance?.frame("scan_start",frame.sourceFrameIndex,task.frames.size)
            store.scanPng(frame.opticalPath,settings,output,cancel,observationHeapHeadroom(),if(jobGpuEnabled) pixelCompute.open() else null,bitDepth=ScanDelivery.bits(settings)).also { size ->
                val format=ScanDelivery.format(settings)
                activePerformance?.output(format.name,size.first,size.second,ScanDelivery.bits(settings),if(format==ScanImageFormat.JPEG) ScanDelivery.jpegQuality(settings) else 0)
            }
        }
        val albumNote=if(outcome.status=="completed" && preferences.getBoolean("album_task_${task.id}",false)) {
            val saved=outcome.frames.map { scans.requireSaved(it.scanId,it.resultId,task.recipeShare,task.id) }
            "\n"+exportUi.summary(libraryExports.gallery(saved.map(libraryExports::photo),control))
        } else ""
        return if(outcome.status=="paused") copy("已暂停 ${outcome.completed}/${outcome.frames.size}，从胶卷库继续原任务","Paused ${outcome.completed}/${outcome.frames.size}; resume from the roll library")
        else copy("已完成 ${outcome.completed} 张照片，底片保留","Finished ${outcome.completed} photos; negatives retained")+albumNote
    }
    private fun showSavedScan(saved: SavedScan,control: WorkflowControl,viewerToken: Int?=null,pageToken: ViewerPageIdentity.Token?=null) {
        control.checkCancel()
        // Keep the existing identity check; absent sources are allowed, conflicting sources are not.
        resourceSession.lineage(saved.rollId,saved.frameIndex,saved.resultId)
        val bitmap=decodeSavedPhoto(saved)
        val sequence=orderedPhotos(scans.matching(galleryRoll?.takeIf { it==saved.rollId },control::checkCancel),galleryDisplay.query())
        activity.runOnUiThread { if(!activity.isDestroyed) {
            if(viewerToken != null && (viewerToken != viewerRevision || shell.selected != MobileShell.Page.VIEWER)) { bitmap.recycle();return@runOnUiThread }
            showingSaved=saved
            viewerSceneLight=false
            image.orientationDegrees=saved.viewRotationDegrees
            libraryResult=library.developedResults().firstOrNull { it.resultId == saved.resultId }
            viewerTitle.text=frameLabel(saved.frameIndex)+" · "+scanLabel(saved.id);viewerPrimary.text=copy("导出","Export")
            viewerCaption.text=photoCaption(saved)+copy(" · 预览 "," · Preview ")+"${bitmap.width}×${bitmap.height}";updateViewerInformation(libraryResult,saved)
            photoSequence=sequence
            if(!replaceImage(image,bitmap,"photo:${saved.id}",pageToken,"display:photo:${saved.id}")) return@runOnUiThread
            if(viewerToken==null) shell.select(MobileShell.Page.OBSERVE) // A completed save returns through Library, not back through an expired editor.
            shell.select(MobileShell.Page.VIEWER);updateViewerNavigation()
        } else bitmap.recycle() }
    }
    private fun decodeSavedPhoto(saved: SavedScan,edge: Int?=null): Bitmap {
        val sample=if(edge!=null) PhotoDisplayPolicy.sampleForEdge(saved.width,saved.height,edge)
            else PhotoDisplayPolicy.sample(saved.width,saved.height,AndroidProcessingMemory.snapshot(activity).budgetBytes)
        return decodeDisplayPhoto(saved,sample)
    }
    private fun decodeDisplayPhoto(saved: SavedScan,sample: Int): Bitmap {
        if(android.os.Build.VERSION.SDK_INT>=28) {
            val root=java.io.File(storage.privateDir()).canonicalFile
            val file=java.io.File(root,saved.displayPath).canonicalFile
            require(file.path.startsWith(root.path+java.io.File.separator))
            return android.graphics.ImageDecoder.decodeBitmap(android.graphics.ImageDecoder.createSource(file)) { decoder,_,_->
                decoder.allocator=android.graphics.ImageDecoder.ALLOCATOR_SOFTWARE
                decoder.setTargetColorSpace(android.graphics.ColorSpace.get(android.graphics.ColorSpace.Named.SRGB))
                decoder.setTargetSize(maxOf(1,saved.width/sample),maxOf(1,saved.height/sample))
            }.apply { setHasMipMap(true) }
        }
        return storage.readPrivate(saved.displayPath) { android.graphics.BitmapFactory.decodeStream(it,null,android.graphics.BitmapFactory.Options().apply { inSampleSize=sample;inPreferredConfig=Bitmap.Config.ARGB_8888 }) }?.apply { setHasMipMap(true) } ?: error("Cannot decode saved photo")
    }
    private fun stopScene() { sceneEpoch++;closeScene?.invoke();closeScene=null;image.liveDisplay=false }
    private fun openCameraView(result: DevelopedResult) {
        stopScene();val epoch=sceneEpoch;val revision=++viewerRevision
        image.orientationDegrees=0
        viewerDisplayMode="viewer_light_table";viewerSceneLight=true;libraryResult=result;showingSaved=null
        val page=beginViewerPage("negative:${result.resultId}",preserveCurrent=true)
        viewerCaption.text=negativeCaption(result)+copy(" · 打开实景…"," · Opening scene…")
        viewerHint.text=viewingHint("viewer_light_table","scene_camera");updateViewerNavigation()
        fun stale()=activity.isDestroyed || epoch!=sceneEpoch || revision!=viewerRevision
        viewerWork.submit({ cancelled ->
            val control=WorkflowControl(cancelRequested={ cancelled() || stale() })
            val preview=previewCache.get(result.resultId+":"+result.opticalPath) { opticalProxies.get(result.resultId,result.opticalPath,960,control::checkCancel) }
            val scene=LiveSceneSession(preview,control);control.checkCancel()
            activity.runOnUiThread {
                if(stale() || shell.selected!=MobileShell.Page.VIEWER) return@runOnUiThread
                val presenting=AtomicBoolean(false)
                val scratch=ViewerRowScratch();var packed: IntArray?=null
                image.beginLive(scene.filmAspect)
                val caption=negativeCaption(result);var first=true
                closeScene=streamScene({ frame ->
                    if(!stale() && presenting.compareAndSet(false,true)) {
                        try {
                            val frameControl=WorkflowControl(cancelRequested=::stale)
                            val rows=ViewerFrameRows(frame,frameControl::checkCancel,scratch)
                            val pose=image.livePose
                            val pixels=packed?.takeIf { it.size==rows.width*rows.height } ?: IntArray(rows.width*rows.height).also { packed=it }
                            scene.renderArgb(rows.rows(),pixels,pose.zoom,pose.x,pose.y)
                            frameControl.checkCancel()
                            activity.runOnUiThread {
                                try {
                                    if(!stale() && shell.selected==MobileShell.Page.VIEWER) {
                                        image.presentLivePixels(page,rows.width,rows.height,pixels)
                                        if(first) { first=false;viewerCaption.text=caption;viewerTitle.text=frameLabel(result.sourceFrameIndex)+copy(" · 实景透射"," · Live scene") }
                                    }
                                } finally { presenting.set(false) }
                            }
                        } catch(error: Throwable) { presenting.set(false);throw error }
                    }
                },{ error ->
                    if(!stale()) {
                        stopScene();viewerCaption.text=copy("实景未能启动，可点重启实景","Scene unavailable; tap Restart camera")
                        viewerCaption.contentDescription=error.message;Toast.makeText(activity,error.message,Toast.LENGTH_LONG).show()
                    }
                },{ !stale() && !presenting.get() })
            }
        },{ error -> if(!stale()) { viewerCaption.text=copy("底片读取失败，可重试","Film read failed; retry");viewerCaption.contentDescription=error.message } })
    }
    fun refresh() {
        clearRemovedRollNavigation()
        listOf(rollSelection,negativeSelection,photoSelection,batchSelection).filter { it.state.active }.forEach { it.refresh() }
        if(exposureOwner!=null && exposureOwner!==camera()) { exposureEpoch++;exposureOwner=null;exposureBusy=false;exposureTarget=null;shell.closeQuickControls() }
        shell.refreshQuickControls()
        val roll = current?.let(library::roll)
        if(::stages.isInitialized) {
            val processing=(developmentRoll ?: current)?.let(library::roll)
            stages.bindDevelopment(processing?.developRecipe.orEmpty(),processing?.filmRecipe.orEmpty(),processing?.let(::materialLabel),processing?.rollId,
                if(processing==null) defaultFormationProfile() else processing.captureSettings[PhoneFormationProfile.ROLL_SETTING])
        }
        val pace=roll?.captureSettings?.get("pace")?.toString() ?: "still"
        val actualCapture=(camera() as? StoredStillCamera)?.plannedDimensions()
        val precisionW=actualCapture?.first ?: (roll?.captureSettings?.get("precision_width") as? Number)?.toInt() ?: 0
        val precisionH=actualCapture?.second ?: (roll?.captureSettings?.get("precision_height") as? Number)?.toInt() ?: 0
        frameCount.text = if(roll == null) copy("未装卷","No roll") else "${roll.exposedCount()}/${roll.capacity}"
        val rewind=when(roll?.captureSettings?.get("rewind")?.toString()) { "crank" -> copy("摇把倒片","Crank rewind"); "auto" -> copy("自动倒片","Automatic rewind"); else -> "" }
        counter.text = roll?.label?.ifBlank { roll.rollId.take(8) } ?: ""
        if(rewind.isNotEmpty()) counter.append(if(counter.text.isEmpty()) rewind else " · $rewind")
        if(roll != null && precisionW > 0 && precisionH > 0) {
            val gate=StillFrameFormat.create(roll.captureSettings["format"]?.toString() ?: "legacy",false,precisionW,precisionH,
                roll.captureSettings["gate_revision"]?.toString() ?: StillFrameFormat.LEGACY_MODEL)
            counter.append(copy(" · 原片 "," · Source ")+"${precisionW}×${precisionH}"+copy(" → 片门 "," → gate ")+"${gate.width}×${gate.height}")
        }
        if(roll != null) counter.append(if(pace == "motion") copy(" · 过片"," · motion") else copy(" · 静照"," · still"))
        if(pace != "motion") (camera() as? CameraXCameraAdapter)?.nativeRequests()?.let { native ->
            val balance=listOf("","",copy("白炽","Incandescent"),copy("荧光","Fluorescent"),copy("暖荧光","Warm fluorescent"),copy("日光","Daylight"),copy("阴天","Cloudy"),copy("暮光","Twilight"),copy("阴影","Shade")).getOrNull(native.whiteBalance)
            if(native.format=="raw") counter.append(" · RAW")
            else if(native.format=="ultra_hdr") counter.append(" · HDR")
            if(native.whiteBalance!=1 && balance!=null) counter.append(copy(" · 白平衡 "," · WB ")+balance)
            if(native.lockAe) counter.append(copy(" · AE锁"," · AE lock"))
            if(native.lockAwb) counter.append(copy(" · AWB锁"," · AWB lock"))
            if(native.focusDiopters!=null) counter.append(" · MF")
        }
        val motionCeiling=(roll?.captureSettings?.get("motion_fps_max") as? Number)?.toInt() ?: 0
        if(pace == "motion" && motionCeiling in 1 until ShootingBudget.MOTION_TARGET_FPS) counter.append(" · ${motionCeiling} fps")
        val open=roll?.frames?.firstOrNull { it.state == FrameState.CAPTURING }
        if(open != null) counter.append(" · ${open.captureExposures}×")
        val queued=stillQueue.waiting()
        if(queued > 0) counter.append(copy(" · 排队 $queued"," · queued $queued"))
        if(roll != null && roll.exposedCount() == roll.capacity && open == null) counter.append(copy(" · 已满卷，请封卷", " · Roll full; seal to develop"))
        seal.text = if(roll != null && roll.exposedCount() in 1 until roll.capacity)
            copy("提前封卷（剩余 ${roll.capacity-roll.exposedCount()} 格不再拍摄）", "Seal early (${roll.capacity-roll.exposedCount()} unused frames)")
            else copy("封卷并退出拍摄", "Seal and leave camera")
        val enabled = !running && !captureInProgress() && available()
        controls.forEach { it.isEnabled = enabled;if(it is LinearLayout) it.alpha=if(enabled) 1f else .45f }
        photoTab.isEnabled=canBrowse();negativeTab.isEnabled=canBrowse()
        recipe.isEnabled = enabled
        autoAdvance.isEnabled=enabled
        multiple.isEnabled=enabled && pace != "motion" && roll != null && (open == null || !open.shutterArmed)
        multiple.text=if(holdFirst) copy("多曝 ✓","Multi ✓") else copy("多曝","Multi")
        multiple.isActivated=holdFirst
        multiple.contentDescription=if(open != null) copy("当前格多曝上弦","Cock this frame for multiple exposure") else copy("准备多重曝光","Prepare multiple exposure")
        panorama.isEnabled=enabled && roll?.captureSettings?.get("format") == "135"
        if(roll?.captureSettings?.get("format") != "135" && panorama.isChecked) panorama.isChecked=false
        baffles(panorama.isChecked)
        frameFormat(roll?.captureSettings?.get("format")?.toString() ?: "135",roll?.captureSettings?.get("gate_revision")?.toString() ?: StillFrameFormat.LEGACY_MODEL)
        val applied=(camera() as? CameraXCameraAdapter)?.nativeRequests()
        readout.value("iso",if(applied==null) "--" else applied.iso?.toString() ?: "AUTO",copy("已应用的相机 ISO 设定","Applied camera ISO request"),applied!=null)
        paintExposureReadout()
        readout.value("frame",if(roll==null) "--/--" else "${roll.exposedCount().toString().padStart(2,'0')}/${roll.capacity}",copy("本卷已曝光 ${roll?.exposedCount() ?: 0} 格，共 ${roll?.capacity ?: 0} 格","${roll?.exposedCount() ?: 0} frames exposed of ${roll?.capacity ?: 0}"),roll!=null)
        val multi=when { open!=null->"${open.captureExposures}× "+if(open.shutterArmed) "ON" else "OFF";holdFirst->"ON";else->"OFF" }
        readout.value("multi",multi,copy("本格曝光次数与多曝准备：$multi；使用过片旁的多曝按钮操作","Frame exposures and multiple-exposure readiness: $multi; use the Multi button beside Advance"),open?.shutterArmed ?: holdFirst)
        readout.value("shutter",applied?.exposureNs?.let(::shutterCaption) ?: if(applied==null) "--" else "AUTO",copy("已应用的快门。短按打开整档轮盘，双击回到自动曝光，下次从 1/125 起。自动时不虚构实测速度。","Applied shutter. Short-press opens the stop dial; double-tap returns to automatic exposure and the next dial starts at 1/125. AUTO does not invent a measured speed."),applied!=null)
        val imprint=cameraSettings();val printing=imprint["imprint"]?.toString().orEmpty().isNotBlank() && imprint["imprint_style"]!="none"
        val print=if(!printing) "OFF" else when(imprint["imprint_style"]) { "laser_dot"->"DOT";"lab_edge"->"LAB";else->"AMB" }
        readout.value("print",print,copy("之后曝光的相机文字打印：$print","Camera text imprint for future exposures: $print"),printing)
        autoFirstPreview.isEnabled=enabled
        val shutterOpen=roll != null && (if(open != null) open.shutterArmed else roll.exposedCount() < roll.capacity)
        shoot.text=if(pace == "motion") { if(motionJob) copy("停止","Stop") else copy("过片","Motion") } else copy("快门","Shutter")
        shoot.contentDescription=if(pace == "motion") copy("开始或停止过片","Start or stop motion")
            else if(open != null && !open.shutterArmed) copy("快门锁定，等待过片或多曝上弦","Shutter locked; advance or cock for multiple exposure") else copy("快门","Shutter")
        advance.text=copy("过片","Advance");advance.contentDescription=copy("结束本格，过片并上弦","Finish frame, advance and cock")
        menuPause.isEnabled=running
        val canQueue=autoAdvance.isChecked && !holdFirst && open == null
        val outstanding=stillQueue.outstanding()
        shoot.isEnabled = available() && !running && !exposureBusy && roll != null && (if(pace == "motion") motionJob || (!motionDraining && shutterOpen) else shutterOpen && outstanding < 3 &&
            outstanding < (if(open == null) roll.capacity-roll.exposedCount() else 1) && (canQueue || outstanding == 0))
        startSummary.text=if(roll == null) copy("尚未装卷；选择材料与规格后启动拍摄。","Load film and choose a format before starting.") else
            "${roll.label.ifBlank { roll.rollId.take(8) }} · ${materialLabel(roll)} · ${roll.exposedCount()}/${roll.capacity}\n${rollProgress(roll)}"
        startShooting.text=when {
            roll == null -> copy("装填胶卷并开始","Load film to start")
            open != null -> copy("继续拍摄 · 已拍 ${roll.exposedCount()}/${roll.capacity}","Continue · ${roll.exposedCount()}/${roll.capacity} exposed")
            roll.exposedCount() == roll.capacity -> copy("胶卷已满，进入相机封卷","Roll full · return to camera to seal")
            roll.exposedCount() == 0 -> copy("开始拍摄已装胶卷","Start shooting loaded film")
            else -> copy("继续拍摄 · 已拍 ${roll.exposedCount()}/${roll.capacity}","Continue · ${roll.exposedCount()}/${roll.capacity} exposed")
        }
        if(enabled && shell.selected==MobileShell.Page.START) for(button in listOf(startShooting)) {
            button.isPressed=false;button.refreshDrawableState();button.jumpDrawablesToCurrentState()
        }
        advance.isEnabled=enabled && open != null
        advance.visibility=android.view.View.VISIBLE
        advance.contentDescription=when {
            open!=null->copy("结束本格，过片并上弦","Finish frame, advance and cock")
            autoAdvance.isChecked->copy("自动过片已开；保存完成后会自动过片","Auto advance is on; film advances after saving")
            else->copy("当前无需过片；曝光后可过片或多曝上弦","No advance needed now; after exposure, advance or cock for another exposure")
        }
        wideGate.text=if(panorama.isChecked) copy("宽幅 开","Wide ON") else copy("宽幅 关","Wide OFF")
        wideGate.isActivated=panorama.isChecked
        wideGate.isEnabled=panorama.isEnabled
        wideGate.visibility=if(roll?.captureSettings?.get("format") == "135") android.view.View.VISIBLE else android.view.View.GONE
        advanceMode.text=if(autoAdvance.isChecked) copy("连拍","Run") else copy("单格","Hold")
        advanceMode.isActivated=autoAdvance.isChecked
        advanceMode.isEnabled=enabled && pace != "motion" && roll != null
        advanceMode.visibility=if(pace == "motion") android.view.View.GONE else android.view.View.VISIBLE
        advanceMode.contentDescription=if(autoAdvance.isChecked) copy("自动过片已开，点按改为这一格拍完再过片","Auto advance is on; tap to hold each frame")
            else copy("单格拍摄，点按改为自动过片","Holding each frame; tap for auto advance")
        seal.isEnabled = enabled && roll != null && roll.exposedCount() > 0
        updateDevelopmentHeader()
        if(enabled && listsDirty && shell.selected in listOf(MobileShell.Page.ROLLS,MobileShell.Page.ROLL_DETAIL,MobileShell.Page.ROLL_BATCH,MobileShell.Page.OBSERVE)) { refreshLists();listsDirty=false }
        for(parent in listOf(rollRows,resultRows,scanRows)) for(i in 0 until parent.childCount) {
            val view=parent.getChildAt(i);if(view is Button) view.isEnabled=enabled
        }
        shell.decorate()
        val previewReady=scanResult?.let { scanPreview.recipeFor(it.resultId,it.opticalPath) }!=null
        savePhoto.isEnabled=enabled && previewReady
        scanBatchWithSettings.isEnabled=enabled && previewReady && scanBatchFrames.isNotEmpty()
        updateViewerNavigation()
        syncCamera()
    }
    fun captureRollId(): String?=current
    fun captureSizeForLens(sizes: List<Pair<Int,Int>>,referenceSizes: List<Pair<Int,Int>>): Pair<Int,Int>? {
        val roll=current?.let(library::roll) ?: return null
        val settings=roll.captureSettings
        val w=(settings["precision_width"] as? Number)?.toInt() ?: 0
        val h=(settings["precision_height"] as? Number)?.toInt() ?: 0
        val requested=if(w>0 && h>0) w to h else null
        val format=settings["format"]?.toString() ?: "legacy"
        // Old rolls have no policy. Infer once from the original lens, not each newly selected lens.
        val key="capture_precision_policy:${roll.rollId}"
        val policy=settings["precision_policy"]?.toString() ?: preferences.getString(key,null) ?:
            (if(requested!=null && StillResolutionPlan.isMaximum(requested,referenceSizes,format)) "maximum" else "fixed").also {
                if(referenceSizes.isNotEmpty()) preferences.edit().putString(key,it).apply()
            }
        val held=roll.frames.firstOrNull { it.state==FrameState.CAPTURING }?.capturePath?.let { path ->
            captureStore.read(path).sources.first().let { it.width to it.height }
        }
        return StillResolutionPlan.forLens(requested,sizes,format,policy=="maximum",held)
    }
    fun validateCaptureGeometry(width: Int,height: Int) {
        val roll=current?.let(library::roll) ?: return
        roll.frames.firstOrNull { it.state==FrameState.CAPTURING }?.capturePath?.let { path ->
            val source=captureStore.read(path).sources.first()
            require(source.width==width && source.height==height) { "Advance the current frame before changing capture geometry" }
        }
        RecipeDrafts.validateCaptureSize(roll.filmRecipe,roll.captureSettings,width,height)
    }
    fun captureTarget(): String {
        val settings=current?.let { library.roll(it).captureSettings }.orEmpty()
        val pace=settings["pace"]?.toString() ?: "still"
        val width=(settings["precision_width"] as? Number)?.toInt() ?: 0
        val height=(settings["precision_height"] as? Number)?.toInt() ?: 0
        val low=(settings["motion_fps_min"] as? Number)?.toInt() ?: 0
        val high=(settings["motion_fps_max"] as? Number)?.toInt() ?: 0
        return "$pace:$width:$height:$low:$high"
    }
    fun captureInProgress()=drainingStill || motionDraining
    fun pauseCapture(cancelWaiting: Boolean=false) {
        shell.closeQuickControls()
        stopMotion()
        if(cancelWaiting) {
            val removed=stillQueue.discardWaiting()
            if(removed > 0) status(copy("已取消 $removed 个尚未采集的快门请求；已保存曝光保留","Cancelled $removed waiting shutter requests; saved exposures retained"))
        }
    }
    private fun expose() {
        shell.closeQuickControls()
        val id=current ?: return
        val roll=library.roll(id)
        if((roll.captureSettings["pace"]?.toString() ?: "still") == "motion") {
            if(motionJob) stopMotion() else startMotion(id)
            return
        }
        if(running || motionDraining || exposureBusy || !available()) return
        val open=roll.frames.firstOrNull { it.state == FrameState.CAPTURING }
        val armed=if(open != null) open.shutterArmed else roll.exposedCount() < roll.capacity
        if(!armed) return
        if(camera() !is StoredStillCamera) { requestCamera();return }
        val shot=StillShot(id,holdFirst,if(autoAdvance.isChecked) "auto" else "manual",
            panorama.isChecked && roll.captureSettings["format"] == "135",cameraSettings())
        val canQueue=autoAdvance.isChecked && !holdFirst && open == null
        if(!stillQueue.offer(shot,if(open == null) roll.capacity-roll.exposedCount() else 1,canQueue)) return
        val start=!drainingStill
        if(start) drainingStill=true
        refresh()
        if(start) worker.execute { drainStill() }
    }
    private fun drainStill() {
        var error: String?=null
        try {
            while(true) {
                val shot=stillQueue.take() ?: break
                val saved: DeferredShootSession.Saved
                try {
                    val stored=camera() as? StoredStillCamera ?: throw IllegalStateException("camera is not ready")
                    saved=runBlocking { deferred.capture(shot.id,stored,shot.hold,0.0,WorkflowControl(),shot.settings,shot.advance,shot.wide) }
                } finally { stillQueue.complete() }
                if(!saved.recovered && saved.frame.frameIndex==1 && saved.exposures==1) {
                    val rollId=shot.id
                    worker.execute {
                        val wrote=runCatching { references.ensure(captureStore,rollId) }.getOrDefault(false)
                        if(wrote) activity.runOnUiThread { if(!activity.isDestroyed) { listsDirty=true;refresh() } }
                    }
                }
                activity.runOnUiThread { if(!activity.isDestroyed) {
                    listsDirty=true;refresh()
                    status(if(saved.recovered) copy("已接回第 ${saved.frame.frameIndex} 格的已存曝光，没有重新拍摄","Recovered saved exposure for frame ${saved.frame.frameIndex}; no new capture")
                        else copy("第 ${saved.frame.frameIndex} 格 · ${saved.exposures} 次曝光 · 采集并保存 ${saved.captureSaveMs} ms",
                            "Frame ${saved.frame.frameIndex} · ${saved.exposures} exposures · capture and save ${saved.captureSaveMs} ms"))
                } }
            }
        } catch(thrown: Throwable) {
            val removed=stillQueue.discardWaiting()
            error=thrown.message+if(removed > 0) copy("；取消 $removed 个待采集请求","; cancelled $removed waiting requests") else ""
        } finally {
            activity.runOnUiThread { if(!activity.isDestroyed) {
                drainingStill=false
                // A tap may have arrived after the worker saw an empty queue but before this UI callback.
                if(stillQueue.waiting() > 0) { drainingStill=true;worker.execute { drainStill() } }
                refresh()
                if(error != null) { status(copy("曝光未完成：","Exposure incomplete: ")+error);Toast.makeText(activity,error,Toast.LENGTH_LONG).show() }
            } }
        }
    }
    private fun startMotion(id: String) {
        if(running || captureInProgress() || !available()) return
        val adapter=camera() as? CameraXCameraAdapter
        if(adapter == null || !adapter.acceptsMotion()) {
            requestCamera();status(copy("正在按过片尺寸启用相机…","Starting the motion-sized camera…"));return
        }
        val roll=library.roll(id)
        val open=roll.frames.firstOrNull { it.state == FrameState.CAPTURING }
        if(open != null) { status(copy("请先结束当前多曝格再开始过片","Finish the current held frame before motion capture"));return }
        if(roll.exposedCount() >= roll.capacity) { status(copy("已满卷","Roll is full"));return }
        motionQueue.clear();motionCount=0;motionStarted=System.nanoTime();motionRemaining=roll.capacity-roll.exposedCount();motionJob=true;motionDraining=true;refresh()
        status(copy("过片进行中","Motion capture running"))
        val wide=panorama.isChecked && roll.captureSettings["format"] == "135"
        val settings=cameraSettings()
        adapter.armMotion { jpeg,width,height ->
            var accepted=false
            while(motionJob && !accepted) {
                synchronized(motionLock) {
                    if(motionJob && motionRemaining > 0 && motionQueue.offer(MotionFrame(jpeg,width,height))) {
                        motionRemaining--;accepted=true
                        if(motionRemaining == 0) { motionJob=false;adapter.disarmMotion() }
                    }
                }
                if(!accepted && motionJob) Thread.sleep(5)
            }
        }
        worker.execute { drainMotion(id,wide,settings) }
    }
    private fun stopMotion() {
        synchronized(motionLock) { motionJob=false }
        (camera() as? CameraXCameraAdapter)?.disarmMotion()
    }
    private fun drainMotion(id: String,wide: Boolean,settings: Map<String,Any?>) {
        var error: String?=null
        try {
            while(true) {
                val frame=motionQueue.poll(150,TimeUnit.MILLISECONDS)
                if(frame == null) { if(!motionJob) break; continue }
                val roll=library.roll(id)
                val open=roll.frames.firstOrNull { it.state == FrameState.CAPTURING }
                if(open == null && roll.exposedCount() >= roll.capacity) { stopMotion();break }
                val stored=object: StoredStillCamera {
                    override fun saveStill(target: java.io.File): StoredStillInfo {
                        java.io.FileOutputStream(target).use { it.write(frame.jpeg) }
                        return StoredStillInfo(frame.width,frame.height,CaptureRecord(CaptureKind.DISPLAY_REFERRED_SRGB,true,
                            sourceEncoding="iec61966_2_1_srgb",bitDepth=8))
                    }
                }
                val saved=runBlocking { deferred.capture(id,stored,false,0.0,WorkflowControl(),settings,"auto",wide) }
                if(!saved.recovered && saved.frame.frameIndex==1 && saved.exposures==1) worker.execute {
                    runCatching { references.ensure(captureStore,id) }
                }
                motionCount++
                val progress=roll.exposedCount()
                activity.runOnUiThread { if(!activity.isDestroyed) {
                    frameCount.text="$progress/${roll.capacity}"
                    readout.value("frame","${progress.toString().padStart(2,'0')}/${roll.capacity}",copy("已曝光 $progress 格，共 ${roll.capacity} 格","$progress frames exposed of ${roll.capacity}"))
                } }
            }
        } catch(thrown: Throwable) {
            stopMotion();error=thrown.message
        } finally {
            while(motionQueue.poll(50,TimeUnit.MILLISECONDS) != null) Unit
            val count=motionCount
            val seconds=(System.nanoTime()-motionStarted)/1_000_000_000.0
            val rate=if(seconds > 0.05) count/seconds else 0.0
            activity.runOnUiThread { if(!activity.isDestroyed) {
                motionJob=false;motionDraining=false;listsDirty=true;refresh()
                val rateText=java.lang.String.format(java.util.Locale.ROOT,"%.1f",rate)
                status(error?.let { copy("过片未完成：","Motion incomplete: ")+it } ?: copy(
                    "过片 $count 张 · $rateText 张/秒",
                    "Motion $count frames · $rateText fps"))
            } }
        }
    }
    /** Catalog metadata on UI thread; only visible thumbnails decode on their own bounded workers. */
    private fun refreshLists() {
        rollSelection.clearBindings();negativeSelection.clearBindings()
        listOf(rollSelection,negativeSelection,photoSelection,batchSelection).forEach { it.refresh() }
        rollRows.removeAllViews();resultRows.removeViews(1,(resultRows.childCount-1).coerceAtLeast(0))
        galleryGuide.text=galleryLead()
        val rolls=library.visibleRolls().reversed()
        rollPage=rollPage.coerceIn(0,((rolls.size-1).coerceAtLeast(0))/20)
        if(rolls.isEmpty()) shell.note(rollRows,"还没有胶卷。装入一卷，从第一格开始。","No rolls yet. Load a roll and start with frame one.")
        for(roll in rolls.drop(rollPage*20).take(20)) {
            val sealed=roll.lifecycle == RollLifecycle.SEALED
            val batches=library.resultsForRoll(roll.rollId).map { RollLibraryContents.batchId(it) }.distinct().size
            val caption=if(roll.lifecycle==RollLifecycle.DISCARDED) copy("曝光已释放 · 保留已有结果","Exposures removed · saved results retained") else copy(if(sealed) "已封卷 · "+if(batches == 0) "暂无底片" else "保留 $batches 批底片" else "正在拍摄 · 尚未封卷",
                if(sealed) "Sealed · "+if(batches == 0) "No developed film" else "$batches retained batches" else "Shooting · not sealed")
            val label=roll.label.ifBlank { roll.rollId.take(8) }
            val tile=shell.listTile(rollRows,label,caption,if(roll.lifecycle==RollLifecycle.DISCARDED) "›" else "${roll.exposedCount()}/${roll.capacity}  ›",rollMark(roll)) {
                if(canBrowse()) {
                    rollDetail(roll)
                    refresh()
                }
            }
            bindRollInformation(tile.getChildAt(tile.childCount-1) as TextView,roll,caption+"\n"+"${roll.captureSettings["format"] ?: "135"} · ")
            val row=tile.getChildAt(0) as LinearLayout
            val check=CheckBox(activity).apply { isClickable=false;isFocusable=false;importantForAccessibility=android.view.View.IMPORTANT_FOR_ACCESSIBILITY_NO;visibility=android.view.View.GONE }
            row.addView(check,0,LinearLayout.LayoutParams(shell.dp(40),shell.dp(44)))
            rollSelection.bind(tile,roll,{ rollDetail(roll);refresh() }) { active,selected -> check.visibility=if(active) android.view.View.VISIBLE else android.view.View.GONE;check.isChecked=selected }
        }
        pagination(rollRows,rolls.size,rollPage) { rollPage=it;refreshLists();shell.decorate() }
        val visible=rolls.drop(rollPage*20).take(20).map { it.rollId }
        worker.execute {
            val wrote=visible.any { id -> runCatching { references.ensure(captureStore,id) }.getOrDefault(false) }
            if(wrote) activity.runOnUiThread { if(!activity.isDestroyed) { listsDirty=true;refresh() } }
        }
        loadNegativeCatalog()
        loadPhotoCatalog()
        resultRows.visibility=if(negativesSelected) android.view.View.VISIBLE else android.view.View.GONE
        scanRows.visibility=if(negativesSelected) android.view.View.GONE else android.view.View.VISIBLE
        photoSelection.visibility=scanRows.visibility;negativeSelection.visibility=resultRows.visibility
        photoTab.tag=if(!negativesSelected) "primary" else null;negativeTab.tag=if(negativesSelected) "primary" else null
        if(shell.selected==MobileShell.Page.ROLL_DETAIL) renderRollDetail()
        if(shell.selected==MobileShell.Page.ROLL_BATCH) renderBatch()
    }
    private fun paintGalleryLayout() {
        val cols=galleryDisplay.columns
        galleryDisplayButton.setImageDrawable(CatalogGlyph(if(cols==1) "list" else "grid$cols",color=if(shell.ivory) android.graphics.Color.DKGRAY else android.graphics.Color.WHITE))
        galleryDisplayButton.contentDescription=copy("当前${if(cols==1) "列表" else "$cols 列"}，点按切换排列","Current ${if(cols==1) "list" else "$cols columns"}; tap to switch layout")
        galleryDisplayButton.tooltipText=galleryDisplayButton.contentDescription
    }
    private fun fileFacts(path: String): Pair<Long,Long> {
        val root=java.io.File(storage.privateDir()).canonicalFile;val file=java.io.File(root,path).canonicalFile
        require(file.path.startsWith(root.path+java.io.File.separator))
        return file.length() to file.lastModified()
    }
    private fun catalogTime(text: String?,fallback: Long)=text?.let { runCatching { java.time.Instant.parse(it).toEpochMilli() }.getOrNull() } ?: fallback
    private fun orderedPhotos(items: List<SavedScan>,settings: GalleryQuery): List<SavedScan> {
        val byId=items.associateBy { it.id }
        val facts=items.map { saved -> val key="photo:${saved.id}";val file=fileFacts(saved.imagePath)
            GalleryQuery.Item(saved.id,rollName(saved.rollId),catalogTime(saved.createdAt,file.second),file.first,galleryDisplay.rating(key),galleryDisplay.favorite(key)) }
        return settings.ordered(facts).map { byId.getValue(it.id) }
    }
    private fun loadNegativeCatalog() {
        val serial=negativeCatalogSerial.incrementAndGet();val roll=galleryRoll;val settings=galleryDisplay.query()
        val page=resultPage;val continuous=galleryDisplay.continuous;val scrollY=galleryScroll()?.scrollY ?: 0
        val source=library.developedResults().filter { roll==null || it.sourceRollId==roll }
        worker.execute {
            if(serial!=negativeCatalogSerial.get()) return@execute
            val result=runCatching {
                val byId=source.associateBy { it.resultId }
                val facts=source.map { item ->
                    check(serial==negativeCatalogSerial.get())
                    val key="negative:${item.resultId}";val file=fileFacts(item.opticalPath)
                    val time=if(settings.sort=="time") information.negative(item).time else null
                    GalleryQuery.Item(item.resultId,rollName(item.sourceRollId),catalogTime(time,file.second),file.first,galleryDisplay.rating(key),galleryDisplay.favorite(key))
                }
                settings.ordered(facts).map { byId.getValue(it.id) }
            }
            activity.runOnUiThread {
                if(activity.isDestroyed || serial!=negativeCatalogSerial.get()) return@runOnUiThread
                result.fold({ results ->
                    negativeSelection.clearBindings();resultRows.removeViews(1,(resultRows.childCount-1).coerceAtLeast(0))
                    resultPage=page.coerceIn(0,(results.size-1).coerceAtLeast(0)/20)
                    if(results.isEmpty()) shell.note(resultRows,"没有符合筛选条件的底片。","No film matches these filters.")
                    val items=if(continuous) results.take((resultPage+1)*20) else results.drop(resultPage*20).take(20)
                    negativeGrid.onEndReached=if(continuous && items.size<results.size) ({ resultPage++;loadNegativeCatalog() }) else null
                    negativeGrid.submit(items.map { negativeEntry(it,results) },galleryDisplay.columns);resultRows.addView(negativeGrid)
                    pagination(resultRows,results.size,resultPage) { resultPage=it;loadNegativeCatalog() }
                    negativeTab.text=copy("底片 ${results.size}","Film ${results.size}");shell.decorate(resultRows)
                    if(continuous) galleryScroll()?.post { galleryScroll()?.scrollTo(0,scrollY) }
                },{ statusChanged(copy("底片目录读取失败：","Film catalog unavailable: ")+it.message) })
            }
        }
    }
    private fun loadPhotoCatalog() {
        photoGrid.suspendLoading()
        if(!galleryDisplay.continuous || scanPage==0) {
            scanRows.removeViews(1,(scanRows.childCount-1).coerceAtLeast(0))
            shell.note(scanRows,"正在整理照片…","Loading photos…")
        }
        val query=PhotoQuery(catalogSerial.incrementAndGet(),galleryRoll,scanPage,galleryDisplay.continuous,galleryScroll()?.scrollY ?: 0,galleryDisplay.query())
        pendingCatalog.set(query);runPhotoCatalog()
    }
    private fun runPhotoCatalog() {
        if(catalogLoading || activity.isDestroyed) return
        val query=pendingCatalog.getAndSet(null) ?: return
        catalogLoading=true
        worker.execute {
            val result=runCatching {
                val all=orderedPhotos(scans.matching(query.roll) { if(catalogSerial.get()!=query.serial) throw InterruptedException("Gallery query superseded") },query.settings)
                PrivateScanStore.Page(all.size,if(query.continuous) all.take((query.page+1)*20) else all.drop(query.page*20).take(20))
            }
            activity.runOnUiThread {
                catalogLoading=false
                if(activity.isDestroyed) return@runOnUiThread
                if(query.serial==catalogSerial.get()) result.fold({ (count,items) ->
                    photoSelection.clearBindings()
                    scanPage=query.page.coerceIn(0,((count-1).coerceAtLeast(0))/20)
                    if(scanPage!=query.page) { loadPhotoCatalog();return@fold }
                    scanRows.removeViews(1,(scanRows.childCount-1).coerceAtLeast(0))
                    if(count==0) {
                        shell.note(scanRows,"暂无照片。打开底片，扫描并保存后即可在这里查看。","No photos. Open developed film, then scan and save it to Library.")
                        shell.action(scanRows,"去看底片","View negatives") { selectGallery(true) }
                    }
                    query.roll?.let { shell.note(scanRows,rollName(it),rollName(it)) }
                    photoGrid.onEndReached=if(query.continuous && items.size<count) ({ scanPage++;loadPhotoCatalog() }) else null
                    photoGrid.submit(items.map { saved -> ThumbnailGrid.Entry("photo:${saved.id}",LibraryInfoPresentation.photoTitle(rollName(saved.rollId),saved.frameIndex,saved.id,copy("zh","en")=="en")+" "+galleryDisplay.badge("photo:${saved.id}"),tileInformationText(LibraryFacts(saved.displayWidth,saved.displayHeight,saved.sourcePolarity,saved.createdAt),photo=true),
                        load={ check -> decodePhotoThumbnail(saved,check) },open={ openSavedPhoto(saved,items) },bindSelection={ tile,check ->
                            photoSelection.bind(tile,saved,{ openSavedPhoto(saved,items) }) { active,selected -> check.visibility=if(active) android.view.View.VISIBLE else android.view.View.GONE;check.isChecked=selected }
                        },information={ check -> check();if(galleryDisplay.columns!=1) rollName(saved.rollId)+" · "+saved.createdAt.take(16)+" "+galleryDisplay.badge("photo:${saved.id}") else tileInformationText(information.photo(saved,library.developedResults().firstOrNull { it.resultId==saved.resultId }),photo=true)+(if(galleryDisplay.columns==1) "\n${saved.format} · ${saved.precisionBits} bit\n${saved.id}" else "").also { check() } },rotationDegrees=saved.viewRotationDegrees,compact=rollName(saved.rollId)+" · "+saved.createdAt.take(16)+" "+galleryDisplay.badge("photo:${saved.id}")) },galleryDisplay.columns)
                    scanRows.addView(photoGrid);photoTab.text=copy("照片 $count","Photos $count")
                    if(query.continuous) galleryScroll()?.post { galleryScroll()?.scrollTo(0,query.scrollY) }
                    pagination(scanRows,count,scanPage) { scanPage=it;refreshLists();shell.decorate() };shell.decorate()
                },{ error -> status(copy("图库读取失败；原文件保留：","Could not load Library; files retained: ")+error.message) })
                runPhotoCatalog()
            }
        }
    }
    private fun decodePhotoThumbnail(saved: SavedScan,check: ()->Unit): Bitmap {
        check();var sample=1
        while(maxOf(saved.width,saved.height)/sample>384) sample*=2
        val bitmap=decodeDisplayPhoto(saved,sample)
        try { check();return bitmap } catch(error: Exception) { bitmap.recycle();throw error }
    }
    private fun galleryScroll(): ScrollView? {
        var view: android.view.ViewParent?=scanRows.parent
        while(view!=null) { if(view is ScrollView) return view;view=view.parent };return null
    }
    private fun pagination(parent: LinearLayout,total: Int,page: Int,change: (Int)->Unit) {
        if(galleryDisplay.continuous && (parent===scanRows || parent===resultRows)) return
        if(total <= 20) return
        shell.note(parent,"第 ${page+1}/${(total+19)/20} 页 · 共 $total 项","Page ${page+1}/${(total+19)/20} · $total items")
        if(page > 0) shell.action(parent,"上一页","Previous") { if(canBrowse()) change(page-1) }
        if((page+1)*20 < total) shell.action(parent,"下一页","Next") { if(canBrowse()) change(page+1) }
    }
    private fun job(message: String,onSuccess: ()->Unit={},onFailure: (Throwable)->Unit={},allowBrowsing: Boolean=false, work: suspend (WorkflowControl) -> String) {
        if(running || metadataBusy || captureInProgress() || !available() || activity.isDestroyed) return
        stopScene();displayObservation.clear()
        scanWork.cancel();scanFeedback.finish()
        running = true; cancelled.set(false); busy(true);stages.setScanBusy(true); refresh(); status(message)
        com.filmfoundry.app.session.ProcessingWork.launch(activity,taskOwner,message,{ cancelled.set(true) },body={
            jobGpuEnabled=gpuEnabled
            jobRowWorkers=if(Runtime.getRuntime().availableProcessors()>=4 && preferences.getBoolean("cpu_parallel_development",true)) 2 else 1
            jobOpticalHandoff=performance.handoffEnabled
            val recording=runCatching { performance.begin(jobRowWorkers,jobGpuEnabled,rowBudget()) }.getOrNull();activePerformance=recording
            val timings=linkedMapOf<String,Long>()
            val outcome=runCatching {
                try { runBlocking { work(WorkflowControl(cancelRequested=cancelled::get,
                    onProgress={ command,_->recording?.frame(command,0,0) },
                    onTiming={ command,section,nanos -> val key="$command.$section";synchronized(timings) { timings[key]=(timings[key] ?: 0L)+nanos };recording?.timing(command,section,nanos) })) } }
                finally {
                    // Write once per job, never per pixel/block or into material/recipe files.
                    try { preferences.edit().apply {
                            if(timings.isEmpty()) remove("last_formation_timing") else
                                putString("last_formation_timing",RecipeJson.text(timings.mapValues { it.value/1_000_000 }))
                        }.apply()
                    } catch(_: Exception) { /* Diagnostics cannot prevent hardware release. */ }
                    runCatching { pixelCompute.close() }.onFailure { error ->
                    preferences.edit().putString("last_decode_backend","GPU cleanup failed: ${error.message}").apply()
                };jobGpuEnabled=false }
            }
            runCatching { recording?.finish(if(cancelled.get() || (outcome.exceptionOrNull() as? WorkflowException)?.code=="cancelled") "paused" else if(outcome.isSuccess) "completed" else "failed",outcome.exceptionOrNull()) }
            activePerformance=null
            outcome
        },allowBrowsing=allowBrowsing,complete={ result ->
            if(!activity.isDestroyed) {
                // Finish durable work now; navigation/pickers wait until this screen is visible.
                val present: ()->Unit={
                running = false;listsDirty=true; busy(false);stages.setScanBusy(false); refresh();saveDrafts()
                if(result.isSuccess) onSuccess()
                else {
                    onFailure(result.exceptionOrNull()!!)
                    shell.showScanTaskStatus()
                    if(shell.selected == MobileShell.Page.CAPTURE) Toast.makeText(activity,result.exceptionOrNull()?.message,Toast.LENGTH_LONG).show()
                }
                status(result.fold({ it }, { error ->
                    if(error is WorkflowException && error.code == "cancelled") copy("任务已暂停；可从任务库继续，原片与已完成结果保留","Task paused; resume from Tasks. Sources and completed results retained")
                    else copy("任务未完成；可重试原任务，已保存格和结果仍保留：","Task incomplete; retry the original task. Saved frames/results retained: ")+error.message
                }))
                scheduleScanPreview()
                }
                if(activity.lifecycle.currentState.isAtLeast(androidx.lifecycle.Lifecycle.State.STARTED)) present()
                else pendingCompletion=present
            }
        })
        if(android.os.Build.VERSION.SDK_INT>=33 && activity.checkSelfPermission(android.Manifest.permission.POST_NOTIFICATIONS)!=android.content.pm.PackageManager.PERMISSION_GRANTED &&
            !preferences.getBoolean("asked_processing_notifications",false)) {
            preferences.edit().putBoolean("asked_processing_notifications",true).apply()
            activity.requestPermissions(arrayOf(android.Manifest.permission.POST_NOTIFICATIONS),4102)
        }
    }
    fun cancel(keepDisplayedPreview: Boolean=false,pauseTask: Boolean=true) {
        stopScene()
        viewerWork.cancel();scanWork.cancel();scanFeedback.finish();viewerRevision++
        image.cancelTurn()
        previewHandler.removeCallbacks(refreshScanPreview)
        if(keepDisplayedPreview) scanPreview.cancelPendingKeepingDisplayed() else scanPreview.invalidate()
        val ready=scanResult?.let { scanPreview.recipeFor(it.resultId,it.opticalPath) }!=null
        pendingScanPreview=keepDisplayedPreview && scanResult!=null && !ready
        savePhoto.isEnabled=ready && !running && available()
        scanBatchWithSettings.isEnabled=savePhoto.isEnabled && scanBatchFrames.isNotEmpty()
        if(!ready && shell.selected==MobileShell.Page.SCAN)
            scanCaption.text=scanResult?.let(::resultLabel).orEmpty()+copy(" · 点刷新更新预览"," · Tap Refresh to update")
        if(running && pauseTask) cancelled.set(true)
        if(shell.selected == MobileShell.Page.LOADING) shell.select(MobileShell.Page.START,false)
    }
    private fun observationHeapHeadroom(): Long {
        return rowBudget()
    }
}
