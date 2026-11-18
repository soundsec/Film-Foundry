package com.filmfoundry.platform

import android.content.Context
import androidx.camera.core.CameraSelector
import androidx.camera.core.ImageCapture
import androidx.camera.core.ImageCaptureException
import androidx.camera.core.ImageProxy
import androidx.camera.core.Preview
import androidx.camera.lifecycle.ProcessCameraProvider
import androidx.core.content.ContextCompat
import androidx.lifecycle.Lifecycle
import androidx.lifecycle.LifecycleOwner
import java.util.concurrent.Executor

/** One preview and one JPEG still. It does not own a roll or the host camera's use cases. */
class SingleStillCamera(context: Context, private val owner: LifecycleOwner) {
    private val app = context.applicationContext
    private var provider: ProcessCameraProvider? = null
    private var useCases = emptyList<androidx.camera.core.UseCase>()
    private var capture: ImageCapture? = null
    private var bindRevision = 0L

    fun bind(surface: Preview.SurfaceProvider, rotation: Int, onReady: (Throwable?) -> Unit) {
        val revision = ++bindRevision
        val future = ProcessCameraProvider.getInstance(app)
        future.addListener({
            if (revision != bindRevision || !owner.lifecycle.currentState.isAtLeast(Lifecycle.State.STARTED)) return@addListener
            try {
                val cameraProvider = future.get()
                capture?.let { cameraProvider.unbind(*useCases.toTypedArray()) }
                val preview = Preview.Builder().setTargetRotation(rotation).build().also { it.setSurfaceProvider(surface) }
                val still = ImageCapture.Builder().setCaptureMode(ImageCapture.CAPTURE_MODE_MINIMIZE_LATENCY).setTargetRotation(rotation).build()
                cameraProvider.bindToLifecycle(owner, CameraSelector.DEFAULT_BACK_CAMERA, preview, still)
                provider = cameraProvider
                capture = still
                useCases = listOf(preview, still)
                onReady(null)
            } catch (error: Throwable) {
                capture = null
                useCases = emptyList()
                onReady(error)
            }
        }, ContextCompat.getMainExecutor(app))
    }

    fun takeJpeg(executor: Executor, onJpeg: (ByteArray, Int) -> Unit, onError: (Throwable) -> Unit) {
        val still = capture ?: return onError(IllegalStateException("Camera is not ready"))
        still.takePicture(executor, object : ImageCapture.OnImageCapturedCallback() {
            override fun onCaptureSuccess(image: ImageProxy) {
                try {
                    val buffer = image.planes[0].buffer
                    val bytes = ByteArray(buffer.remaining())
                    buffer.get(bytes)
                    val rotation = image.imageInfo.rotationDegrees
                    image.close()
                    onJpeg(bytes, rotation)
                } catch (error: Throwable) {
                    image.close()
                    onError(error)
                }
            }
            override fun onError(exception: ImageCaptureException) = onError(exception)
        })
    }

    fun unbind() {
        // Invalidate provider callbacks even when the first bind is still pending.
        bindRevision++
        val cameraProvider = provider ?: return
        if (useCases.isNotEmpty()) cameraProvider.unbind(*useCases.toTypedArray())
        useCases = emptyList()
        capture = null
    }
}
