package com.filmfoundry.platform

import com.filmfoundry.interchange.RecipeShareCodec
import com.filmfoundry.workflow.DevelopedResult
import com.filmfoundry.workflow.WorkflowControl
import com.filmfoundry.workflow.ObservationImplementation
import org.json.JSONArray
import org.json.JSONObject
import java.io.OutputStream
import java.time.Instant
import java.util.UUID

data class SavedScan(val id: String,val resultId: String,val rollId: String,val frameIndex: Int,
    val width: Int,val height: Int,val createdAt: String,val recipeShare: String,val observationModel: String?=null,
    val sourcePolarity: String?=null,val sourceWidth: Int?=null,val sourceHeight: Int?=null,
    val sourceDevelopRecipeShare: String?=null,val viewRotationDegrees: Int=0,val precisionBits: Int=8,val format: ScanImageFormat=ScanImageFormat.PNG) {
    val imagePath get()="scans/$id/image.${format.extension}"
    val displayPath get()=if(format==ScanImageFormat.TIFF) "scans/$id/image.png" else imagePath
    val displayName get()="FilmFoundry-${id}.${format.extension}"
    val displayWidth get()=OutputOrientation.size(width,height,viewRotationDegrees).first
    val displayHeight get()=OutputOrientation.size(width,height,viewRotationDegrees).second
    fun editingRecipe()=RecipeShareCodec.decode(recipeShare).let { OutputOrientation.apply(it,OutputOrientation.read(it)+viewRotationDegrees) }
}

