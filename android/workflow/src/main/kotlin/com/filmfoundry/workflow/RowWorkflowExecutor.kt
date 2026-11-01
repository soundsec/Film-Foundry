package com.filmfoundry.workflow

import com.filmfoundry.interchange.*
import com.filmfoundry.kernel.*
import java.io.File
import java.io.InputStream
import java.io.OutputStream

/** Application I/O contract. Writes commit atomically only after beforeCommit succeeds. */
interface RowMediumIo {
    val scratchRoot: File
    fun <T> read(path: String, reader: (InputStream) -> T): T
    fun write(path: String, beforeCommit: () -> Unit, writer: (OutputStream) -> Unit)
    fun sameLocation(a: String, b: String): Boolean
    /** Null keeps the standard archive read path for adapters without stable private files. */
    fun version(path: String): RowMediumVersion? = null
}

interface RgbRows: LinearRgbRows

fun workingRgbRows(width: Int,height: Int,read: (Int,Int)->FloatArray): RgbRows = object: RgbRows {
    override val width=width
    override val height=height
    override fun readRows(start: Int,count: Int)=RgbVolume(width,count,3,read(start,count))
}

fun prepareCaptureConditions(width: Int,height: Int,settings: Map<String,Any?>): (FloatArray,Int,Int)->Unit {
    val prepared=PhoneCaptureConditions(width,height,settings)
    return prepared::apply
}
const val PHONE_CAPTURE_CONDITIONS_MODEL=PhoneCaptureConditions.MODEL

fun validateFilmStorageConditions(settings: Map<String,Any?>) { PhoneFilmStorageConditions(settings) }

fun filmStorageRgbRows(source: RgbRows,settings: Map<String,Any?>): RgbRows {
    val prepared=PhoneFilmStorageConditions(settings)
    return object: RgbRows {
        override val width=source.width
        override val height=source.height
        override fun readRows(start: Int,count: Int)=source.readRows(start,count).also { prepared.apply(it.linearRgb) }
    }
}

