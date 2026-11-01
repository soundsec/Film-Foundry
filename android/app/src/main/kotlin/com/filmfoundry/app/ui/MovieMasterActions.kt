package com.filmfoundry.app.ui

import android.app.AlertDialog
import android.graphics.Bitmap
import android.provider.DocumentsContract
import android.text.InputType
import android.widget.*
import androidx.activity.ComponentActivity
import androidx.activity.result.contract.ActivityResultContracts
import com.filmfoundry.interchange.*
import com.filmfoundry.platform.AndroidMovieFrames
import com.filmfoundry.platform.MovieSource
import com.filmfoundry.workflow.*
import java.io.File
import java.util.UUID
import java.util.concurrent.Executor
import java.util.concurrent.atomic.AtomicBoolean
import kotlinx.coroutines.runBlocking

/** Thin UI wrapper over the headless material-master session. */
class MovieMasterActions(private val activity: ComponentActivity,column: LinearLayout,private val worker: Executor,
    private val sources: ()->List<MovieSource>,private val busy: (Boolean)->Unit,private val statusChanged: (String)->Unit,
    private val available: ()->Boolean={true}) {
    private val taskOwner=Any()
    private val status: (String)->Unit={ message ->
        com.filmfoundry.app.session.ProcessingWork.progress(taskOwner,message)
        if(!activity.isDestroyed) statusChanged(message)
    }
    private val session=MovieMasterSession()
    private val transport=MovieMasterZip()
    private val library=File(activity.filesDir,"material_masters")
    private val cancelled=AtomicBoolean(false)
    private var running=false
    private var export: File?=null
    private val recipe=EditText(activity)
    private val preview=ImageView(activity)
    private val expose=Button(activity)
    private val browse=Button(activity)
    private val importZip=Button(activity)
    private val cancel=Button(activity)
    private fun copy(zh: String,en: String)=if(activity.resources.configuration.locales[0].language == "zh") zh else en
    private val importMaster=activity.registerForActivityResult(ActivityResultContracts.OpenDocument()) { uri ->
        if(uri != null) job { control ->
            val target=File(library,UUID.randomUUID().toString())
            activity.contentResolver.openInputStream(uri)?.use { transport.import(it,target,control) }
                ?: error("Cannot open master ZIP")
            copy("材料母带已导入：","Material master imported: ")+target.name
        }
    }
    private val exportMaster=activity.registerForActivityResult(ActivityResultContracts.CreateDocument("application/zip")) { uri ->
        val root=export;export=null
        if(uri != null && root != null) job { control ->
            try {
                activity.contentResolver.openOutputStream(uri,"w")?.use { output ->
                    transport.export(root,output,control)
                } ?: error("Cannot open master export destination")
                copy("材料母带已导出（JSON＋逐帧NPZ）","Material master exported (JSON + frame NPZ)")
            } catch(error: Throwable) {
                runCatching { DocumentsContract.deleteDocument(activity.contentResolver,uri) }
                throw error
            }
        }
    }
    init {
        column.addView(TextView(activity).apply { text=copy(
            "材料母带：逐帧NPZ保留完整时间轴，占用可能很大。冲洗另存结果，观察不改母带。声音留在采集源，不进入材料母带。",
            "Material masters: NPZ per frame, original timestamps, potentially large storage. Development creates a separate result; observation is read-only. Audio remains in the source.") })
        recipe.hint=copy("FFC2 配方（留空使用内核默认）","FFC2 recipe (empty uses kernel defaults)")
        recipe.setSingleLine(true);column.addView(recipe)
        expose.text=copy("视频源 → 潜影母带","Video source → latent master")
        expose.setOnClickListener {
            val rows=sources()
            if(rows.isEmpty()) status(copy("请先导入或拍摄视频","Import or record a source first"))
            else AlertDialog.Builder(activity).setItems(rows.map { it.displayName }.toTypedArray()) { _,index ->
                val source=rows[index]
                val frozen=runCatching { readRecipe() }.getOrElse { status(it.message.orEmpty());return@setItems }
                job { control ->
                    val target=File(library,UUID.randomUUID().toString())
                    session.expose(AndroidMovieFrames(source.file,control),target,frozen,source=mapOf(
                        "kind" to "video","name" to source.displayName,"color_ingest" to "android_sdr_bt709_linear_rows_v1_unspecified_assumed_bt709_limited",
                        "audio" to "not_part_of_material_master"),control=control)
                    copy("潜影母带已保存：","Latent master saved: ")+target.name
                }
            }.show()
        }
        column.addView(expose)
        importZip.text=copy("导入材料母带 ZIP","Import material master ZIP")
        importZip.setOnClickListener {
            if(!available() || running) status(copy("请先结束当前任务","Finish the current task first"))
            else importMaster.launch(arrayOf("application/zip","application/x-zip-compressed","application/octet-stream"))
        }
        column.addView(importZip)
        browse.text=copy("材料母带库：冲洗／观察／导出","Master library: develop / observe / export")
        browse.setOnClickListener {
            job { control ->
                session.recover(library,control)
                val roots=library.listFiles().orEmpty().filter { it.isDirectory && !it.name.startsWith(".") && File(it,MovieMasterCodec.MANIFEST).isFile }
                val choices=roots.map { session.read(it) }
                activity.runOnUiThread { if(!activity.isDestroyed) {
                    if(roots.isEmpty()) status(copy("尚无材料母带","No material masters yet"))
                    else AlertDialog.Builder(activity).setItems(roots.mapIndexed { i,f ->
                        "${choices[i].stage} · ${choices[i].frames.size} frames · ${f.name.take(8)}"
                    }.toTypedArray()) { _,index -> chooseMaster(roots[index],choices[index]) }.show()
                } }
                copy("母带库已读取","Master library loaded")
            }
        }
        column.addView(browse)
        cancel.text=copy("取消母带任务","Cancel master task");cancel.isEnabled=false
        cancel.setOnClickListener { cancelled.set(true);cancel.isEnabled=false }
        column.addView(cancel)
        preview.adjustViewBounds=true;preview.scaleType=ImageView.ScaleType.FIT_CENTER
        column.addView(preview,LinearLayout.LayoutParams(LinearLayout.LayoutParams.MATCH_PARENT,(260*activity.resources.displayMetrics.density).toInt()))
    }
    private fun readRecipe(): Map<String,Any?> = recipe.text.toString().trim().let { if(it.isEmpty()) emptyMap() else RecipeShareCodec.decode(it) }
    private fun chooseMaster(root: File,document: MovieMaterialMaster) {
        val form=document.stage == "exposed_latent"
        val actions=arrayOf(if(form) copy("冲洗为新光学母带","Develop new optical master") else copy("只读观察所选帧","Observe a frame"),copy("导出完整材料母带ZIP","Export complete material master ZIP"))
        AlertDialog.Builder(activity).setItems(actions) { _,which ->
            if(which == 1) { export=root;exportMaster.launch(root.name+".material-master.zip") }
            else if(form) {
                val frozen=runCatching { readRecipe() }.getOrElse { status(it.message.orEmpty());return@setItems }
                job { control ->
                    val target=File(library,UUID.randomUUID().toString())
                    session.develop(root,target,frozen,control)
                    copy("光学母带已另存，原潜影保持不变：","Optical master saved; source latent unchanged: ")+target.name
                }
            } else {
                val index=EditText(activity).apply { inputType=InputType.TYPE_CLASS_NUMBER;setText("1") }
                val viewer=CheckBox(activity).apply { text=copy("透射灯台（关闭则扫描解释）","Transmission viewer (off: scanner interpretation)") }
                val formView=LinearLayout(activity).apply { orientation=LinearLayout.VERTICAL;addView(index);addView(viewer) }
                AlertDialog.Builder(activity).setTitle("1–${document.frames.size}").setView(formView)
                    .setPositiveButton(copy("观察","Observe")) { _,_ ->
                        val selected=index.text.toString().toIntOrNull()
                        if(selected == null || selected !in 1..document.frames.size) { status(copy("帧序号无效","Invalid frame index"));return@setPositiveButton }
                        val frozen=runCatching { readRecipe() }.getOrElse { status(it.message.orEmpty());return@setPositiveButton }
                        val transmission=viewer.isChecked
                        job { control ->
                            val observation=session.observe(root,selected,frozen,transmission,control)
                            val pixels=IntArray(observation.width*observation.height) { p ->
                                fun channel(c: Int)=(observation.rgb[p*3+c].coerceIn(0f,1f)*255).toInt()
                                (255 shl 24) or (channel(0) shl 16) or (channel(1) shl 8) or channel(2)
                            }
                            activity.runOnUiThread { if(!activity.isDestroyed) preview.setImageBitmap(Bitmap.createBitmap(pixels,observation.width,observation.height,Bitmap.Config.ARGB_8888)) }
                            copy("只读观察完成，母带未改写","Observation complete; material master unchanged")
                        }
                    }.show()
            }
        }.show()
    }
    private fun job(work: suspend (WorkflowControl)->String) {
        if(running) return
        if(!available()) { status(copy("请先结束当前录像或导入任务","Finish recording/import before processing a master"));return }
        running=true;cancelled.set(false);busy(true)
        expose.isEnabled=false;browse.isEnabled=false;importZip.isEnabled=false;recipe.isEnabled=false;cancel.isEnabled=true
        status(copy("正在处理材料母带…","Processing material master…"))
        com.filmfoundry.app.session.ProcessingWork.launch(activity,taskOwner,copy("处理材料母带","Processing material master"),{ cancelled.set(true) },body={
            var frame=0
            val control=WorkflowControl(cancelRequested=cancelled::get,onProgress={ command,stage ->
                if(stage == "exposing" || stage == "developing" || (command == "movie_import" && stage == "loading")) {
                    val number=++frame
                    val message=copy("正在处理第 $number 帧","Processing frame $number")
                    com.filmfoundry.app.session.ProcessingWork.progress(taskOwner,message)
                    activity.runOnUiThread { if(!activity.isDestroyed) status(message) }
                }
            })
            runCatching { runBlocking { work(control) } }
        },complete={ result ->
            if(!activity.isDestroyed) {
                running=false;busy(false);expose.isEnabled=true;browse.isEnabled=true;importZip.isEnabled=true;recipe.isEnabled=true;cancel.isEnabled=false
                status(result.fold({it},{it.message.orEmpty()}))
            }
        })
    }
    fun cancel() { if(running) cancelled.set(true) }
}
