package com.filmfoundry.workflow

import com.filmfoundry.interchange.*
import com.filmfoundry.kernel.*
import java.io.File
import java.io.RandomAccessFile
import java.nio.ByteBuffer
import java.nio.ByteOrder

/** File scratch and output ownership belong to the application layer, never to the numerical core. */
object RowObservationSession {
    interface Output {
        fun begin(width: Int,height: Int)
        fun rows(start: Int,count: Int,rgb: FloatArray)
    }
    fun scan(io: RowMediumIo,path: String,recipe: Map<String,Any?>,output: Output,control: WorkflowControl=WorkflowControl(),
        memoryBudgetBytes: Long?=null,outputBytesPerPixel: Int=4,transmissionCompute: DensityTransmissionCompute?=null,handoff: RowMediumHandoff?=null): Map<String,Any?> {
        val frozen=MovieMasterCodec.snapshot(recipe)
        val mode=frozen["observe_mode"]?.toString() ?: "scanner"
        require(mode in listOf("scanner","viewer_projection","viewer_light_table")) { "Unsupported row observation mode" }
        control.checkCancel()
        val retained=handoff?.let { control.measured("scan","archive_handoff") { it.take(io,path) } }
        val spool=retained ?: control.measured("scan","archive_read") { io.read(path) { RowArchiveSpool.readOptical(it,io.scratchRoot,control::checkCancel) } }
        if(retained!=null) control.measured("scan","handoff_hit") { }
        return spool.use { source ->
            val scanner=frozen["scanner"] as? Map<*,*>
            val grid=PhoneObservationGrid.resolve(source.width,source.height,scanner?.get("scan_grid_long_edge") ?: frozen["scan_grid_long_edge"])
            val width=grid.width;val height=grid.height;val pixels=Math.multiplyExact(width,height)
            require(outputBytesPerPixel>=0)
            val needed=pixels.toLong()*outputBytesPerPixel+16L*1024*1024+maxOf(width,source.width)*15L*4*4
            if(memoryBudgetBytes!=null && needed>memoryBudgetBytes) throw WorkflowException("invalid_request","Output ${width}×${height} needs about ${needed/1048576} MiB; original film retained")
            require(io.scratchRoot.usableSpace>=(if(mode=="scanner") pixels.toLong()*12 else 0)+16L*1024*1024) { "Insufficient scan scratch storage" }
            val step=maxOf(1,16384/width);val math=PhoneRowObservation(frozen,transmissionCompute)
            fun readBlock(start: Int,count: Int): OpticalMaster = control.measured("scan","optical_read") {
                if(width==source.width && height==source.height) {
                    val block=source.opticalRows(start,count)
                    // The old identity box sampler adds to +0 then divides by one.
                    // Preserve its signed-zero behavior without resampling every field.
                    for(field in listOfNotNull(block.opticalDensityRgb,block.spectralComponentAmounts,block.spectralPostProcessDensityRgb)) {
                        val values=field.values
                        for(i in values.indices) if(values[i]==0f) values[i]=0f
                    }
                    OpticalMaster(block.opticalDensityRgb,block.metadata,block.clearBaseOpticalDensityRgb,block.spectralComponentAmounts,block.spectralPostProcessDensityRgb)
                } else PhoneRowObservation.sampleRows(source.width,source.height,width,height,start,count) { y ->
                    control.checkCancel();val row=source.opticalRows(y,1)
                    OpticalMaster(row.opticalDensityRgb,row.metadata,row.clearBaseOpticalDensityRgb,row.spectralComponentAmounts,row.spectralPostProcessDensityRgb)
                }
            }
            if(mode!="scanner") {
                // Viewers have no whole-image normalization. Stream directly into the display/PNG
                // instead of writing and reading an additional full RGB scratch file.
                output.begin(width,height)
                var metadata: Map<String,Any?> = emptyMap()
                for(start in 0 until height step step) {
                    control.checkCancel();val count=minOf(step,height-start)
                    val block=readBlock(start,count)
                    val linear=math.linear(block);metadata=linear.metadata;math.encode(linear.outputRgb.values)
                    output.rows(start,count,linear.outputRgb.values)
                }
                control.checkCancel()
                return@use metadata+if(width==source.width && height==source.height) emptyMap() else mapOf("observation_grid" to mapOf(
                    "model" to "scan_grid_v1","sampler" to if(source.width%width==0 && source.height%height==0) "box_average_v1" else "bilinear_v1",
                    "source_width" to source.width,"source_height" to source.height,"width" to width,"height" to height))
            }
            val lease=OwnedScratch.create(io.scratchRoot)
            val file=try { File.createTempFile("film-scan-",".f32",lease.directory) } catch(error: Throwable) { lease.close();throw error }
            try { RandomAccessFile(file,"rw").use { raw ->
                val bytes=ByteArray(maxOf(width,16384)*3*4);val buffer=ByteBuffer.wrap(bytes).order(ByteOrder.LITTLE_ENDIAN)
                val floats=buffer.asFloatBuffer()
                var metadata: Map<String,Any?> = emptyMap()
                for(start in 0 until height step step) {
                    control.checkCancel();val count=minOf(step,height-start)
                    val block=readBlock(start,count)
                    val linear=control.measured("scan","linear_compute") { math.linear(block) };metadata=linear.metadata
                    floats.clear();floats.put(linear.outputRgb.values)
                    raw.write(bytes,0,linear.outputRgb.values.size*4)
                }
                fun eachBlock(consume: (Int,Int,FloatArray)->Unit) {
                    raw.seek(0)
                    for(start in 0 until height step step) {
                        control.checkCancel();val count=minOf(step,height-start);val n=width*count*3
                        raw.readFully(bytes,0,n*4);floats.clear();val values=FloatArray(n);floats.get(values);consume(start,count,values)
                    }
                }
                val imageRows=FilmRebateGeometry.gateRows(source.opticalRows(0,1).metadata,height)
                val outputPlan=control.measured("scan","normalization") { math.outputPlan(width*imageRows.count()) { consume -> eachBlock { start,count,values ->
                    val a=maxOf(start,imageRows.first);val b=minOf(start+count,imageRows.last+1)
                    if(b>a) consume(if(a==start && b==start+count) values else values.copyOfRange((a-start)*width*3,(b-start)*width*3))
                } } }
                control.checkCancel();output.begin(width,height)
                control.measured("scan","encode_output") { eachBlock { start,count,values -> control.measured("scan","tone_encode") { math.output(values,outputPlan) };control.measured("scan","image_rows") { output.rows(start,count,values) } } }
                control.checkCancel()
                metadata+if(width==source.width && height==source.height) emptyMap() else mapOf("observation_grid" to mapOf(
                    "model" to "scan_grid_v1","sampler" to if(source.width%width==0 && source.height%height==0) "box_average_v1" else "bilinear_v1",
                    "source_width" to source.width,"source_height" to source.height,"width" to width,"height" to height))
            } } finally { lease.close() }
        }
    }
}
