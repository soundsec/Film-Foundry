package com.filmfoundry.kernel

import com.filmfoundry.interchange.*
import com.filmfoundry.kernel.materials.*
import org.junit.jupiter.api.Assertions.*
import org.junit.jupiter.api.Test
import java.io.*
import kotlin.math.abs
import kotlin.math.max

class PhoneExperimentalMaterialsTest {
    private val rgb=RgbVolume(24,16,linearRgb=FloatArray(24*16*3) { .005f+(it%71)/80f })
    private fun formed(family: MaterialFamily,extra: Map<String,Any?> = emptyMap(),arithmetic: String=PhoneMaterialKernel.ARITHMETIC_HALF): PhoneMaterialState {
        var s=PhoneMaterialKernel.expose(family,rgb,extra,groupId="test_${family.name}",seed=19,arithmetic=arithmetic)
        val steps=when(family) {
            MaterialFamily.PEEL->listOf("spread","advance","separate")
            MaterialFamily.INSTANT->listOf("eject","advance","finish")
            MaterialFamily.SILVER->listOf("develop","fix","gild","tarnish")
            MaterialFamily.PAPER->listOf("develop",if(extra["kind"]=="color")"bleach_fix" else "fix","wash")
            MaterialFamily.WET->listOf("develop","fix","wash","dry","varnish")
            MaterialFamily.CYANOTYPE->listOf("wash","oxidize")
            MaterialFamily.PLATINUM->listOf("develop","clear","wash")
        }
        for(op in steps)s=PhoneMaterialKernel.process(s,op,when(op){
            "advance"->mapOf("seconds" to 180.0,"light_exposure" to .3)
            "dry"->mapOf("seconds" to 7200.0,"humidity" to 0.0)
            else->emptyMap()
        })
        return s
    }
    private fun sharedCatalog() = requireNotNull(javaClass.getResourceAsStream("/material_state_fields.json"))
        .bufferedReader().use { RecipeJson.objectMap(it.readText()) }

    @Test fun exposedStateFieldsMatchTheSharedVocabulary() {
        val catalog = sharedCatalog()
        val latent = catalog.getValue("film_latent") as Map<*,*>
        assertEquals(latent["required"],DataKernel.LATENT_REQUIRED_ARRAYS)
        assertEquals(latent["optional"],DataKernel.LATENT_OPTIONAL_ARRAYS)
        val film = PhoneReducedKernel().expose(rgb,emptyMap())
        for (key in latent["scalars"] as List<*>) assertTrue(film.metadata[key] is Number, key.toString())
        val families = catalog.getValue("material_families") as Map<*,*>
        assertEquals(MaterialFamily.entries.map { it.id }.toSet(),families.keys)
        for (family in MaterialFamily.entries) {
            val spec = families[family.id] as Map<*,*>
            val transfers = family == MaterialFamily.PEEL || family == MaterialFamily.INSTANT
            for (layers in if (transfers) listOf(1,3) else listOf(3)) {
                val params = if (transfers) mapOf("transfer" to if (layers == 1) "silver_compound" else "dye") else emptyMap()
                val state = PhoneMaterialKernel.expose(family,rgb,params,groupId="contract",seed=1)
                val expected = mutableMapOf<String,List<Int>>()
                for ((group,tail) in mapOf("scalar" to emptyList(), "rgb" to listOf(3), "layers" to listOf(layers))) {
                    for (name in spec[group] as? List<*> ?: emptyList<Any>())
                        expected[name as String] = listOf(rgb.height,rgb.width) + tail
                }
                if (transfers) {
                    expected["phone_developer_used_mean"] = listOf(rgb.height,rgb.width)
                    expected["phone_developer_available_mean"] = listOf(rgb.height,rgb.width)
                }
                assertEquals(expected,state.fields().mapValues { it.value.shape.toList() },family.id)
            }
        }
    }

