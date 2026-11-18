package com.filmfoundry.interchange

import org.junit.jupiter.api.Assertions.*
import org.junit.jupiter.api.Test

class MaterialStoragePrecisionTest {
    @Test fun ieeeHalfRoundEvenSubnormalAndRange() {
        val input=floatArrayOf(0f,-0f,1f,-2f,65504f,1f/16777216f,1f/33554432f,1f+1f/2048f,1f+3f/2048f)
        val encoded=NpyCodec.encode(NpyValue.Float32(FloatNd(intArrayOf(input.size),input),16))
        assertEquals("<f2",NpyCodec.readHeader(encoded.inputStream()).descriptor)
        val values=(NpyCodec.decode(encoded) as NpyValue.Float32).array.values
        assertArrayEquals(floatArrayOf(0f,-0f,1f,-2f,65504f,1f/16777216f,0f,1f,1f+1f/512f),values,0f)
        assertEquals((-0f).toRawBits(),values[1].toRawBits())
        assertThrows(IllegalArgumentException::class.java) {NpyCodec.encode(NpyValue.Float32(FloatNd(intArrayOf(1),floatArrayOf(70000f)),16))}
    }
    @Test fun authorityInventoriesAndFullPrecisionNeverUseHalf() {
        val field=FloatNd(intArrayOf(1,2),floatArrayOf(.123456f,.887654f))
        assertEquals(16,MaterialStoragePrecision.encode("platinum_palladium_v1","paper",field).storageBits)
        assertEquals(32,MaterialStoragePrecision.encode("cyanotype_v1","paper",field).storageBits)
        assertEquals(32,MaterialStoragePrecision.encode("cyanotype_v1","paper",field,MaterialStoragePrecision.Policy.FULL).storageBits)
        for(key in listOf("optical_density_rgb","received","halide","coverage","density_cmy"))
            assertEquals(32,MaterialStoragePrecision.encode("cyanotype_v1",key,field).storageBits)
        assertEquals(32,MaterialStoragePrecision.encode("film","paper",field).storageBits)
        assertEquals(32,MaterialStoragePrecision.encode("cyanotype_v1","paper",FloatNd(intArrayOf(1),floatArrayOf(70000f))).storageBits)
        val body=MaterialBody("platinum_palladium_v1",1,"g","b","print","reflectance",mapOf("paper" to field))
        val source=MaterialProducts("g",listOf(body),"{}")
        val small=java.io.ByteArrayOutputStream();MaterialProductArchive.write(source,small)
        val saved=MaterialProductArchive.read(small.toByteArray().inputStream())
        val full=java.io.ByteArrayOutputStream();MaterialProductArchive.write(saved,full,MaterialStoragePrecision.Policy.FULL)
        val reloaded=MaterialProductArchive.read(full.toByteArray().inputStream())
        assertArrayEquals(saved.bodies[0].fields().getValue("paper").values,reloaded.bodies[0].fields().getValue("paper").values,0f)
        val precision=RecipeJson.objectMap(reloaded.provenanceJson)["phone_storage_precision"] as Map<*,*>
        assertEquals(listOf("paper"),(precision["quantization_history"] as Map<*,*>)["b"])
        assertEquals(emptyList<String>(),(precision["float16_fields"] as Map<*,*>)["b"])
    }
}