/** Owns orchestration and disposable files; all formulas remain in RowReducedKernel. */
internal class RowWorkflowExecutor(private val kernel: ReducedKernel) {
    fun run(request: WorkflowRequest, recipe: Map<String, Any?>): WorkflowResult {
        val engine = kernel as? RowReducedKernel ?: throw WorkflowException("unsupported_command", "kernel has no row execution capability")
        val io = request.rowIo!!
        val control = request.control
        val command = request.command.wireName
        require(request.rowWorkers in 1..2) { "rowWorkers must be 1 or 2" }
        fun <T> timed(section: String,work: ()->T): T = control.measured(command,section,work)
        val codec = NpzMediumArchive()
        fun progress(stage: String) { control.checkCancel(); control.report(command, stage); control.checkCancel() }
        if(request.rowBudgetBytes==null) require(request.rowBlockPixels in 1..262144) { "rowBlockPixels must be in 1..262144" }
        fun step(width: Int,height: Int): Int {
            val pixels=request.rowBudgetBytes?.let { PhoneComputeSchedule.blockPixels(width,height,it) } ?: request.rowBlockPixels
            return maxOf(1,pixels/width)
        }
        progress("loading")
        when (request.command) {
            Command.EXPOSE -> {
                require(request.output.path.endsWith(DataKernel.LATENT_SUFFIX)) { "expose output must be latent NPZ" }
                val source = request.rgbRows ?: request.linearRgb?.let { volume -> object : RgbRows {
                    override val width = volume.width
                    override val height = volume.height
                    override fun readRows(start: Int, count: Int) = RgbVolume(width, count, linearRgb =
                        volume.linearRgb.copyOfRange(start * width * 3, (start + count) * width * 3))
                } } ?: throw WorkflowException("invalid_request", "expose requires linear RGB rows")
                require(source.width > 0 && source.height > 0)
                progress("exposing")
                val prepared = timed("setup") { engine.prepareExposure(recipe, source.width, source.height) }
                control.checkCancel()
                val output=RowArchiveSpool(io.scratchRoot, source.height, source.width, control::checkCancel)
                var handedOff=false
                try {
                  val checkedSource=object: LinearRgbRows {
                    override val width=source.width;override val height=source.height
                    override fun checkActive()=control.checkCancel()
                    override fun readRows(start: Int,count: Int): RgbVolume {
                        control.checkCancel()
                        return timed("source_read") { source.readRows(start,count) }.also { require(it.width == width && it.height == count);control.checkCancel() }
                    }
                  }
                  timed("spatial_prepare") { prepared.bindSource(checkedSource) }.use { bound ->
                    for (start in 0 until source.height step step(source.width,source.height)) {
                        control.checkCancel()
                        val count = minOf(step(source.width,source.height), source.height - start)
                        val latent = stampCapture(timed("material_compute") { bound.exposeRows(start,count) }, request.captureRecord)
                        timed("archive_spool") { output.append(codec.latentEntries(LatentArchive(latent.arrays, latent.metadata)), count) }
                    }
                  }
                    progress("saving")
                    timed("archive_pack") { io.write(request.output.path, control::checkCancel) { output.pack(it, java.util.zip.Deflater.BEST_SPEED) } }
                    handedOff=request.rowHandoff?.retain(io,request.output.path,output)==true
                } finally { if(!handedOff) output.close() }
            }
            Command.DEVELOP_LATENT -> {
                require(request.input.path.endsWith(DataKernel.LATENT_SUFFIX)) { "develop input must be latent NPZ" }
                require(request.output.path.endsWith(DataKernel.NEGATIVE_SUFFIX) || request.output.path.endsWith(DataKernel.POSITIVE_SUFFIX)) { "develop output must be optical NPZ" }
                require(!io.sameLocation(request.input.path, request.output.path)) { "source roll must not be overwritten" }
                val retained=timed("archive_handoff") { request.rowHandoff?.take(io,request.input.path) }
                (retained ?: timed("archive_read") { io.read(request.input.path) { RowArchiveSpool.readLatent(it, io.scratchRoot, control::checkCancel) } }).use { input ->
                    progress("developing")
                    val (prepared,sourceMetadata,materialRecipe)=timed("setup") { input.latentRows(0,minOf(step(input.width,input.height),input.height)).let { first ->
                        verifyDevelopmentSource(first.metadata,request.developmentRecord)
                        val materialRecipe=MaterialSnapshot.resolve(recipe,first.metadata)
                        Triple(engine.prepareDevelopment(materialRecipe,LatentState(first.arrays,first.metadata),input.height),first.metadata,materialRecipe)
                    } }
                    val rowAborted=java.util.concurrent.atomic.AtomicBoolean(false)
                    val source=object: LatentRows {
                        override val width=input.width;override val height=input.height
                        override val readRowsPerPass=step(input.width,input.height)
                        override fun checkActive() { control.checkCancel();if(rowAborted.get()) throw java.util.concurrent.CancellationException("Row execution stopped") }
                        override fun readRows(start: Int,count: Int): LatentState {
                            checkActive()
                            val block=synchronized(input) { checkActive();input.latentRows(start,count) }
                            return LatentState(block.arrays,block.metadata).also { checkActive() }
                        }
                    }
                    val border=timed("border_prepare") { FormedFilmBorder.prepare(kernel,materialRecipe,input.width,input.height) }
                    val output=RowArchiveSpool(io.scratchRoot,border?.layout?.height ?: input.height,input.width,control::checkCancel)
                    var handedOff=false
                    try {
                      fun append(optical: OpticalMaster,count: Int) {
                          val stamped=stampDevelopment(copyCapture(optical,sourceMetadata),request.developmentRecord)
                          timed("archive_spool") { output.append(codec.opticalEntries(DevelopedOpticalArchive(stamped.opticalDensityRgb,stamped.metadata,
                              stamped.clearBaseOpticalDensityRgb,stamped.spectralComponentAmounts,stamped.spectralPostProcessDensityRgb)),count) }
                      }
                      timed("spatial_prepare") { prepared.bindSource(source) }.use { bound ->
                        val originalRows=step(input.width,input.height)
                        val workers=if(bound.concurrentRows && originalRows>=2) request.rowWorkers else 1
                        val rows=maxOf(1,originalRows/workers)
                        val firstCount=minOf(rows,input.height)
                        val first=timed("material_compute") { bound.developRows(0,firstCount) }
                        fun strip(start: Int) {
                            if(border==null) return
                            for(offset in 0 until border.layout.rebate step step(input.width,input.height)) {
                                control.checkCancel();val count=minOf(step(input.width,input.height),border.layout.rebate-offset)
                                append(timed("border_compute") { border.strip(start+offset,count,first) },count)
                            }
                        }
                        strip(0)
                        append(if(border==null) first else timed("border_compute") { border.stamp(first) },firstCount)
                        timed("row_pipeline_wall") { OrderedRowWorkers.run(firstCount until input.height step rows,workers,rowAborted,source::checkActive,{ start ->
                            val count=minOf(rows,input.height-start)
                            val begin=System.nanoTime();val optical=bound.developRows(start,count)
                            optical to (System.nanoTime()-begin)
                        }) { start,measured ->
                            control.checkCancel()
                            val count = minOf(rows, input.height - start)
                            val (optical,nanos)=measured
                            control.onTiming?.invoke(command,"material_compute",nanos)
                            append(if(border==null) optical else timed("border_compute") { border.stamp(optical) },count)
                        }
                        }
                        strip((border?.layout?.rebate ?: 0)+input.height)
                      }
                        progress("saving")
                        timed("archive_pack") { io.write(request.output.path, control::checkCancel) { output.pack(it, java.util.zip.Deflater.BEST_SPEED) } }
                        handedOff=request.opticalHandoff?.retain(io,request.output.path,output)==true
                    } finally { if(!handedOff) output.close() }
                }
            }
            Command.SCAN -> throw WorkflowException("unsupported_command", "row formation does not estimate scanner statistics per block")
        }
        // Cancellation during commit cannot revoke an already completed atomic write.
        control.report(command, "completed")
        return WorkflowResult(request.command, request.output.path, request.output.path, mediumCommitted = true)
    }
}
