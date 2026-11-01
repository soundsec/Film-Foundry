package com.filmfoundry.platform

import android.annotation.SuppressLint
import android.content.Context
import android.hardware.camera2.CameraCharacteristics
import android.hardware.camera2.CaptureRequest
import android.os.Handler
import android.os.Looper
import android.util.Range
import android.util.Size
import androidx.camera.camera2.interop.Camera2CameraInfo
import androidx.camera.camera2.interop.Camera2Interop
import androidx.camera.core.CameraSelector
import androidx.camera.core.resolutionselector.ResolutionSelector
import androidx.camera.core.resolutionselector.ResolutionStrategy
import androidx.camera.core.ImageAnalysis
import androidx.camera.core.ImageCapture
import androidx.camera.core.ImageCaptureException
import androidx.camera.core.ImageProxy
import androidx.camera.core.Preview
import androidx.camera.core.UseCase
import androidx.camera.lifecycle.ProcessCameraProvider
import androidx.lifecycle.LifecycleOwner
import java.util.concurrent.CountDownLatch
import java.util.concurrent.Executor
import java.util.concurrent.Executors
import java.util.concurrent.TimeUnit
import java.util.concurrent.atomic.AtomicReference
import java.util.concurrent.atomic.AtomicBoolean
import java.io.File

/**
 * Live CameraX still + analysis adapter. ImageCapture fills a roll;
 * ImageAnalysis only lights the viewer. RAW uses an actual saved DNG file.
 * Kernel never sees ImageProxy.
 */
