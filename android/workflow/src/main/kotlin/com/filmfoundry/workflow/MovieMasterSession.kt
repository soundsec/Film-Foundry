package com.filmfoundry.workflow

import com.filmfoundry.interchange.*
import java.io.File
import java.io.FileOutputStream
import java.io.InputStream
import java.io.OutputStream
import java.nio.file.Files
import java.nio.file.StandardCopyOption
import java.util.UUID

/** Decoder/platform owns the frame until next() or close(); rows are borrowed synchronously. */
data class MovieLinearFrame(val rows: RgbRows,val pts: Long,val timeBaseNum: Long=1,val timeBaseDen: Long=1_000_000,
    val duration: Long?=null)
interface MovieFrameReader: AutoCloseable { fun next(): MovieLinearFrame? }

fun movieRgbRows(width: Int,height: Int,read: (Int,Int)->FloatArray): RgbRows = object: RgbRows {
    override val width=width
    override val height=height
    override fun readRows(start: Int,count: Int)=com.filmfoundry.kernel.RgbVolume(width,count,3,read(start,count))
}

/** Platform-free application coordination; formulas remain in the selected phone kernel. */
class MovieMasterSession(private val facade: WorkflowFacade=DefaultWorkflowFacade(),
    private val storage: MaterialMasterWorkspace=MaterialMasterWorkspace()) {
    fun recover(parent: File,control: WorkflowControl=WorkflowControl())=storage.recover(parent,control)
    suspend fun expose(reader: MovieFrameReader,output: File,recipe: Map<String,Any?>,seed: Long=0,
        source: Map<String,Any?>?=null,control: WorkflowControl=WorkflowControl()): File {
        val frozen=MovieMasterCodec.snapshot(recipe)
        val id=UUID.randomUUID().toString()
        reader.use {
            transaction(output,control) { pending,io,control ->
                val frames=mutableListOf<MovieMaterialFrame>()
                var width=0;var height=0
                while(true) {
                    control.checkCancel()
                    val frame=reader.next() ?: break
                    if(frames.isEmpty()) { width=frame.rows.width;height=frame.rows.height }
                    require(width == frame.rows.width && height == frame.rows.height) { "Changing movie dimensions need another master" }
                    val index=frames.size+1
                    val relative="frames/${index.toString().padStart(8,'0')}${DataKernel.LATENT_SUFFIX}"
                    val item=MovieMaterialFrame(index,relative,frame.pts,frame.timeBaseNum,frame.timeBaseDen,frame.duration)
                    // Validate timing before doing expensive work or writing a new frame.
                    val boundary=if(frames.isEmpty()) listOf(item) else listOf(frames.last().copy(index=1),item.copy(index=2))
                    MovieMasterCodec.validate(MovieMaterialMaster(id,id,"exposed_latent",width,height,seed,frozen,boundary))
                    val result=facade.run(WorkflowRequest(Command.EXPOSE,MediumRef("movie"),MediumRef(File(pending,relative).path),
                        frozen,frameIdentity=FrameIdentity(id,frameIndex=index),control=control,rowIo=io,rgbRows=frame.rows))
                    check(result.mediumCommitted)
                    frames.add(item)
                }
                MovieMaterialMaster(id,id,"exposed_latent",width,height,seed,frozen,frames,source=source)
            }
        }
        return output.canonicalFile
    }

    suspend fun develop(source: File,output: File,recipe: Map<String,Any?>,control: WorkflowControl=WorkflowControl()): File {
        val root=source.canonicalFile
        val original=read(root)
        require(original.stage == "exposed_latent")
        val target=output.canonicalFile
        require(!inside(root,target) && !inside(target,root)) { "Derived master must be separate" }
        val frozen=MovieMasterCodec.snapshot(recipe).toMutableMap()
        // Material belongs to the exposed master; caller changes process/observation only.
        frozen["film"]=original.recipe["film"] ?: emptyMap<String,Any?>()
        for(key in listOf("mode","enable_subtractive","medium")) {
            if(original.recipe.containsKey(key)) frozen[key]=original.recipe[key] else frozen.remove(key)
        }
        transaction(target,control,listOf(root)) { pending,io,control ->
            val frames=original.frames.map { frame ->
                control.checkCancel()
                val relative="frames/${frame.index.toString().padStart(8,'0')}${DataKernel.NEGATIVE_SUFFIX}"
                val guarded=object: RowMediumIo by io {
                    override fun <T> read(path: String,reader: (InputStream)->T): T {
                        val loaded=io.read(path,reader)
                        if(loaded is RowArchiveSpool) try {
                            require(loaded.width == original.width && loaded.height == original.height)
                            identity(loaded.latentRows(0,1).metadata,original.rollId,frame.index)
                        } catch(error: Throwable) { loaded.close();throw error }
                        return loaded
                    }
                }
                val result=facade.run(WorkflowRequests.developLatent(File(root,frame.path).path,File(pending,relative).path,
                    frozen,rowIo=guarded,control=control))
                check(result.mediumCommitted)
                // Read only the JSON member; numerical fields stay on disk. The
                // output suffix follows its own frozen polarity, not a UI guess.
                val file=File(pending,relative)
                val polarity=java.util.zip.ZipFile(file).use { zip ->
                    val entry=zip.getEntry("${DataKernel.DEVELOPED_METADATA_KEY}.npy") ?: error("Missing optical metadata")
                    val value=zip.getInputStream(entry).use { NpyCodec.decode(it.readBytes()) }
                    NpzMediumArchive().developedHeader(value)["image_polarity"] == "positive"
                }
                val actual=if(polarity) relative.replace(DataKernel.NEGATIVE_SUFFIX,DataKernel.POSITIVE_SUFFIX) else relative
                if(actual != relative) Files.move(file.toPath(),File(pending,actual).toPath(),StandardCopyOption.ATOMIC_MOVE)
                frame.copy(path=actual)
            }
            MovieMaterialMaster(UUID.randomUUID().toString(),original.rollId,"developed_medium",original.width,original.height,
                original.seed,frozen,frames,sourceMasterId=original.masterId)
        }
        return target
    }

    data class Observation(val width: Int,val height: Int,val rgb: FloatArray,val metadata: Map<String,Any?>)
    suspend fun observe(root: File,index: Int,recipe: Map<String,Any?>,viewer: Boolean=false,
        control: WorkflowControl=WorkflowControl()): Observation {
        val document=read(root)
        require(document.stage == "developed_medium" && index in 1..document.frames.size)
        control.checkCancel()
        val archive=File(root,document.frames[index-1].path).inputStream().use { NpzMediumArchive().readDevelopedOptical(it) }
        identity(archive.metadata,document.rollId,index)
        require(archive.opticalDensityRgb.shape.take(2) == listOf(document.height,document.width))
        val frozen=MovieMasterCodec.snapshot(recipe).toMutableMap()
        val scanner=(frozen["scanner"] as? Map<*,*>)?.entries?.associate { it.key.toString() to it.value }?.toMutableMap() ?: mutableMapOf()
        scanner["scan_normalize"]=false
        frozen["scanner"]=scanner
        frozen["observe_mode"]=if(viewer) "viewer" else "scanner"
        val optical=com.filmfoundry.kernel.OpticalMaster(archive.opticalDensityRgb,archive.metadata,archive.clearBaseOpticalDensityRgb,
            archive.spectralComponentAmounts,archive.spectralPostProcessDensityRgb)
        val result=facade.run(WorkflowRequest(Command.SCAN,MediumRef(File(root,document.frames[index-1].path).path),
            MediumRef("observation"),frozen,control=control,optical=optical)).observation!!
        return Observation(document.width,document.height,result.outputRgb.values,result.metadata)
    }

    private fun identity(metadata: Map<String,Any?>,rollId: String,index: Int) {
        val frame=metadata["frame_identity"] as? Map<*,*> ?: error("Movie material has no frame identity")
        require(frame["roll_id"] == rollId && (frame["frame_index"] as? Number)?.toLong() == index.toLong()) { "Movie frame identity disagrees with timeline" }
    }

    fun read(root: File): MovieMaterialMaster {
        val canonical=root.canonicalFile
        val master=MovieMasterCodec.decode(File(canonical,MovieMasterCodec.MANIFEST).readBytes())
        for(frame in master.frames) {
            val file=File(canonical,frame.path).canonicalFile
            require(inside(canonical,file) && file.isFile && file.length() > 0) { "Missing or escaping movie material frame" }
        }
        return master
    }

    private suspend fun transaction(output: File,control: WorkflowControl,readRoots: List<File> = emptyList(),
        build: suspend (File,RowMediumIo,WorkflowControl)->MovieMaterialMaster) {
        val target=output.canonicalFile
        require(!target.exists()) { "New master needs a new directory" }
        val parent=target.parentFile!!
        parent.mkdirs()
        storage.open(parent,control).use { job ->
            val pending=job.payload
            // Row spooling/packing already checks this control at bounded chunks.
            // Throttle filesystem queries; do not stat storage for every pixel.
            var lastSpaceCheck=System.nanoTime()-250_000_000L
            val guarded=WorkflowControl(cancelRequested={
                control.checkCancel()
                val now=System.nanoTime()
                if(now-lastSpaceCheck >= 250_000_000L) { storage.checkSpace(parent);lastSpaceCheck=now }
                false
            },onProgress=control.onProgress)
            File(pending,"frames").mkdir()
            val scratch=File(pending,"scratch").apply { mkdir() }
            val io=object: RowMediumIo {
                override val scratchRoot=scratch
                override fun <T> read(path: String,reader: (InputStream)->T): T {
                    val file=File(path).canonicalFile
                    require((readRoots+pending).any { inside(it.canonicalFile,file) })
                    return file.inputStream().buffered(64*1024).use(reader)
                }
                override fun write(path: String,beforeCommit: ()->Unit,writer: (OutputStream)->Unit) {
                    val file=File(path).canonicalFile
                    require(inside(pending,file))
                    storage.checkSpace(parent)
                    FileOutputStream(file).use { out -> val buffered=out.buffered(64*1024);writer(buffered);buffered.flush();out.fd.sync() }
                    storage.checkSpace(parent)
                    beforeCommit()
                }
                override fun sameLocation(a: String,b: String)=File(a).canonicalFile == File(b).canonicalFile
            }
            val document=build(pending,io,guarded)
            require(scratch.listFiles().orEmpty().isEmpty()) { "Movie row scratch not empty" }
            scratch.delete()
            FileOutputStream(File(pending,MovieMasterCodec.MANIFEST)).use { out -> out.write(MovieMasterCodec.encode(document));out.fd.sync() }
            control.checkCancel()
            storage.checkSpace(parent)
            require(!target.exists())
            Files.move(pending.toPath(),target.toPath(),StandardCopyOption.ATOMIC_MOVE)
        }
    }
    private fun inside(root: File,file: File)=file.path.startsWith(root.path+File.separator) || file == root
}
