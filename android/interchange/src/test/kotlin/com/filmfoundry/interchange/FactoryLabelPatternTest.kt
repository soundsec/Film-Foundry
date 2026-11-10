package com.filmfoundry.interchange

import org.junit.jupiter.api.Assertions.*
import org.junit.jupiter.api.Test

class FactoryLabelPatternTest {
    @Test fun sameCanonicalPlateAsDesktopAndStableRoundtrip() {
        val expected="FFEM1:128x9:00000000000000000000000A000000043EF8011C700000000000000A2EBEEB8420800322880000000000000A2EBEEB8420800526980000000000000A2EBEEB843CF0092AA80000000000000A2EBEEB8420800FB2C80000000000000A2EBEEB8420800122880000000000000A2EBEEB842080011C700000000000000A2EBEEB8400000000000000000000000A00000004"
        assertEquals(expected,FactoryLabelPattern.create("ff 400",1234).encode())
        assertEquals(expected,EdgeMarkPattern.decode(expected).encode())
        assertThrows(IllegalArgumentException::class.java) { FactoryLabelPattern.create("X".repeat(15)) }
        assertThrows(IllegalArgumentException::class.java) { FactoryLabelPattern.create("FF",4096) }
    }
}
