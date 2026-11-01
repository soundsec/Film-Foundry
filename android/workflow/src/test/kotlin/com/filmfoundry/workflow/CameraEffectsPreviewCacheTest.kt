package com.filmfoundry.workflow

import com.filmfoundry.interchange.*
import org.junit.jupiter.api.Assertions.*
import org.junit.jupiter.api.Test
import java.io.File

class CameraEffectsPreviewCacheTest {
    @Test fun thumbnailResolutionHasItsOwnCacheIdentityAndKeepsTheFullSampleDefault() {
        val film=mapOf<String,Any?>("contrast" to 1.0)
        val cache=CameraEffectsPreviewCache()
        val small=CameraEffectsPreview.Request(film,longEdge=160)
        val image=cache.render(small,emptyMap())
        assertEquals(160,image.shape[1]);assertTrue(image.shape[0]<160)
        assertSame(image,cache.render(CameraEffectsPreview.Request(film,longEdge=160),emptyMap()))
        val full=cache.render(CameraEffectsPreview.Request(film),emptyMap())
        assertEquals(480,full.shape[1]);assertNotSame(image,full);assertEquals(2,cache.computations)
        assertThrows(IllegalArgumentException::class.java) { CameraEffectsPreview.Request(film,longEdge=0) }
    }
    @Test fun lazyComparisonAndWarmReopenMatchFreshFormationWithOpticsAndBorder() {
        val material=BuiltinRecipeCatalog.read(File("../app/src/main/assets/phone_builtin_recipes.json").readBytes()).materials.first()
        val film=PhoneMaterialDefaults.withEffects(material.recipe["film"] as Map<String,Any?>,.22,.12,1.0)
        val capture=mapOf("format" to "135","material_model" to PhoneProcessModels.CURRENT,
            "spatial_effects_revision" to PhoneMaterialDefaults.REVISION,PhoneMaterialPhenomena.ROLL_SETTING to PhoneMaterialPhenomena.CURRENT)
        val selected=mapOf("film_border" to FilmEdgeSettings.create("135",true,"STOCK","MY FILM"))
        val request=CameraEffectsPreview.Request(film,capture,selected)
        val camera=mapOf("leak" to .3,"debris" to true,"imprint" to "12:34","imprint_style" to "laser_dot")
        val reference=CameraEffectsPreview.render(camera,film,capture,selected)
        val cache=CameraEffectsPreviewCache()
        val first=cache.render(request,camera)
        assertEquals(1,cache.computations,"Cold display must form only the requested image")
        assertArrayEquals(reference.effects.values,first.values,0f)
        assertSame(first,cache.render(CameraEffectsPreview.Request(film,capture,selected),camera))
        assertEquals(1,cache.computations,"Identical reopen must not form again")
        val edited=camera+("imprint" to "20:59")
        val next=cache.render(request,edited)
        assertEquals(2,cache.computations);assertFalse(first.values.contentEquals(next.values))
        assertArrayEquals(CameraEffectsPreview.render(edited,film,capture,selected).effects.values,next.values,0f)
        assertArrayEquals(reference.clean.values,cache.render(request,edited,comparison=true).values,0f)
        assertEquals(3,cache.computations)
        cache.render(request,edited,comparison=true);assertEquals(3,cache.computations)
        val ev=cache.render(request,edited,.5)
        assertArrayEquals(CameraEffectsPreview.render(edited,film,capture,selected,.5).effects.values,ev.values,0f)
        assertEquals(4,cache.computations);assertTrue(cache.retainedBytes<=12L*1024*1024)
    }
    @Test fun everyRecipeOwnerInvalidatesAndCancelledOrEvictedWorkCannotPoisonTheNextRequest() {
        val film=mutableMapOf<String,Any?>("contrast" to 1.0)
        val original=CameraEffectsPreview.Request(film);film["contrast"]=2.0
        assertEquals(1.0,original.film["contrast"])
        val cache=CameraEffectsPreviewCache()
        var checks=0
        assertThrows(InterruptedException::class.java) { cache.render(original,emptyMap()) { if(++checks==3) throw InterruptedException() } }
        assertEquals(0,cache.computations)
        val a=cache.render(original,emptyMap());assertEquals(1,cache.computations)
        assertArrayEquals(CameraEffectsPreview.render(emptyMap(),original.film).clean.values,a.values,0f)
        for(request in listOf(CameraEffectsPreview.Request(film),CameraEffectsPreview.Request(film,mapOf("age" to .3)),
            CameraEffectsPreview.Request(film,mapOf("age" to .3),mapOf("film_border" to FilmEdgeSettings.create("135",true,"OTHER"))))) {
            cache.render(request,emptyMap())
        }
        assertEquals(4,cache.computations);cache.clear();assertEquals(0L,cache.retainedBytes)
        val tiny=CameraEffectsPreviewCache(1)
        tiny.render(original,emptyMap());assertEquals(0L,tiny.retainedBytes)
        tiny.render(original,emptyMap());assertEquals(2,tiny.computations)
    }
}
