package com.filmfoundry.workflow

import com.filmfoundry.interchange.*
import com.filmfoundry.kernel.*
import org.junit.jupiter.api.Assertions.*
import org.junit.jupiter.api.Test
import java.io.File

class SeasonalCollectionTest {
    @Test fun eightSharedPairsFormBothPolaritiesAndReadoutRemainsReadOnly() {
        val assets=File("../app/src/main/assets")
        val bytes=File(assets,"finder_resources.bin").readBytes()
        val key="FF native finder resources / 1".toByteArray()
        val compressed=ByteArray(bytes.size-5) { (bytes[it+5].toInt() xor key[it%key.size].toInt()).toByte() }
        val raw=java.util.zip.InflaterInputStream(java.io.ByteArrayInputStream(compressed)).use { it.readBytes() }
        val resources=RecipeJson.objectMap(raw.toString(Charsets.UTF_8))
        @Suppress("UNCHECKED_CAST")
        fun section(name: String)=RecipeJson.text(resources[name] as Map<String,Any?>).toByteArray()
        val catalog=BuiltinRecipeCatalog.read(section("phone_catalog"))
        val pairs=PhoneCreativePairings.read(section("phone_pairings"))
        assertEquals(8,catalog.materials.size);assertEquals(8,catalog.scanners.size);assertEquals(8,pairs.size)
        val source=RgbVolume(48,32,linearRgb=FloatArray(48*32*3) { i ->
            val p=i/3;val c=i%3
            if(p/48<16) p%48/47f else listOf(.7f,.3f,.06f)[c]
        })
        val original=source.linearRgb.copyOf();val kernel=PhoneReducedKernel()
        val output=mutableListOf<FloatArray>()
        for(pair in pairs) {
            val recipe=pair.recipe+mapOf(PhoneProcessModels.OPTION to PhoneProcessModels.CURRENT)
            assertEquals(catalog.materials.single { it.id==pair.materialId }.recipe["film"],recipe["film"])
            val latent=kernel.expose(source,recipe);val medium=kernel.develop(latent,recipe)
            val density=medium.opticalDensityRgb.values.copyOf()
            assertEquals(if(pair.id.endsWith("reversal")) "positive" else "negative",medium.metadata["image_polarity"])
            val rendered=kernel.observe(medium,recipe).outputRgb.values
            assertTrue(rendered.all { it.isFinite() && it in 0f..1f })
            assertArrayEquals(density,medium.opticalDensityRgb.values,0f)
            output+=rendered
        }
        val cache=CameraEffectsPreviewCache()
        val previews=pairs.map { pair ->
            val film=pair.recipe["film"] as Map<String,Any?>
            cache.render(CameraEffectsPreview.Request(film,selected=pair.recipe,longEdge=64),emptyMap()).values.copyOf()
        }
        for(a in previews.indices) for(b in a+1 until previews.size) assertFalse(previews[a].contentEquals(previews[b]),"Paired stock samples must retain their scanner styles")
        assertArrayEquals(original,source.linearRgb,0f)
        for(a in output.indices) for(b in a+1 until output.size) assertFalse(output[a].contentEquals(output[b]))
    }
}
