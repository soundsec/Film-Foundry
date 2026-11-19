package com.filmfoundry.app.ui

import android.Manifest
import android.content.pm.PackageManager
import android.graphics.Bitmap
import android.graphics.BitmapFactory
import android.graphics.Color
import android.graphics.Matrix
import android.graphics.drawable.GradientDrawable
import android.os.Bundle
import android.view.Gravity
import android.view.View
import android.widget.Button
import android.widget.FrameLayout
import android.widget.HorizontalScrollView
import android.widget.ImageView
import android.widget.LinearLayout
import android.widget.TextView
import androidx.activity.ComponentActivity
import androidx.activity.result.contract.ActivityResultContracts
import androidx.camera.view.PreviewView
import androidx.core.content.ContextCompat
import com.filmfoundry.app.session.CameraSounds
import com.filmfoundry.platform.SingleStillCamera
import com.filmfoundry.workflow.MaterialPicture
import com.filmfoundry.platform.TransferFunctions
import com.filmfoundry.workflow.ExperimentalMaterialShot
import com.filmfoundry.workflow.StillPixels
import java.util.concurrent.Executors
import kotlin.math.pow

/** A plain one-shot camera. It is not part of the roll, library, or main navigation. */
class ExperimentalMaterialActivity : ComponentActivity() {
    override fun attachBaseContext(newBase: android.content.Context) = super.attachBaseContext(MobileLanguages.configured(newBase))
    private val words by lazy { MobileLanguages.forContext(this) }
    private val worker = Executors.newSingleThreadExecutor()
    private val sounds by lazy { CameraSounds.get(this) }
    private lateinit var camera: SingleStillCamera
    private lateinit var preview: PreviewView
    private lateinit var stillView: ImageView
    private lateinit var peelView: ImageView
    private var peelStart = 0f
    private var peelOrigin = 0f
    private lateinit var shutter: Button
    private lateinit var status: TextView
    private lateinit var choices: LinearLayout
    private lateinit var processButton: Button
    private lateinit var retakeButton: Button
    private var selected = ExperimentalMaterialShot.choices.first().id
    private var captured: StillPixels? = null
    private var busy = false
    private var resumed = false
    private val retryBind = Runnable { bindCamera() }

