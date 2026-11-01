package com.filmfoundry.kernel

import com.filmfoundry.interchange.*
import org.junit.jupiter.api.Assertions.*
import org.junit.jupiter.api.Test

class PhoneActivationLedgerTest {
    private fun program(steps: List<Map<String,Any?>>,color: Boolean=false,ledger: Boolean=true)=
        PhoneProcessProgram.from(mapOf("program_key" to "custom","process_steps" to steps),color,false,interactive=true,activationLedger=ledger)!!
    private fun develop(action: String="develop_silver",strength: Double=1.0)=mapOf<String,Any?>("action" to action,"strength" to strength)

    @Test fun repeatedSilverAndColorDevelopmentCannotReuseConsumedLatentHalide() {
        for(color in listOf(false,true)) {
            val action=if(color) "develop_color" else "develop_silver"
            val p=PhoneProcessProgram.Pools(1f,.5f,0f,1f,0f)
            program(List(2) { develop(action) },color).apply(p,color,1f)
            assertEquals(.5f,p.silver,1e-6f);assertEquals(.5f,p.halide,1e-6f);assertEquals(0f,p.activation,1e-6f)
            if(color) { assertEquals(.5f,p.dye,1e-6f);assertEquals(.5f,p.coupler,1e-6f) }
            val partial=PhoneProcessProgram.Pools(1f,.5f,0f,1f,0f)
            program(List(2) { develop(action,.5) },color).apply(partial,color,1f)
            assertEquals(.375f,partial.silver,1e-6f)
            assertEquals(.125f,partial.halide*partial.activation,1e-6f)
            assertEquals(.5f,partial.halide*(1-partial.activation),1e-6f)
        }
    }
    @Test fun gammaChangesKineticsWhileFogAndReversalExplicitlyActivateMaterial() {
        val contrast=PhoneProcessProgram.Pools(1f,.5f,0f)
        program(List(2) { develop()+("developer_type" to "high_contrast") }).apply(contrast,false,1f)
        assertTrue(contrast.silver>0f && contrast.silver<=.5f)
        assertEquals(.5f,contrast.halide*contrast.activation+contrast.silver,1e-6f)
        val untouched=PhoneProcessProgram.Pools(1f,.5f,0f)
        program(listOf(develop(strength=0.0)+("developer_type" to "exhausted"))).apply(untouched,false,1f)
        assertEquals(.5f,untouched.activation,0f);assertEquals(0f,untouched.silver,0f)
        val fog=PhoneProcessProgram.Pools(1f,0f,0f)
        program(listOf(develop()+("developer_type" to "exhausted"))).apply(fog,false,1f)
        assertTrue(fog.silver>0f)
        assertTrue(fog.silver+fog.halide*fog.activation<.04f)
        val reversed=PhoneProcessProgram.Pools(1f,.4f,0f)
        program(listOf(develop(),mapOf("action" to "remove_silver"),mapOf("action" to "activate_remaining_halide"),develop())).apply(reversed,false,1f)
        assertEquals(.6f,reversed.silver,1e-6f);assertEquals(0f,reversed.halide,1e-6f)
    }
    @Test fun legacyBathModelKeepsItsNumbersAndNewModelRecordsTheLedger() {
        val steps=List(2) { develop() }
        val old=program(steps,ledger=false);val p=PhoneProcessProgram.Pools(1f,.5f,0f)
        old.apply(p,false,1f)
        assertEquals(.75f,p.silver,0f);assertEquals(.5f,p.activation,0f)
        assertEquals(PhoneProcessModels.PREVIOUS,old.model);assertFalse(old.record().containsKey("activation_ledger"))
        assertEquals("remaining_developable_halide_v1",program(steps).record()["activation_ledger"])
    }
    @Test fun consumingAndPartitionedNewProgramsPreserveReactiveInventoryAndReadOnlySources() {
        val kernel=PhoneReducedKernel()
        val recipe=mapOf(PhoneProcessModels.OPTION to PhoneProcessModels.CURRENT,"film" to mapOf("color_process" to "bw"),
            "develop" to mapOf("program_key" to "custom","process_steps" to List(2) { develop(strength=.5) }))
        val latent=kernel.expose(RgbVolume(4,3,linearRgb=FloatArray(36) { .08f+it%7*.1f }),recipe)
        val before=latent.arrays.mapValues { it.value.values.copyOf() }
        val optical=kernel.develop(latent,recipe)
        before.forEach { (key,v)->assertArrayEquals(v,latent.arrays.getValue(key).values,0f) }
        fun row(y: Int)=LatentState(latent.arrays.mapValues { (_,v)->FloatNd(intArrayOf(1,4,v.shape[2]),v.values.copyOfRange(y*4*v.shape[2],(y+1)*4*v.shape[2])) },latent.metadata)
        val prepared=kernel.prepareDevelopment(recipe,row(0),3)
        for(y in 2 downTo 0) assertArrayEquals(optical.opticalDensityRgb.values.copyOfRange(y*12,(y+1)*12),prepared.developRowsAt(row(y),y).opticalDensityRgb.values,0f)
        val consumed=LatentState(latent.arrays.mapValues { (_,v)->FloatNd(v.shape,v.values.copyOf()) },latent.metadata)
        assertArrayEquals(optical.opticalDensityRgb.values,kernel.develop(consumed,recipe,true).opticalDensityRgb.values,0f)
        for(i in before.getValue("halide").indices) assertEquals(before.getValue("developability")[i],
            consumed.arrays.getValue("metallic_silver").values[i]+consumed.arrays.getValue("halide").values[i]*consumed.arrays.getValue("developability").values[i],1e-6f)
    }
}
