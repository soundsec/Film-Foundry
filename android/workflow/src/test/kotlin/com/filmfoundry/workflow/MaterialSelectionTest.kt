package com.filmfoundry.workflow

import com.filmfoundry.interchange.*
import com.filmfoundry.kernel.*
import java.io.File
import java.awt.image.BufferedImage
import javax.imageio.ImageIO
import org.junit.jupiter.api.Assertions.*
import org.junit.jupiter.api.Test

class MaterialSelectionTest {
    private val catalog get()=BuiltinRecipeCatalog.read(File("../app/src/main/assets/phone_builtin_recipes.json").readBytes())
    @Test fun dailyMaterialsBalanceAllFourIdentitiesAndFiltersUseValuesRatherThanNames() {
        val daily=catalog.dailyMaterials();assertEquals(12,daily.size)
        for(polarity in listOf("negative","positive")) for(color in listOf("color","bw")) {
            val filter=MaterialSelection.Filter(polarity,color,"official")
            val matches=daily.filter { filter.accepts(it.recipe["film"] as Map<String,Any?>,"official") }
            assertEquals(if(color=="bw") 2 else 4,matches.size)
            assertFalse(filter.accepts(matches.first().recipe["film"] as Map<String,Any?>,"custom"))
        }
        assertTrue(MaterialSelection.Filter("positive","bw","custom").accepts(mapOf("name" to "Color negative", "image_polarity" to "positive","color_process" to "monochrome"),"custom"))
        assertFalse(daily.any { it.id=="blue_highlight_negative" })
        assertTrue(catalog.dailyMaterials("blue_highlight_negative").any { it.id=="blue_highlight_negative" })
    }
    @Test fun nativeMaterialProgramsRetainDeclaredPolarityAndNewStocksHaveDistinctFormation() {
        val k=PhoneReducedKernel();val width=180;val height=120
        val samples=FloatArray(width*height*3) { i -> val x=i/3%width;val y=i/3/width
            if(y<height/2) .004f+x.toFloat()/width*1.2f else arrayOf(floatArrayOf(.6f,.05f,.03f),floatArrayOf(.04f,.55f,.08f),floatArrayOf(.03f,.12f,.7f))[x*3/width][i%3] }
        val density=mutableMapOf<String,FloatArray>();val daily=catalog.dailyMaterials()
        val contact=BufferedImage(width*6,(height+22)*2,BufferedImage.TYPE_INT_RGB);val g=contact.createGraphics()
        try { for((i,entry) in daily.withIndex()) {
            val film=(entry.recipe["film"] as Map<String,Any?>)+("grain_strength" to 0.0)
            RecipeDrafts.material(film)
            val r=mapOf("film" to film,PhoneProcessModels.OPTION to PhoneProcessModels.CURRENT,
                "develop" to mapOf("program_key" to "auto"),"scanner" to PhoneFinishRecipes.scanner("ready"))
            val medium=k.develop(k.expose(RgbVolume(width,height,linearRgb=samples),r),r)
            assertEquals(MaterialSelection.tags(film).polarity,medium.metadata["image_polarity"],entry.id)
            density[entry.id]=medium.opticalDensityRgb.values.copyOf()
            val scan=k.observe(medium,r).outputRgb.values;assertTrue(scan.all { it.isFinite() })
            val image=BufferedImage(width,height,BufferedImage.TYPE_INT_RGB)
            for(y in 0 until height) for(x in 0 until width) { var rgb=0;for(c in 0..2) rgb=rgb or ((scan[(y*width+x)*3+c].coerceIn(0f,1f)*255).toInt() shl (16-c*8));image.setRGB(x,y,rgb) }
            g.drawImage(image,(i%6)*width,(i/6)*(height+22),null);g.color=java.awt.Color.WHITE;g.drawString(entry.id,(i%6)*width+3,(i/6)*(height+22)+height+15)
        } } finally { g.dispose() }
        for((a,b) in listOf("color_reversal_amber" to "color_reversal_transparency","color_reversal_cobalt" to "color_reversal_vivid",
            "monochrome_fine_negative" to "monochrome_push","monochrome_reversal_graphic" to "monochrome_reversal_transparency")) {
            val difference=density.getValue(a).indices.map { kotlin.math.abs(density.getValue(a)[it]-density.getValue(b)[it]) }.average()
            println("material_difference $a $b $difference")
            assertTrue(difference>.02,"$a must differ in formed material from $b: $difference")
        }
        System.getenv("FILM_FOUNDRY_REVIEW_OUTPUT")?.let { path -> File(path).apply { mkdirs() };ImageIO.write(contact,"png",File(path,"daily-materials.png")) }
    }
    @Test fun printEditsCannotChangeBodyLeaksOrGateDirtAndAreDeeplyDetached() {
        val component=mutableMapOf<String,Any?>("shape" to "edge","strength" to .7)
        val camera=mapOf("leak" to .6,"seed" to 71,"gate_seed" to 14,"debris" to true,"leak_components" to listOf(component))
        val next=CameraPrintSettings.update(camera,"12:34","laser_dot","bottom")
        assertEquals(camera,next-"imprint"-"imprint_style"-"imprint_side")
        component["strength"]=0.0
        assertEquals(.7,((next["leak_components"] as List<*>).first() as Map<*,*>)["strength"])
        assertThrows(IllegalArgumentException::class.java) { CameraPrintSettings.update(next,"TEXT","none","bottom") }
    }
}
