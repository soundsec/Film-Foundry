package com.filmfoundry.app.ui

import android.app.AlertDialog
import android.content.Context
import android.content.Intent
import android.os.Build
import android.provider.DocumentsContract
import android.widget.*
import androidx.activity.ComponentActivity
import androidx.activity.result.contract.ActivityResultContracts
import com.filmfoundry.app.session.*
import com.filmfoundry.platform.*
import com.filmfoundry.workflow.*

/** Destination/permission controls; bytes and ownership stay with application export sessions. */
class LibraryExportUi(private val activity: ComponentActivity,private val shell: MobileShell,
    private val planner: LibraryExportPlanner,private val exports: LibraryExportSession,
    private val idle: ()->Boolean,private val status: (String)->Unit,
    private val runJob: (String,suspend (WorkflowControl)->String)->Unit) {
    private val prefs=activity.getSharedPreferences("library_export",Context.MODE_PRIVATE)
    private var checkingFormats=false
    private var afterPermission: (()->Unit)?=null
    private val permission=activity.registerForActivityResult(ActivityResultContracts.RequestPermission()) { granted ->
        val action=afterPermission;afterPermission=null
        if(granted) action?.invoke() else status(copy("未授予相册权限，操作尚未开始","Gallery permission denied; operation has not started"))
    }
    private val folder=activity.registerForActivityResult(ActivityResultContracts.OpenDocumentTree()) { uri ->
        val text=prefs.getString("pending",null);prefs.edit().remove("pending").apply()
        if(uri!=null && text!=null && idle()) {
            val request=runCatching { LibraryExportRequest.decode(text) }.getOrElse { status(it.message.orEmpty());return@registerForActivityResult }
            runJob(copy("正在导出文件…","Exporting files…")) { control ->
                val items=planner.items(request,control)
                val parent=DocumentsContract.buildDocumentUriUsingTree(uri,DocumentsContract.getTreeDocumentId(uri))
                val outcome=exports.run(items,control,::progress) { item,writer ->
                    // Providers create a new document. Never open a pre-existing user file for overwrite.
                    val target=DocumentsContract.createDocument(activity.contentResolver,parent,item.mime,item.name)
                        ?: error("Cannot create export file")
                    try {
                        activity.contentResolver.openOutputStream(target,"w")?.use(writer) ?: error("Cannot open export file")
                        control.checkCancel()
                    } catch(error: Throwable) { runCatching { DocumentsContract.deleteDocument(activity.contentResolver,target) };throw error }
                }
                summary(outcome)
            }
        }
    }
    private fun copy(zh: String,en: String)=shell.copy(zh,en)
    private fun progress(done: Int,total: Int) { activity.runOnUiThread { if(!activity.isDestroyed) status(copy("已导出 $done/$total","Exported $done/$total")) } }
    fun summary(outcome: LibraryExportSession.Outcome)=copy("导出成功 ${outcome.saved}，失败 ${outcome.failed.size}","Exported ${outcome.saved}, failed ${outcome.failed.size}")+
        (if(outcome.cancelled) copy(" · 已停止，已导出的保留"," · Stopped; completed exports retained") else "")+
        (if(outcome.failed.isNotEmpty()) "\n"+outcome.failed.take(3).joinToString("\n") else "")
    fun withGalleryPermission(action: ()->Unit) {
        if(!idle()) return
        if(Build.VERSION.SDK_INT<=28 && activity.checkSelfPermission(android.Manifest.permission.WRITE_EXTERNAL_STORAGE)!=android.content.pm.PackageManager.PERMISSION_GRANTED) {
            afterPermission=action;permission.launch(android.Manifest.permission.WRITE_EXTERNAL_STORAGE)
        } else action()
    }
    fun confirmExecution(title: String,info: String,albumAvailable: Boolean?=true,initialAlbum: Boolean=false,outputRecipe: Map<String,Any?>?=null,run: (Boolean)->Unit) {
        if(!idle()) return
        val panel=LinearLayout(activity).apply { orientation=LinearLayout.VERTICAL;setPadding(shell.dp(20),shell.dp(8),shell.dp(20),0) }
        shell.note(panel,info,info)
        val album=CheckBox(activity).apply { text=copy("完成后直接导出到系统相册","Export to system gallery when finished");isChecked=initialAlbum && albumAvailable==true;isEnabled=albumAvailable==true }
        if(albumAvailable!=null) panel.addView(album)
        if(albumAvailable==false) shell.note(panel,"开启自动扫描后可导出照片到相册。此操作只保存冲洗底片。","Enable automatic scanning to export photos to the gallery. This operation saves developed film only.")
        shell.decorate(panel)
        AlertDialog.Builder(activity).setTitle(title).setView(ScrollView(activity).apply { addView(panel) }).setNegativeButton(copy("取消","Cancel"),null)
            .setPositiveButton(copy("开始执行","Start")) { _,_->if(idle()) {
                if(album.isChecked) chooseOutput(galleryOnly=true,allowed=outputRecipe?.let { ScanDelivery.allowed(ScanDelivery.format(it),ScanDelivery.bits(it)) } ?: ScanImageFormat.entries.toSet()) { format,quality,_ ->
                    activity.getSharedPreferences("capture_conditions",0).edit().putString("confirmed_export_format",format.name).putInt("confirmed_export_jpeg_quality",quality).apply()
                    withGalleryPermission { run(true) }
                } else run(false)
            } }.show()
    }
    fun files(request: LibraryExportRequest) {
        if(!idle()) return
        prefs.edit().putString("pending",request.encode()).apply();folder.launch(null)
    }
    private fun chooseOutput(galleryOnly: Boolean=false,allowed: Set<ScanImageFormat>,count: Int?=null,chosen: (ScanImageFormat,Int,Boolean)->Unit) {
        if(!idle()) return
        val choices=ScanImageFormat.entries.filter { it in allowed && (!galleryOnly || it!=ScanImageFormat.TIFF) }
        if(choices.isEmpty()) { status(copy("没有兼容的输出格式","No compatible output format"));return }
        var selected: ScanImageFormat?=null
        var jpegQuality=ScanOutputSettings.jpegLevels.minBy { kotlin.math.abs(it-prefs.getInt("jpeg_quality",100)) }
        val panel=LinearLayout(activity).apply { orientation=LinearLayout.VERTICAL;setPadding(shell.dp(20),shell.dp(8),shell.dp(20),shell.dp(8)) }
        if(count!=null) shell.note(panel,"导出所选 $count 张图片，软件内原件保留。","Export $count selected images; originals in the app stay.")
        val formats=RadioGroup(activity);panel.addView(formats)
        val quality=LinearLayout(activity).apply { orientation=LinearLayout.VERTICAL;visibility=android.view.View.GONE }
        OutputQualityControls.jpeg(activity,shell,quality,jpegQuality) { jpegQuality=it }
        shell.note(quality,"已有JPG未旋转时直接复制原件。仅转换或旋转时重新编码，质量不高于原JPG记录值。","Existing JPEGs without rotation are copied unchanged. Conversion or rotation re-encodes, capped at the original JPEG's recorded quality.")
        panel.addView(quality)
        val detail=TextView(activity);panel.addView(detail)
        shell.note(panel,"保存到","Save to")
        val destinations=RadioGroup(activity)
        val gallery=RadioButton(activity).apply { id=android.view.View.generateViewId();text=copy("系统相册 · Pictures/FilmFoundry","Gallery · Pictures/FilmFoundry") }
        val files=RadioButton(activity).apply { id=android.view.View.generateViewId();text=copy("图片文件 · 选择文件夹","Image files · choose folder") }
        destinations.addView(gallery);if(!galleryOnly) destinations.addView(files)
        destinations.check(gallery.id);panel.addView(destinations)
        val scroll=ScrollView(activity).apply { addView(panel) }
        val dialog=AlertDialog.Builder(activity).setTitle(copy("格式、质量与保存位置","Format, quality & destination"))
            .setView(scroll).setNegativeButton(copy("取消","Cancel"),null)
            .setPositiveButton(copy(if(galleryOnly) "确认并执行" else "开始导出",if(galleryOnly) "Confirm & start" else "Export"),null).create()
        for(format in choices) formats.addView(RadioButton(activity).apply {
            id=android.view.View.generateViewId()
            text=when(format) {
                ScanImageFormat.JPEG -> copy("JPG · 8位有损","JPG · 8-bit lossy")
                ScanImageFormat.PNG -> copy("PNG · 无损","PNG · lossless")
                ScanImageFormat.TIFF -> copy("TIFF · 16位无损文件","TIFF · 16-bit lossless file")
            }
            setOnClickListener {
                selected=format;quality.visibility=if(format==ScanImageFormat.JPEG) android.view.View.VISIBLE else android.view.View.GONE
                gallery.isEnabled=format!=ScanImageFormat.TIFF
                if(format==ScanImageFormat.TIFF) destinations.check(files.id)
                detail.text=when(format) {
                    ScanImageFormat.JPEG -> ""
                    ScanImageFormat.PNG -> copy("保留源片位深与尺寸。","Retains source bit depth and dimensions.")
                    ScanImageFormat.TIFF -> copy("保留16位扫描数据；TIFF保存到文件夹。","Retains 16-bit scan data; TIFF is saved to a folder.")
                }
                dialog.getButton(AlertDialog.BUTTON_POSITIVE).isEnabled=true
            }
        })
        // Deliberately no format preselection: every export needs an explicit choice.
        dialog.setOnShowListener {
            dialog.getButton(AlertDialog.BUTTON_POSITIVE).apply {
                isEnabled=false
                setOnClickListener { selected?.let { format -> if(idle()) {
                    prefs.edit().putInt("jpeg_quality",jpegQuality).apply()
                    dialog.dismiss();chosen(format,jpegQuality,files.isChecked)
                } } }
            }
        };dialog.show()
    }
    fun images(request: LibraryExportRequest) {
        if(!idle() || checkingFormats) return
        checkingFormats=true
        status(copy("正在检查导出格式…","Checking export formats…"))
        Thread({
            val allowed=runCatching { planner.allowedFormats(request) }
            activity.runOnUiThread {
                checkingFormats=false
                if(!activity.isDestroyed && idle()) allowed.fold({ formats ->
                    chooseOutput(allowed=formats,count=request.ids.size) { format,quality,toFiles ->
                        val frozen=request.copy(format=format.name,jpegQuality=quality)
                        if(toFiles) files(frozen) else withGalleryPermission {
                            runJob(copy("正在导出图片…","Exporting images…")) { control ->
                                summary(exports.gallery(planner.items(frozen,control),control,::progress))
                            }
                        }
                    }
                },{ status(it.message.orEmpty()) })
            }
        },"FilmFoundry-export-check").start()
    }
}
