package com.filmfoundry.workflow

import com.filmfoundry.interchange.PhoneProcessModels
import com.filmfoundry.kernel.*
import kotlinx.coroutines.runBlocking
import org.junit.jupiter.api.Assertions.*
import org.junit.jupiter.api.Test

class WorkflowLatentOwnershipTest {
    @Test fun independentDevelopmentChoicesPreserveBorrowedLatentAndMatchKernelReference() = runBlocking {
        val kernel=PhoneReducedKernel()
        val facade=DefaultWorkflowFacade(kernel)
        val recipe: Map<String,Any?> = mapOf("film" to mapOf("image_polarity" to "negative", "grain_strength" to 0.0),
            PhoneProcessModels.OPTION to PhoneProcessModels.CURRENT)
        val latent=kernel.expose(RgbVolume(3,1,linearRgb=floatArrayOf(.02f,.03f,.04f,.18f,.18f,.18f,1f,.8f,.6f)),recipe)
        val before=latent.arrays.mapValues { it.value.values.copyOf() }
        for(program in listOf("color_negative","color_reversal","bw_negative")) {
            val choice=recipe+("develop" to mapOf("program_key" to program))
            val expected=kernel.develop(latent,choice)
            val actual=facade.run(WorkflowRequest(Command.DEVELOP_LATENT,MediumRef("borrowed"),MediumRef("result"),choice,latent=latent)).optical!!
            assertArrayEquals(expected.opticalDensityRgb.values,actual.opticalDensityRgb.values,0f,program)
            before.forEach { (name,values)->assertArrayEquals(values,latent.arrays.getValue(name).values,0f,"$program: $name") }
            assertEquals(false,latent.metadata["consumed"])
        }
    }
}
