package com.filmfoundry.interchange

import org.junit.jupiter.api.Assertions.assertEquals
import org.junit.jupiter.api.Assertions.assertThrows
import org.junit.jupiter.api.Assertions.assertTrue
import org.junit.jupiter.api.Test
import java.nio.charset.StandardCharsets

class LibraryIndexCodecTest {
    @Test
    fun emptyCatalogRoundTrip() {
        val encoded = LibraryIndexCodec.encode(LibraryIndexDocument(emptyList(), emptyList()))
        val decoded = LibraryIndexCodec.decode(encoded)
        assertTrue(decoded.rolls.isEmpty())
        assertTrue(decoded.results.isEmpty())
        val text = String(encoded, StandardCharsets.UTF_8)
        assertTrue(text.contains(DataKernel.PHONE_LIBRARY_INDEX_KIND))
        assertTrue(!text.contains(".npz"))
    }

    @Test
    fun sealedRollAndResultRoundTrip() {
        val encoded = LibraryIndexCodec.encode(
            LibraryIndexDocument(
                rolls = listOf(rollMap("roll-c", latentIndex = 1, path = "f1.darkroom_latent.npz")),
                results = listOf(
                    linkedMapOf(
                        "result_id" to "result-a",
                        "source_roll_id" to "roll-c",
                        "source_frame_index" to 1,
                        "optical_path" to "a.darkroom_negative.npz",
                        "develop" to mapOf("time_min" to 8.0),
                        "source_roll_present" to true,
                    ),
                ),
            ),
        )
        val decoded = LibraryIndexCodec.decode(encoded)
        assertEquals("sealed", decoded.rolls.single()["lifecycle"])
        assertEquals("f1.darkroom_latent.npz", (decoded.rolls.single()["frames"] as List<*>).let { frames ->
            val frame = frames[0] as Map<*, *>
            frame["latent_path"]
        })
        assertEquals("result-a", decoded.results.single()["result_id"])
    }

    @Test
    fun truncatedJsonFailsClosed() {
        assertThrows(LibraryIndexException::class.java) {
            LibraryIndexCodec.decode("{".toByteArray(StandardCharsets.UTF_8))
        }
    }

    @Test
    fun consumedFrameIsNotALibraryState() {
        val roll = rollMap("roll-x", latentIndex = 1, path = "f1.darkroom_latent.npz").toMutableMap()
        val frames = (roll["frames"] as List<*>).map { item ->
            val frame = (item as Map<*, *>).entries.associate { it.key.toString() to it.value }.toMutableMap()
            if (frame["frame_index"] == 1) {
                frame["state"] = "consumed"
            }
            frame
        }
        roll["frames"] = frames
        assertThrows(LibraryIndexException::class.java) {
            LibraryIndexCodec.encode(LibraryIndexDocument(listOf(roll), emptyList()))
        }
    }

    @Test
    fun jpegIsNotALatentPath() {
        assertThrows(LibraryIndexException::class.java) {
            LibraryIndexCodec.encode(
                LibraryIndexDocument(
                    rolls = listOf(rollMap("roll-x", latentIndex = 1, path = "frame.jpg")),
                    results = emptyList(),
                ),
            )
        }
    }

    @Test
    fun unknownKindFailsClosed() {
        val json = """{"kind":"nope","version":1,"rolls":[],"results":[]}"""
        assertThrows(LibraryIndexException::class.java) {
            LibraryIndexCodec.decode(json.toByteArray(StandardCharsets.UTF_8))
        }
    }

    @Test fun legacyCatalogRemainsReadableAndNewCapturesCannotClaimLatents() {
        val legacy=javaClass.getResourceAsStream("/phone_library_index_sample.json")!!.use { it.readBytes() }
        assertTrue(LibraryIndexCodec.decode(legacy).rolls.isEmpty())
        val roll=rollMap("deferred",1,"old.darkroom_latent.npz").toMutableMap()
        roll["lifecycle"]="shooting"
        val frames=(roll["frames"] as List<*>).map { (it as Map<*,*>).entries.associate { it.key.toString() to it.value }.toMutableMap() }
        frames[0].apply { put("state","capturing");put("latent_path",null);put("capture_path","deferred/frame_01.capture/receipt.json");put("capture_exposures",2) }
        roll["frames"]=frames
        val encoded=LibraryIndexCodec.encode(LibraryIndexDocument(listOf(roll),emptyList()))
        val frame=((LibraryIndexCodec.decode(encoded).rolls[0]["frames"] as List<*>)[0] as Map<*,*>)
        assertEquals("capturing",frame["state"]);assertEquals(2,frame["capture_exposures"])
        frames[0]["latent_path"]="pretend.darkroom_latent.npz"
        assertThrows(LibraryIndexException::class.java) { LibraryIndexCodec.encode(LibraryIndexDocument(listOf(roll),emptyList())) }
    }

    private fun rollMap(rollId: String, latentIndex: Int, path: String): Map<String, Any?> {
        return linkedMapOf(
            "roll_id" to rollId,
            "capacity" to 12,
            "lifecycle" to "sealed",
            "label" to "night",
            "film" to emptyMap<String, Any?>(),
            "develop" to emptyMap<String, Any?>(),
            "scanner" to emptyMap<String, Any?>(),
            "frames" to (1..12).map { index ->
                linkedMapOf(
                    "frame_index" to index,
                    "frame_id" to "$rollId:$index",
                    "state" to if (index == latentIndex) "latent" else "empty",
                    "latent_path" to if (index == latentIndex) path else null,
                    "labels" to if (index == latentIndex) listOf("neon") else emptyList<String>(),
                )
            },
        )
    }
}