    @Test fun sevenFamiliesFormIndependentProductsRoundTripAndObserveReadOnly() {
        val specs = sharedCatalog().getValue("material_families") as Map<*,*>
        val precisionErrors=linkedMapOf<String,Float>()
        for(family in MaterialFamily.entries) {
            val s=formed(family);val products=PhoneMaterialKernel.finish(s)
            val provenance=RecipeJson.objectMap(products.provenanceJson)
            assertEquals(PhoneMaterialKernel.MODEL,(provenance["process"] as Map<*,*>)["model"])
            assertEquals(PhoneMaterialKernel.ARITHMETIC_HALF,(provenance["execution_implementation"] as Map<*,*>)["precision"])
            val version = ((specs[family.id] as Map<*,*>)["schema_version"] as Number).toInt()
            assertTrue(products.bodies.all { it.schemaVersion == version })
            assertEquals(if(family==MaterialFamily.PEEL)2 else 1,products.bodies.size)
            val output=ByteArrayOutputStream();MaterialProductArchive.write(products,output)
            val loaded=MaterialProductArchive.read(ByteArrayInputStream(output.toByteArray()))
            assertEquals(RecipeJson.objectMap(products.provenanceJson),RecipeJson.objectMap(loaded.provenanceJson)-"phone_storage_precision")
            precisionErrors[family.name]=loaded.bodies.flatMapIndexed { i,b ->
                val a=PhoneMaterialObserve.observe(products.bodies[i]).linearRgb;val c=PhoneMaterialObserve.observe(b).linearRgb
                a.indices.map { kotlin.math.abs(a[it]-c[it]) }
            }.max()
            for(body in loaded.bodies) {
                val before=body.fields();val image=PhoneMaterialObserve.observe(body)
                assertTrue(image.linearRgb.all {it.isFinite() && it>=0});assertEquals(rgb.width,image.width)
                val view=MaterialView(exposureEv=-1.0,illuminant=if(family==MaterialFamily.SILVER)listOf(1.0,1.0,1.0) else listOf(.7,1.0,.4))
                val dim=PhoneMaterialObserve.observe(body,view)
                for(i in image.linearRgb.indices)assertEquals(image.linearRgb[i]*view.illuminant[i%3].toFloat()*.5f,dim.linearRgb[i],1e-6f)
                before.forEach {(k,v)->assertArrayEquals(v.values,body.fields().getValue(k).values,0f)}
            }
            // Optional tiny interop evidence only when explicitly requested by the runner.
            System.getenv("FILM_MOBILE_INTEROP_DIR")?.let { path->
                File(path).mkdirs();File(path,"${family.name}.ffmaterial.npz").outputStream().use {MaterialProductArchive.write(products,it)}
                val images=loaded.bodies.mapIndexed {index,body->"image_$index" to NpyValue.Float32(FloatNd(intArrayOf(rgb.height,rgb.width,3),PhoneMaterialObserve.observe(body).linearRgb))}.toMap()
                File(path,"${family.name}_observation.npz").outputStream().use {NpzFile.write(images,it)}
                if(family==MaterialFamily.WET) {
                    val tint=PhoneMaterialKernel.finish(formed(MaterialFamily.WET,mapOf("support" to "tintype")))
                    File(path,"TINTYPE.ffmaterial.npz").outputStream().use {MaterialProductArchive.write(tint,it)}
                    val image=PhoneMaterialObserve.observe(tint.bodies[0])
                    File(path,"TINTYPE_observation.npz").outputStream().use {NpzFile.write(mapOf("image_0" to NpyValue.Float32(FloatNd(intArrayOf(image.height,image.width,3),image.linearRgb))),it)}
                }
            }
        }
        assertTrue(precisionErrors.values.all {it<.0002f},"Appearance errors: $precisionErrors")
    }
    @Test fun explicitPaperDeveloperAndLimitedTransferPreserveTheImageCarrier() {
        val base=PhoneMaterialKernel.expose(MaterialFamily.PAPER,rgb,mapOf("kind" to "color"),groupId="paper")
        fun develop(type: String)=PhoneMaterialKernel.process(base,"develop",mapOf("developer" to type))
        val bw=develop("bw");val color=develop("color")
        assertArrayEquals(color.fields().getValue("silver").values,bw.fields().getValue("silver").values,0f)
        assertTrue(color.fields().getValue("dye").values.any { it>0 })
        assertTrue(bw.fields().getValue("dye").values.all { it==0f })
        val stopped=PhoneMaterialKernel.process(bw,"stop")
        assertEquals(bw.elapsedSeconds,stopped.elapsedSeconds)
        assertThrows(IllegalArgumentException::class.java) { PhoneMaterialKernel.process(stopped,"develop") }
        val cleared=PhoneMaterialKernel.process(stopped,"bleach_fix",mapOf("seconds" to 1200))
        assertTrue(cleared.fields().getValue("silver").values.all { it<1e-6f })
        assertTrue(cleared.fields().getValue("dye").values.all { it==0f })
        for(family in listOf(MaterialFamily.PEEL, MaterialFamily.INSTANT)) {
            val neutral=RgbVolume(1,1,linearRgb=FloatArray(3) { .18f })
            val sheet=PhoneMaterialKernel.expose(family,neutral,mapOf("developer_capacity" to .12,"transfer" to "dye"),groupId="sheet")
            val contact=PhoneMaterialKernel.process(sheet,if(family==MaterialFamily.PEEL) "spread" else "eject")
            fun react(light: Double)=PhoneMaterialKernel.process(contact,"advance",mapOf("seconds" to 60,"light_exposure" to light)).fields()
            val dark=react(0.0);val light=react(1.0)
            for(state in listOf(dark,light)) {
                val received=state.getValue("received").values
                for(c in 0..2) {
                    assertEquals(received[0],received[c],0f)
                    assertEquals(1f,received[c]+state.getValue("remaining").values[c]+state.getValue("immobile").values[c],1e-6f)
                    assertEquals(.12f,received[c]+state.getValue("developed_donor").values[c],1e-6f)
                }
            }
            assertTrue(light.getValue("received").values[0]<dark.getValue("received").values[0])
        }
    }

