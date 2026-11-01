package com.filmfoundry.workflow

import org.junit.jupiter.api.Test
import org.junit.jupiter.api.Assertions.*

class CameraEffectsPreviewTest {
    @Test fun englishImprintNormalizesFullWidthAndRejectsUnsupportedInput() {
        val original=mapOf<String,Any?>("leak" to .3,"imprint_brightness" to 1.45,"imprint_x" to .29,"imprint_y" to .58)
        val edited=CameraPrintSettings.update(original,"Ｆｉｌｍ　Ａａ－０１＠５０％！","laser_dot","bottom")
        assertEquals("Film Aa-01@50%!",edited["imprint"])
        assertEquals(original,edited-"imprint"-"imprint_style"-"imprint_side")
        CameraEffectsPreview.validate(edited)
        assertTrue(CameraPrintSettings.validText("Abc!?@#_[]{}+="))
        assertTrue(CameraPrintSettings.validText("a".repeat(24)))
        for(text in listOf("a".repeat(25),"中文","🙂","a\nb","café")) {
            assertFalse(CameraPrintSettings.validText(text))
            assertThrows(IllegalArgumentException::class.java) { CameraPrintSettings.update(original,text,"laser_dot","bottom") }
        }
    }
    @Test fun rollMaterialPreviewIncludesFrozenOpticsAndBorder() {
        val catalog=com.filmfoundry.interchange.BuiltinRecipeCatalog.read(java.io.File("../app/src/main/assets/phone_builtin_recipes.json").readBytes())
        val film=PhoneMaterialDefaults.withEffects(catalog.materials.first().recipe["film"] as Map<String,Any?>,.22,.12,1.0)
        val capture=mapOf("format" to "135","spatial_effects_revision" to PhoneMaterialDefaults.REVISION,
            "material_model" to com.filmfoundry.interchange.PhoneProcessModels.CURRENT,
            com.filmfoundry.interchange.PhoneMaterialPhenomena.ROLL_SETTING to com.filmfoundry.interchange.PhoneMaterialPhenomena.CURRENT)
        val result=CameraEffectsPreview.render(emptyMap(),film,capture,selected=mapOf("film_border" to mapOf("enabled" to true,"format_key" to "135")))
        assertTrue(result.effects.values.all { it.isFinite() })
        assertTrue(result.effects.values.max()>.2f)
        assertTrue(result.effects.values.max()-result.effects.values.min()>.1f)
    }
    @Test fun editedDateTextCanUseDotsAndFullWidthInputWithoutChangingBodySettings() {
        val original=mapOf("leak" to .3,"gate_seed" to 22L,"debris" to true)
        val edited=CameraPrintSettings.update(original,"２０２６．１０．２６ １２：３４","optical_amber","bottom")
        assertEquals("2026.10.26 12:34",edited["imprint"])
        assertEquals(original,edited-"imprint"-"imprint_style"-"imprint_side")
        val a=CameraEffectsPreview.render(edited,emptyMap())
        val b=CameraEffectsPreview.render(CameraPrintSettings.update(edited,"10/27 20:59","optical_amber","bottom"),emptyMap())
        assertFalse(a.effects.values.contentEquals(b.effects.values))
        assertArrayEquals(a.clean.values,b.clean.values,0f)
        val error=assertThrows(IllegalArgumentException::class.java) { CameraPrintSettings.update(original,"中文","none","bottom") }
        assertTrue(error.message!!.contains("24 English"))
    }
    @Test fun currentStockGeometryAndHalfFramePreviewRunWithTheirFrozenOptics() {
        val film=PhoneMaterialDefaults.withEffects(mapOf("spatial_reference_short_edge_mm" to 24.0),.22,.12,1.0)
        val capture=mapOf("format" to "half","material_model" to com.filmfoundry.interchange.PhoneProcessModels.CURRENT,
            "spatial_effects_revision" to PhoneMaterialDefaults.REVISION)
        val preview=CameraEffectsPreview.render(mapOf("debris" to true),film,capture)
        assertTrue(preview.effects.shape[0]>preview.effects.shape[1])
        assertFalse(preview.clean.values.contentEquals(preview.effects.values))
    }
    @Test fun sampleUsesFormationAndShowsCombinationsWithoutChangingInputs() {
        val camera=mapOf("seed" to 17L,"gate_seed" to 73L,"leak" to .8,"debris" to true,"imprint" to "12:34",
            "leak_components" to listOf(mapOf("shape" to "edge","side" to "left"),mapOf("shape" to "cone","side" to "bottom")))
        val first=CameraEffectsPreview.render(camera,emptyMap())
        val second=CameraEffectsPreview.render(camera,emptyMap())
        assertFalse(first.clean.values.contentEquals(first.effects.values))
        assertArrayEquals(first.effects.values,second.effects.values)
        assertArrayEquals(first.clean.shape,first.effects.shape)
        assertTrue(first.effects.values.all { it.isFinite() })
        assertEquals(2,(camera["leak_components"] as List<*>).size)
    }
    @Test fun borderLayoutIsExplicitlyADiagramAnd120HasNoSprockets() {
        val full=FilmBorderPreview.layout("135",360,240)
        assertEquals(16,full.holes.size);assertEquals("interface_only",full.pipelineStatus)
        assertEquals(8,FilmBorderPreview.layout("half_frame",180,240).holes.size)
        assertTrue(FilmBorderPreview.layout("120",300,300).holes.isEmpty())
        assertEquals(8,FilmBorderPreview.layout("135",360,240,"top").holes.size)
        assertTrue(full.height>240)
    }
    @Test fun edgeMarksHaveASeparateStripBelowTheSelectedSprocketRowAtEveryPreviewSize() {
        for(format in FilmBorderPreview.formats) for(height in listOf(1,80,180,320)) for(top in listOf(false,true)) {
            val layout=FilmBorderPreview.layout(format,480,height,"both",top=top)
            val band=layout.textBand;val start=if(top) 0 else layout.height-layout.rebate
            assertTrue(band.top>=start && band.bottom<=start+layout.rebate)
            val row=layout.holes.filter { it.top>=start && it.top<start+layout.rebate }
            assertTrue(row.all { if(top) it.top>band.bottom else it.bottom<band.top },"$format $height top=$top")
            assertTrue(band.fontSize<band.bottom-band.top)
        }
    }
}
