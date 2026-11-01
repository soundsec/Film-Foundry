package com.filmfoundry.workflow

import org.junit.jupiter.api.Assertions.*
import org.junit.jupiter.api.Test

class PhoneMaterialDefaultsTest {
    @Test fun naturalKeepsStockStrengthsAndOtherLevelsAdjustThemWithoutChangingTheSource() {
        val stock=mapOf("grain_strength" to .045,"silver_grain_strength" to .06,"emulsion_mtf_strength" to .3,"halation_strength" to .2,
            "name" to "fixture","density_max" to listOf(2.0,1.9,2.1))
        val natural=PhoneMaterialDefaults.withLevels(stock,2,1,1,1.0)
        stock.forEach { (key,value)->assertEquals(value,natural[key]) }
        val fine=PhoneMaterialDefaults.withLevels(stock,1,1,1,1.0)
        val strong=PhoneMaterialDefaults.withLevels(stock,3,2,2,1.0)
        assertEquals(.0216,fine["grain_strength"] as Double,1e-12)
        assertEquals(.0288,fine["silver_grain_strength"] as Double,1e-12)
        assertEquals(.09,strong["grain_strength"] as Double,1e-12)
        assertEquals(.12,strong["silver_grain_strength"] as Double,1e-12)
        assertTrue(strong["emulsion_mtf_strength"] as Double>.3);assertEquals(.5,strong["halation_strength"])
        assertEquals(.045,stock["grain_strength"]);assertEquals(.06,stock["silver_grain_strength"])
    }
    @Test fun offDisablesSilverAsWellAsDyeGrainAndDefaultNaturalKeepsCurrentLoadingValues() {
        val off=PhoneMaterialDefaults.withLevels(mapOf("grain_strength" to .045,"silver_grain_strength" to .06),0,0,0,1.0)
        for(key in listOf("grain_strength","silver_grain_strength","emulsion_mtf_strength","halation_strength")) assertEquals(0.0,off[key])
        val normal=PhoneMaterialDefaults.withLevels(emptyMap(),2,1,1,1.0)
        assertEquals(.025,normal["grain_strength"]);assertEquals(.22,normal["emulsion_mtf_strength"]);assertEquals(.12,normal["halation_strength"])
        val limits=PhoneMaterialDefaults.withLevels(mapOf("grain_strength" to .2,"silver_grain_strength" to .4,"emulsion_mtf_strength" to .8,"halation_strength" to 1.8),3,2,2,1.0)
        assertEquals(.25,limits["grain_strength"]);assertEquals(.5,limits["silver_grain_strength"]);assertEquals(1.0,limits["emulsion_mtf_strength"]);assertEquals(2.0,limits["halation_strength"])
    }
}
