package com.filmfoundry.app.ui

import org.junit.jupiter.api.Assertions.*
import org.junit.jupiter.api.Test

class ScanStyleChangesTest {
    @Test fun filmInterpretationSwitchesDoNotMarkAStyleAsCustom() {
        val style=mapOf("scanner" to mapOf("scan_saturation" to 1.2,"remove_base_mask" to true,"invert_transmission" to true))
        val manual=mapOf("scanner" to mapOf("scan_saturation" to 1.2,"remove_base_mask" to false,"invert_transmission" to false))
        assertTrue(ScanStyleChanges.between(style,manual).isEmpty())
        assertTrue(ScanStyleChanges.between(style,mapOf("scanner" to mapOf("scan_saturation" to 1.2))).isEmpty())
    }
    @Test fun presetCurvesAndExplicitDefaultValuesAreNotMislabelledAsCustom() {
        val style=mapOf("scanner" to mapOf("tone_curve" to listOf(listOf(0,0),listOf(1,1))))
        val current=mapOf("scanner" to (style["scanner"]!!+mapOf("scan_normalize_strength" to .45,"remove_base_mask" to true,"scan_saturation" to 1)))
        assertTrue(ScanStyleChanges.between(style,current).isEmpty())
    }
    @Test fun adjustmentsShowTheirKindsAndReturningToTheStyleClearsThem() {
        val style=mapOf("scanner" to mapOf("scan_saturation" to 1.2))
        val current=mapOf("scanner" to mapOf("scan_saturation" to 1.4,"tone_curve" to listOf(listOf(0,0),listOf(.5,.6),listOf(1,1))),"look" to mapOf("print_exposure_ev" to .5,"print_contrast" to 1.2))
        assertEquals(setOf(ScanStyleChanges.Kind.BRIGHTNESS,ScanStyleChanges.Kind.CONTRAST,ScanStyleChanges.Kind.COLOR,ScanStyleChanges.Kind.CURVES),ScanStyleChanges.between(style,current))
        assertTrue(ScanStyleChanges.between(style,style).isEmpty())
    }
    @Test fun outputResizingDoesNotChangeColorStyleAndInheritedValuesAreRespected() {
        val style=mapOf("scanner" to emptyMap<String,Any?>(),"transmission_light_ev" to .5)
        val sized=style+mapOf("scanner" to mapOf("scan_grid_long_edge" to 1280,"transmission_light_ev" to .5))
        assertTrue(ScanStyleChanges.between(style,sized).isEmpty())
        assertEquals(setOf(ScanStyleChanges.Kind.LIGHT),ScanStyleChanges.between(style,style+mapOf("scanner" to mapOf("transmission_light_ev" to 1))))
    }
}