class CameraXCameraAdapter(
    private val context: Context,
    private val lifecycleOwner: LifecycleOwner,
    private val jpegDecoder: JpegStillDecoder = AndroidJpegDecoder,
    private val cameraSelector: CameraSelector = CameraSelector.DEFAULT_BACK_CAMERA,
    private val previewSurfaceProvider: Preview.SurfaceProvider? = null,
    private val viewerAnalysisEnabled: Boolean = true,
    private val targetRotation: Int = android.view.Surface.ROTATION_0,
    private val frameWidth: Int = 0,
    private val frameHeight: Int = 0,
    private val motionFrames: Boolean = false,
    private val motionFps: Range<Int>? = null,
    private val nativeLens: NativeCameraCatalog.Lens? = null,
    @Volatile private var nativeSettings: NativeCaptureSettings = NativeCaptureSettings(),
    private val viewerOnly: Boolean = false,
) : CameraAdapter, StoredStillCamera {
    private val cameraExecutor = Executors.newSingleThreadExecutor()
    private var imageCapture = ImageCapture.Builder()
        .setTargetRotation(targetRotation)
        .setCaptureMode(ImageCapture.CAPTURE_MODE_MINIMIZE_LATENCY)
        .build()
    private val imageAnalysis = ImageAnalysis.Builder()
        .setBackpressureStrategy(ImageAnalysis.STRATEGY_KEEP_ONLY_LATEST)
        .setOutputImageFormat(ImageAnalysis.OUTPUT_IMAGE_FORMAT_YUV_420_888)
        .build()
    private val latestAnalysis = AtomicReference<CameraXImageProxy?>()
    private val viewerSample=AtomicReference<((Result<List<Double>>)->Unit)?>(null)
    private val viewerFrames=AtomicReference<((ViewerPreviewFrame)->Unit)?>(null)
    private var streamingRgb: FloatArray?=null
    private val streamingPlanes=ViewerPlaneScratch()
    private val viewerReady=AtomicReference<()->Boolean>({ true })
    private val viewerFailure=AtomicReference<((Throwable)->Unit)?>(null)
    fun streamViewerFrames(deliver: (ViewerPreviewFrame)->Unit,failed: (Throwable)->Unit,ready: ()->Boolean={true}) {
        check(viewerOnly && viewerAnalysisEnabled)
        viewerFrames.set(deliver);viewerFailure.set(failed);viewerReady.set(ready)
    }
    fun sampleViewerLight(done: (Result<List<Double>>)->Unit) {
        check(viewerOnly && viewerAnalysisEnabled) { "One-shot viewer light requires an analysis-only session" }
        viewerSample.set(done)
    }
    private val bindError = AtomicReference<Throwable?>()
    private var provider: ProcessCameraProvider? = null
    @Volatile private var liveCamera: androidx.camera.core.Camera?=null
    private val lastStillResult=AtomicReference<Map<String,Any?>?>()
    data class EvaluativeMeter(val state: String,val ev100: Double?)
    @Volatile var meterWatch: ((EvaluativeMeter)->Unit)?=null
    @Volatile private var evaluativeMeter=EvaluativeMeter("inactive",null)
    fun evaluativeMeter(): EvaluativeMeter=evaluativeMeter
    private val nativeControlSerial=java.util.concurrent.atomic.AtomicInteger()
    private val nativeHandler=Handler(Looper.getMainLooper())
    private var nativeTimeout: Runnable?=null
    private var ownedUseCases: List<UseCase> = emptyList()
    private val motionArmed = AtomicBoolean(false)
    @Volatile private var motionHandler: ((ByteArray, Int, Int) -> Unit)? = null
    data class NativeExposure(val supported: Boolean,val index: Int,val lower: Int,val upper: Int,val stepEv: Double) {
        fun metadata(): Map<String,Any?> = mapOf("model" to "camerax_native_exposure_compensation_v1","index" to index,"step_ev" to stepEv,"ev" to index*stepEv)
    }
    fun nativeExposure(): NativeExposure {
        val state=liveCamera?.cameraInfo?.exposureState ?: return NativeExposure(false,0,0,0,0.0)
        return NativeExposure(state.isExposureCompensationSupported && nativeSettings.iso==null,state.exposureCompensationIndex,
            state.exposureCompensationRange.lower,state.exposureCompensationRange.upper,state.exposureCompensationStep.toDouble())
    }
    data class NativeZoom(val ratio: Float,val minimum: Float,val maximum: Float)
    fun lensLabel(zh: Boolean): String=nativeLens?.label(zh) ?: if(zh) "默认相机" else "Default camera"
    fun nativeRequests(): NativeCaptureSettings=nativeSettings
    @SuppressLint("UnsafeOptInUsageError")
    fun applyNativeSettings(settings: NativeCaptureSettings,checkFormat: Boolean=true,done: (Throwable?)->Unit) {
        val camera=liveCamera ?: return done(CaptureNotReady("Camera is not ready"))
        val validation=runCatching {
            nativeLens?.let { validateNativeSettings(it,settings) }
            require(!checkFormat || outputFormat(settings.format)==boundOutputFormat) { "Format changes require a new camera binding" }
        }.exceptionOrNull()
        if(validation!=null) return done(validation)
        val serial=nativeControlSerial.incrementAndGet();val finished=AtomicBoolean(false)
        nativeTimeout?.let(nativeHandler::removeCallbacks)
        lateinit var timeout: Runnable
        fun finish(error: Throwable?) {
            if(!finished.compareAndSet(false,true)) return
            nativeHandler.removeCallbacks(timeout)
            if(nativeTimeout === timeout) nativeTimeout=null
            done(error)
        }
        timeout=Runnable { if(liveCamera === camera && nativeControlSerial.get()==serial) {
            nativeControlSerial.incrementAndGet();finish(CaptureNotReady("Native camera controls timed out"))
        } }
        nativeTimeout=timeout;nativeHandler.postDelayed(timeout,15_000)
        val options=androidx.camera.camera2.interop.CaptureRequestOptions.Builder()
            .setCaptureRequestOption(CaptureRequest.CONTROL_AE_LOCK,settings.lockAe)
            .setCaptureRequestOption(CaptureRequest.CONTROL_AWB_LOCK,settings.lockAwb)
        val temperature=settings.colorTemperatureK
        if(temperature==null) options.setCaptureRequestOption(CaptureRequest.CONTROL_AWB_MODE,settings.whiteBalance)
        else {
            val gains=colorTemperatureGains(temperature)
            options.setCaptureRequestOption(CaptureRequest.CONTROL_AWB_MODE,CaptureRequest.CONTROL_AWB_MODE_OFF)
                .setCaptureRequestOption(CaptureRequest.COLOR_CORRECTION_MODE,CaptureRequest.COLOR_CORRECTION_MODE_TRANSFORM_MATRIX)
                .setCaptureRequestOption(CaptureRequest.COLOR_CORRECTION_GAINS,android.hardware.camera2.params.RggbChannelVector(gains[0],gains[1],gains[2],gains[3]))
                .setCaptureRequestOption(CaptureRequest.COLOR_CORRECTION_TRANSFORM,android.hardware.camera2.params.ColorSpaceTransform(intArrayOf(
                    1,1,0,1,0,1, 0,1,1,1,0,1, 0,1,0,1,1,1)))
        }
        settings.iso?.let {
            options.setCaptureRequestOption(CaptureRequest.CONTROL_AE_MODE,CaptureRequest.CONTROL_AE_MODE_OFF)
            options.setCaptureRequestOption(CaptureRequest.SENSOR_SENSITIVITY,it)
            options.setCaptureRequestOption(CaptureRequest.SENSOR_EXPOSURE_TIME,settings.exposureNs!!)
        }
        settings.focusDiopters?.let {
            options.setCaptureRequestOption(CaptureRequest.CONTROL_AF_MODE,CaptureRequest.CONTROL_AF_MODE_OFF)
            options.setCaptureRequestOption(CaptureRequest.LENS_FOCUS_DISTANCE,it)
        }
        val future=try { androidx.camera.camera2.interop.Camera2CameraControl.from(camera.cameraControl).setCaptureRequestOptions(options.build()) }
            catch(error: Exception) { finish(error);return }
        future.addListener({
            if(liveCamera !== camera || nativeControlSerial.get()!=serial) return@addListener
            val error=runCatching { future.get();Unit }.exceptionOrNull()
            if(error!=null) { finish(error);return@addListener }
            imageCapture.flashMode=when(settings.flash) { "auto"->ImageCapture.FLASH_MODE_AUTO;"on"->ImageCapture.FLASH_MODE_ON;else->ImageCapture.FLASH_MODE_OFF }
            if(!camera.cameraInfo.hasFlashUnit()) { nativeSettings=settings;finish(null);return@addListener }
            val torch=camera.cameraControl.enableTorch(settings.torch)
            torch.addListener({ if(liveCamera===camera && nativeControlSerial.get()==serial) {
                val torchError=runCatching { torch.get();Unit }.exceptionOrNull()
                if(torchError==null) nativeSettings=settings
                finish(torchError)
            } },mainExecutor())
        },mainExecutor())
    }
    fun nativeZoom(): NativeZoom?=liveCamera?.cameraInfo?.zoomState?.value?.let { NativeZoom(it.zoomRatio,it.minZoomRatio,it.maxZoomRatio) }
    /** Nonblocking native controls. Preview coordinates must come from PreviewView's factory. */
    fun zoomTo(ratio: Float,done: (Throwable?)->Unit) {
        val camera=liveCamera ?: return done(CaptureNotReady("Camera is not ready"))
        val state=nativeZoom() ?: return done(CaptureNotReady("Zoom unavailable"))
        if(!ratio.isFinite()) return done(CaptureRejected("Invalid zoom"))
        val future=camera.cameraControl.setZoomRatio(ratio.coerceIn(state.minimum,state.maximum))
        future.addListener({ if(liveCamera === camera) done(runCatching { future.get();Unit }.exceptionOrNull()) },mainExecutor())
    }
    fun meterAt(point: androidx.camera.core.MeteringPoint,done: (Boolean,Throwable?)->Unit) {
        val camera=liveCamera ?: return done(false,CaptureNotReady("Camera is not ready"))
        var flags=0
        if(nativeSettings.focusDiopters==null) flags=flags or androidx.camera.core.FocusMeteringAction.FLAG_AF
        if(nativeSettings.iso==null && !nativeSettings.lockAe) flags=flags or androidx.camera.core.FocusMeteringAction.FLAG_AE
        if(nativeSettings.whiteBalance==1 && !nativeSettings.lockAwb) flags=flags or androidx.camera.core.FocusMeteringAction.FLAG_AWB
        if(flags==0) return done(false,CaptureRejected("Focus and metering are locked or manual"))
        val action=androidx.camera.core.FocusMeteringAction.Builder(point,flags)
            .setAutoCancelDuration(4,TimeUnit.SECONDS).build()
        if(!camera.cameraInfo.isFocusMeteringSupported(action)) return done(false,CaptureRejected("Point metering unavailable"))
        val future=camera.cameraControl.startFocusAndMetering(action)
        future.addListener({ if(liveCamera === camera) {
            val result=runCatching { future.get().isFocusSuccessful };done(result.getOrDefault(false),result.exceptionOrNull())
        } },mainExecutor())
    }
    fun stillGeometry(): IntArray?=imageCapture.resolutionInfo?.let { intArrayOf(it.resolution.width,it.resolution.height,it.rotationDegrees) }
    override fun plannedOrientationDegrees(): Int?=stillGeometry()?.get(2)
    override fun plannedDimensions(): Pair<Int,Int>?=if(nativeSettings.format=="raw") null else stillGeometry()?.let { if(nativeLens!=null && it[2] in listOf(90,270)) it[1] to it[0] else it[0] to it[1] }
    @Synchronized fun setNativeExposureIndex(index: Int): NativeExposure {
        if(Looper.myLooper() == Looper.getMainLooper()) throw CaptureRejected("Exposure adjustment must run on a worker thread")
        ensureBound();val state=nativeExposure()
        require(state.supported && index in state.lower..state.upper) { "native exposure compensation unavailable or out of range" }
        liveCamera!!.cameraControl.setExposureCompensationIndex(index).get(10,TimeUnit.SECONDS)
        return nativeExposure()
    }
    /** Shooting control: returns immediately and does not take the workflow lock. */
    fun adjustExposureCompensation(index: Int,done: (Throwable?)->Unit) {
        val camera=liveCamera ?: return done(CaptureNotReady("Camera is not ready"))
        val state=nativeExposure()
        if(!state.supported || index !in state.lower..state.upper) return done(CaptureRejected("native exposure compensation unavailable or out of range"))
        val future=camera.cameraControl.setExposureCompensationIndex(index)
        future.addListener({
            if(liveCamera === camera) done(runCatching { future.get();Unit }.exceptionOrNull())
            else done(CaptureNotReady("Camera is not ready"))
        },mainExecutor())
    }
    @Volatile private var bound = false
    @Volatile private var closed = false
    @Volatile private var boundOutputFormat = ImageCapture.OUTPUT_FORMAT_JPEG
    @Volatile private var reported = CameraCapabilities(
        previewIsNotCapture = true,
        previewMayIlluminateViewer = true,
    )

    fun bind(preferHdr: Boolean = false, onBound: ((Throwable?) -> Unit)? = null) {
        bindFormat(if(preferHdr) ImageCapture.OUTPUT_FORMAT_JPEG_ULTRA_HDR else outputFormat(nativeSettings.format),onBound)
    }
    private fun outputFormat(format: String)=when(format) { "raw"->ImageCapture.OUTPUT_FORMAT_RAW;"ultra_hdr"->ImageCapture.OUTPUT_FORMAT_JPEG_ULTRA_HDR;else->ImageCapture.OUTPUT_FORMAT_JPEG }

    private fun bindFormat(outputFormat: Int,onBound: ((Throwable?) -> Unit)? = null) {
        val future = ProcessCameraProvider.getInstance(context)
        future.addListener(
            {
                if(closed) {
                    onBound?.invoke(CaptureRejected("Camera adapter has closed."))
                    return@addListener
                }
                try {
                    val cameraProvider = future.get()
                    nativeLens?.let { validateNativeSettings(it) }
                    val captureBuilder = ImageCapture.Builder()
                        .setTargetRotation(targetRotation)
                        .setCaptureMode(ImageCapture.CAPTURE_MODE_MINIMIZE_LATENCY)
                        .setOutputFormat(outputFormat)
                        .apply { resolution()?.let(::setResolutionSelector) }
                    configureNative(captureBuilder)
                    val candidate = captureBuilder.build().also { it.flashMode=when(nativeSettings.flash) { "on"->ImageCapture.FLASH_MODE_ON;"auto"->ImageCapture.FLASH_MODE_AUTO;else->ImageCapture.FLASH_MODE_OFF } }
                    if(frameWidth>0 && frameHeight>0 && outputFormat!=ImageCapture.OUTPUT_FORMAT_RAW) candidate.setCropAspectRatio(android.util.Rational(frameWidth,frameHeight))
                    if (viewerAnalysisEnabled) imageAnalysis.setAnalyzer(cameraExecutor) { image ->
                        try {
                            if(!viewerOnly || bound && (viewerSample.get()!=null || viewerFrames.get()!=null)) {
                                if(viewerOnly && viewerSample.get()==null && !viewerReady.get().invoke()) return@setAnalyzer
                                val packet=CameraXImageProxies.from(image,CameraXUseCase.IMAGE_ANALYSIS,jpegDecoder,
                                    if(viewerOnly && viewerFrames.get()!=null) streamingPlanes else null)
                                val streaming=viewerFrames.get()
                                if(viewerOnly && streaming!=null) {
                                    val size=packet.width*packet.height*3
                                    val scratch=streamingRgb?.takeIf { it.size==size } ?: FloatArray(size).also { streamingRgb=it }
                                    // The stream consumes synchronously; no frame pixels escape to UI or storage.
                                    streaming(CameraXCaptureBridge.preview(packet,scratch))
                                }
                                else if(viewerOnly) viewerSample.getAndSet(null)?.let { done ->
                                    val result=runCatching { ViewerIlluminantSampler.mean(CameraXCaptureBridge.preview(packet)) }
                                    mainExecutor().execute { if(!closed) done(result) }
                                } else latestAnalysis.set(packet)
                            }
                        } catch (error: CaptureException) {
                            latestAnalysis.set(null)
                            if(viewerOnly) viewerFailure.getAndSet(null)?.let { failed -> mainExecutor().execute { if(!closed) failed(error) } }
                        } finally {
                            image.close()
                        }
                    }
                    if(ownedUseCases.isNotEmpty()) cameraProvider.unbind(*ownedUseCases.toTypedArray())
                    latestAnalysis.set(null)
                    val useCases = mutableListOf<UseCase>()
                    if(viewerOnly) useCases.add(imageAnalysis)
                    else {
                    if (!motionFrames || outputFormat == ImageCapture.OUTPUT_FORMAT_RAW) useCases.add(candidate)
                    if (outputFormat != ImageCapture.OUTPUT_FORMAT_RAW || nativeSettings.format=="raw") {
                        if (viewerAnalysisEnabled && !motionFrames) useCases.add(imageAnalysis)
                        if (motionFrames) useCases.add(motionAnalysis())
                        previewSurfaceProvider?.let { surface ->
                            useCases.add(Preview.Builder().setTargetRotation(targetRotation)
                                .apply { setResolutionSelector(previewResolution());configureNative(this) }
                                .build().also { it.setSurfaceProvider(surface) })
                        }
                    }
                    if (useCases.isEmpty()) useCases.add(candidate)
                    }
                    val camera = cameraProvider.bindToLifecycle(lifecycleOwner,nativeLens?.selector() ?: cameraSelector,*useCases.toTypedArray())
                    ownedUseCases=useCases
                    liveCamera=camera
                    provider = cameraProvider
                    imageCapture = candidate
                    boundOutputFormat = outputFormat
                    reported = capabilitiesFrom(camera.cameraInfo)
                    bound = true
                    bindError.set(null)
                    if(viewerOnly) onBound?.invoke(null) else applyNativeSettings(nativeSettings,checkFormat=false) { error ->
                        if(error!=null) { bound=false;bindError.set(error) }
                        onBound?.invoke(error)
                    }
                    return@addListener
                } catch (error: Throwable) {
                    bound = false
                    bindError.set(error)
                }
                onBound?.invoke(bindError.get())
            },
            mainExecutor(),
        )
    }

    fun unbind() {
        disarmMotion()
        nativeControlSerial.incrementAndGet();nativeTimeout?.let(nativeHandler::removeCallbacks);nativeTimeout=null
        liveCamera=null
        meterWatch=null
        evaluativeMeter=EvaluativeMeter("inactive",null)
        closed = true
        bound = false
        latestAnalysis.set(null)
        viewerSample.set(null)
        viewerFrames.set(null);viewerFailure.set(null)
        try {
            imageAnalysis.clearAnalyzer()
            if(ownedUseCases.isNotEmpty()) provider?.unbind(*ownedUseCases.toTypedArray())
            ownedUseCases=emptyList()
        } finally {
            cameraExecutor.shutdownNow()
        }
    }

    override fun capabilities(): CameraCapabilities = reported
    override fun transfersStillOwnership(): Boolean = true

    @Synchronized
    override fun sourceFileName()=if(nativeSettings.format=="raw") "source.dng" else "source.jpg"

    @Synchronized
    override fun saveStill(target: File): StoredStillInfo {
        if(viewerOnly) throw CaptureRejected("Viewer light sessions cannot expose a roll")
        if(Looper.myLooper() == Looper.getMainLooper()) throw CaptureRejected("Still capture must run on a worker thread")
        ensureBound();ensureOutputFormat(nativeSettings.format=="ultra_hdr",nativeSettings.format=="raw")
        val nativeEv=nativeExposure();val nativeZoomRatio=nativeZoom()?.ratio ?: 1f
        require(!target.exists()) { "capture source must not be overwritten" }
        lastStillResult.set(null)
        val done=CountDownLatch(1);val abandoned=AtomicBoolean(false);val error=AtomicReference<Throwable?>()
        imageCapture.takePicture(ImageCapture.OutputFileOptions.Builder(target).build(),cameraExecutor,
            object: ImageCapture.OnImageSavedCallback {
                override fun onImageSaved(result: ImageCapture.OutputFileResults) { if(abandoned.get()) target.delete();done.countDown() }
                override fun onError(exception: ImageCaptureException) { error.set(exception);if(abandoned.get()) target.delete();done.countDown() }
            })
        try {
            val timeout=maxOf(30L,(nativeSettings.exposureNs ?: 0L)/1_000_000_000L+15L)
            if(!done.await(timeout,TimeUnit.SECONDS)) throw CaptureNotReady("Still capture timed out")
            error.get()?.let { throw CaptureNotReady(it.message ?: "Still capture failed") }
            java.io.FileOutputStream(target,true).use { it.fd.sync() }
            if(nativeSettings.format=="raw") {
                val size=DngRawDecoder.storedDimensions(target,applyOrientation=true)
                val result=lastStillResult.get().orEmpty()
                return StoredStillInfo(size.first,size.second,CaptureRecord(CaptureKind.SENSOR_RAW_DNG,false,
                    sourceEncoding="stored_sensor_dng_v1",bitDepth=null,
                    exposureTimeNs=(result["exposure_time_ns"] as? Number)?.toLong(),iso=(result["iso"] as? Number)?.toInt()),
                    mapOf("native_exposure_compensation" to nativeEv.metadata(),"native_zoom_ratio" to nativeZoomRatio,
                        "native_capture_requests" to nativeSettings.metadata(),"native_camera" to (nativeLens?.metadata() ?: emptyMap<String,Any?>()),
                        "native_capture_result" to result,"stored_pixel_orientation" to "dng_exif_v1"))
            }
            val size=JpegSize.read(target)
            val exif=android.media.ExifInterface(target.path)
            val orientation=exif.getAttributeInt(android.media.ExifInterface.TAG_ORIENTATION,1).let { if(it in 1..8) it else 1 }
            val pixels=CapturePixelOrientation(size.first,size.second,if(nativeLens!=null) orientation else 1)
            val seconds=exif.getAttributeDouble("ExposureTime",0.0)
            val iso=exif.getAttributeInt("ISOSpeedRatings",0).takeIf { it > 0 }
            val hdr=nativeSettings.format=="ultra_hdr"
            return StoredStillInfo(pixels.width,pixels.height,CaptureRecord(if(hdr) CaptureKind.ULTRA_HDR_GAINMAP else CaptureKind.DISPLAY_REFERRED_SRGB,true,
                sourceEncoding=if(hdr) "stored_ultra_hdr_jpeg_r_v1" else "iec61966_2_1_srgb",bitDepth=8,hdrGainmap=if(hdr) "present_not_applied" else "absent",
                exposureTimeNs=if(seconds > 0) (seconds*1e9).toLong() else null,iso=iso),
                extra=mapOf("native_exposure_compensation" to nativeEv.metadata(),"native_zoom_ratio" to nativeZoomRatio,
                    "native_capture_requests" to nativeSettings.metadata(),"native_camera" to (nativeLens?.metadata() ?: emptyMap<String,Any?>()),
                    "stored_pixel_orientation" to if(nativeLens!=null) "exif_v1" else "encoded_v0",
                    "native_capture_result" to lastStillResult.get().orEmpty(),
                    "exif_focal_length_mm" to exif.getAttributeDouble("FocalLength",0.0).takeIf { it>0 },
                    "exif_equivalent_focal_length_mm" to exif.getAttributeInt("FocalLengthIn35mmFilm",0).takeIf { it>0 }))
        } finally { abandoned.set(true) }
    }

    @Synchronized
    override fun captureStill(request: StillCaptureRequest): CaptureSource {
        if(viewerOnly) throw CaptureRejected("Viewer light sessions cannot expose a roll")
        if (Looper.myLooper() == Looper.getMainLooper())
            throw CaptureRejected("Still capture must run on a worker thread.")
        ensureBound()
        if (request.preferRaw) {
            if (request.preferHdr) throw CaptureRejected("RAW and Ultra HDR are separate capture modes.")
            if (!capabilities().supportsDng) throw CaptureNotReady("This CameraX camera has no RAW DNG output.")
            ensureOutputFormat(false,true)
            return takeDng()
        }
        if (request.preferHdr && !capabilities().supportsUltraHdr)
            throw CaptureNotReady("This CameraX device does not support Ultra HDR capture.")
        ensureOutputFormat(request.preferHdr)
        val proxy = takeStill()
        return CameraXCaptureBridge.still(proxy, request, capabilities())
    }

    override fun previewForViewer(): ViewerPreviewFrame {
        ensureBound()
        val proxy = latestAnalysis.get() ?: throw CaptureNotReady(
            "CameraX ImageAnalysis has not produced a viewer frame yet.",
        )
        return CameraXCaptureBridge.preview(proxy)
    }

    private fun ensureBound() {
        bindError.get()?.let { error ->
            throw CaptureNotReady(error.message ?: "CameraX bind failed.")
        }
        if (!bound) {
            throw CaptureNotReady(
                "CameraX adapter is not bound. Do not substitute a preview frame or a JPEG thumbnail for RAW.",
            )
        }
    }

    private fun ensureOutputFormat(preferHdr: Boolean,preferRaw: Boolean=false) {
        val desired = if (preferRaw) ImageCapture.OUTPUT_FORMAT_RAW else if (preferHdr) ImageCapture.OUTPUT_FORMAT_JPEG_ULTRA_HDR else ImageCapture.OUTPUT_FORMAT_JPEG
        if (boundOutputFormat == desired) return
        if (Looper.myLooper() == Looper.getMainLooper())
            throw CaptureRejected("Camera capture and format changes must run on a worker thread.")
        val latch = CountDownLatch(1)
        mainExecutor().execute { bindFormat(desired) { latch.countDown() } }
        if (!latch.await(20,TimeUnit.SECONDS)) throw CaptureNotReady("CameraX format change timed out.")
        ensureBound()
    }

    private fun takeDng(): DngCapture {
        val file=File.createTempFile("film-raw-", ".dng",context.cacheDir)
        val latch=CountDownLatch(1)
        val abandoned=AtomicBoolean(false)
        val failure=AtomicReference<Throwable?>()
        try {
            imageCapture.takePicture(ImageCapture.OutputFileOptions.Builder(file).build(),cameraExecutor,
                object : ImageCapture.OnImageSavedCallback {
                    override fun onImageSaved(result: ImageCapture.OutputFileResults) {
                        if (abandoned.get()) file.delete()
                        latch.countDown()
                    }
                    override fun onError(error: ImageCaptureException) {
                        failure.set(error)
                        if (abandoned.get()) file.delete()
                        latch.countDown()
                    }
                })
            if (!latch.await(30,TimeUnit.SECONDS)) throw CaptureNotReady("RAW DNG capture timed out.")
            failure.get()?.let { throw CaptureNotReady(it.message ?: "RAW DNG capture failed.") }
            if (file.length() !in 8..(512L*1024*1024)) throw CaptureRejected("RAW DNG file size is invalid.")
            return DngCapture(file.readBytes())
        } finally {
            abandoned.set(true)
            file.delete()
        }
    }

    private fun takeStill(): CameraXImageProxy {
        val latch = CountDownLatch(1)
        val captured = AtomicReference<CameraXImageProxy?>()
        val failure = AtomicReference<Throwable?>()
        imageCapture.takePicture(
            cameraExecutor,
            object : ImageCapture.OnImageCapturedCallback() {
                override fun onCaptureSuccess(image: ImageProxy) {
                    try {
                        captured.set(
                            CameraXImageProxies.from(
                                image,
                                CameraXUseCase.IMAGE_CAPTURE,
                                jpegDecoder,
                            ),
                        )
                    } catch (error: Throwable) {
                        failure.set(error)
                    } finally {
                        image.close()
                        latch.countDown()
                    }
                }

                override fun onError(exception: ImageCaptureException) {
                    failure.set(exception)
                    latch.countDown()
                }
            },
        )
        if (!latch.await(20, TimeUnit.SECONDS)) {
            throw CaptureNotReady("ImageCapture timed out.")
        }
        failure.get()?.let { error ->
            when (error) {
                is CaptureException -> throw error
                else -> throw CaptureNotReady(error.message ?: "ImageCapture failed.")
            }
        }
        return captured.get() ?: throw CaptureNotReady("ImageCapture produced no buffer.")
    }

    private fun recordEvaluativeMeter(result: android.hardware.camera2.TotalCaptureResult) {
        val ae=result.get(android.hardware.camera2.CaptureResult.CONTROL_AE_STATE)
        val state=when(ae) {
            android.hardware.camera2.CaptureResult.CONTROL_AE_STATE_SEARCHING->"searching"
            android.hardware.camera2.CaptureResult.CONTROL_AE_STATE_CONVERGED->"converged"
            android.hardware.camera2.CaptureResult.CONTROL_AE_STATE_LOCKED->"locked"
            android.hardware.camera2.CaptureResult.CONTROL_AE_STATE_FLASH_REQUIRED->"flash"
            android.hardware.camera2.CaptureResult.CONTROL_AE_STATE_PRECAPTURE->"precapture"
            else->"inactive"
        }
        val iso=result.get(android.hardware.camera2.CaptureResult.SENSOR_SENSITIVITY)
        val exposureNs=result.get(android.hardware.camera2.CaptureResult.SENSOR_EXPOSURE_TIME)
        val aperture=result.get(android.hardware.camera2.CaptureResult.LENS_APERTURE)
        val ev=if(state!="inactive" && iso!=null && iso>0 && exposureNs!=null && exposureNs>0 && aperture!=null && aperture>0f) {
            val seconds=exposureNs/1e9
            val value=kotlin.math.log2(aperture.toDouble()*aperture/seconds)-kotlin.math.log2(iso/100.0)
            if(value.isFinite()) kotlin.math.round(value*10)/10.0 else null
        } else null
        val previous=evaluativeMeter
        if(previous.state==state && previous.ev100==ev) return
        val next=EvaluativeMeter(state,ev)
        evaluativeMeter=next
        val watch=meterWatch ?: return
        mainExecutor().execute { if(meterWatch===watch) watch(next) }
    }
    @SuppressLint("UnsafeOptInUsageError")
    private fun <T> configureNative(builder: androidx.camera.core.ExtendableBuilder<T>) {
        val interop=Camera2Interop.Extender(builder)
        interop.setSessionCaptureCallback(object: android.hardware.camera2.CameraCaptureSession.CaptureCallback() {
            override fun onCaptureCompleted(session: android.hardware.camera2.CameraCaptureSession,request: android.hardware.camera2.CaptureRequest,result: android.hardware.camera2.TotalCaptureResult) {
                recordEvaluativeMeter(result)
                if(result.get(android.hardware.camera2.CaptureResult.CONTROL_CAPTURE_INTENT)!=CaptureRequest.CONTROL_CAPTURE_INTENT_STILL_CAPTURE) return
                val physical=if(android.os.Build.VERSION.SDK_INT>=28) result.get(android.hardware.camera2.CaptureResult.LOGICAL_MULTI_CAMERA_ACTIVE_PHYSICAL_ID) else null
                lastStillResult.set(mapOf("sensor_timestamp_ns" to result.get(android.hardware.camera2.CaptureResult.SENSOR_TIMESTAMP),
                    "iso" to result.get(android.hardware.camera2.CaptureResult.SENSOR_SENSITIVITY),
                    "exposure_time_ns" to result.get(android.hardware.camera2.CaptureResult.SENSOR_EXPOSURE_TIME),
                    "focal_length_mm" to result.get(android.hardware.camera2.CaptureResult.LENS_FOCAL_LENGTH),
                    "focus_diopters" to result.get(android.hardware.camera2.CaptureResult.LENS_FOCUS_DISTANCE),
                    "active_physical_id" to physical))
            }
        })
    }
    private fun validateNativeSettings(lens: NativeCameraCatalog.Lens,settings: NativeCaptureSettings=nativeSettings) {
        val chars=lens.characteristics
        val format=when(settings.format) { "raw"->android.graphics.ImageFormat.RAW_SENSOR;"ultra_hdr"->android.graphics.ImageFormat.JPEG_R;else->android.graphics.ImageFormat.JPEG }
        if(frameWidth>0 && frameHeight>0) require(lens.sizes(format).any { (it.first==frameWidth && it.second==frameHeight) || (it.first==frameHeight && it.second==frameWidth) }) { "Selected lens and format cannot provide this roll's capture size" }
        require(settings.format!="ultra_hdr" || lens.ultraHdr) { "Ultra HDR is unavailable for this camera selection" }
        require(settings.format!="raw" || lens.rawCapture) { "RAW file capture is unavailable for this selection" }
        require(!settings.torch && settings.flash=="off" || chars.get(CameraCharacteristics.FLASH_INFO_AVAILABLE)==true) { "This camera has no flash unit" }
        require(!settings.lockAe || chars.get(CameraCharacteristics.CONTROL_AE_LOCK_AVAILABLE)==true)
        require(!settings.lockAwb || chars.get(CameraCharacteristics.CONTROL_AWB_LOCK_AVAILABLE)==true)
        val awbModes=chars.get(CameraCharacteristics.CONTROL_AWB_AVAILABLE_MODES) ?: intArrayOf()
        if(settings.colorTemperatureK==null) require(awbModes.contains(settings.whiteBalance)) { "White balance mode unavailable" }
        else {
            require(awbModes.contains(CaptureRequest.CONTROL_AWB_MODE_OFF)) { "This camera cannot turn auto white balance off" }
            val keys=chars.availableCaptureRequestKeys.orEmpty()
            require(keys.contains(CaptureRequest.COLOR_CORRECTION_GAINS) && keys.contains(CaptureRequest.COLOR_CORRECTION_MODE)) { "This camera cannot apply a color temperature" }
        }
        settings.iso?.let { iso ->
            require((chars.get(CameraCharacteristics.REQUEST_AVAILABLE_CAPABILITIES) ?: intArrayOf()).contains(CameraCharacteristics.REQUEST_AVAILABLE_CAPABILITIES_MANUAL_SENSOR)) { "Manual sensor control unavailable" }
            require(chars.get(CameraCharacteristics.SENSOR_INFO_SENSITIVITY_RANGE)?.contains(iso)==true)
            require(chars.get(CameraCharacteristics.SENSOR_INFO_EXPOSURE_TIME_RANGE)?.contains(settings.exposureNs!!)==true)
        }
        settings.focusDiopters?.let {
            require(chars.availableCaptureRequestKeys.orEmpty().contains(CaptureRequest.LENS_FOCUS_DISTANCE)) { "Manual focus requests unavailable" }
            require(it <= (chars.get(CameraCharacteristics.LENS_INFO_MINIMUM_FOCUS_DISTANCE) ?: 0f))
        }
        if(motionFrames) require(settings==NativeCaptureSettings()) { "Native still controls are not applied to motion capture" }
    }
    @SuppressLint("UnsafeOptInUsageError")
    private fun capabilitiesFrom(info: androidx.camera.core.CameraInfo): CameraCapabilities {
        val camera2 = Camera2CameraInfo.from(info)
        val caps = camera2.getCameraCharacteristic(
            CameraCharacteristics.REQUEST_AVAILABLE_CAPABILITIES,
        ) ?: intArrayOf()
        val supportsRaw = nativeLens?.raw ?: caps.contains(CameraCharacteristics.REQUEST_AVAILABLE_CAPABILITIES_RAW)
        val pixelArray: Size? = nativeLens?.characteristics?.get(CameraCharacteristics.SENSOR_INFO_PIXEL_ARRAY_SIZE) ?: camera2.getCameraCharacteristic(
            CameraCharacteristics.SENSOR_INFO_PIXEL_ARRAY_SIZE,
        )
        return CameraCapabilities(
            maxStillWidth = pixelArray?.width,
            maxStillHeight = pixelArray?.height,
            supportsDng = supportsRaw && ImageCapture.getImageCaptureCapabilities(info).supportedOutputFormats.contains(ImageCapture.OUTPUT_FORMAT_RAW),
            supportsUltraHdr = nativeLens?.ultraHdr ?: (android.os.Build.VERSION.SDK_INT >= 34 &&
                ImageCapture.getImageCaptureCapabilities(info).supportedOutputFormats.contains(ImageCapture.OUTPUT_FORMAT_JPEG_ULTRA_HDR)),
            supportsUnprocessedRaw = supportsRaw,
            previewIsNotCapture = true,
            previewMayIlluminateViewer = true,
        )
    }

    fun armMotion(handler: (ByteArray, Int, Int) -> Unit) {
        require(motionFrames) { "motion frames are not bound" }
        motionHandler = handler
        motionArmed.set(true)
    }

    fun disarmMotion() {
        motionArmed.set(false)
        motionHandler = null
    }

    fun acceptsMotion(): Boolean = motionFrames && !closed

    private fun resolution(): ResolutionSelector? {
        if (frameWidth <= 0 || frameHeight <= 0) return null
        return ResolutionSelector.Builder().setAspectRatioStrategy(
            if(kotlin.math.abs(frameWidth.toDouble()/frameHeight-16.0/9)<.05) androidx.camera.core.resolutionselector.AspectRatioStrategy.RATIO_16_9_FALLBACK_AUTO_STRATEGY
            else androidx.camera.core.resolutionselector.AspectRatioStrategy.RATIO_4_3_FALLBACK_AUTO_STRATEGY
        ).setResolutionStrategy(
            ResolutionStrategy(Size(frameWidth, frameHeight), if(nativeLens!=null) ResolutionStrategy.FALLBACK_RULE_NONE else ResolutionStrategy.FALLBACK_RULE_CLOSEST_HIGHER_THEN_LOWER),
        ).build()
    }

    /** Viewfinder is a display surface, not a second full-size still stream. */
    private fun previewResolution(): ResolutionSelector = ResolutionSelector.Builder()
        .setAspectRatioStrategy(
            if(frameWidth>0 && frameHeight>0 && kotlin.math.abs(frameWidth.toDouble()/frameHeight-16.0/9)<.05)
                androidx.camera.core.resolutionselector.AspectRatioStrategy.RATIO_16_9_FALLBACK_AUTO_STRATEGY
            else androidx.camera.core.resolutionselector.AspectRatioStrategy.RATIO_4_3_FALLBACK_AUTO_STRATEGY)
        .setResolutionStrategy(ResolutionStrategy(Size(1920,1080),ResolutionStrategy.FALLBACK_RULE_CLOSEST_LOWER_THEN_HIGHER))
        .build()

    @SuppressLint("UnsafeOptInUsageError")
    private fun motionAnalysis(): ImageAnalysis {
        val builder = ImageAnalysis.Builder()
            .setTargetRotation(targetRotation)
            .setBackpressureStrategy(ImageAnalysis.STRATEGY_BLOCK_PRODUCER)
            .setOutputImageFormat(ImageAnalysis.OUTPUT_IMAGE_FORMAT_YUV_420_888)
        resolution()?.let { builder.setResolutionSelector(it) }
        motionFps?.let { Camera2Interop.Extender(builder).setCaptureRequestOption(CaptureRequest.CONTROL_AE_TARGET_FPS_RANGE, it) }
        return builder.build().also { analysis ->
            analysis.setAnalyzer(cameraExecutor) { image ->
                if (!motionArmed.get()) {
                    image.close()
                    return@setAnalyzer
                }
                try {
                    val jpeg = YuvJpeg.encode(image)
                    val width = image.width
                    val height = image.height
                    image.close()
                    motionHandler?.invoke(jpeg, width, height)
                } catch (_: Throwable) {
                    runCatching { image.close() }
                }
            }
        }
    }

    private fun mainExecutor(): Executor {
        val handler = Handler(Looper.getMainLooper())
        return Executor { command -> handler.post(command) }
    }
}
