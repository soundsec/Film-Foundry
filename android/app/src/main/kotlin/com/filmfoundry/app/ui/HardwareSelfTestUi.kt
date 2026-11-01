package com.filmfoundry.app.ui

import android.app.AlertDialog
import android.content.Context
import android.content.Intent
import android.content.IntentFilter
import android.os.BatteryManager
import android.os.Build
import android.os.PowerManager
import android.widget.LinearLayout
import android.widget.ScrollView
import android.widget.TextView
import androidx.activity.ComponentActivity
import com.filmfoundry.app.session.ProcessingWork
import com.filmfoundry.interchange.RecipeJson
import com.filmfoundry.platform.HardwareComputeSelfTest
import org.json.JSONObject
import java.time.LocalDateTime
import java.util.concurrent.CancellationException
import java.util.concurrent.atomic.AtomicBoolean

/** A separate processing lease prevents real jobs or capture competing with the measurement. */
class HardwareSelfTestUi(private val activity: ComponentActivity,private val shell: MobileShell,
    private val idle: ()->Boolean) {
    private val preferences=activity.getSharedPreferences("hardware_self_test",Context.MODE_PRIVATE)
    private val owner=Any()
    private var cancellation: AtomicBoolean?=null
    private var dialog: AlertDialog?=null
    private var closed=false
    private fun copy(zh: String,en: String)=shell.copy(zh,en)
    private fun label(operation: String)=when(operation) {
        "jpeg_srgb"->copy("JPEG 输入线性化","JPEG input linearization")
        "rgb_transmission"->copy("普通 RGB 透射","RGB transmission")
        "scan_output"->copy("扫描输出联合计算","Combined scan output")
        else->copy("31 波长光谱积分","31-wavelength integration")
    }
    private fun format(raw: String): String=runCatching {
        val data=JSONObject(raw);val result=data.getJSONObject("result");val rows=result.getJSONArray("measurements")
        buildString {
            append(data.getString("date"));append(" · ");append(data.getString("build"));append('\n')
            append(copy("GPU 初始化：","GPU initialization: "));append("%.1f ms\n".format(result.getDouble("initialization_ms")))
            for(i in 0 until rows.length()) {
                val row=rows.getJSONObject(i)
                append('\n');append(label(row.getString("operation")));append(" · ${row.getInt("pixels")} px\n")
                append("CPU %.2f ms".format(row.getDouble("cpu_ms")))
                if(row.isNull("gpu_ms")) {
                    append(copy(" · GPU 未通过 / 不可用\n"," · GPU failed / unavailable\n"));append(row.getString("detail"))
                } else {
                    val cpu=row.getDouble("cpu_ms");val gpu=row.getDouble("gpu_ms")
                    append(" · GPU %.2f ms\n".format(gpu))
                    append(copy("精度通过 · ","Accuracy passed · "))
                    append(if(row.getBoolean("recommend_gpu")) copy("此块建议 GPU","GPU recommended for this block") else copy("此块建议 CPU","CPU recommended for this block"))
                    append(" · %.2f×".format(cpu/gpu.coerceAtLeast(.000001)))
                }
                append('\n')
            }
            append('\n');append(copy("温度/热状态（前 → 后）：","Temperature / thermal state (before → after): "))
            append(data.optString("environment_before"));append(" → ");append(data.optString("environment_after"))
        }
    }.getOrElse { copy("旧记录无法读取，请重新测试。","Previous result unreadable; run again.") }
    private fun environment(): String {
        val temperature=activity.registerReceiver(null,IntentFilter(Intent.ACTION_BATTERY_CHANGED))
            ?.getIntExtra(BatteryManager.EXTRA_TEMPERATURE,-1)?.takeIf { it>=0 }?.let { "${it/10.0}°C" } ?: "?"
        val thermal=if(Build.VERSION.SDK_INT>=29) activity.getSystemService(PowerManager::class.java).currentThermalStatus.toString() else "?"
        return "$temperature / $thermal"
    }
    fun open(): AlertDialog? {
        if(closed || dialog?.isShowing==true) return null
        val panel=LinearLayout(activity).apply { orientation=LinearLayout.VERTICAL;setPadding(shell.dp(20),shell.dp(12),shell.dp(20),shell.dp(12)) }
        shell.note(panel,"用合成数据自动检查精度和速度，不读取照片。测试期间暂停其他处理，完成后保存最近一次结果。","Automatically checks accuracy and speed using synthetic data, without reading photos. Other processing is held during testing; the latest result is saved.")
        shell.note(panel,"每项预热2次、交替测7次取中位数；GPU耗时含上传、读回与运行护栏，初始化单列。结果是计算块耗时，不是整张冲洗用时；不会更改加速开关。","Two warmups and seven alternating samples per operation, using medians. GPU timing includes upload, readback and runtime guards; initialization is separate. These are block timings, not whole-photo processing times. Acceleration preferences stay unchanged.")
        shell.note(panel,"RGB 分别测试常用的16384像素块和32768像素块。实际任务还包含显影、读取和编码，会按输入与当次耗时重新择优；这里建议GPU，实际任务仍可能选择CPU。","RGB uses both the usual 16384-pixel block and a 32768-pixel block. Real tasks also develop, read and encode; they select again using the actual input and timing. A GPU recommendation here may still result in CPU selection during a real task.")
        shell.note(panel,"扫描输出联合计算：归一化应用、曝光与灯光颜色、对比度、sRGB、RGB曲线、亮度曲线，一次上传与读回。全图统计和文件压缩仍由CPU完成。","Combined scan output applies normalization, exposure and lamp color, contrast, sRGB, RGB curves and tone curve in one upload/readback. Whole-image statistics and file compression remain on CPU.")
        val vulkan=activity.packageManager.systemAvailableFeatures.firstOrNull { it.name=="android.hardware.vulkan.version" }?.version
        val version=vulkan?.let { "${it ushr 22}.${(it ushr 12) and 1023}.${it and 4095}" }
        shell.note(panel,"当前实测后端：OpenGL ES 3.1。Vulkan：${version?.let { "系统声明 $it" } ?: "系统未声明"}，本应用尚未实现其计算后端。",
            "Test backend: OpenGL ES 3.1. Vulkan: ${version?.let { "system declares $it" } ?: "not declared"}; this app has no Vulkan compute backend yet.")
        val result=TextView(activity).apply { text=preferences.getString("last_result",null)?.let(::format) ?: copy("尚未测试。","Not tested yet.");setTextIsSelectable(true) };panel.addView(result)
        val view=AlertDialog.Builder(activity).setTitle(copy("硬件加速自测","Hardware acceleration self-test"))
            .setView(ScrollView(activity).apply { addView(panel) }).setPositiveButton(copy("开始测试","Run test"),null)
            .setNegativeButton(copy("关闭","Close"),null).create()
        dialog=view
        view.setOnDismissListener { cancellation?.set(true);dialog=null }
        view.setOnShowListener { view.getButton(AlertDialog.BUTTON_POSITIVE).setOnClickListener {
            if(!idle() || ProcessingWork.busy || cancellation!=null) {
                result.text=copy("请先结束拍摄、冲洗或其他任务。","Finish capture, processing or other tasks first.");return@setOnClickListener
            }
            val cancel=AtomicBoolean(false);cancellation=cancel
            val completedRecord=java.util.concurrent.atomic.AtomicReference<String>()
            val before=environment()
            val date=LocalDateTime.now().toString()
            val build=activity.packageManager.getPackageInfo(activity.packageName,0).versionName
            view.getButton(AlertDialog.BUTTON_POSITIVE).isEnabled=false
            view.getButton(AlertDialog.BUTTON_NEGATIVE).text=copy("取消测试","Cancel test")
            result.text=copy("正在准备…","Preparing…")
            val title=copy("硬件加速自测","Hardware acceleration self-test")
            ProcessingWork.launch(activity,owner,title,{cancel.set(true)},body={
                runCatching {
                    val report=HardwareComputeSelfTest().run({cancel.get()}) { operation ->
                        val message=copy("正在测试：","Testing: ")+label(operation)
                        ProcessingWork.progress(owner,message)
                        activity.runOnUiThread { if(!closed && view.isShowing) result.text=message }
                    }
                    if(cancel.get()) throw CancellationException("Self-test cancelled")
                    val raw=RecipeJson.text(mapOf("date" to date,"build" to build,"device" to "${Build.MANUFACTURER} ${Build.MODEL}",
                        "environment_before" to before,"environment_after" to environment(),"result" to report.values()))
                    completedRecord.set(raw)
                    copy("硬件自测完成","Hardware self-test completed")
                }
            },complete={ outcome ->
                // Commit on the UI thread, after any queued cancellation, so cancellation keeps the previous result.
                if(outcome.isSuccess && !cancel.get()) completedRecord.get()?.let { preferences.edit().putString("last_result",it).apply() }
                cancellation=null
                if(!closed && view.isShowing) {
                    result.text=if(cancel.get()) copy("测试已取消，保留上次结果。","Test cancelled; previous result retained.")
                        else outcome.fold({ format(preferences.getString("last_result",null).orEmpty()) }) { copy("测试未完成：","Test incomplete: ")+it.message }
                    view.getButton(AlertDialog.BUTTON_POSITIVE).isEnabled=true
                    view.getButton(AlertDialog.BUTTON_NEGATIVE).text=copy("关闭","Close")
                }
            })
        } }
        shell.decorate(panel);view.show()
        return view
    }
    fun close() { closed=true;cancellation?.set(true);dialog?.dismiss();dialog=null }
}
