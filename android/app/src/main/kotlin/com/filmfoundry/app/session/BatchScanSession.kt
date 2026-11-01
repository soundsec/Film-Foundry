package com.filmfoundry.app.session

import com.filmfoundry.interchange.RecipeJson
import com.filmfoundry.interchange.RecipeShareCodec
import com.filmfoundry.platform.*
import com.filmfoundry.workflow.*
import java.io.OutputStream
import java.util.UUID

data class ScanTaskFrame(val resultId: String,val opticalPath: String,val frameIndex: Int,val scanId: String,val done: Boolean=false)
data class ScanTask(val id: String,val rollId: String,val recipeShare: String,val observationModel: String,
    val frames: List<ScanTaskFrame>,val status: String="ready",val error: String?=null) {
    val completed get()=frames.count { it.done }
    val unfinished get()=status !in listOf("completed","abandoned")
}

/** App-only journal: it holds identifiers and a frozen observation recipe, never material or pixel arrays. */
class PrivateScanTaskStore(private val storage: StorageAdapter) {
    private fun base(rollId: String): String {
        require(rollId.isNotBlank() && rollId !in listOf(".","..") && rollId.none { it in "/\\:" })
        return "scan_tasks/$rollId"
    }
    private fun path(rollId: String,id: String): String { require(UUID.fromString(id).toString()==id);return "${base(rollId)}/$id.json" }
    private fun objectAt(path: String)=storage.readPrivate(path) { input ->
        val output=java.io.ByteArrayOutputStream();val buffer=ByteArray(8192)
        while(true) { val count=input.read(buffer);if(count<0) break;require(output.size()+count<=262144);output.write(buffer,0,count) }
        RecipeJson.objectMap(output.toString(Charsets.UTF_8.name()))
    }
    fun latest(rollId: String): ScanTask? {
        val pointer="${base(rollId)}/latest.json"
        if(!storage.existsPrivate(pointer)) return null
        val id=objectAt(pointer)["id"] as String
        return read(rollId,id)
    }
    fun read(rollId: String,id: String): ScanTask {
        val raw=objectAt(path(rollId,id));require(raw["model"]=="phone_batch_scan_v1" && raw["id"]==id && raw["roll_id"]==rollId)
        val frames=(raw["frames"] as List<*>).map { value -> val f=value as Map<*,*>
            ScanTaskFrame(f["result_id"] as String,f["optical_path"] as String,(f["frame_index"] as Number).toInt(),f["scan_id"] as String,f["done"]==true)
        }
        require(frames.isNotEmpty() && frames.size<=1000 && frames.map { it.resultId }.distinct().size==frames.size && frames.map { it.scanId }.distinct().size==frames.size)
        frames.forEach { require(it.frameIndex>0 && UUID.fromString(it.scanId).toString()==it.scanId) }
        val state=raw["status"] as String;require(state in listOf("ready","running","paused","failed","completed","abandoned"))
        return ScanTask(id,rollId,raw["recipe_share"] as String,raw["observation_model"] as String,frames,state,raw["error"] as? String)
    }
    fun create(task: ScanTask) {
        require(latest(task.rollId)?.unfinished!=true) { "Resume or end the unfinished scan task first" }
        require(!storage.existsPrivate(path(task.rollId,task.id)))
        write(task);storage.writePrivate("${base(task.rollId)}/latest.json",RecipeJson.text(mapOf("id" to task.id)).toByteArray(Charsets.UTF_8))
    }
    fun save(task: ScanTask) {
        val old=read(task.rollId,task.id)
        require(old.copy(frames=task.frames,status=task.status,error=task.error)==task &&
            old.frames.map { it.copy(done=false) }==task.frames.map { it.copy(done=false) } &&
            old.frames.zip(task.frames).all { !it.first.done || it.second.done }) { "Frozen scan task changed" }
        write(task)
    }
    private fun write(task: ScanTask) {
        val data=mapOf("model" to "phone_batch_scan_v1","id" to task.id,"roll_id" to task.rollId,"recipe_share" to task.recipeShare,
            "observation_model" to task.observationModel,"status" to task.status,"error" to task.error,
            "frames" to task.frames.map { mapOf("result_id" to it.resultId,"optical_path" to it.opticalPath,"frame_index" to it.frameIndex,"scan_id" to it.scanId,"done" to it.done) })
        val bytes=RecipeJson.text(data).toByteArray(Charsets.UTF_8);require(bytes.size<=262144)
        storage.writePrivate(path(task.rollId,task.id),bytes)
    }
}

class BatchScanSession(private val library: FilmLibrary,private val storage: StorageAdapter,private val scans: PrivateScanStore,
    private val tasks: PrivateScanTaskStore) {
    fun create(rollId: String,frames: List<DevelopedResult>,recipe: Map<String,Any?>): ScanTask {
        require(frames.isNotEmpty() && frames.all { it.sourceRollId==rollId } && frames.map { it.resultId }.distinct().size==frames.size)
        val frozen=frozenRecipe(recipe)
        return ScanTask(UUID.randomUUID().toString(),rollId,RecipeShareCodec.encode(frozen),ObservationImplementation.forRecipe(frozen),
            frames.map { ScanTaskFrame(it.resultId,it.opticalPath,it.sourceFrameIndex,UUID.randomUUID().toString()) }).also(tasks::create)
    }
    fun abandon(rollId: String,id: String) {
        require(!library.hasActiveRollJob(rollId)) { "Scan is still running" }
        val task=tasks.read(rollId,id);require(task.unfinished);tasks.save(task.copy(status="abandoned",error=null))
    }
    suspend fun run(rollId: String,id: String,control: WorkflowControl=WorkflowControl(),progress: (ScanTask)->Unit={},
        render: (DevelopedResult,Map<String,Any?>,WorkflowControl,OutputStream)->Pair<Int,Int>): ScanTask = library.withObservationJob(rollId) {
        var task=tasks.read(rollId,id);require(task.unfinished) { "Scan task already ended" }
        val recipe=RecipeShareCodec.decode(task.recipeShare)
        val runtime=ObservationImplementation.frozenRuntime(recipe,task.observationModel)
        try {
            task=task.copy(status="running",error=null);tasks.save(task)
            for((index,frame) in task.frames.withIndex()) {
                control.checkCancel()
                if(frame.done) { scans.requireSaved(frame.scanId,frame.resultId,task.recipeShare,task.id);continue }
                val result=library.resultsForRoll(rollId).firstOrNull { it.resultId==frame.resultId && it.opticalPath==frame.opticalPath && it.sourceFrameIndex==frame.frameIndex }
                    ?: error("Frame ${frame.frameIndex}: source negative is missing; saved photos stay")
                require(storage.existsPrivate(result.opticalPath)) { "Frame ${frame.frameIndex}: source negative file is missing" }
                scans.saveOwnedFrame(frame.scanId,task.id,result,recipe,control,task.observationModel) { render(result,runtime,control,it) }
                // Commit the receipt even when pause arrives immediately after the photo commit.
                task=task.copy(frames=task.frames.mapIndexed { i,value -> if(i==index) value.copy(done=true) else value })
                tasks.save(task);progress(task)
            }
            task=task.copy(status="completed",error=null);tasks.save(task);task
        } catch(error: Exception) {
            val paused=error is WorkflowException && error.code=="cancelled"
            val updated=task.copy(status=if(paused) "paused" else "failed",error=if(paused) null else error.message?.take(1000))
            runCatching { tasks.save(updated) }.exceptionOrNull()?.let(error::addSuppressed)
            if(paused) updated else throw error
        }
    }
}
