package com.filmfoundry.workflow

import com.filmfoundry.interchange.PhoneScanModels
import org.junit.jupiter.api.Assertions.*
import org.junit.jupiter.api.Test

class ObservationImplementationTest {
    @Test fun allFrozenModelsRestoreTheirOwnExecutionAndNewScansUseCalibratedExposure() {
        val device=mapOf("scanner" to mapOf("scanner_response_matrix" to List(3){y->List(3){x->if(x==y) .9 else 0.0}}))
        for((model,recipe,implementation) in listOf(
            Triple(ObservationImplementation.LEGACY,emptyMap(),PhoneScanModels.LEGACY),
            Triple(ObservationImplementation.DEVICE_RESPONSE,device,PhoneScanModels.LEGACY),
            Triple(ObservationImplementation.RESULT_DEFAULTS,emptyMap(),PhoneScanModels.RESULT_BASE),
            Triple(ObservationImplementation.RESULT_DEVICE,device,PhoneScanModels.RESULT_BASE),
            Triple(ObservationImplementation.CALIBRATED,emptyMap(),PhoneScanModels.CALIBRATED),
            Triple(ObservationImplementation.CALIBRATED_DEVICE,device,PhoneScanModels.CALIBRATED),
            Triple(ObservationImplementation.RANGE_FIRST,emptyMap(),PhoneScanModels.CURRENT),
            Triple(ObservationImplementation.RANGE_FIRST_DEVICE,device,PhoneScanModels.CURRENT))) {
            val runtime=ObservationImplementation.frozenRuntime(recipe,model)
            assertEquals(implementation,runtime[PhoneScanModels.OPTION]);assertEquals(model,ObservationImplementation.forRecipe(runtime))
        }
        assertEquals(ObservationImplementation.RANGE_FIRST,ObservationImplementation.forRecipe(emptyMap()))
        assertEquals(ObservationImplementation.RANGE_FIRST_DEVICE,ObservationImplementation.forRecipe(device))
        assertThrows(IllegalArgumentException::class.java) { ObservationImplementation.frozenRuntime(emptyMap(),"unknown") }
        assertThrows(IllegalArgumentException::class.java) { ObservationImplementation.frozenRuntime(device,ObservationImplementation.RESULT_DEFAULTS) }
    }
}