/** Private scanner outputs only. This index never replaces the roll or optical-master index. */
class PrivateScanStore(private val storage: StorageAdapter,
    private val convert: (String,ScanImageFormat,Int,OutputStream,()->Unit)->Unit={ path,format,quality,out,check -> ScanImageExport.write(storage,path,format,0,out,check,quality) }) {
    private val renderLock=Any() // Serial writers; catalog readers never wait for numerical rendering.
    private val index="phone_scan_index.json"
    private fun ids(): List<String> {
        if(!storage.existsPrivate(index)) return emptyList()
        val bytes=storage.readPrivate(index);require(bytes.size <= 4*1024*1024)
        val root=JSONObject(bytes.toString(Charsets.UTF_8));require(root.getString("model") == "phone_scan_index_v1")
        val values=root.getJSONArray("ids");return (0 until values.length()).map { values.getString(it).also(::checkId) }.also { require(it.distinct().size == it.size) }
    }
    private fun checkId(id: String) { require(UUID.fromString(id).toString() == id) }
    @Synchronized fun count(): Int=ids().size
    /** Metadata only, for explicit select-all and continuous browsing. No image decode. */
    @Synchronized fun matching(rollId: String?=null,check: ()->Unit={}): List<SavedScan> =
        ids().asReversed().map { check();readSaved(it) }.filter { rollId==null || it.rollId==rollId }
    @Synchronized fun ownsPath(path: String): Boolean = ids().any { path=="scans/$it" || path in listOf("scans/$it/image.png","scans/$it/image.jpg","scans/$it/image.tif") || path=="scans/$it/scan.json" }
    /** Index transaction only; the resource owner stages/removes the files separately. */
    @Synchronized fun remove(id: String) {
        checkId(id);val before=ids();require(id in before) { "unknown saved photo" }
        // Keep a tiny deletion receipt outside the removed image directory. A stale pending task
        // must not mistake an intentionally removed photo for one that never committed.
        storage.writePrivate("scan_removed/$id.json",JSONObject().put("id",id).put("model","phone_scan_removal_v1").toString().toByteArray(Charsets.UTF_8))
        storage.writePrivate(index,JSONObject().put("model","phone_scan_index_v1").put("ids",JSONArray(before-id)).toString().toByteArray(Charsets.UTF_8))
    }
    @Synchronized fun listNewest(offset: Int=0,limit: Int=20): List<SavedScan> {
        require(offset >= 0 && limit in 1..20)
        return ids().asReversed().drop(offset).take(limit).map(::readSaved)
    }
    private fun readSaved(id: String): SavedScan {
        val bytes=storage.readPrivate("scans/$id/scan.json");require(bytes.size <= 1024*1024)
        val o=JSONObject(bytes.toString(Charsets.UTF_8));require(o.getString("model") == "phone_saved_scan_v1" && o.getString("id") == id)
        return SavedScan(id,o.getString("result_id"),o.getString("roll_id"),o.getInt("frame_index"),o.getInt("width"),o.getInt("height"),o.getString("created_at"),o.getString("recipe_share"),
            if(o.has("observation_implementation") && !o.isNull("observation_implementation")) o.getString("observation_implementation") else null,
            o.optString("source_polarity").takeIf { it in listOf("positive","negative") },
            o.optInt("source_width").takeIf { it>0 },o.optInt("source_height").takeIf { it>0 },
            o.optString("source_develop_recipe_share").takeIf { it.isNotBlank() },OutputOrientation.normalized(o.optInt("view_rotation_degrees",0)),o.optInt("scan_sample_bits",8),ScanImageFormat.valueOf(o.optString("output_format","PNG")))
    }
    /** Non-destructive photo orientation; original PNG, recipe and task receipts stay intact. */
    @Synchronized fun rotateClockwise(id: String): SavedScan {
        checkId(id);require(id in ids()) { "Saved photo is missing" }
        val saved=readSaved(id);require(storage.existsPrivate(saved.imagePath)) { "Saved photo file is missing" }
        val angle=OutputOrientation.normalized(saved.viewRotationDegrees+90)
        val path="scans/$id/scan.json";val record=JSONObject(storage.readPrivate(path).toString(Charsets.UTF_8))
        record.put("view_rotation_degrees",angle)
        storage.writePrivate(path,record.toString().toByteArray(Charsets.UTF_8))
        return saved.copy(viewRotationDegrees=angle)
    }
    data class Page(val total: Int,val items: List<SavedScan>)
    @Synchronized fun findNewest(resultId: String,recipeShare: String,check: ()->Unit={},minimumBits: Int=8): SavedScan? {
        val expected=ObservationImplementation.forRecipe(RecipeShareCodec.decode(recipeShare))
        for(id in ids().asReversed()) { check();val saved=readSaved(id)
            if(saved.precisionBits>=minimumBits && saved.resultId==resultId && saved.viewRotationDegrees==0 && saved.recipeShare==recipeShare && ObservationImplementation.matches(saved.observationModel,expected)) return saved
        };return null
    }
    /** One index snapshot per query. Caller runs I/O off the UI thread; image bytes are untouched. */
    @Synchronized fun pageNewest(rollId: String?=null,offset: Int=0,limit: Int=20,check: ()->Unit={}): Page {
        require(offset>=0 && limit in 1..20);check()
        val all=ids().asReversed()
        if(rollId==null) return Page(all.size,all.drop(offset).take(limit).map { check();readSaved(it) })
        var total=0;val items=mutableListOf<SavedScan>()
        for(id in all) { check();val entry=readSaved(id);if(entry.rollId==rollId) {
            if(total>=offset && items.size<limit) items+=entry
            total++
        } }
        return Page(total,items)
    }
    fun save(result: DevelopedResult,width: Int,height: Int,recipe: Map<String,Any?>,
        control: WorkflowControl=WorkflowControl(),writePng: (OutputStream)->Unit): SavedScan {
        require(width > 0 && height > 0)
        return saveStreamed(result,recipe,control) { output -> writePng(output);width to height }
    }
    /** Dimensions arrive from the row scanner; no whole output Bitmap is required. */
    fun saveStreamed(result: DevelopedResult,recipe: Map<String,Any?>,
        control: WorkflowControl=WorkflowControl(),writePng: (OutputStream)->Pair<Int,Int>): SavedScan {
        return synchronized(renderLock) { commit(UUID.randomUUID().toString(),null,result,recipe,control,writePng) }
    }
    /** A durable task reserves the ID before rendering. Completed owned files may be registered after a crash. */
    fun saveOwnedFrame(id: String,taskId: String,result: DevelopedResult,recipe: Map<String,Any?>,
        control: WorkflowControl=WorkflowControl(),observationModel: String=ObservationImplementation.forRecipe(recipe),writePng: (OutputStream)->Pair<Int,Int>): SavedScan {
        return synchronized(renderLock) { saveOwnedUnlocked(id,taskId,result,recipe,control,observationModel,writePng) }
    }
    private fun saveOwnedUnlocked(id: String,taskId: String,result: DevelopedResult,recipe: Map<String,Any?>,
        control: WorkflowControl,observationModel: String,writePng: (OutputStream)->Pair<Int,Int>): SavedScan {
        ObservationImplementation.frozenRuntime(recipe,observationModel)
        checkId(id);checkId(taskId);control.checkCancel()
        val before=ids();val path="scans/$id/scan.json";val image="scans/$id/image.png"
        require(id in before || !storage.existsPrivate("scan_removed/$id.json")) { "Task photo was removed; end the task before starting again" }
        if(storage.existsPrivate(path)) {
            val saved=readSaved(id);val record=JSONObject(storage.readPrivate(path).toString(Charsets.UTF_8))
            require(record.optString("owner_task_id")==taskId && saved.resultId==result.resultId && saved.rollId==result.sourceRollId &&
                saved.frameIndex==result.sourceFrameIndex && record.optString("source_optical_path")==result.opticalPath &&
                saved.recipeShare==RecipeShareCodec.encode(recipe) && saved.observationModel==observationModel) { "reserved photo belongs to another task or recipe" }
            require(storage.existsPrivate(saved.imagePath)) { "Saved task photo is missing; end the task before starting again" }
            if(id !in before) synchronized(this) { storage.writePrivate(index,JSONObject().put("model","phone_scan_index_v1").put("ids",JSONArray((ids()+id).distinct())).toString().toByteArray(Charsets.UTF_8)) }
            return saved
        }
        require(id !in before && listOf("png","jpg","tif").none { storage.existsPrivate("scans/$id/image.$it") }) { "Incomplete reserved photo; end the task before starting again" }
        return commit(id,taskId,result,recipe,control,writePng,observationModel)
    }
    @Synchronized fun requireSaved(id: String,resultId: String,recipeShare: String,taskId: String): SavedScan {
        checkId(id);checkId(taskId);require(id in ids()) { "Completed task photo was removed; end this task to start again" }
        return readSaved(id).also {
            val record=JSONObject(storage.readPrivate("scans/$id/scan.json").toString(Charsets.UTF_8))
            require(it.resultId==resultId && it.recipeShare==recipeShare && record.optString("owner_task_id")==taskId && storage.existsPrivate(it.imagePath)) { "Completed task photo is missing or changed" }
        }
    }
    private fun commit(id: String,taskId: String?,result: DevelopedResult,recipe: Map<String,Any?>,
        control: WorkflowControl,writePng: (OutputStream)->Pair<Int,Int>,observationModel: String=ObservationImplementation.forRecipe(recipe)): SavedScan {
        control.checkCancel()
        val before=ids();require(id !in before)
        val share=RecipeShareCodec.encode(recipe)
        val sourceShare=runCatching { RecipeShareCodec.encode(result.developRecipe) }.getOrNull()
        val imagePath="scans/$id/image.png"
        val path="scans/$id/scan.json"
        try {
            var dimensions: Pair<Int,Int>?=null
            control.measured("scan","render_and_png_commit") { storage.writePrivate(imagePath,control::checkCancel) { output ->
                dimensions=writePng(output).also { require(it.first>0 && it.second>0) }
            } }
            val (width,height)=checkNotNull(dimensions)
            val format=ScanDelivery.format(recipe)
            if(format!=ScanImageFormat.PNG) control.measured("scan","${format.extension}_encode_commit") { storage.writePrivate("scans/$id/image.${format.extension}",control::checkCancel) {
                convert(imagePath,format,ScanDelivery.jpegQuality(recipe),it,control::checkCancel)
            } }
            val source=runCatching {
                val root=java.io.File(storage.privateDir())
                if(root.isDirectory) OpticalFileInformationReader.read(PrivateMediumFiles(root).resolve(result.opticalPath)) else null
            }.getOrNull()
            val encodedBits=storage.readPrivate(imagePath) { input ->
                val header=ByteArray(25);var n=0
                while(n<header.size) { val read=input.read(header,n,header.size-n);if(read<0) break;n+=read }
                val png=n==25 && header.take(8)==listOf(137.toByte(),80.toByte(),78.toByte(),71.toByte(),13.toByte(),10.toByte(),26.toByte(),10.toByte())
                if((recipe["scanner"] as? Map<*,*>)?.containsKey("output_format")==true) {
                    require(png) { "Scanner did not produce PNG rows" }
                    val size=java.nio.ByteBuffer.wrap(header).order(java.nio.ByteOrder.BIG_ENDIAN)
                    require(size.getInt(16)==width && size.getInt(20)==height) { "Scan dimensions differ from its pixels" }
                    if(format!=ScanImageFormat.JPEG) require(header[24].toInt()==ScanDelivery.bits(recipe)) { "Scanner sample depth differs from the selected output quality" }
                }
                if(png && header[24].toInt()==16) 16 else 8
            }
            if(format==ScanImageFormat.JPEG) {
                storage.deletePrivate(imagePath)
                require(!storage.existsPrivate(imagePath)) { "Cannot release temporary scan pixels" }
            }
            val saved=SavedScan(id,result.resultId,result.sourceRollId,result.sourceFrameIndex,width,height,Instant.now().toString(),share,observationModel,
                source?.polarity,source?.width,source?.height,sourceShare,precisionBits=if(format==ScanImageFormat.JPEG) 8 else encodedBits,format=format)
            val record=JSONObject(mapOf("model" to "phone_saved_scan_v1","id" to id,"result_id" to result.resultId,
                "roll_id" to result.sourceRollId,"frame_index" to result.sourceFrameIndex,"source_optical_path" to result.opticalPath,
                "width" to width,"height" to height,"created_at" to saved.createdAt,"recipe_share" to share,"observation_implementation" to saved.observationModel))
            record.put("scan_sample_bits",saved.precisionBits).put("output_format",format.name)
            sourceShare?.let { record.put("source_develop_recipe_share",it) }
            source?.let { record.put("source_polarity",it.polarity).put("source_width",it.width).put("source_height",it.height) }
            if(taskId!=null) record.put("owner_task_id",taskId)
            storage.writePrivate(path,record.toString().toByteArray(Charsets.UTF_8))
            control.checkCancel()
            synchronized(this) { storage.writePrivate(index,JSONObject().put("model","phone_scan_index_v1").put("ids",JSONArray((ids()+id).distinct())).toString().toByteArray(Charsets.UTF_8)) }
            return saved
        } catch(error: Exception) {
            // Only this unregistered scan's files are owned here; originals and earlier scans survive.
            // An adapter can report an error after its atomic index commit. Never remove a published photo.
            if(runCatching { id !in ids() }.getOrDefault(false)) { listOf("png","jpg","tif").forEach { storage.deletePrivate("scans/$id/image.$it") };storage.deletePrivate(path) }
            throw error
        }
    }
}
