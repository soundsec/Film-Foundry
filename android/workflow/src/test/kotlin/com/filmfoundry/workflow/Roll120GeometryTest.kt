package com.filmfoundry.workflow

import com.filmfoundry.interchange.*
import com.filmfoundry.kernel.*
import org.junit.jupiter.api.Assertions.*
import org.junit.jupiter.api.Test

class Roll120GeometryTest {
    @Test fun deferredExposureUsesActual120DimensionsWhileOldRollsKeepTheirMapping() {
        val film=PhoneMaterialDefaults.withEffects(mapOf("spatial_reference_short_edge_mm" to 24.0,"grain_strength" to .025),0.0,0.0,1.0)
        val kernel=PhoneReducedKernel()
        for(format in StillFrameFormat.roll120Frames.keys) {
            val settings=mapOf("spatial_effects_revision" to PhoneMaterialDefaults.REVISION,"material_model" to PhoneProcessModels.CURRENT,"format" to format)
            val recipe=PhoneMaterialDefaults.exposureRecipe(film,settings)
            val latent=kernel.expose(RgbVolume(13,9,linearRgb=FloatArray(13*9*3) { .2f }),recipe)
            val geometry=latent.metadata[DataKernel.MATERIAL_GEOMETRY_KEY] as Map<*,*>
            assertEquals(StillFrameFormat.longEdgeMm[format],geometry["frame_long_edge_mm"])
            assertEquals(24.0,geometry["reference_short_edge_mm"])
        }
        assertFalse(PhoneMaterialDefaults.exposureRecipe(film,mapOf("format" to "6x6")).containsKey(PhoneProcessModels.OPTION))
    }
}
