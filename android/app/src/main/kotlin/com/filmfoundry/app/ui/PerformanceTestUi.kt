package com.filmfoundry.app.ui

import android.app.AlertDialog
import android.widget.*
import androidx.activity.ComponentActivity
import androidx.activity.result.contract.ActivityResultContracts
import com.filmfoundry.app.session.DevicePerformance
import java.time.LocalDateTime
import java.time.format.DateTimeFormatter

class PerformanceTestUi(private val activity: ComponentActivity,private val shell: MobileShell,
    private val recorder: DevicePerformance,private val idle: ()->Boolean,private val status: (String)->Unit) {
    private fun copy(zh: String,en: String)=shell.copy(zh,en)
    private val export=activity.registerForActivityResult(ActivityResultContracts.CreateDocument("application/json")) { uri ->
        if(uri!=null) Thread({
            val result=runCatching {
                val bytes=recorder.export().toByteArray(Charsets.UTF_8)
                activity.contentResolver.openOutputStream(uri,"w")?.use { it.write(bytes) } ?: error("Cannot open output")
            }
            activity.runOnUiThread { if(!activity.isDestroyed) status(result.fold({copy("测试数据已导出","Test data exported")},{copy("导出失败：","Export failed: ")+it.message})) }
        },"FilmFoundry-performance-export").start()
    }
    fun open() {
        if(!idle()) return
        val panel=LinearLayout(activity).apply { orientation=LinearLayout.VERTICAL;setPadding(shell.dp(20),shell.dp(12),shell.dp(20),shell.dp(12)) }
        val enabled=CheckBox(activity).apply {
            text=copy("记录实机性能测试","Record device performance tests");isChecked=recorder.enabled
            setOnCheckedChangeListener { _,checked -> recorder.enabled=checked }
        };panel.addView(enabled)
        shell.note(panel,"开启后正常冲洗、扫描、导出即可自动记录。下一次任务生效，最多保留20次；不记录图片、文件路径或完整配方。","Enable, then develop, scan or export normally. Recording starts with the next task and retains 20 runs. Images, file paths and full recipes are excluded.")
        val handoff=CheckBox(activity).apply {
            text=copy("冲洗后直接交给扫描（新优化）","Pass developed rows directly to scanning");isChecked=recorder.testHandoff;isEnabled=recorder.enabled
            setOnCheckedChangeListener { _,checked->recorder.testHandoff=checked }
        };panel.addView(handoff)
        enabled.setOnCheckedChangeListener { _,checked->recorder.enabled=checked;handoff.isEnabled=checked }
        shell.note(panel,"交接对比需要在冲洗准备页开启自动扫描。","Enable automatic scanning on the development preparation page to compare handoff.")
        shell.note(panel,"对比方法：优先选已有潜影的卷，新建两次冲洗批次，保持冲洗和扫描设置相同，只切换上方交接开关；不要用跳过已完成照片的续跑结果比较，也不要把首次形成潜影与已有潜影混比。CPU并行可在上一级切换，保持充电状态与温度接近。关闭测试模式后默认使用新优化。","Use a roll with existing latent frames and create two new batches with identical settings, changing only handoff. Do not compare resumed jobs that skip photos or mix first-time exposure formation with existing latents. CPU workers can be changed on the previous screen; keep charging and temperature comparable. Normal mode uses the optimization.")
        shell.note(panel,"自动收集阶段耗时、任务状态、每5秒内存/进程CPU时间/电池温度/热状态。阶段可能嵌套，不能直接相加当总耗时。","Collects stage timings, task status and memory/process CPU time/battery temperature/thermal status every 5 seconds. Stages may be nested; their sum is not total elapsed time.")
        val records=recorder.records()
        shell.note(panel,"已记录 ${records.size} 次任务","${records.size} recorded runs")
        records.take(5).forEach { record ->
            val failures=((record["event_totals"] as? Map<*,*>)?.get("scan_failed") as? Number)?.toInt() ?: 0
            val exportFailures=(((record["stage_timings"] as? Map<*,*>)?.get("export.file_failed") as? Map<*,*>)?.get("count") as? Number)?.toInt() ?: 0
            val state=when(record["status"]) { "completed"->if(failures+exportFailures>0) copy("完成 · 扫描失败 $failures / 导出失败 $exportFailures","Completed · $failures scan / $exportFailures export failures") else copy("完成","Completed");"paused"->copy("暂停","Paused");"failed"->copy("失败","Failed");"interrupted"->copy("中断","Interrupted");else->copy("记录中","Recording") }
            val config=record["configuration"] as? Map<*,*>
            val operations=(record["operations"] as? List<*>).orEmpty().joinToString(" / ") { when(it) {
                "expose"->copy("潜影","Exposure");"develop_latent"->copy("冲洗","Development");"scan"->copy("扫描","Scan");"export"->copy("导出","Export");else->copy("任务","Task")
            } }
            val seconds=((record["wall_ms"] as? Number)?.toDouble() ?: 0.0)/1000.0
            val line="${config?.get("started_at")} · $state · ${"%.1f".format(seconds)} s\n$operations · CPU ${config?.get("cpu_workers")} · "+copy("交接 ","handoff ")+config?.get("optical_handoff")
            shell.note(panel,line,line)
        }
        recorder.lastError?.let { shell.note(panel,"测试记录写入异常：$it","Test recording error: $it") }
        shell.decorate(panel)
        AlertDialog.Builder(activity).setTitle(copy("实机性能测试","Device performance tests"))
            .setView(ScrollView(activity).apply { addView(panel) }).setNegativeButton(copy("关闭","Close"),null)
            .setPositiveButton(copy("导出数据","Export")) { _,_->if(idle()) export.launch("FilmFoundry-performance-${LocalDateTime.now().format(DateTimeFormatter.ofPattern("yyyyMMdd-HHmmss"))}.json") }
            .setNeutralButton(copy("清空记录","Clear")) { _,_->
                if(idle()) AlertDialog.Builder(activity).setMessage(copy("清空本机性能测试记录？照片和底片保留。","Clear local performance records? Photos and film stay."))
                    .setNegativeButton(copy("取消","Cancel"),null).setPositiveButton(copy("清空","Clear")) { _,_->
                        if(idle()) runCatching { recorder.clear() }.fold({status(copy("测试记录已清空","Test records cleared"))},{status(it.message.orEmpty())})
                    }.show()
            }.show()
    }
}
