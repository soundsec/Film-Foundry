package com.filmfoundry.app.ui

import org.junit.jupiter.api.Assertions.*
import org.junit.jupiter.api.Test

class StageOverrideValuesTest {
    @Test fun changingColorPresetsCannotChangeIndependentFilmInterpretation() {
        val source=mapOf("remove_base_mask" to true,"invert_transmission" to false)
        val a=mapOf("scan_saturation" to 1.2,"remove_base_mask" to false,"invert_transmission" to true)
        val b=mapOf("scan_saturation" to .8)
        for(baseMode in 0..2) for(inversionMode in 0..2) {
            val first=StageOverrideValues.interpretation(a,baseMode,inversionMode)
            val second=StageOverrideValues.interpretation(b,baseMode,inversionMode)
            assertEquals(first.filterKeys { it in StageOverrideValues.interpretationKeys },second.filterKeys { it in StageOverrideValues.interpretationKeys })
            assertEquals(1.2,first["scan_saturation"]);assertEquals(.8,second["scan_saturation"])
        }
        assertEquals(a-StageOverrideValues.interpretationKeys,StageOverrideValues.interpretation(a,0,0))
        assertEquals(source,StageOverrideValues.interpretation(b,1,2).filterKeys { it in StageOverrideValues.interpretationKeys })
        assertEquals(false,a["remove_base_mask"]);assertEquals(true,source["remove_base_mask"])
    }
    @Test fun automaticBaseChoiceDoesNotReuseAPresetAndManualOptionsRemainIndependent() {
        val preset=mapOf("remove_base_mask" to true,"invert_transmission" to false)
        assertFalse(StageOverrideValues.base(preset,0).containsKey("remove_base_mask"))
        assertEquals(true,StageOverrideValues.base(preset,1)["remove_base_mask"])
        assertEquals(false,StageOverrideValues.base(preset,2)["remove_base_mask"])
        assertEquals(preset,StageOverrideValues.base(preset,3))
        assertEquals(false,StageOverrideValues.base(preset,0)["invert_transmission"])
    }
    @Test fun explicitBleachingOffSuppressesAnInheritedOrBakedOverrideButInheritRetainsIt() {
        val saved=mapOf("mask_bleach_completion" to 1.0,"time_min" to 8.0)
        assertEquals(0.0,StageOverrideValues.bleach(saved,false,.7)["mask_bleach_completion"])
        assertEquals(saved,StageOverrideValues.bleach(saved,null,.7))
        assertEquals(.7,StageOverrideValues.bleach(saved,true,.7)["mask_bleach_completion"])
        assertEquals(1.0,saved["mask_bleach_completion"])
    }
    @Test fun automaticPolarityDoesNotManufactureANegativeInversionAndExplicitChoicesRemainPossible() {
        val ready=mapOf("scan_normalize" to true)
        assertFalse(StageOverrideValues.inversion(ready,0).containsKey("invert_transmission"))
        assertEquals(true,StageOverrideValues.inversion(ready,1)["invert_transmission"])
        assertEquals(false,StageOverrideValues.inversion(ready,2)["invert_transmission"])
        val explicitPreset=ready+("invert_transmission" to false)
        assertFalse(StageOverrideValues.inversion(explicitPreset,0).containsKey("invert_transmission"))
        assertEquals(explicitPreset,StageOverrideValues.inversion(explicitPreset,3))
    }
}
