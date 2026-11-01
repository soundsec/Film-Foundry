package com.filmfoundry.kernel

import com.filmfoundry.interchange.*
import org.junit.jupiter.api.Assertions.*
import org.junit.jupiter.api.Test

class PhoneDevelopmentSpatialTest {
    @Test fun cancellationInsideOneLargeBlockLeavesSourcePoolsUntouched() {
        val a=latent();val before=a.arrays.mapValues { it.value.values.copyOf() }
        var armed=false;var read=false;var checksAfterRead=0
        val source=object: LatentRows by rows(a) {
            override fun readRows(start: Int,count: Int): LatentState {
                if(!armed) return slice(a,start,count)
                assertEquals(0,start);assertEquals(h,count);read=true;return a
            }
            override fun checkActive() { if(armed && read && ++checksAfterRead==3) error("paused within block") }
        }
        kernel.prepareDevelopment(recipe(),slice(a,0,1),h).bindSource(source).use { bound ->
            armed=true
            val error=assertThrows(IllegalStateException::class.java) { bound.developRows(0,h) }
            assertEquals("paused within block",error.message)
        }
        before.forEach { (key,value)->assertArrayEquals(value,a.arrays.getValue(key).values,0f) }
    }
    private val kernel=PhoneReducedKernel()
    private val w=67;private val h=35
    private val matrix=listOf(listOf(0.0,.55,0.0),listOf(0.0,0.0,0.0),listOf(0.0,0.0,0.0))
    private fun recipe(dir: Boolean=false,adjacency: Double=0.0,extra: Map<String,Any?> = emptyMap())=mapOf(
        PhoneProcessModels.OPTION to PhoneProcessModels.CURRENT,
        "film" to mapOf("grain_strength" to 0.0,"dir_material_response_matrix" to if(dir) matrix else List(3) { List(3) { 0.0 } },"dir_diffusion_radius" to .04),
        "develop" to (mapOf("program_key" to "color_negative","development_adjacency_strength" to adjacency,"development_adjacency_radius" to .04,
            "uneven_development" to 0.0,"chemical_stain" to 0.0,"reticulation_strength" to 0.0)+extra))
    private fun latent(flat: Boolean=false): LatentState {
        val a=kernel.expose(RgbVolume(w,h,linearRgb=FloatArray(w*h*3) { .18f }),emptyMap())
        for(i in a.arrays.getValue("developability").values.indices) a.arrays.getValue("developability").values[i]=if(flat || (i/3)%w<w/2) .15f else .8f
        return a
    }
    private fun copy(a: LatentState)=slice(a,0,h)
    private fun slice(a: LatentState,y: Int,count: Int)=LatentState(a.arrays.mapValues { (_,v)->
        FloatNd(intArrayOf(count,w,v.shape[2]),v.values.copyOfRange(y*w*v.shape[2],(y+count)*w*v.shape[2])) },a.metadata)
    private fun rows(a: LatentState)=object: LatentRows {
        override val width=w;override val height=h
        override fun readRows(start: Int,count: Int)=slice(a,start,count)
    }
    @Test fun dirChangesOnlyTheTargetLayerAndDoesNotMutateReadOnlySource() {
        val a=latent();val before=a.arrays.mapValues { it.value.values.copyOf() }
        val base=copy(a);kernel.develop(base,recipe(),true)
        val target=copy(a);val result=kernel.develop(target,recipe(dir=true),true)
        assertFalse(base.arrays.getValue("dye").values.contentEquals(target.arrays.getValue("dye").values))
        for(i in target.arrays.getValue("dye").values.indices) {
            val expected=base.arrays.getValue("dye").values[i];val actual=target.arrays.getValue("dye").values[i]
            if(i%3==1) assertTrue(actual<expected) else assertEquals(expected,actual,0f)
        }
        kernel.develop(a,recipe(dir=true))
        before.forEach { (key,v)->assertArrayEquals(v,a.arrays.getValue(key).values,0f) }
        assertTrue(((result.metadata["film_process_model"] as Map<*,*>)["spatial_development"] as Map<*,*>).isNotEmpty())
    }
    @Test fun adjacencyLeavesFlatFieldsExactAndChangesOnlyEdgeKineticsWithinInventory() {
        val flat=latent(true)
        assertArrayEquals(kernel.develop(flat,recipe()).opticalDensityRgb.values,kernel.develop(flat,recipe(adjacency=1.0)).opticalDensityRgb.values,0f)
        val a=latent();val consumed=copy(a)
        val out=kernel.develop(consumed,recipe(adjacency=1.0,extra=mapOf("process_steps" to listOf(mapOf("action" to "develop_color","strength" to .5)))),true)
        val base=kernel.develop(a,recipe(extra=mapOf("process_steps" to listOf(mapOf("action" to "develop_color","strength" to .5)))))
        assertFalse(base.opticalDensityRgb.values.contentEquals(out.opticalDensityRgb.values))
        for(i in consumed.arrays.getValue("halide").values.indices) {
            val h0=a.arrays.getValue("halide").values[i];val a0=a.arrays.getValue("developability").values[i]
            val h1=consumed.arrays.getValue("halide").values[i];val a1=consumed.arrays.getValue("developability").values[i]
            assertEquals(h0,consumed.arrays.getValue("metallic_silver").values[i]+h1,1e-6f)
            assertEquals(h0*a0,consumed.arrays.getValue("metallic_silver").values[i]+h1*a1,1e-6f)
        }
    }
    @Test fun fullFrameAndReversePartitionsAreExactAndClosedBindingsRejectReuse() {
        val a=latent();val r=recipe(true,.7);val whole=kernel.develop(a,r)
        for(step in listOf(1,8)) {
            val bound=kernel.prepareDevelopment(r,slice(a,0,1),h).bindSource(rows(a))
            for(y in (0 until h step step).reversed()) {
                val n=minOf(step,h-y);val block=bound.developRows(y,n)
                assertEquals(whole.metadata,block.metadata)
                assertArrayEquals(whole.opticalDensityRgb.values.copyOfRange(y*w*3,(y+n)*w*3),block.opticalDensityRgb.values,0f)
            }
            bound.close();assertThrows(IllegalStateException::class.java) { bound.developRows(0,1) }
        }
        assertThrows(IllegalArgumentException::class.java) { kernel.prepareDevelopment(r,slice(a,0,1),h).developRowsAt(slice(a,0,1),0) }
    }
    @Test fun reversalDirDefaultsOffAndExplicitOverrideActsOnlyOnColorDevelopment() {
        val a=latent();val extra=mapOf("program_key" to "color_reversal")
        assertArrayEquals(kernel.develop(a,recipe(extra=extra)).opticalDensityRgb.values,kernel.develop(a,recipe(true,extra=extra)).opticalDensityRgb.values,0f)
        assertFalse(kernel.develop(a,recipe(true,extra=extra)).opticalDensityRgb.values.contentEquals(
            kernel.develop(a,recipe(true,extra=extra+("dir_process_effectiveness" to 1.0))).opticalDensityRgb.values))
        val p=PhoneProcessProgram.from(mapOf("process_steps" to listOf(mapOf("action" to "develop_silver","strength" to .5),
            mapOf("action" to "activate_remaining_halide"),mapOf("action" to "develop_color"))),true,false,interactive=true)!!
        val projection=PhoneProcessProgram.Projection();val a0=PhoneProcessProgram.Pools(1f,.6f,0f,1f)
        p.apply(a0,true,1f,colorRate=.4f,projection=projection)
        assertEquals(.3f,projection.firstSilver,1e-6f)
        assertEquals(.28f,projection.firstColorDye,1e-6f)
    }
    @Test fun precedingFixingSuppressesSourceAndLegacyLedgerIgnoresSpatialFields() {
        val a=latent();val fixed=mapOf("process_steps" to listOf(mapOf("action" to "fix_halide"),mapOf("action" to "develop_color")))
        assertArrayEquals(kernel.develop(a,recipe(extra=fixed)).opticalDensityRgb.values,kernel.develop(a,recipe(true,1.0,fixed)).opticalDensityRgb.values,0f)
        val old=PhoneProcessModels.LEDGER
        assertArrayEquals(kernel.develop(a,recipe()+(PhoneProcessModels.OPTION to old)).opticalDensityRgb.values,
            kernel.develop(a,recipe(true,1.0)+(PhoneProcessModels.OPTION to old)).opticalDensityRgb.values,0f)
    }
    @Test fun spatialSourceCancellationCannotConsumeAuthoritativePoolsAndParametersRejectInvalidValues() {
        val a=latent();val before=a.arrays.mapValues { it.value.values.copyOf() };var checks=0
        val source=object: LatentRows by rows(a) { override fun checkActive() { if(++checks==7) error("cancelled") } }
        assertThrows(IllegalStateException::class.java) { kernel.prepareDevelopment(recipe(true,.5),slice(a,0,1),h).bindSource(source) }
        before.forEach { (key,v)->assertArrayEquals(v,a.arrays.getValue(key).values,0f) }
        assertThrows(IllegalArgumentException::class.java) { validatePhoneDevelopmentSpatial(mapOf("dir_material_response_matrix" to List(3) { List(3) { .4 } })) }
        assertThrows(IllegalArgumentException::class.java) { validatePhoneDevelopmentSpatial(develop=mapOf("development_adjacency_radius" to Double.NaN)) }
    }
    @Test fun reactionGridIsBoundedAndPreservesFullOutputSize() {
        val a=kernel.expose(RgbVolume(1031,17,linearRgb=FloatArray(1031*17*3) { if(it%17<8) .1f else .8f }),emptyMap())
        val out=kernel.develop(a,recipe(true,.5))
        assertArrayEquals(intArrayOf(17,1031,3),out.opticalDensityRgb.shape)
        val grid=((out.metadata["film_process_model"] as Map<*,*>)["spatial_development"] as Map<*,*>)["work_grid"] as List<*>
        assertEquals(512,grid.maxOf { (it as Number).toInt() })
    }
    @Test fun boundSourceMayLendItsRowsWithoutLosingMaterialAndReversalSkipIsExplained() {
        val a=latent();val before=a.arrays.mapValues { it.value.values.copyOf() };val r=recipe(true,.7)
        val source=object: LatentRows {
            override val width=w;override val height=h
            override fun readRows(start: Int,count: Int): LatentState { require(start==0 && count==h);return a }
        }
        val expected=kernel.develop(a,r)
        kernel.prepareDevelopment(r,slice(a,0,1),h).bindSource(source).use {
            assertArrayEquals(expected.opticalDensityRgb.values,it.developRows(0,h).opticalDensityRgb.values,0f)
        }
        before.forEach { (key,v)->assertArrayEquals(v,a.arrays.getValue(key).values,0f) }
        val skip=(kernel.develop(a,recipe(true,extra=mapOf("program_key" to "color_reversal"))).metadata["film_process_model"] as Map<*,*>)["spatial_development"] as Map<*,*>
        assertEquals(false,skip["enabled"]);assertEquals("reversal_default_off",skip["dir_reason"]);assertNull(skip["work_grid"])
    }
    @Test fun firstBathAgitationUsesItsOwnDurationAndOverridesTheGlobalCondition() {
        val step=mapOf("action" to "develop_color","agitation" to .8,"agitation_time_s" to 120.0,"time_min" to 8.0)
        val out=kernel.develop(latent(),recipe(adjacency=.5,extra=mapOf("agitation" to 3.0,"process_steps" to listOf(step))))
        val record=(out.metadata["film_process_model"] as Map<*,*>)["spatial_development"] as Map<*,*>
        val edge=record["adjacency"] as Map<*,*>
        assertEquals(.2,(edge["effective_first_bath_agitation"] as Number).toDouble(),1e-10)
    }
    @Test fun grainSharedScratchIsExactlyEquivalentForArchivedAndCurrentModels() {
        val r=mapOf("film" to mapOf("grain_strength" to .04,"grain_scales" to listOf(.004,.02,.06),"grain_scale_weights" to listOf(.7,.22,.08)))
        val a=kernel.expose(RgbVolume(w,h,linearRgb=FloatArray(w*h*3) { .18f }),r)
        val profile=a.metadata[PhoneGrainProfile.KEY] as Map<*,*>
        for(model in listOf(null,PhoneGrainProfile.PREVIOUS_MODEL,PhoneGrainProfile.MODEL)) {
            val metadata=if(model==null) a.metadata-PhoneGrainProfile.KEY else a.metadata+(PhoneGrainProfile.KEY to (profile.entries.associate { it.key.toString() to it.value }+("model" to model)))
            val effects=PhoneFormationEffects(r,metadata,w,h);val scratch=PhoneFormationEffects.GrainSamples()
            for(y in h-1 downTo 0 step 5) for(x in w-1 downTo 0 step 3) for(layer in 2 downTo 0) {
                val amount=.15f+layer*.3f
                assertEquals(effects.grainFactor(x,y,layer,amount),effects.grainFactor(x,y,layer,amount,scratch),0f)
            }
        }
    }
}
