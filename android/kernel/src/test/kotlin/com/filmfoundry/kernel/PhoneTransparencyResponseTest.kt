package com.filmfoundry.kernel

import org.junit.jupiter.api.Assertions.assertArrayEquals
import org.junit.jupiter.api.Test

class PhoneTransparencyResponseTest {
    @Test fun standardSlideTracksDesktopDensityAndEndpointChroma() {
        val response = PhoneTransparencyResponse(mapOf(
            "positive_density_contrast" to 1.18, "positive_density_bias" to .025,
            "positive_latitude_compression" to .42, "positive_midtone_density" to .20,
            "positive_shadow_toe" to .24, "positive_shadow_toe_width" to .26,
            "positive_highlight_shoulder" to .42, "positive_highlight_shoulder_width" to .24,
            "positive_dye_saturation" to 1.06, "positive_highlight_chroma_retention" to .18,
            "positive_shadow_chroma_retention" to .24,
        ))
        // References from the desktop material transform, prior to scanning.
        val wedge = floatArrayOf(0f, .13f, .47f, .82f, 1f).map(response::density).toFloatArray()
        assertArrayEquals(floatArrayOf(.05544005f, .11839157f, .52729190f, .88786060f, .97191990f), wedge, .00001f)
        val thin = floatArrayOf(.02f, .14f, .25f)
        val dense = floatArrayOf(.72f, .89f, .97f)
        response.dye(thin)
        response.dye(dense)
        assertArrayEquals(floatArrayOf(.08364201f, .12662749f, .18438062f), thin, .00001f)
        assertArrayEquals(floatArrayOf(.85925390f, .91550700f, .93348390f), dense, .00001f)
    }
}
