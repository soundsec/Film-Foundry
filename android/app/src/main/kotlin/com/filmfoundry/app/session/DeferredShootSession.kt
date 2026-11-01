package com.filmfoundry.app.session

import com.filmfoundry.interchange.*
import com.filmfoundry.platform.*
import com.filmfoundry.workflow.*
import java.io.File
import java.io.RandomAccessFile
import java.nio.ByteBuffer
import java.nio.ByteOrder
import kotlin.math.pow

/** Shutter saves sources/parameters. Material formation runs explicitly after capture, never in this call. */
class DeferredShootSession(private val library: FilmLibrary,private val medium: MediumStore,
    private val captures: CaptureReceiptStore,private val scratch: File,
    private val facade: WorkflowFacade=DefaultWorkflowFacade(),
    private val openPixels: (File)->StoredExposurePixels={openStoredExposurePixels(it)},
    private val rowBudget: ()->Long?={ null }) {
    data class Saved(val frame: Frame,val exposures: Int,val finalized: Boolean,val recovered: Boolean,val captureSaveMs: Long=0)

    suspend fun capture(rollId: String,camera: StoredStillCamera,multiple: Boolean=false,ev: Double=0.0,
        control: WorkflowControl=WorkflowControl(),cameraSettings: Map<String,Any?> = emptyMap(),
        advanceMode: String?=null,panorama: Boolean=false): Saved {
        require(advanceMode == null || advanceMode in listOf("auto","manual"))
        val started=System.nanoTime()
        val frozenCamera=MovieMasterCodec.snapshot(cameraSettings)
        var receipt: CaptureReceipt?=null;var recovered=false
        val frame=library.recordCameraExposure(rollId,{ roll ->
            (roll.frames.firstOrNull { it.state == FrameState.CAPTURING }
                ?: roll.frames.firstOrNull { it.state == FrameState.EMPTY })?.frameIndex
                ?: throw WorkflowException("invalid_request","roll is full")
        }) { roll,frame ->
            control.checkCancel()
            val path=captures.path(rollId,frame.frameIndex)
            val saved=if(captures.exists(path)) captures.read(path) else null
            require(saved == null || (saved.rollId == rollId && saved.frameId == frame.frameId && RecipeJson.equivalent(saved.film,MovieMasterCodec.snapshot(roll.filmRecipe))))
            // Receipt published but index failed: this retry registers it without another shutter.
            require(saved == null || saved.sources.size >= frame.captureExposures) { "capture receipt is older than index" }
            val value=if(saved != null && (saved.sources.size > frame.captureExposures || saved.finalized)) {
                recovered=true;saved
            } else {
                require(frame.state != FrameState.CAPTURING || frame.shutterArmed) { "advance film or cock for multiple exposure first" }
                var taken=false
                val tracked=object: StoredStillCamera {
                    override fun sourceFileName()=camera.sourceFileName()
                    override fun saveStill(target: File)=camera.saveStill(target).also { taken=true }
                    override fun plannedDimensions()=camera.plannedDimensions()
                    override fun plannedOrientationDegrees()=camera.plannedOrientationDegrees()
                }
                val conditions=if(frozenCamera.isEmpty() && roll.captureSettings.isEmpty()) emptyMap() else
                    mapOf("model" to PHONE_CAPTURE_CONDITIONS_MODEL,"camera" to frozenCamera,"film" to roll.captureSettings)
                if(conditions.isNotEmpty()) prepareCaptureConditions(1,1,conditions) // validate before taking a photograph
                validateFilmStorageConditions(roll.captureSettings)
                val format=roll.captureSettings["format"] as? String
                captures.append(path,rollId,frame.frameIndex,frame.frameId,roll.filmRecipe,
                    multiple || advanceMode == "manual" || frame.state == FrameState.CAPTURING,ev,tracked,control,conditions,format,panorama,
                    roll.captureSettings["gate_revision"] as? String ?: StillFrameFormat.LEGACY_MODEL)
                    .also { recovered=!taken }
            }
            receipt=value
            FilmLibrary.CapturedUpdate(path,!value.finalized,value.sources.size,advanceMode == null || value.finalized,
                value.sources.all { StillFrameFormat.read(it.record["frame_format"],it.width,it.height).panorama })
        }
        val value=receipt!!
        return Saved(frame,value.sources.size,value.finalized,recovered,((System.nanoTime()-started)/1_000_000).coerceAtLeast(0))
    }

    suspend fun finishFrame(rollId: String,control: WorkflowControl=WorkflowControl()): Frame =
        library.recordCameraExposure(rollId,{ roll ->
            roll.frames.firstOrNull { it.state == FrameState.CAPTURING }?.frameIndex
                ?: throw WorkflowException("invalid_request","no open multiple exposure")
        }) { _,frame ->
            control.checkCancel();val path=frame.capturePath!!;val receipt=captures.finish(path)
            FilmLibrary.CapturedUpdate(path,false,receipt.sources.size,gatePanorama=frame.gatePanorama)
        }

    suspend fun seal(rollId: String,control: WorkflowControl=WorkflowControl()): Roll {
        // Fixed slots only. Reconcile a complete receipt after an index-write failure.
        for(frame in library.roll(rollId).frames.toList()) {
            val path=captures.path(rollId,frame.frameIndex)
            if(frame.state == FrameState.EMPTY && captures.hasSaved(path)) {
                library.recordCameraExposure(rollId,{frame.frameIndex}) { roll,slot ->
                    control.checkCancel();val receipt=captures.recover(path) ?: error("no saved exposure")
                    require(receipt.frameId == slot.frameId && RecipeJson.equivalent(receipt.film,MovieMasterCodec.snapshot(roll.filmRecipe)))
                    FilmLibrary.CapturedUpdate(path,!receipt.finalized,receipt.sources.size,receipt.finalized,
                        receipt.sources.all { StillFrameFormat.read(it.record["frame_format"],it.width,it.height).panorama })
                }
            }
        }
        if(library.roll(rollId).frames.any { it.state == FrameState.CAPTURING }) finishFrame(rollId,control)
        return SealSession(library,medium).seal(rollId,control)
    }

    suspend fun prepareRoll(rollId: String,control: WorkflowControl=WorkflowControl()) {
        val roll=library.roll(rollId);require(roll.lifecycle == RollLifecycle.SEALED)
        for(slot in roll.frames.toList()) if(slot.state == FrameState.CAPTURED) {
            prepareFrame(rollId,slot.frameIndex,control)
        }
    }

    suspend fun prepareFrame(rollId: String,index: Int,control: WorkflowControl=WorkflowControl(),grainModel: String?=null,
        rowIo: RowMediumIo?=null,rowHandoff: RowMediumHandoff?=null): Frame {
        control.checkCancel()
        val roll=library.roll(rollId);require(roll.lifecycle == RollLifecycle.SEALED && index in 1..roll.capacity)
        val current=roll.frames[index-1]
        if(current.state == FrameState.LATENT) return current
        return library.materializeCapturedFrame(rollId,index) { _,frame ->
                val receipt=captures.read(frame.capturePath!!)
                require(receipt.finalized && receipt.frameId == frame.frameId && RecipeJson.equivalent(receipt.film,MovieMasterCodec.snapshot(roll.filmRecipe)))
                val path=cameraLatentPath(rollId,frame.frameIndex)
                val identity=FrameIdentity(rollId,frame.frameId,frame.frameIndex)
                if(!medium.hasRecoverableCameraLatent(path,identity,control)) {
                    withExposureRows(frame.capturePath!!,receipt,control) { rows ->
                        val io=rowIo ?: medium.rowExecutionIo() ?: error("deferred capture requires row storage")
                        val exposure=PhoneMaterialDefaults.exposureRecipe(receipt.film,roll.captureSettings)+
                            (grainModel?.let { mapOf(PhoneGrainModels.EXPOSURE_OPTION to it) } ?: emptyMap())
                        val request=WorkflowRequest(Command.EXPOSE,MediumRef("stored_camera"),MediumRef(path),
                            exposure,frameIdentity=identity,control=control,rowIo=io,rgbRows=rows,
                            captureRecord=record(receipt),rowBudgetBytes=rowBudget(),rowHandoff=rowHandoff)
                        check(facade.run(request).mediumCommitted)
                    }
                }
                path
        }
    }

    private suspend fun withExposureRows(path: String,receipt: CaptureReceipt,control: WorkflowControl,
        work: suspend (RgbRows)->Unit) {
        val first=receipt.sources.first()
        val aperture=StillFrameFormat.read(first.record["frame_format"],first.width,first.height)
        val apertures=receipt.sources.associate { source ->
            source.path to StillFrameFormat.read(source.record["frame_format"],source.width,source.height).also {
                require(it.copy(panorama=aperture.panorama) == aperture) { "Exposure geometry changed within frame" }
            }
        }
        val width=aperture.width;val height=aperture.height
        val conditions=receipt.sources.associate { source ->
            @Suppress("UNCHECKED_CAST") val settings=source.record["exposure_conditions"] as? Map<String,Any?>
            source.path to settings?.let { prepareCaptureConditions(width,height,it) }
        }
        @Suppress("UNCHECKED_CAST") val frozen=(first.record["exposure_conditions"] as? Map<String,Any?>)?.get("film") as? Map<String,Any?> ?: emptyMap()
        suspend fun finish(rows: RgbRows) { work(filmStorageRgbRows(rows,frozen)) }
        fun linear(source: StoredExposure,pixels: StoredExposurePixels,start: Int,count: Int): FloatArray {
            control.checkCancel()
            val rgb=pixels.linearWindowRows(aperture.left,aperture.top+start,width,count,CaptureKind.fromWire(source.record["kind"] as String))
            require(rgb.size == width*count*3)
            val weight=2.0.pow(source.ev).toFloat()
            for(i in rgb.indices) rgb[i]=0f+rgb[i]*weight
            conditions[source.path]?.invoke(rgb,start,count)
            val sourceAperture=apertures.getValue(source.path)
            if(sourceAperture.panorama) {
                val area=sourceAperture.openArea()
                for(y in 0 until count) for(x in 0 until width) if(x < area[0] || x >= area[0]+area[2] || start+y < area[1] || start+y >= area[1]+area[3]) {
                    val i=(y*width+x)*3;rgb[i]=0f;rgb[i+1]=0f;rgb[i+2]=0f
                }
            }
            return rgb
        }
        if(receipt.sources.size == 1) {
            openPixels(captures.sourceFile(path,first)).use { pixels ->
                pixels.configureCapture(first.record)
                require(pixels.width == first.width && pixels.height == first.height)
                finish(workingRgbRows(width,height) { start,count -> linear(first,pixels,start,count) })
            }
            return
        }
        require(scratch.isDirectory || scratch.mkdirs())
        require(scratch.usableSpace >= width.toLong()*height*12+16L*1024*1024) { "Not enough storage to form multiple exposure" }
        val lease=com.filmfoundry.interchange.OwnedScratch.create(scratch)
        val file=try { File.createTempFile("film-exposure-sum-",".f32",lease.directory) } catch(error: Throwable) { lease.close();throw error }
        try {
            RandomAccessFile(file,"rw").use { output ->
                val step=maxOf(1,(rowBudget()?.let { PhoneComputeSchedule.blockPixels(width,height,it) } ?: 16384)/width)
                for((index,source) in receipt.sources.withIndex()) {
                    openPixels(captures.sourceFile(path,source)).use { pixels ->
                        pixels.configureCapture(source.record)
                        require(pixels.width == first.width && pixels.height == first.height)
                        for(start in 0 until height step step) {
                            val count=minOf(step,height-start)
                            val rgb=linear(source,pixels,start,count)
                            val offset=start.toLong()*width*3*4
                            val bytes=ByteArray(rgb.size*4)
                            if(index > 0) {
                                output.seek(offset);output.readFully(bytes)
                                val buffer=ByteBuffer.wrap(bytes).order(ByteOrder.LITTLE_ENDIAN)
                                for(i in rgb.indices) rgb[i]=buffer.float+rgb[i]
                            }
                            val buffer=ByteBuffer.wrap(bytes).order(ByteOrder.LITTLE_ENDIAN)
                            for(value in rgb) buffer.putFloat(value)
                            output.seek(offset);output.write(bytes)
                        }
                    }
                }
                finish(workingRgbRows(width,height) { start,count ->
                    control.checkCancel();val bytes=ByteArray(width*count*3*4)
                    output.seek(start.toLong()*width*3*4);output.readFully(bytes)
                    val buffer=ByteBuffer.wrap(bytes).order(ByteOrder.LITTLE_ENDIAN)
                    FloatArray(width*count*3) { buffer.float }
                })
            }
        } finally { lease.close() }
    }
    private fun record(receipt: CaptureReceipt): Map<String,Any?> {
        val first=receipt.sources[0].record
        val gates=receipt.sources.map { StillFrameFormat.read(it.record["frame_format"],it.width,it.height) }
        val mixedGate=gates.any { it.panorama!=gates.first().panorama }
        val frameFormat=if(mixedGate) mapOf("frame_format" to gates.first().copy(panorama=false).metadata(),"gate_application" to "per_exposure_v1")
            else first["frame_format"]?.let { mapOf("frame_format" to it) } ?: emptyMap()
        val stacked=receipt.sources.size > 1 || receipt.sources[0].ev != 0.0
        val hdr=receipt.sources.any { it.record["kind"]==CaptureKind.ULTRA_HDR_GAINMAP.wireName }
        val raw=first["kind"]==CaptureKind.SENSOR_RAW_DNG.wireName
        return CaptureRecord(if(stacked) CaptureKind.EXPOSURE_STACK else if(raw) CaptureKind.SENSOR_RAW_DNG else if(hdr) CaptureKind.ULTRA_HDR_GAINMAP else CaptureKind.DISPLAY_REFERRED_SRGB,
            receipt.sources.any { it.record["isp_processed"]==true },
            sourceEncoding=if(stacked) "linear_sum_relative_ev" else if(raw) {
                if(first["stored_pixel_orientation"]=="dng_exif_v1") DngRawDecoder.ORIENTED_MODEL else DngRawDecoder.MODEL
            } else if(hdr) "ultra_hdr_gainmap_full_linear_srgb_v1" else "iec61966_2_1_srgb",bitDepth=if(raw) (first["bit_depth"] as? Number)?.toInt() else 8,hdrGainmap=if(hdr) "applied" else "absent",
            exposureTimeNs=(first["exposure_time_ns"] as? Number)?.toLong(),iso=(first["iso"] as? Number)?.toInt(),
            frameCount=receipt.sources.size).toMetadata()+frameFormat+mapOf("stored_exposures" to receipt.sources.map {
                mapOf("relative_ev" to it.ev,"capture" to it.record)
            })
    }
}
