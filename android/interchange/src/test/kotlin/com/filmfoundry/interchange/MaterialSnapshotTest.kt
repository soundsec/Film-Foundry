package com.filmfoundry.interchange

import org.junit.jupiter.api.Assertions.*
import org.junit.jupiter.api.Test

class MaterialSnapshotTest {
    @Test fun parametersAreDetachedImmutableAndCodecPreservesSnapshotDuringConsumption() {
        val base=mutableListOf<Any?>(.1,.2,.3)
        val snapshot=MaterialSnapshot.capture(mapOf("film_base_density_rgb" to base))
        base[0]=9.0
        val film=MaterialSnapshot.film(snapshot)
        assertEquals(.1,(film["film_base_density_rgb"] as List<*>)[0])
        assertThrows(UnsupportedOperationException::class.java) { (film as MutableMap)["x"]=1 }
        val metadata=mapOf("medium_family" to "film","medium_process" to "negative","image_polarity" to "negative",
            "view_mode" to "transmissive","base_type" to "orange_mask","color_system" to "color_negative_dye",
            "compatible_interpreters" to listOf("negative_scan"),DataKernel.MATERIAL_SNAPSHOT_KEY to snapshot)
        val arrays=mapOf("halide" to FloatNd(intArrayOf(1,1,3),floatArrayOf(1f,1f,1f)),
            "developability" to FloatNd(intArrayOf(1,1,3),floatArrayOf(.2f,.4f,.8f)),
            "metallic_silver" to FloatNd(intArrayOf(1,1,3),FloatArray(3)))
        val codec=NpzMediumArchive()
        val bytes=codec.writeLatent(LatentArchive(arrays,metadata))
        val loaded=codec.readLatent(codec.markLatentConsumed(bytes),true)
        assertTrue(RecipeJson.equivalent(snapshot,loaded.metadata[DataKernel.MATERIAL_SNAPSHOT_KEY]))
        assertEquals(true,loaded.metadata["consumed"])
        assertThrows(IllegalArgumentException::class.java) { MaterialSnapshot.capture(mapOf("bad" to Double.NaN)) }
    }
}
