package com.filmfoundry.platform

import com.filmfoundry.interchange.*
import com.filmfoundry.workflow.WorkflowControl
import java.io.File
import java.io.FileOutputStream
import java.nio.file.Files
import java.nio.file.StandardCopyOption

/** One durable source bundle per shutter press. Only small receipts are read on the capture path. */
class CaptureReceiptStore(root: File) {
    private val transport=PrivateMediumFiles(root)
    fun path(roll: String,index: Int) = "$roll/frame_${index.toString().padStart(2,'0')}.capture/receipt.json"
    fun exists(path: String) = transport.resolve(path).isFile
    fun hasSaved(path: String) = exists(path) || File(transport.resolve(path).parentFile,"exposure_000001").exists()
    fun read(path: String): CaptureReceipt {
        val file=transport.resolve(path);require(file.length() <= 1024*1024)
        return CaptureReceiptCodec.decode(file.readBytes()).also { receipt ->
            require(path == path(receipt.rollId,receipt.frameIndex)) { "capture receipt identity/path mismatch" }
        }
    }
    fun sourceFile(path: String,source: StoredExposure): File {
        val parent=transport.resolve(path).parentFile!!
        val file=File(parent,source.path).canonicalFile
        require(file.path.startsWith(parent.canonicalPath+File.separator))
        return file
    }
    fun append(path: String,roll: String,index: Int,frameId: String,film: Map<String,Any?>,hold: Boolean,ev: Double,
        camera: StoredStillCamera,control: WorkflowControl,conditions: Map<String,Any?> = emptyMap(),
        format: String?=null,panorama: Boolean=false,gateModel: String=StillFrameFormat.MODEL): CaptureReceipt {
        control.checkCancel(); require(ev.isFinite() && ev in -20.0..20.0)
        require(format == null || format in listOf("135","half")+StillFrameFormat.roll120Frames.keys)
        require(!panorama || format == "135")
        require(path == path(roll,index))
        val material=MovieMasterCodec.snapshot(film)
        CaptureReceiptCodec.validateMaterial(material)
        val previous=if(exists(path)) read(path) else null
        require(previous == null || (!previous.finalized && previous.frameId == frameId && RecipeJson.equivalent(previous.film,material))) { "frame is already advanced or material changed" }
        previous?.sources?.first()?.let {
            val aperture=StillFrameFormat.read(it.record["frame_format"],it.width,it.height)
            require(aperture.format == (format ?: "legacy")) { "finish the current frame before changing format" }
            camera.plannedDimensions()?.let { planned -> require(planned.first==it.width && planned.second==it.height) { "Selected camera size differs from this open frame; advance before switching" } }
        }
        val folder=transport.resolve(path).parentFile!!
        require(folder.isDirectory || folder.mkdirs())
        require(folder.usableSpace >= 32L*1024*1024) { "Not enough capture storage" }
        val ordinal=(previous?.sources?.size ?: 0)+1
        require(ordinal <= CaptureReceiptCodec.MAX_EXPOSURES) { "Exposure count limit reached" }
        val name="exposure_${ordinal.toString().padStart(6,'0')}"
        val target=File(folder,name)
        val metadata=File(target,"source.json")
        val source=if(target.exists()) {
            require(metadata.isFile && metadata.length() <= 1024*1024) { "Incomplete saved exposure; preserved" }
            CaptureReceiptCodec.decodeBundle(metadata.readBytes()).also { bundle ->
                require(bundle.rollId == roll && bundle.frameIndex == index && bundle.frameId == frameId && RecipeJson.equivalent(bundle.film,material)) { "saved exposure identity mismatch; preserved" }
            }.source
        } else {
            val pending=Files.createTempDirectory(folder.toPath(),".capture-").toFile()
            try {
                control.checkCancel()
                val filename=camera.sourceFileName();require(filename in listOf("source.jpg","source.dng"))
                val info=camera.saveStill(File(pending,filename))
                require(filename==CaptureReceiptCodec.sourceName(info.record.toMetadata()+info.extra)) { "Camera file format differs from its source record" }
                previous?.sources?.first()?.let { require(info.width == it.width && info.height == it.height) { "capture dimensions changed; frame remains open" } }
                val frozenGate=(previous?.sources?.firstOrNull()?.record?.get("frame_format") as? Map<*,*>)?.get("model") as? String ?: gateModel
                val extras=if(format == null && !panorama) emptyMap() else mapOf("frame_format" to StillFrameFormat.create(format ?: "legacy",panorama,info.width,info.height,frozenGate).metadata())
                val source=StoredExposure("$name/$filename",info.width,info.height,ev,info.record.toMetadata()+info.extra+extras+
                    if(conditions.isEmpty()) emptyMap() else mapOf("exposure_conditions" to MovieMasterCodec.snapshot(conditions)))
                val bytes=CaptureReceiptCodec.encodeBundle(StoredExposureBundle(roll,index,frameId,material,source))
                FileOutputStream(File(pending,"source.json")).use { it.write(bytes);it.fd.sync() }
                // A completed exposure is never discarded by a late cancellation.
                Files.move(pending.toPath(),target.toPath(),StandardCopyOption.ATOMIC_MOVE)
                source
            } finally {
                // Only fixed, private scratch files are cleaned; completed source bundles are retained.
                File(pending,"source.jpg").delete();File(pending,"source.dng").delete();File(pending,"source.json").delete();pending.delete()
            }
        }
        require(source.path == "$name/${CaptureReceiptCodec.sourceName(source.record)}" && sourceFile(path,source).isFile)
        val receipt=CaptureReceipt(roll,index,frameId,material,!hold,(previous?.sources ?: emptyList())+source)
        transport.write(path) { it.write(CaptureReceiptCodec.encode(receipt)) }
        return receipt
    }
    fun finish(path: String): CaptureReceipt {
        val receipt=recover(path) ?: error("no saved exposure")
        if(receipt.finalized) return receipt
        return receipt.copy(finalized=true).also { value -> transport.write(path) { it.write(CaptureReceiptCodec.encode(value)) } }
    }
    /** Reconcile one published source bundle whose receipt write failed, without another camera call. */
    fun recover(path: String): CaptureReceipt? {
        val previous=if(exists(path)) read(path) else null
        if(previous?.finalized == true) return previous
        val ordinal=(previous?.sources?.size ?: 0)+1
        val name="exposure_${ordinal.toString().padStart(6,'0')}"
        val folder=File(transport.resolve(path).parentFile,name)
        if(!folder.exists()) return previous
        val metadata=File(folder,"source.json")
        require(metadata.isFile && metadata.length() <= 1024*1024) { "Incomplete saved exposure; preserved" }
        val bundle=CaptureReceiptCodec.decodeBundle(metadata.readBytes())
        require(path == path(bundle.rollId,bundle.frameIndex) && bundle.source.path == "$name/${CaptureReceiptCodec.sourceName(bundle.source.record)}")
        require(sourceFile(path,bundle.source).isFile)
        require(previous == null || (bundle.frameId == previous.frameId && RecipeJson.equivalent(bundle.film,previous.film)))
        val receipt=CaptureReceipt(bundle.rollId,bundle.frameIndex,bundle.frameId,bundle.film,false,
            (previous?.sources ?: emptyList())+bundle.source)
        transport.write(path) { it.write(CaptureReceiptCodec.encode(receipt)) }
        return receipt
    }
}
