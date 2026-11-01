package com.filmfoundry.kernel

import com.filmfoundry.interchange.DataKernel
import com.filmfoundry.interchange.LatentArchive
import com.filmfoundry.interchange.NpzMediumArchive
import org.junit.jupiter.api.Assertions.*
import org.junit.jupiter.api.Test

class MaterialGeometrySupportTest {
    private val kernel = PhoneReducedKernel()
    private val image = RgbVolume(3, 2, linearRgb = FloatArray(18) { 0.4f })
    private val recipe = mapOf(
        "film" to mapOf("spatial_reference_short_edge_mm" to 24.0),
        "develop" to mapOf("frame_size" to "6x7"),
    )

    @Test
    fun physicalExtentIsFrozenAndPortableWhileSpatialOperatorsRemainOmitted() {
        val latent = kernel.expose(image, recipe)
        val geometry = latent.metadata[DataKernel.MATERIAL_GEOMETRY_KEY] as Map<*, *>
        assertEquals(69.0, geometry["frame_long_edge_mm"])
        assertEquals(listOf(2, 3), geometry["full_shape"])
        val legacy = kernel.expose(image, emptyMap())
        assertArrayEquals(legacy.arrays.getValue("developability").values, latent.arrays.getValue("developability").values, 0f)
        val codec = NpzMediumArchive()
        val loaded = codec.readLatent(codec.writeLatent(LatentArchive(latent.arrays, latent.metadata)))
        val optical = kernel.develop(LatentState(loaded.arrays, loaded.metadata), mapOf(
            "film" to mapOf("spatial_reference_short_edge_mm" to 12.0),
            "develop" to mapOf("frame_long_edge_mm" to 100.0),
        ))
        val process = optical.metadata["film_process_model"] as Map<*, *>
        val formed = process[DataKernel.MATERIAL_GEOMETRY_KEY] as Map<*, *>
        assertEquals(geometry["frame_long_edge_mm"], formed["frame_long_edge_mm"])
        assertEquals(geometry["reference_short_edge_mm"], formed["reference_short_edge_mm"])
        assertEquals(geometry["full_shape"].toString(), formed["full_shape"].toString())
        val implementation = process["execution_implementation"] as Map<*, *>
        assertEquals(DataKernel.PHONE_REDUCED_PROFILE_ID, implementation["profile_id"])
        val models = implementation["component_models"] as Map<*, *>
        assertEquals("omitted_v0", models["grain"])
        val before = optical.opticalDensityRgb.values.copyOf()
        kernel.observe(optical, recipe)
        assertArrayEquals(before, optical.opticalDensityRgb.values, 0f)
    }

    @Test
    fun invalidGeometryFailsBeforePoolConsumption() {
        for (invalid in listOf(
            mapOf("film" to mapOf("spatial_reference_short_edge_mm" to 0.0)),
            mapOf("develop" to mapOf("frame_long_edge_mm" to 36.0)),
        )) assertThrows(KernelException::class.java) { kernel.expose(image, invalid) }
        val valid = kernel.expose(image, recipe)
        val geometry = valid.metadata[DataKernel.MATERIAL_GEOMETRY_KEY] as Map<*, *>
        for (bad in listOf(geometry + ("model" to "unknown"), geometry + ("full_shape" to listOf(1, 2)))) {
            val latent = LatentState(valid.arrays, valid.metadata + (DataKernel.MATERIAL_GEOMETRY_KEY to bad))
            val before = latent.arrays.getValue("halide").values.copyOf()
            assertThrows(KernelException::class.java) { kernel.develop(latent, emptyMap(), true) }
            assertArrayEquals(before, latent.arrays.getValue("halide").values, 0f)
        }
    }

    @Test
    fun laterRecipeCannotInventExposureExtentForLegacyLatent() {
        val optical = kernel.develop(kernel.expose(image, emptyMap()), recipe)
        val process = optical.metadata["film_process_model"] as Map<*, *>
        assertFalse(DataKernel.MATERIAL_GEOMETRY_KEY in process)
    }
}
