package com.filmfoundry.interchange

import org.junit.jupiter.api.Assertions.assertEquals
import org.junit.jupiter.api.Assertions.assertFalse
import org.junit.jupiter.api.Assertions.assertThrows
import org.junit.jupiter.api.Assertions.assertTrue
import org.junit.jupiter.api.Test

class RecipeShareCodecTest {
    @Test fun desktopExposureTransportIsPreservedWithoutClaimingExecution() {
        val code=javaClass.getResource("/ffc2_desktop_exposure_sample.txt")!!.readText().trim()
        val decoded=RecipeShareCodec.decode(code)
        assertEquals(12.5,((decoded["exposure"] as Map<*,*>)["duration_seconds"] as Number).toDouble())
        assertEquals(decoded,RecipeShareCodec.decode(RecipeShareCodec.encode(decoded)))
        assertThrows(ConfigShareException::class.java) { RecipeShareCodec.encode(mapOf("exposure" to 3)) }
    }
    private val sample = mapOf(
        "film" to mapOf(
            "name" to "Pocket Custom",
            "contrast" to 1.37,
        ),
        "look" to mapOf(
            "exposure_ev" to -0.4,
        ),
    )

    @Test
    fun roundTripKeepsCustomValues() {
        val code = RecipeShareCodec.encode(sample)
        assertTrue(code.startsWith(DataKernel.CONFIG_SHARE_PREFIX))
        assertFalse(code.substring(DataKernel.CONFIG_SHARE_PREFIX.length).contains("/"))
        val decoded = RecipeShareCodec.decode("\uFEFF $code \n")
        val film = decoded["film"] as Map<*, *>
        val look = decoded["look"] as Map<*, *>
        assertEquals("Pocket Custom", film["name"])
        assertEquals(1.37, (film["contrast"] as Number).toDouble(), 1e-12)
        assertEquals(-0.4, (look["exposure_ev"] as Number).toDouble(), 1e-12)
    }

    @Test fun rejectsOversizedInputAndNeverEmitsAnUnreadableOversizedCode() {
        assertThrows(ConfigShareException::class.java) { RecipeShareCodec.decode("FFC2:"+"A".repeat(524289)) }
        assertThrows(ConfigShareException::class.java) { RecipeShareCodec.encode(mapOf("film" to mapOf("name" to "x".repeat(262145)))) }
    }
    @Test
    fun chemistryAliasBecomesDevelop() {
        val code = RecipeShareCodec.encode(
            mapOf("chemistry" to mapOf("time_min" to 7.25)),
        )
        val decoded = RecipeShareCodec.decode(code)
        assertFalse(decoded.containsKey("chemistry"))
        val develop = decoded["develop"] as Map<*, *>
        assertEquals(7.25, (develop["time_min"] as Number).toDouble(), 1e-12)
    }

    @Test
    fun rejectsPresetNameCodes() {
        assertThrows(ConfigShareException::class.java) {
            RecipeShareCodec.decode(
                "FFC1:clear_modern_negative/standard_color_negative/neutral_scan",
            )
        }
        assertThrows(ConfigShareException::class.java) {
            RecipeShareCodec.encode(
                mapOf(
                    "film" to "clear_modern_negative",
                    "develop" to "standard_color_negative",
                    "scanner" to "neutral_scan",
                ),
            )
        }
    }

    @Test
    fun decodesDesktopPythonFixture() {
        val code = javaClass.getResource("/ffc2_python_sample.txt")!!.readText().trim()
        val decoded = RecipeShareCodec.decode(code)
        val film = decoded["film"] as Map<*, *>
        val look = decoded["look"] as Map<*, *>
        assertEquals("Pocket Custom", film["name"])
        assertEquals(1.37, (film["contrast"] as Number).toDouble(), 1e-12)
        assertEquals(-0.4, (look["exposure_ev"] as Number).toDouble(), 1e-12)
    }
}
