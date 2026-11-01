package com.filmfoundry.app.session

import com.filmfoundry.platform.*
import com.filmfoundry.workflow.*
import com.filmfoundry.interchange.*
import java.io.*
import java.nio.file.*
import kotlinx.coroutines.runBlocking
import org.junit.jupiter.api.Assertions.*
import org.junit.jupiter.api.Test
import org.junit.jupiter.api.io.TempDir

class DeferredShootSessionTest {
    @Test fun new120AndHalfGatesSaveSourcesAndFormTheAdvertisedWindowWhileV3StaysFrozen() = runBlocking {
        val formats=listOf("6x4.5","6x6","6x7","6x9","half")
        val cases=formats.map { it to StillFrameFormat.MODEL }+listOf("half" to StillFrameFormat.V3_MODEL)
        for((ordinal,entry) in cases.withIndex()) {
            val (format,model)=entry;val id="aperture-$ordinal"
            val length=if(StillFrameFormat.is120(format)) 120 else 12
            val index=MemoryLibraryIndexStore()
            val library=FilmLibrary(index);library.loadRoll(id,StillFrameFormat.capacity(format,length),emptyMap(),
                captureSettings=mapOf("format" to format,"stock_length" to length,"gate_revision" to model))
            var decoded=0
            val camera=object: StoredStillCamera {
                override fun saveStill(target: File): StoredStillInfo {
                    target.writeBytes(byteArrayOf(7))
                    return StoredStillInfo(120,90,CaptureRecord(CaptureKind.DISPLAY_REFERRED_SRGB,true,sourceEncoding="iec61966_2_1_srgb"))
                }
            }
            val session=DeferredShootSession(library,MediumStore(storage()),CaptureReceiptStore(root.toFile()),root.resolve("scratch").toFile(),openPixels= {
                decoded++;object: StoredExposurePixels {
                    override val width=120;override val height=90
                    override fun encodedRows(start: Int,count: Int)=FloatArray(width*count*3) { (it/3%width+start+it/(width*3))/210f }
                    override fun close()=Unit
                }
            })
            session.capture(id,camera,advanceMode="manual");assertEquals(0,decoded)
            library.rearmCurrentFrame(id);session.capture(id,camera,advanceMode="manual");assertEquals(0,decoded)
            assertEquals(2,library.roll(id).frames[0].captureExposures)
            val restored=FilmLibrary(index).roll(id)
            assertEquals(StillFrameFormat.capacity(format,length),restored.capacity)
            assertEquals(2,restored.frames[0].captureExposures)
            assertEquals(model,restored.captureSettings["gate_revision"])
            session.seal(id);session.prepareFrame(id,1)
            val actual=MediumStore(storage()).readLatent(cameraLatentPath(id,1))
            val gate=StillFrameFormat.create(format,false,120,90,model)
            val input=FloatArray(gate.width*gate.height*3) { i ->
                TransferFunctions.srgbToLinear((gate.left+i/3%gate.width+gate.top+i/(gate.width*3))/210f)*2f
            }
            val reference=DefaultWorkflowFacade().run(WorkflowRequests.expose("reference.darkroom_latent.npz",emptyMap(),gate.width,gate.height,input)).requireLatentArchive()
            for((key,array) in reference.arrays) assertArrayEquals(array.values,actual.arrays.getValue(key).values,key)
            assertArrayEquals(byteArrayOf(7),root.resolve("$id/frame_01.capture/exposure_000001/source.jpg").toFile().readBytes())
            assertArrayEquals(byteArrayOf(7),root.resolve("$id/frame_01.capture/exposure_000002/source.jpg").toFile().readBytes())
        }
        Unit
    }
    @Test fun rawIsSavedWithoutDecodingAndSharesTheExistingLatentFormationAndConditions() = runBlocking {
        val library=FilmLibrary();library.loadRoll("raw",12,emptyMap());var opened=0
        val rawCamera=object: StoredStillCamera {
            override fun sourceFileName()="source.dng"
            override fun saveStill(target: File): StoredStillInfo { target.writeBytes(byteArrayOf(42));return StoredStillInfo(2,2,CaptureRecord(CaptureKind.SENSOR_RAW_DNG,false,sourceEncoding="stored_sensor_dng_v1",bitDepth=16)) }
        }
        val session=DeferredShootSession(library,MediumStore(storage()),CaptureReceiptStore(root.toFile()),root.resolve("scratch").toFile(),openPixels= { file ->
            assertEquals("source.dng",file.name);opened++
            object: StoredExposurePixels {
                override val width=2;override val height=2
                override fun encodedRows(start: Int,count: Int): FloatArray=error("RAW must not use sRGB")
                override fun linearWindowRows(left: Int,top: Int,windowWidth: Int,count: Int,kind: CaptureKind): FloatArray { assertEquals(CaptureKind.SENSOR_RAW_DNG,kind);return FloatArray(windowWidth*count*3){2f} }
                override fun close()=Unit
            }
        })
        session.capture("raw",rawCamera,advanceMode="auto");assertEquals(0,opened)
        session.seal("raw");session.prepareFrame("raw",1)
        val latent=MediumStore(storage()).readLatent(cameraLatentPath("raw",1))
        val reference=DefaultWorkflowFacade().run(WorkflowRequests.expose("ref.darkroom_latent.npz",emptyMap(),2,2,FloatArray(12){2f})).requireLatentArchive()
        for((key,array) in reference.arrays) assertArrayEquals(array.values,latent.arrays.getValue(key).values,0f)
        val capture=latent.metadata["capture"] as Map<*,*>
        assertEquals("sensor_raw_dng",capture["kind"]);assertEquals(false,capture["isp_processed"]);assertEquals(DngRawDecoder.MODEL,capture["source_encoding"])
        assertArrayEquals(byteArrayOf(42),root.resolve("raw/frame_01.capture/exposure_000001/source.dng").toFile().readBytes())
        Unit
    }
    @Test fun nativeSizeChangeIsRejectedBeforeAnotherExposureAndOriginalReceiptStaysUnchanged() = runBlocking {
        val library=FilmLibrary();library.loadRoll("native-size",12,emptyMap());val session=session(library)
        session.capture("native-size",camera,advanceMode="manual");library.rearmCurrentFrame("native-size")
        val path=library.roll("native-size").frames[0].capturePath!!;val before=root.resolve(path).toFile().readBytes()
        val changed=object: StoredStillCamera {
            override fun plannedDimensions()=4 to 3
            override fun saveStill(target: File): StoredStillInfo=error("Must reject before touching the shutter")
        }
        assertThrows(IllegalArgumentException::class.java) { runBlocking { session.capture("native-size",changed,advanceMode="manual") } }
        assertArrayEquals(before,root.resolve(path).toFile().readBytes());assertEquals(1,shots)
        assertTrue(library.roll("native-size").frames[0].shutterArmed)
        Unit
    }
    @Test fun storedHdrOnlyReconstructsDuringDeferredFormationAndKeepsNativeSourceIdentity() = runBlocking {
        val library=FilmLibrary();library.loadRoll("native-hdr",12,emptyMap());var opened=0
        val hdrCamera=object: StoredStillCamera {
            override fun saveStill(target: File): StoredStillInfo {
                target.writeBytes(byteArrayOf(7))
                return StoredStillInfo(2,2,CaptureRecord(CaptureKind.ULTRA_HDR_GAINMAP,true,sourceEncoding="stored_ultra_hdr_jpeg_r_v1",hdrGainmap="present_not_applied"),
                    mapOf("native_camera" to mapOf("camera_id" to "logical","requested_physical_id" to null)))
            }
        }
        val session=DeferredShootSession(library,MediumStore(storage()),CaptureReceiptStore(root.toFile()),root.resolve("scratch").toFile(),openPixels= {
            opened++;object: StoredExposurePixels {
                override val width=2;override val height=2
                override fun encodedRows(start: Int,count: Int): FloatArray=error("HDR must not fall back to encoded SDR")
                override fun linearWindowRows(left: Int,top: Int,windowWidth: Int,count: Int,kind: CaptureKind): FloatArray {
                    assertEquals(CaptureKind.ULTRA_HDR_GAINMAP,kind);return FloatArray(windowWidth*count*3){2f}
                }
                override fun close()=Unit
            }
        })
        session.capture("native-hdr",hdrCamera,advanceMode="auto");assertEquals(0,opened)
        session.seal("native-hdr");session.prepareFrame("native-hdr",1)
        val latent=MediumStore(storage()).readLatent(cameraLatentPath("native-hdr",1))
        val reference=DefaultWorkflowFacade().run(WorkflowRequests.expose("ref.darkroom_latent.npz",emptyMap(),2,2,FloatArray(12){2f})).requireLatentArchive()
        for((key,array) in reference.arrays) assertArrayEquals(array.values,latent.arrays.getValue(key).values,key)
        val capture=latent.metadata["capture"] as Map<*,*>
        assertEquals("applied",capture["hdr_gainmap"]);assertEquals("ultra_hdr_gainmap",capture["kind"])
        assertEquals("logical",(((capture["stored_exposures"] as List<*>).single() as Map<*,*>)["capture"] as Map<*,*>).let { (it["native_camera"] as Map<*,*>)["camera_id"] })
        assertArrayEquals(byteArrayOf(7),root.resolve("native-hdr/frame_01.capture/exposure_000001/source.jpg").toFile().readBytes())
        Unit
    }
    @Test fun multipleExposureDoesNotRecordOrCompareOrientation() = runBlocking {
        val library=FilmLibrary();library.loadRoll("orientation",12,emptyMap())
        var calls=0;var orientation=90
        val adapter=object: StoredStillCamera {
            override fun plannedOrientationDegrees()=orientation
            override fun saveStill(target: File): StoredStillInfo {
                calls++;target.writeBytes(byteArrayOf(1))
                return StoredStillInfo(2,2,CaptureRecord(CaptureKind.DISPLAY_REFERRED_SRGB,true,sourceEncoding="iec61966_2_1_srgb",orientationDegrees=orientation))
            }
        }
        val session=session(library)
        session.capture("orientation",adapter,advanceMode="manual")
        library.rearmCurrentFrame("orientation");orientation=0
        session.capture("orientation",adapter,advanceMode="manual")
        assertEquals(2,calls);assertEquals(2,library.roll("orientation").frames[0].captureExposures)
        val receipt=CaptureReceiptStore(root.toFile()).read(library.roll("orientation").frames[0].capturePath!!)
        assertTrue(receipt.sources.all { "orientation_degrees" !in it.record })
        assertEquals(0,decodes)
        Unit
    }
    @Test fun squareFormatActuallyFormsCenteredSixBySixMaterialAndLeavesSourceIntact() = runBlocking {
        val library=FilmLibrary();library.loadRoll("square",12,emptyMap(),captureSettings=mapOf("format" to "6x6"))
        val camera=object: StoredStillCamera {
            override fun saveStill(target: File): StoredStillInfo {
                target.writeBytes(byteArrayOf(7))
                return StoredStillInfo(8,6,CaptureRecord(CaptureKind.DISPLAY_REFERRED_SRGB,true,sourceEncoding="iec61966_2_1_srgb"))
            }
        }
        val session=DeferredShootSession(library,MediumStore(storage()),CaptureReceiptStore(root.toFile()),root.resolve("scratch").toFile(),openPixels= {
            object: StoredExposurePixels {
                override val width=8;override val height=6
                override fun encodedRows(start: Int,count: Int)=FloatArray(width*count*3){(it/3%width)/10f}
                override fun close()=Unit
            }
        })
        session.capture("square",camera,advanceMode="auto");session.seal("square");session.prepareFrame("square",1)
        val actual=MediumStore(storage()).readLatent(cameraLatentPath("square",1))
        val expectedInput=FloatArray(6*6*3) { TransferFunctions.srgbToLinear((it/3%6+1)/10f) }
        val reference=DefaultWorkflowFacade().run(WorkflowRequests.expose("reference.darkroom_latent.npz",emptyMap(),6,6,expectedInput)).requireLatentArchive()
        assertArrayEquals(intArrayOf(6,6,3),actual.arrays.getValue("developability").shape)
        for((key,array) in reference.arrays) assertArrayEquals(array.values,actual.arrays.getValue(key).values,key)
        assertArrayEquals(byteArrayOf(7),root.resolve("square/frame_01.capture/exposure_000001/source.jpg").toFile().readBytes())
        Unit
    }
    @Test fun manualShutterNeedsDurableCockingAndNeverAdvancesForMultipleExposure() = runBlocking {
        val memory=MemoryLibraryIndexStore();var fail=false
        val index=object: LibraryIndexStore by memory {
            override fun writeIndex(bytes: ByteArray) { if(fail) throw IOException("index failed");memory.writeIndex(bytes) }
        }
        var library=FilmLibrary(index);library.loadRoll("manual",72,emptyMap())
        var session=session(library)
        session.capture("manual",camera,advanceMode="manual")
        assertFalse(library.roll("manual").frames[0].shutterArmed)
        assertThrows(IllegalArgumentException::class.java) { runBlocking { session.capture("manual",camera,advanceMode="manual") } }
        assertEquals(1,shots)
        assertThrows(IllegalArgumentException::class.java) { runBlocking { session.capture("manual",camera) } }
        library=FilmLibrary(index);session=session(library)
        assertFalse(library.roll("manual").frames[0].shutterArmed)
        fail=true;assertThrows(IOException::class.java) { library.rearmCurrentFrame("manual") }
        assertFalse(library.roll("manual").frames[0].shutterArmed);fail=false
        library.rearmCurrentFrame("manual")
        library=FilmLibrary(index);session=session(library)
        assertTrue(library.roll("manual").frames[0].shutterArmed)
        session.capture("manual",camera,advanceMode="manual")
        assertEquals(1,library.roll("manual").exposedCount());assertEquals(2,library.roll("manual").frames[0].captureExposures)
        assertFalse(library.roll("manual").frames[0].shutterArmed)
        session.finishFrame("manual")
        assertEquals(2,session.capture("manual",camera,advanceMode="manual").frame.frameIndex)
        assertEquals(3,shots);assertEquals(0,decodes)
        Unit
    }
    @Test fun wideGateKeeps135DimensionsAndUnexposedPoolsWithoutDoublingNativeEv() = runBlocking {
        for(model in listOf(StillFrameFormat.LEGACY_MODEL,StillFrameFormat.MODEL)) for(rotation in listOf(0,90)) {
            val library=FilmLibrary();val id="gate-$model-$rotation"
            library.loadRoll(id,12,emptyMap(),captureSettings=mapOf("format" to "135","gate_revision" to model))
            var addNativeRecord=true
            val camera=object: StoredStillCamera {
                override fun saveStill(target: File): StoredStillInfo {
                    target.writeBytes(byteArrayOf(1))
                    return StoredStillInfo(12,8,CaptureRecord(CaptureKind.DISPLAY_REFERRED_SRGB,true,
                        sourceEncoding="iec61966_2_1_srgb",orientationDegrees=rotation),if(addNativeRecord) mapOf("native_exposure_compensation" to mapOf("ev" to 2.0)) else emptyMap())
                }
            }
            val session=DeferredShootSession(library,MediumStore(storage()),CaptureReceiptStore(root.toFile()),root.resolve("scratch").toFile(),openPixels= {
                object: StoredExposurePixels {
                    override val width=12;override val height=8
                    override fun encodedRows(start: Int,count: Int)=FloatArray(width*count*3){.4f}
                    override fun close()=Unit
                }
            })
            session.capture(id,camera,advanceMode="manual",panorama=true)
            assertTrue(library.roll(id).frames[0].gatePanorama)
            assertFalse(library.roll(id).frames[0].shutterArmed)
            val receipt=CaptureReceiptStore(root.toFile()).read(library.roll(id).frames[0].capturePath!!)
            assertEquals(0.0,receipt.sources[0].ev)
            session.seal(id);session.prepareFrame(id,1)
            val actual=MediumStore(storage()).readLatent(cameraLatentPath(id,1))
            val pool=actual.arrays.getValue("developability");assertEquals(8,pool.shape[0]);assertEquals(12,pool.shape[1])
            addNativeRecord=false
            val plain="$id-plain";library.loadRoll(plain,12,emptyMap(),captureSettings=mapOf("format" to "135","gate_revision" to model))
            session.capture(plain,camera,advanceMode="auto");session.seal(plain);session.prepareFrame(plain,1)
            val reference=MediumStore(storage()).readLatent(cameraLatentPath(plain,1)).arrays.getValue("developability")
            val area=StillFrameFormat.create("135",true,12,8,model).openArea()
            assertEquals(if(model==StillFrameFormat.LEGACY_MODEL) 5 else 4,area[3])
            for(y in 0 until 8) for(x in 0 until 12) {
                val value=pool.values[(y*12+x)*3]
                if(x < area[0] || x >= area[0]+area[2] || y < area[1] || y >= area[1]+area[3]) assertEquals(0f,value)
                else assertEquals(reference.values[(y*12+x)*3],value)
            }
        }
        Unit
    }
    @Test fun mixedWideGateAppliesEachFrozenExposureBeforeSumming() = runBlocking {
        for(wideFirst in listOf(true,false)) {
            val library=FilmLibrary();val id="mixed-$wideFirst"
            library.loadRoll(id,12,emptyMap(),captureSettings=mapOf("format" to "135","gate_revision" to StillFrameFormat.MODEL))
            val camera=object: StoredStillCamera {
                override fun saveStill(target: File): StoredStillInfo {
                    target.writeBytes(byteArrayOf(7))
                    return StoredStillInfo(12,8,CaptureRecord(CaptureKind.DISPLAY_REFERRED_SRGB,true,sourceEncoding="iec61966_2_1_srgb"))
                }
            }
            val store=CaptureReceiptStore(root.toFile())
            val session=DeferredShootSession(library,MediumStore(storage()),store,root.resolve("scratch").toFile(),openPixels= {
                object: StoredExposurePixels {
                    override val width=12;override val height=8
                    override fun encodedRows(start: Int,count: Int)=FloatArray(width*count*3){.4f}
                    override fun close()=Unit
                }
            })
            session.capture(id,camera,advanceMode="manual",panorama=wideFirst)
            val path=library.roll(id).frames[0].capturePath!!;val original=store.read(path).sources[0]
            library.rearmCurrentFrame(id)
            session.capture(id,camera,advanceMode="manual",panorama=!wideFirst)
            val receipt=store.read(path)
            assertEquals(original,receipt.sources[0]);assertEquals(2,receipt.sources.size)
            assertFalse(library.roll(id).frames[0].gatePanorama)
            session.seal(id);session.prepareFrame(id,1)
            val actual=MediumStore(storage()).readLatent(cameraLatentPath(id,1))
            val area=StillFrameFormat.create("135",true,12,8).openArea()
            val light=TransferFunctions.srgbToLinear(.4f)
            val expected=FloatArray(12*8*3) { i->light*if(i/3/12 in area[1] until area[1]+area[3]) 2f else 1f }
            val reference=DefaultWorkflowFacade().run(WorkflowRequests.expose("reference.darkroom_latent.npz",emptyMap(),12,8,expected)).requireLatentArchive()
            for((key,array) in reference.arrays) assertArrayEquals(array.values,actual.arrays.getValue(key).values,key)
            assertEquals("per_exposure_v1",(actual.metadata["capture"] as Map<*,*>)["gate_application"])
            assertArrayEquals(byteArrayOf(7),store.sourceFile(path,original).readBytes())
        }
        Unit
    }
    @TempDir lateinit var root: Path
    private var shots=0
    private var decodes=0
    private val camera=object: StoredStillCamera {
        override fun saveStill(target: File): StoredStillInfo {
            shots++;target.writeBytes(byteArrayOf(shots.toByte()))
            return StoredStillInfo(2,2,CaptureRecord(CaptureKind.DISPLAY_REFERRED_SRGB,true,sourceEncoding="iec61966_2_1_srgb",bitDepth=8))
        }
    }
    private fun session(library: FilmLibrary,facade: WorkflowFacade=DefaultWorkflowFacade())=
        DeferredShootSession(library,MediumStore(storage()),CaptureReceiptStore(root.toFile()),root.resolve("scratch").toFile(),facade,openPixels= { file ->
            decodes++;val value=if(file.readBytes()[0].toInt()%2 == 1) .4f else .7f
            object: StoredExposurePixels {
                override val width=2;override val height=2
                override fun encodedRows(start: Int,count: Int)=FloatArray(2*count*3){value}
                override fun close()=Unit
            }
        })
    @Test fun shutterDoesNoDecodingOrLatentWorkAndMultipleExposureUsesOneSlot() = runBlocking {
        val index=MemoryLibraryIndexStore();val library=FilmLibrary(index);library.loadRoll("roll",12,emptyMap())
        val facade=object: WorkflowFacade { override suspend fun run(request: WorkflowRequest): WorkflowResult=error("shutter must not form material") }
        val session=session(library,facade)
        assertFalse(session.capture("roll",camera,true,-1.0).finalized)
        assertEquals(FrameState.CAPTURING,library.roll("roll").frames[0].state)
        session.capture("roll",camera,true,-2.0)
        assertThrows(WorkflowException::class.java) { library.commitCameraShot("roll",2,"skip.darkroom_latent.npz") }
        assertEquals(1,library.roll("roll").exposedCount());assertEquals(2,shots);assertEquals(0,decodes)
        assertFalse(Files.exists(root.resolve(cameraLatentPath("roll",1))))
        val resumed=FilmLibrary(index)
        assertEquals(2,resumed.roll("roll").frames[0].captureExposures)
        session(resumed,facade).finishFrame("roll")
        assertEquals(FrameState.CAPTURED,resumed.roll("roll").frames[0].state)
        assertEquals(2,session(resumed,facade).capture("roll",camera).frame.frameIndex)
        assertEquals(3,shots);assertEquals(0,decodes)
    }
    @Test fun changedCaptureDimensionsDoNotPublishAnUnusableSecondExposure() = runBlocking {
        val library=FilmLibrary();library.loadRoll("dimensions",12,emptyMap());val session=session(library)
        session.capture("dimensions",camera,true)
        val other=object: StoredStillCamera {
            override fun saveStill(target: File): StoredStillInfo { target.writeBytes(byteArrayOf(8));return StoredStillInfo(3,2,
                CaptureRecord(CaptureKind.DISPLAY_REFERRED_SRGB,true,sourceEncoding="iec61966_2_1_srgb")) }
        }
        assertThrows(IllegalArgumentException::class.java) { runBlocking { session.capture("dimensions",other,true) } }
        assertEquals(1,library.roll("dimensions").frames[0].captureExposures)
        assertFalse(Files.exists(root.resolve("dimensions/frame_01.capture/exposure_000002")))
        session.finishFrame("dimensions");session.seal("dimensions")
        assertEquals(FrameState.CAPTURED,library.roll("dimensions").frames[0].state)
    }
    @Test fun indexFailureAfterAnotherExposureIsRecoveredWithoutRetakingEvenAfterRestart() = runBlocking {
        val memory=MemoryLibraryIndexStore();var fail=false
        val index=object: LibraryIndexStore by memory {
            override fun writeIndex(bytes: ByteArray) { if(fail) throw IOException("index unavailable");memory.writeIndex(bytes) }
        }
        val library=FilmLibrary(index);library.loadRoll("retry",12,mapOf("contrast" to 1.0,"grain_strength" to 0.0))
        session(library).capture("retry",camera,true)
        fail=true
        assertThrows(IOException::class.java) { runBlocking { session(library).capture("retry",camera,true) } }
        assertEquals(1,library.roll("retry").frames[0].captureExposures)
        fail=false
        val resumed=FilmLibrary(index)
        val result=session(resumed).capture("retry",camera,true)
        assertTrue(result.recovered);assertEquals(2,result.exposures);assertEquals(2,shots)
        session(resumed).seal("retry")
        assertEquals(2,resumed.roll("retry").frames[0].captureExposures)
        assertEquals(RollLifecycle.SEALED,resumed.roll("retry").lifecycle)
    }
    @Test fun publishedBundleWithoutReceiptCanBeSealedWithoutCamera() = runBlocking {
        val library=FilmLibrary();library.loadRoll("bundle",12,emptyMap())
        val folder=root.resolve("bundle/frame_01.capture/exposure_000001");Files.createDirectories(folder)
        Files.write(folder.resolve("source.jpg"),byteArrayOf(1))
        val source=StoredExposure("exposure_000001/source.jpg",2,2,-1.0,
            CaptureRecord(CaptureKind.DISPLAY_REFERRED_SRGB,true,sourceEncoding="iec61966_2_1_srgb").toMetadata())
        Files.write(folder.resolve("source.json"),CaptureReceiptCodec.encodeBundle(StoredExposureBundle("bundle",1,"bundle:1",emptyMap(),source)))
        val sealed=session(library).seal("bundle")
        assertEquals(1,sealed.exposedCount());assertEquals(0,shots);assertEquals(0,decodes)
        assertEquals(FrameState.CAPTURED,sealed.frames[0].state)
    }
    @Test fun multipleExposureFormationMatchesLinearSumAndKeepsSources() = runBlocking {
        val library=FilmLibrary();library.loadRoll("sum",12,emptyMap())
        val session=session(library)
        session.capture("sum",camera,true,-1.0);session.capture("sum",camera,true,-2.0)
        val path=library.roll("sum").frames[0].capturePath!!
        val captures=CaptureReceiptStore(root.toFile());val receipt=captures.read(path)
        val before=receipt.sources.map { captures.sourceFile(path,it).readBytes() }
        session.seal("sum");session.prepareRoll("sum")
        val medium=MediumStore(storage());val actual=medium.readLatent(cameraLatentPath("sum",1))
        val input=listOf(.4f,.7f).map { EncodedRgbCapture(2,2,FloatArray(12){_->it}) }
        val sum=DefaultCaptureNormalizer().normalize(ExposureStackCapture(input,floatArrayOf(-1f,-2f)))
        DefaultWorkflowFacade().run(WorkflowRequests.expose("reference.darkroom_latent.npz",emptyMap(),2,2,sum.linearRgb).copy(rowIo=medium.rowExecutionIo()))
        val reference=medium.readLatent("reference.darkroom_latent.npz")
        for((name,array) in reference.arrays) assertArrayEquals(array.values,actual.arrays.getValue(name).values,name)
        assertEquals(2,((actual.metadata["capture"] as Map<*,*>)["frame_count"] as Number).toInt())
        for(i in before.indices) assertArrayEquals(before[i],captures.sourceFile(path,receipt.sources[i]).readBytes())
        assertEquals(1,library.roll("sum").latentCount())
        val decodeCount=decodes;session.prepareRoll("sum");assertEquals(decodeCount,decodes)
        assertTrue(root.resolve("scratch").toFile().listFiles()!!.isEmpty())
    }
    @Test fun fullRollStillAllowsMultipleExposuresOnLastFrameAndNeverCapturesBeyondCapacity() = runBlocking {
        val library=FilmLibrary();library.loadRoll("full",12,emptyMap());val session=session(library)
        repeat(11) { session.capture("full",camera) }
        session.capture("full",camera,true);session.capture("full",camera,true)
        assertEquals(12,library.roll("full").exposedCount());assertEquals(13,shots)
        session.finishFrame("full")
        assertThrows(WorkflowException::class.java) { runBlocking { session.capture("full",camera) } }
        assertEquals(13,shots)
        session.seal("full");assertEquals(0,decodes)
    }
    @Test fun cameraConditionsFreezePerShutterAndFilmAgeAddsFogOncePerFrame() = runBlocking {
        val library=FilmLibrary();library.loadRoll("age",12,emptyMap(),captureSettings=mapOf("age" to .5))
        val session=session(library);val cameraSettings=mutableMapOf<String,Any?>("leak" to .4,"leak_side" to "left","seed" to 3L)
        session.capture("age",camera,true,-1.0,cameraSettings=cameraSettings)
        cameraSettings["leak"]=0.0
        val captures=CaptureReceiptStore(root.toFile())
        val first=captures.read(library.roll("age").frames[0].capturePath!!)
        assertEquals(.4,(((first.sources[0].record["exposure_conditions"] as Map<*,*>)["camera"] as Map<*,*>)["leak"] as Number).toDouble())
        // Use a second roll with no camera defects to compare the one-time film fog with the old linear reference.
        library.loadRoll("age-sum",12,emptyMap(),captureSettings=mapOf("age" to .5))
        session.capture("age-sum",camera,true,-1.0);session.capture("age-sum",camera,true,-1.0)
        session.seal("age-sum");session.prepareRoll("age-sum")
        val sum=(TransferFunctions.srgbToLinear(.7f)*.5f+TransferFunctions.srgbToLinear(.4f)*.5f)*(1f-.5f*.55f)+.5f*.018f
        val medium=MediumStore(storage())
        DefaultWorkflowFacade().run(WorkflowRequests.expose("age-reference.darkroom_latent.npz",emptyMap(),2,2,FloatArray(12){sum}).copy(rowIo=medium.rowExecutionIo()))
        assertArrayEquals(medium.readLatent("age-reference.darkroom_latent.npz").arrays.getValue("developability").values,
            medium.readLatent(cameraLatentPath("age-sum",1)).arrays.getValue("developability").values)
    }
    @Test fun cancellationAndMissingSourcesDoNotPretendLatentsOrRetake() = runBlocking {
        val library=FilmLibrary();library.loadRoll("cancel",12,emptyMap());val session=session(library)
        assertThrows(WorkflowException::class.java) { runBlocking { session.capture("cancel",camera,control=WorkflowControl(cancelRequested={true})) } }
        assertEquals(0,shots)
        session.capture("cancel",camera);session.seal("cancel")
        assertThrows(WorkflowException::class.java) { runBlocking { session.prepareRoll("cancel",WorkflowControl(cancelRequested={true})) } }
        assertEquals(FrameState.CAPTURED,library.roll("cancel").frames[0].state)
        Files.delete(root.resolve("cancel/frame_01.capture/exposure_000001/source.jpg"))
        assertThrows(Exception::class.java) { runBlocking { session.prepareRoll("cancel") } }
        assertEquals(FrameState.CAPTURED,library.roll("cancel").frames[0].state)
        assertEquals(1,shots)
    }
    private fun storage(): StorageAdapter=object: StorageAdapter by MemoryStorageAdapter() {
        override fun existsPrivate(relative: String)=Files.isRegularFile(root.resolve(relative))
        override fun rowScratchRoot()=root.resolve("scratch").toFile()
        override fun samePrivateLocation(a: String,b: String)=root.resolve(a).normalize()==root.resolve(b).normalize()
        override fun <T> readPrivate(relative: String,reader: (InputStream)->T)=root.resolve(relative).toFile().inputStream().use(reader)
        override fun writePrivate(relative: String,writer: (OutputStream)->Unit)=writePrivate(relative,{},writer)
        override fun writePrivate(relative: String,beforeCommit: ()->Unit,writer: (OutputStream)->Unit) {
            val path=root.resolve(relative);Files.createDirectories(path.parent)
            val pending=Files.createTempFile(path.parent,"pending-",".tmp")
            try { pending.toFile().outputStream().use(writer);beforeCommit();Files.move(pending,path,StandardCopyOption.ATOMIC_MOVE,StandardCopyOption.REPLACE_EXISTING) }
            finally { Files.deleteIfExists(pending) }
        }
    }
}
