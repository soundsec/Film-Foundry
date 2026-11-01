package com.filmfoundry.platform

import android.Manifest
import android.content.Context
import android.content.pm.PackageManager
import android.os.Handler
import android.os.Looper
import androidx.camera.core.CameraSelector
import androidx.camera.core.DynamicRange
import androidx.camera.core.Preview
import androidx.camera.lifecycle.ProcessCameraProvider
import androidx.camera.video.FileOutputOptions
import androidx.camera.video.Quality
import androidx.camera.video.QualitySelector
import androidx.camera.video.Recorder
import androidx.camera.video.Recording
import androidx.camera.video.VideoCapture
import androidx.camera.video.VideoRecordEvent
import androidx.lifecycle.LifecycleOwner
import java.util.concurrent.Executor

/** Actual Recorder session. No ImageAnalysis frames, no MediaStore source writes.
 * Host switches still/video ownership on the main thread before calling start.
 * Finalize is the only success boundary; onStop requests stop, never a fake commit.
 */
class CameraXMovieRecorder(private val context: Context,private val owner: LifecycleOwner,
    private val sources: MovieSources,private val worker: Executor) {
    private val main=Executor { Handler(Looper.getMainLooper()).post(it) }
    private var provider: ProcessCameraProvider?=null
    private var capture: VideoCapture<Recorder>?=null
    private var recording: Recording?=null
    private var preview: Preview?=null
    private var stopRequested=false
    var busy=false
        private set

    @android.annotation.SuppressLint("MissingPermission")
    fun start(withAudio: Boolean,surfaceProvider: Preview.SurfaceProvider?=null,onStatus: (String)->Unit,onFinished: (Result<MovieSource>)->Unit) {
        check(Looper.myLooper() == Looper.getMainLooper())
        check(!busy)
        if(context.checkSelfPermission(Manifest.permission.CAMERA) != PackageManager.PERMISSION_GRANTED ||
            withAudio && context.checkSelfPermission(Manifest.permission.RECORD_AUDIO) != PackageManager.PERMISSION_GRANTED)
            throw CaptureRejected("Camera/microphone permission is required for the selected recording mode.")
        busy=true
        stopRequested=false
        val future=ProcessCameraProvider.getInstance(context)
        future.addListener({
            var draft: MovieSources.Draft?=null
            try {
                if(stopRequested) throw CaptureRejected("Movie recording cancelled before binding.")
                val cameraProvider=future.get()
                provider=cameraProvider
                // Explicit SDR/FHD policy. No hidden fallback to a lower capture resolution.
                val recorder=Recorder.Builder().setQualitySelector(QualitySelector.from(Quality.FHD)).build()
                val video=VideoCapture.Builder(recorder).setDynamicRange(DynamicRange.SDR).build()
                capture=video
                val live=surfaceProvider?.let { Preview.Builder().build().also { p -> p.setSurfaceProvider(it) } }
                preview=live
                if(live == null) cameraProvider.bindToLifecycle(owner,CameraSelector.DEFAULT_BACK_CAMERA,video)
                else cameraProvider.bindToLifecycle(owner,CameraSelector.DEFAULT_BACK_CAMERA,video,live)
                draft=sources.draft()
                val pendingDraft=draft
                var pending=recorder.prepareRecording(context,FileOutputOptions.Builder(pendingDraft.file).build())
                if(withAudio) pending=pending.withAudioEnabled()
                recording=pending.start(main) { event ->
                    when(event) {
                        is VideoRecordEvent.Start -> onStatus("recording")
                        is VideoRecordEvent.Finalize -> {
                            recording=null
                            provider?.unbind(video)
                            preview?.let { provider?.unbind(it) }
                            preview=null
                            capture=null
                            worker.execute {
                                val result=runCatching {
                                    if(event.hasError()) throw CaptureRejected("Movie recorder failed (${event.error}): ${event.cause?.message.orEmpty()}")
                                    sources.commit(pendingDraft,"camera")
                                }
                                pendingDraft.abandon()
                                main.execute { busy=false; onFinished(result) }
                            }
                        }
                    }
                }
                if(stopRequested) recording?.stop()
                onStatus("starting")
            } catch(error: Throwable) {
                draft?.abandon()
                capture?.let { provider?.unbind(it) }
                preview?.let { provider?.unbind(it) }
                preview=null
                capture=null
                busy=false
                onFinished(Result.failure(error))
            }
        },main)
    }

    fun stop() { stopRequested=true; recording?.stop() }
    fun close() {
        stop()
        // If binding has not reached start yet, lifecycle prevents capture after destruction.
        if(recording == null) capture?.let { provider?.unbind(it) }
    }
}
