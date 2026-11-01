package com.filmfoundry.workflow

import com.filmfoundry.interchange.*
import com.filmfoundry.kernel.*
import java.io.File
import java.awt.image.BufferedImage
import javax.imageio.ImageIO
import org.junit.jupiter.api.Assertions.*
import org.junit.jupiter.api.Test

/** The explicitly requested synthetic accident review, not a repository-wide effect matrix. */
class FormationReviewTest {
    @Test fun incidentsOnNegativeNativeSlideAndCrossReversalAreDeterministicReadOnlyAndVisible() {
        val width=240;val height=160;val kernel=PhoneReducedKernel()
        val values=FloatArray(width*height*3) { i ->
            val x=i/3%width;val y=i/3/width;val c=i%3
            if(y<height/2) .008f+x.toFloat()/width*1.5f else arrayOf(floatArrayOf(.65f,.06f,.02f),floatArrayOf(.06f,.5f,.1f),floatArrayOf(.04f,.1f,.7f),floatArrayOf(.7f,.7f,.7f))[x*4/width][c]
        }
        val hot=ProcessSteps.template("color_reversal").mapIndexed { i,s -> s+("temperature_c" to if(i%2==0) 20.0 else 48.0) }
        val cases=listOf("clean" to emptyMap(),"uneven" to mapOf("uneven_development" to .9),"stain" to mapOf("chemical_stain" to .8),
            "reticulation" to mapOf("reticulation_strength" to .9),"dye loss" to mapOf("dye_damage_strength" to .7),
            "silver" to mapOf("silver_retention" to .8),"fixer spent" to mapOf("fixer_exhaustion" to .85),
            "developer spent" to mapOf("developer_exhaustion" to .85),"still bath" to mapOf("agitation" to 0.0),
            "heat shock" to mapOf("program_key" to "custom","process_steps" to hot),
            "combined" to mapOf("uneven_development" to .7,"chemical_stain" to .5,"reticulation_strength" to .6,"dye_damage_strength" to .4))
        val routes=listOf("negative" to false,"native slide" to true,"negative reversal" to false)
        val tileW=240;val tileH=185;val contact=BufferedImage(tileW*cases.size,tileH*routes.size,BufferedImage.TYPE_INT_RGB)
        val g=contact.createGraphics();val metrics=mutableListOf<Map<String,Any?>>()
        try { for((row,route) in routes.withIndex()) {
            val film=mapOf("image_polarity" to if(route.second) "positive" else "negative","grain_strength" to 0.0,
                "film_base_density_rgb" to (if(route.second) listOf(.04,.04,.04) else listOf(.24,.14,.06)))
            val base=mapOf("film" to film,PhoneProcessModels.OPTION to PhoneProcessModels.CURRENT,
                "frame_identity" to mapOf("roll_id" to "audit-roll","frame_id" to "a","frame_index" to 1),
                "develop" to mapOf("program_key" to if(row==0) "color_negative" else "color_reversal","chemistry" to if(row==2) "cross" else "native"),
                "scanner" to PhoneFinishRecipes.scanner("ready"))
            val latent=kernel.expose(RgbVolume(width,height,linearRgb=values),base)
            val original=latent.arrays.mapValues { it.value.values.copyOf() };var clean: FloatArray?=null
            for((column,case) in cases.withIndex()) {
                val r=base+("develop" to ((base["develop"] as Map<String,Any?>)+case.second))
                val a=kernel.develop(latent,r);val b=kernel.develop(latent,r)
                assertArrayEquals(a.opticalDensityRgb.values,b.opticalDensityRgb.values,0f,route.first+case.first)
                assertTrue(a.opticalDensityRgb.values.all { it.isFinite() && it>=0f })
                if(column==0) clean=a.opticalDensityRgb.values.copyOf() else assertFalse(clean!!.contentEquals(a.opticalDensityRgb.values),route.first+case.first)
                val another=kernel.develop(LatentState(latent.arrays,latent.metadata+("frame_identity" to mapOf("roll_id" to "audit-roll","frame_id" to "b","frame_index" to 2))),r-"frame_identity")
                assertArrayEquals(a.opticalDensityRgb.values,another.opticalDensityRgb.values,0f,"batch incidents must not resample on frame change")
                val before=a.opticalDensityRgb.values.copyOf();val scan=kernel.observe(a,r).outputRgb
                assertArrayEquals(before,a.opticalDensityRgb.values,0f);original.forEach { (k,v)->assertArrayEquals(v,latent.arrays.getValue(k).values,0f) }
                val image=BufferedImage(width,height,BufferedImage.TYPE_INT_RGB)
                for(y in 0 until height) for(x in 0 until width) { var rgb=0;for(c in 0..2) rgb=rgb or ((scan.values[(y*width+x)*3+c].coerceIn(0f,1f)*255).toInt() shl (16-c*8));image.setRGB(x,y,rgb) }
                g.drawImage(image,column*tileW,row*tileH,null);g.color=java.awt.Color.WHITE;g.drawString(route.first+" / "+case.first,column*tileW+4,row*tileH+176)
                metrics+=mapOf("route" to route.first,"incident" to case.first,"polarity" to a.metadata["image_polarity"],
                    "density_mean" to a.opticalDensityRgb.values.average(),"deterministic" to true,"source_unchanged" to true)
            }
        } } finally { g.dispose() }
        System.getenv("FILM_FOUNDRY_REVIEW_OUTPUT")?.let { path -> File(path).apply { mkdirs() }.let { dir ->
            ImageIO.write(contact,"png",File(dir,"processing-incidents.png"));File(dir,"processing-incidents.json").writeText(RecipeJson.text(mapOf("cases" to metrics)))
        } }
    }
    @Test fun presetsPreviewProcessExposureAndEveryPrintStyleWithDebrisAndBoundedGrid() {
        val film=mapOf("spatial_reference_short_edge_mm" to 24.0)
        val selected=mapOf("develop" to mapOf("program_key" to "color_reversal","mask_bleach_completion" to 1.0),
            "scanner" to (PhoneFinishRecipes.scanner("ready")+("scan_grid_long_edge" to 8192)))
        val a=CameraEffectsPreview.render(emptyMap(),film,selected=selected,exposureEv=-1.0)
        val b=CameraEffectsPreview.render(emptyMap(),film,selected=selected,exposureEv=1.0)
        assertFalse(a.clean.values.contentEquals(b.clean.values));assertTrue(a.clean.shape[1]<=480)
        for(style in listOf("laser_dot","optical_amber","lab_edge")) {
            val effects=CameraEffectsPreview.render(mapOf("debris" to true,"imprint" to "12:34","imprint_style" to style),film,selected=selected)
            assertTrue(effects.effects.values.all { it.isFinite() });assertFalse(effects.clean.values.contentEquals(effects.effects.values),style)
        }
        assertEquals(8192,(selected["scanner"] as Map<*,*>)["scan_grid_long_edge"])
    }
}
