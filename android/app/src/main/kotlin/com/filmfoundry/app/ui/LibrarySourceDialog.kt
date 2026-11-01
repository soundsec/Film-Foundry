package com.filmfoundry.app.ui

import android.app.AlertDialog
import android.widget.LinearLayout
import android.widget.ScrollView
import androidx.activity.ComponentActivity
import com.filmfoundry.app.session.LibraryOrigin
import com.filmfoundry.workflow.ResourceAvailability
import java.util.concurrent.Executor

/** Metadata lookup does not cancel the viewer or acquire the processing-job state. */
class LibrarySourceDialog(private val activity: ComponentActivity,private val shell: MobileShell,private val worker: Executor) {
    fun show(load: ()->LibraryOrigin,openRoll: ()->Unit,openBatch: ()->Unit,openFilm: ()->Unit) {
        val panel=LinearLayout(activity).apply { orientation=LinearLayout.VERTICAL;setPadding(shell.dp(20),shell.dp(8),shell.dp(20),shell.dp(12)) }
        shell.note(panel,"正在查找来源…","Loading source history…")
        val dialog=AlertDialog.Builder(activity).setTitle(shell.copy("来源记录","Source history"))
            .setView(ScrollView(activity).apply { addView(panel) }).setNegativeButton(shell.copy("关闭","Close"),null).create()
        dialog.show();shell.decorate(panel)
        worker.execute {
            val result=runCatching(load)
            activity.runOnUiThread {
                if(activity.isDestroyed || !dialog.isShowing) return@runOnUiThread
                panel.removeAllViews()
                result.fold({ o ->
                    val en=shell.copy("zh","en")=="en"
                    shell.note(panel,o.rollLabel,o.rollLabel)
                    shell.note(panel,LibraryInfoPresentation.frame(o.frameIndex,en),LibraryInfoPresentation.frame(o.frameIndex,en))
                    shell.note(panel,"曝光 · "+LibraryInfoPresentation.availability(o.exposure,false),"Exposure · "+LibraryInfoPresentation.availability(o.exposure,true))
                    if(o.rollListed) shell.action(panel,"打开胶卷","Open roll") { dialog.dismiss();openRoll() }
                    else shell.note(panel,"胶卷已从胶卷库移除。","The roll has been removed from Rolls.")
                    shell.note(panel,LibraryInfoPresentation.batch(o.batchId,en),LibraryInfoPresentation.batch(o.batchId,en))
                    shell.note(panel,LibraryInfoPresentation.availability(o.developed,false),LibraryInfoPresentation.availability(o.developed,true))
                    if(o.developed!=ResourceAvailability.REMOVED) shell.action(panel,"打开冲洗批次","Open development batch") { dialog.dismiss();openBatch() }
                    if(o.developed==ResourceAvailability.AVAILABLE) shell.action(panel,"查看来源底片","View source film") { dialog.dismiss();openFilm() }
                    o.scanId?.let { id ->
                        shell.note(panel,LibraryInfoPresentation.scan(id,en),LibraryInfoPresentation.scan(id,en))
                        shell.note(panel,LibraryInfoPresentation.time(com.filmfoundry.app.session.LibraryFacts(time=o.scanTime),en),LibraryInfoPresentation.time(com.filmfoundry.app.session.LibraryFacts(time=o.scanTime),en))
                    }
                    shell.note(panel,"胶卷、底片、照片独立保留；缺少来源不会影响当前图像。","Rolls, developed film and photos are kept independently. Missing sources do not alter this image.")
                },{ shell.note(panel,"来源记录读取失败，可关闭后重试。","Could not read source history. Close and retry.") })
                shell.decorate(panel)
            }
        }
    }
}