    @Test fun finiteTransferNegativeDeclarationAndStageGuards() {
        val m=mapOf("transfer" to "silver_compound","donor_mode" to "silver_negative","developer_capacity" to .2)
        val s=formed(MaterialFamily.PEEL,m,PhoneMaterialKernel.ARITHMETIC_FULL);val f=s.fields()
        for(i in f.getValue("received").values.indices)assertEquals(1f,f.getValue("received").values[i]+f.getValue("remaining").values[i]+f.getValue("immobile").values[i],1e-6f)
        for(i in f.getValue("phone_developer_used_mean").values.indices)assertEquals(.2f,f.getValue("phone_developer_used_mean").values[i]+f.getValue("phone_developer_available_mean").values[i],1e-6f)
        assertFalse(PhoneMaterialKernel.finish(s).bodies[1].usableNegative)
        val cleared=PhoneMaterialKernel.process(s,"clear_donor");val negative=PhoneMaterialKernel.finish(cleared).bodies[1]
        assertTrue(negative.usableNegative);assertTrue(PhoneMaterialObserve.observe(negative).linearRgb.all {it in 0f..1f})
        assertThrows(IllegalArgumentException::class.java) {PhoneMaterialKernel.process(s,"advance")}
        assertThrows(IllegalArgumentException::class.java) {PhoneMaterialKernel.expose(MaterialFamily.PEEL,rgb,mapOf("donor_mode" to "silver_negative"),groupId="bad")}
    }
    @Test fun directionalPlateAndPaperPolarityAreNativeObservations() {
        val silver=PhoneMaterialKernel.finish(formed(MaterialFamily.SILVER)).bodies[0]
        val a=PhoneMaterialObserve.observe(silver);val b=PhoneMaterialObserve.observe(silver,MaterialView(viewDirection=listOf(0.0,0.0,1.0)))
        assertFalse(a.linearRgb.contentEquals(b.linearRgb))
        val paper=PhoneMaterialKernel.finish(formed(MaterialFamily.PAPER,mapOf("kind" to "color"))).bodies[0]
        assertTrue(PhoneMaterialObserve.observe(paper).linearRgb.all {it.isFinite()})
        val wet=PhoneMaterialKernel.finish(formed(MaterialFamily.WET,mapOf("support" to "tintype"))).bodies[0]
        assertFalse(wet.usableNegative);assertTrue(PhoneMaterialObserve.observe(wet).linearRgb.all {it.isFinite()})
        val defined=PhoneMaterialKernel.finish(formed(MaterialFamily.PAPER,mapOf("surface_gloss" to .8,"pinholes" to .4,"coating_variation" to .2,"loom_state" to 1)))
        val implementation=RecipeJson.objectMap(defined.provenanceJson)["execution_implementation"] as Map<*,*>
        val preserved=implementation["preserved_material_fields"] as List<*>
        assertTrue("loom_state" in preserved && "pinholes" in preserved && "coating_variation" in preserved)
        assertFalse("surface_gloss" in preserved)
        assertFalse("kind" in implementation["preserved_material_fields"] as List<*>)
        assertEquals("paper_reflection_v2",defined.bodies[0].observationModel)
    }
    @Test fun reducedBathsChangeInventoryWithoutLeavingTheFamilyContract() {
        val rc=moisture("rc");val fiber=moisture("fiber")
        assertTrue(rc>fiber)
        val dull=observation(0.0);val shiny=observation(1.0)
        assertFalse(dull.contentEquals(shiny))
        val platinum=noble()
        val once=metal(platinum)
        val twice=metal(PhoneMaterialKernel.process(platinum,"develop",mapOf("seconds" to 600.0)))
        assertEquals(once,twice,1e-3f)
        val salts=platinum.fields();val coating=salts.getValue("coating").values
        for(i in coating.indices) assertEquals(coating[i],salts.getValue("platinum_salt").values[i]+salts.getValue("palladium_salt").values[i]+
            salts.getValue("platinum").values[i]+salts.getValue("palladium").values[i],1e-4f)
        assertTrue(ironBalance(formed(MaterialFamily.CYANOTYPE,arithmetic=PhoneMaterialKernel.ARITHMETIC_FULL))<1e-3f)
        val plain=PhoneMaterialKernel.expose(MaterialFamily.WET,rgb,mapOf("flow_ridges" to 0.0),groupId="flat",seed=3)
        val ridged=PhoneMaterialKernel.expose(MaterialFamily.WET,rgb,mapOf("flow_ridges" to 1.0),groupId="ridged",seed=3)
        assertFalse(plain.fields().getValue("coating").values.contentEquals(ridged.fields().getValue("coating").values))
        var wet=PhoneMaterialKernel.expose(MaterialFamily.WET,rgb,emptyMap(),groupId="wetguard",seed=5)
        wet=PhoneMaterialKernel.process(wet,"develop");wet=PhoneMaterialKernel.process(wet,"fix");wet=PhoneMaterialKernel.process(wet,"wash")
        assertThrows(IllegalArgumentException::class.java){PhoneMaterialKernel.process(wet,"varnish")}
        assertThrows(IllegalArgumentException::class.java){PhoneMaterialKernel.process(wet,"wash",mapOf("region" to "spot"))}
        val tint=PhoneMaterialKernel.finish(formed(MaterialFamily.WET,mapOf("support" to "tintype"))).bodies[0]
        val straight=PhoneMaterialObserve.observe(tint,MaterialView(viewDirection=listOf(0.0,0.0,1.0)))
        assertFalse(PhoneMaterialObserve.observe(tint).linearRgb.contentEquals(straight.linearRgb))
        assertThrows(IllegalStateException::class.java){PhoneMaterialKernel.process(formed(MaterialFamily.SILVER),"weather")}
        assertThrows(IllegalStateException::class.java){PhoneMaterialKernel.process(formed(MaterialFamily.CYANOTYPE),"alkali_contact")}
        assertTrue(received(2.0,40.0)>received(0.0,40.0)*1.15f)
    }
    @Test fun workingImageIsNotCappedAtFourMegapixels() {
        assertEquals(48_000_000L,PhoneMaterialKernel.addressablePixels(8000,6000))
        assertThrows(IllegalArgumentException::class.java){PhoneMaterialKernel.addressablePixels(100_000,100_000)}
    }
    @Test fun halfPrecisionObservationStaysNearFullPrecision() {
        val diffs=linkedMapOf<String,Float>()
        for(family in MaterialFamily.entries) {
            val full=PhoneMaterialKernel.finish(formed(family,arithmetic=PhoneMaterialKernel.ARITHMETIC_FULL))
            val half=PhoneMaterialKernel.finish(formed(family,arithmetic=PhoneMaterialKernel.ARITHMETIC_HALF))
            var maxDiff=0f
            for(i in full.bodies.indices) {
                val a=PhoneMaterialObserve.observe(full.bodies[i]).linearRgb
                val b=PhoneMaterialObserve.observe(half.bodies[i]).linearRgb
                for(k in a.indices) maxDiff=max(maxDiff,abs(a[k]-b[k]))
            }
            diffs[family.name]=maxDiff
        }
        assertTrue(diffs.values.all {it<5e-4f},"Half-precision observation: $diffs")
    }
    private fun moisture(base: String): Float {
        var s=PhoneMaterialKernel.expose(MaterialFamily.PAPER,rgb,mapOf("paper_base" to base,"kind" to "graded"),groupId=base,seed=4)
        s=PhoneMaterialKernel.process(s,"develop",mapOf("seconds" to 5.0))
        return s.fields().getValue("paper_moisture").values.sum()/s.fields().getValue("paper_moisture").values.size
    }
    private fun observation(gloss: Double)=PhoneMaterialObserve.observe(PhoneMaterialKernel.finish(run {
        var s=PhoneMaterialKernel.expose(MaterialFamily.PAPER,rgb,mapOf("kind" to "graded","surface_gloss" to gloss,"drydown" to .2),groupId="gloss$gloss",seed=6)
        s=PhoneMaterialKernel.process(s,"develop");s=PhoneMaterialKernel.process(s,"fix");s=PhoneMaterialKernel.process(s,"wash")
        PhoneMaterialKernel.process(s,"dry",mapOf("seconds" to 5000.0,"humidity" to 0.0))
    }).bodies[0]).linearRgb
    private fun noble(): PhoneMaterialState {
        var s=PhoneMaterialKernel.expose(MaterialFamily.PLATINUM,rgb,emptyMap(),groupId="noble",seed=11,arithmetic=PhoneMaterialKernel.ARITHMETIC_FULL)
        s=PhoneMaterialKernel.process(s,"develop",mapOf("seconds" to 600.0))
        return s
    }
    private fun metal(s: PhoneMaterialState)=s.fields().getValue("platinum").values.sum()+s.fields().getValue("palladium").values.sum()
    private fun ironBalance(s: PhoneMaterialState): Float {
        val f=s.fields();var max=0f
        val coating=f.getValue("coating").values
        for(i in coating.indices) {
            val total=f.getValue("ferric").values[i]+f.getValue("ferrous").values[i]+f.getValue("reduced_pigment").values[i]+
                f.getValue("blue_pigment").values[i]+f.getValue("iron_removed").values[i]
            max=max(max,abs(total-coating[i]))
        }
        return max
    }
    private fun received(sensitivity: Double,temperature: Double): Float {
        var s=PhoneMaterialKernel.expose(MaterialFamily.PEEL,rgb,mapOf("temperature_sensitivity" to sensitivity),groupId="peel$sensitivity$temperature",seed=13,arithmetic=PhoneMaterialKernel.ARITHMETIC_FULL)
        s=PhoneMaterialKernel.process(s,"spread");s=PhoneMaterialKernel.process(s,"advance",mapOf("seconds" to 12.0,"temperature_c" to temperature))
        return s.fields().getValue("received").values.sum()
    }
}