    private val permission = registerForActivityResult(ActivityResultContracts.RequestPermission()) { granted ->
        if (granted) bindCamera() else status.text = text("需要相机权限才能拍摄", "Camera permission is required")
    }

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        camera = SingleStillCamera(this, this)
        val root = FrameLayout(this).apply { setBackgroundColor(Color.BLACK) }
        preview = PreviewView(this).apply { scaleType = PreviewView.ScaleType.FILL_CENTER; implementationMode = PreviewView.ImplementationMode.COMPATIBLE }
        stillView = ImageView(this).apply { scaleType = ImageView.ScaleType.FIT_CENTER; visibility = View.GONE }
        peelView = ImageView(this).apply {
            scaleType = ImageView.ScaleType.FIT_CENTER; visibility = View.GONE
            setOnTouchListener { view, event -> peel(view, event) }
        }
        root.addView(preview, FrameLayout.LayoutParams(-1, -1))
        root.addView(stillView, FrameLayout.LayoutParams(-1, -1))
        root.addView(peelView, FrameLayout.LayoutParams(-1, -1))
        val bottom = LinearLayout(this).apply { orientation = LinearLayout.VERTICAL; setBackgroundColor(0xCC000000.toInt()); setPadding(dp(16), dp(12), dp(16), dp(20)) }
        status = TextView(this).apply { setTextColor(Color.WHITE); textSize = 14f; text = text("拍一张，再选择材料", "Take one photo, then choose a material") }
        bottom.addView(status)
        choices = LinearLayout(this).apply { orientation = LinearLayout.HORIZONTAL; visibility = View.GONE }
        ExperimentalMaterialShot.choices.forEach { choice ->
            choices.addView(Button(this).apply {
                text = text(choice.zh, choice.en); isAllCaps = false; textSize = 13f
                setOnClickListener { selected = choice.id; paintChoices() }
            }, LinearLayout.LayoutParams(-2, -2).apply { marginEnd = dp(8) })
        }
        bottom.addView(HorizontalScrollView(this).apply { addView(choices); isHorizontalScrollBarEnabled = false }, LinearLayout.LayoutParams(-1, -2).apply { topMargin = dp(8) })
        val actions = LinearLayout(this).apply { orientation = LinearLayout.HORIZONTAL; gravity = Gravity.CENTER }
        retakeButton = Button(this).apply { text = text("重拍", "Retake"); isAllCaps = false; visibility = View.GONE; setOnClickListener { live() } }
        processButton = Button(this).apply { text = text("处理这张", "Process this photo"); isAllCaps = false; visibility = View.GONE; setOnClickListener { process() } }
        shutter = Button(this).apply {
            text = ""; contentDescription = text("快门", "Shutter")
            background = GradientDrawable().apply { shape = GradientDrawable.OVAL; setColor(Color.WHITE); setStroke(dp(4), 0xFFB0B0B0.toInt()) }
            setOnClickListener { shoot() }
        }
        actions.addView(retakeButton)
        actions.addView(shutter, LinearLayout.LayoutParams(dp(72), dp(72)).apply { marginStart = dp(16); marginEnd = dp(16) })
        actions.addView(processButton)
        bottom.addView(actions, LinearLayout.LayoutParams(-1, -2).apply { topMargin = dp(12) })
        root.addView(bottom, FrameLayout.LayoutParams(-1, -2, Gravity.BOTTOM))
        setContentView(root)
        paintChoices()
        (choices.parent as View).visibility = View.GONE
    }

    override fun onResume() {
        super.onResume()
        resumed = true
        if (ContextCompat.checkSelfPermission(this, Manifest.permission.CAMERA) == PackageManager.PERMISSION_GRANTED) bindCamera()
        else permission.launch(Manifest.permission.CAMERA)
        window.decorView.postDelayed(retryBind, 400)
    }

    override fun onPause() {
        resumed = false
        window.decorView.removeCallbacks(retryBind)
        sounds.cancelPending()
        camera.unbind()
        super.onPause()
    }

    override fun onDestroy() {
        worker.shutdownNow()
        super.onDestroy()
    }

    private fun bindCamera() {
        if (!resumed || busy || captured != null || isDestroyed || isFinishing) return
        if (ContextCompat.checkSelfPermission(this, Manifest.permission.CAMERA) != PackageManager.PERMISSION_GRANTED) return
        val rotation = preview.display?.rotation ?: android.view.Surface.ROTATION_0
        camera.bind(preview.surfaceProvider, rotation) { error ->
            if (error != null && captured == null) status.text = text("相机还没准备好", "Camera is not ready yet")
        }
    }

    private fun shoot() {
        if (busy || captured != null) return
        busy = true
        shutter.isEnabled = false
        sounds.playShutter(null)
        status.text = text("正在拍摄…", "Taking the photo…")
        camera.takeJpeg(worker, { bytes, rotation ->
            runCatching { prepare(bytes, rotation) }.fold(
                onSuccess = { volume -> runOnUiThread { if (!isDestroyed) showCapture(volume) } },
                onFailure = { error -> runOnUiThread { if (!isDestroyed) fail(error) } },
            )
        }, { error -> runOnUiThread { if (!isDestroyed) fail(error) } })
    }

    private fun prepare(jpeg: ByteArray, rotation: Int): StillPixels {
        val bitmap = BitmapFactory.decodeByteArray(jpeg, 0, jpeg.size) ?: error("Could not read the photo")
        val upright = if (rotation % 360 == 0) bitmap else Bitmap.createBitmap(bitmap, 0, 0, bitmap.width, bitmap.height, Matrix().apply { postRotate(rotation.toFloat()) }, true)
        val width = upright.width
        val height = upright.height
        val pixels = IntArray(width * height)
        upright.getPixels(pixels, 0, width, 0, 0, width, height)
        if (upright !== bitmap) bitmap.recycle()
        upright.recycle()
        val encoded = FloatArray(pixels.size * 3)
        for (i in pixels.indices) {
            encoded[i * 3] = ((pixels[i] shr 16) and 255) / 255f
            encoded[i * 3 + 1] = ((pixels[i] shr 8) and 255) / 255f
            encoded[i * 3 + 2] = (pixels[i] and 255) / 255f
        }
        val fitted = ExperimentalMaterialShot.fit(width, height, encoded)
        TransferFunctions.srgbToLinearInPlace(fitted.values)
        return fitted
    }

    private fun showCapture(volume: StillPixels) {
        busy = false
        captured = volume
        stillView.setImageBitmap(display(volume))
        stillView.visibility = View.VISIBLE
        preview.visibility = View.GONE
        shutter.visibility = View.GONE
        choices.visibility = View.VISIBLE
        (choices.parent as View).visibility = View.VISIBLE
        processButton.visibility = View.VISIBLE
        retakeButton.visibility = View.VISIBLE
        status.text = text("选择一种材料，然后处理。结果只留在这个画面。", "Choose a material, then process. The result stays on this screen.")
    }

    private fun process() {
        val source = captured ?: return
        if (busy) return
        busy = true
        processButton.isEnabled = false
        retakeButton.isEnabled = false
        status.text = text("正在处理…", "Processing…")
        val choice = selected
        val seed = System.currentTimeMillis() and 0x7fffffff
        worker.execute {
            runCatching { ExperimentalMaterialShot.develop(source, choice, seed) }.fold(
                onSuccess = { result -> runOnUiThread { if (!isDestroyed) showResult(result) } },
                onFailure = { error -> runOnUiThread { if (!isDestroyed) fail(error) } },
            )
        }
    }

    private fun showResult(result: MaterialPicture) {
        busy = false
        processButton.isEnabled = true
        retakeButton.isEnabled = true
        stillView.setImageBitmap(display(result.image))
        val cover = result.peelSheet
        if (cover != null) {
            peelView.setImageBitmap(display(cover))
            peelView.translationY = 0f
            peelView.visibility = View.VISIBLE
            status.text = text("按住负片向上揭开", "Drag the negative upward to peel it off")
        } else {
            peelView.visibility = View.GONE
            status.text = text("处理完成。这张图没有写入胶卷库或系统相册。", "Done. This picture was not added to a roll or the system album.")
        }
    }

    private fun peel(view: View, event: android.view.MotionEvent): Boolean {
        if (view.visibility != View.VISIBLE) return false
        when (event.actionMasked) {
            android.view.MotionEvent.ACTION_DOWN -> { peelStart = event.rawY; peelOrigin = view.translationY; return true }
            android.view.MotionEvent.ACTION_MOVE -> {
                view.translationY = (peelOrigin + event.rawY - peelStart).coerceAtMost(0f)
                return true
            }
            android.view.MotionEvent.ACTION_UP, android.view.MotionEvent.ACTION_CANCEL -> {
                val off = -view.translationY > view.height * 0.45f
                view.animate().translationY(if (off) -view.height.toFloat() else 0f).setDuration(180).withEndAction {
                    if (off) {
                        view.visibility = View.GONE
                        status.text = text("负片已揭下。这张图没有写入胶卷库或系统相册。", "Negative peeled off. This picture was not added to a roll or the system album.")
                    }
                }
                return true
            }
        }
        return false
    }

    private fun live() {
        busy = false
        captured = null
        stillView.visibility = View.GONE
        peelView.visibility = View.GONE
        peelView.translationY = 0f
        preview.visibility = View.VISIBLE
        shutter.visibility = View.VISIBLE
        shutter.isEnabled = true
        choices.visibility = View.GONE
        (choices.parent as View).visibility = View.GONE
        processButton.visibility = View.GONE
        retakeButton.visibility = View.GONE
        processButton.isEnabled = true
        retakeButton.isEnabled = true
        status.text = text("拍一张，再选择材料", "Take one photo, then choose a material")
        bindCamera()
    }

    private fun fail(error: Throwable) {
        busy = false
        shutter.isEnabled = true
        processButton.isEnabled = true
        retakeButton.isEnabled = true
        status.text = error.message ?: text("处理没有完成", "Processing did not finish")
    }

    private fun paintChoices() {
        for (i in 0 until choices.childCount) {
            val button = choices.getChildAt(i) as Button
            val choice = ExperimentalMaterialShot.choices[i]
            button.setTextColor(if (choice.id == selected) Color.BLACK else Color.WHITE)
            button.setBackgroundColor(if (choice.id == selected) Color.WHITE else 0xFF2A2A2A.toInt())
        }
    }

    private fun display(volume: StillPixels): Bitmap {
        val pixels = IntArray(volume.width * volume.height)
        val rgb = volume.values
        for (i in pixels.indices) {
            val r = srgbByte(rgb[i * 3]); val g = srgbByte(rgb[i * 3 + 1]); val b = srgbByte(rgb[i * 3 + 2])
            pixels[i] = (0xFF shl 24) or (r shl 16) or (g shl 8) or b
        }
        return Bitmap.createBitmap(pixels, volume.width, volume.height, Bitmap.Config.ARGB_8888)
    }

    private fun srgbByte(linear: Float): Int {
        val x = linear.toDouble().coerceIn(0.0, 1.0)
        val encoded = if (x <= 0.0031308) 12.92 * x else 1.055 * x.pow(1.0 / 2.4) - 0.055
        return (encoded.coerceIn(0.0, 1.0) * 255.0 + 0.5).toInt()
    }

    private fun text(zh: String, en: String) = words.text(zh,en)
    private fun dp(value: Int) = (value * resources.displayMetrics.density).toInt()
}
