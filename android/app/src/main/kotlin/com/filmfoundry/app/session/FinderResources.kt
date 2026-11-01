package com.filmfoundry.app.session

import com.filmfoundry.interchange.RecipeJson
import java.io.ByteArrayInputStream
import java.io.ByteArrayOutputStream
import java.util.zip.InflaterInputStream

/** Packed bundled resources, outside ordinary preset folders. */
class FinderResources(bytes: ByteArray) {
    private val values: Map<String,Any?>
    init {
        val magic=byteArrayOf(70,70,82,80,1)
        require(bytes.size in 6..2097152 && bytes.copyOfRange(0,5).contentEquals(magic))
        val key="FF native finder resources / 1".toByteArray(Charsets.UTF_8)
        val compressed=ByteArray(bytes.size-5) { (bytes[it+5].toInt() xor key[it%key.size].toInt()).toByte() }
        val buffer=ByteArrayOutputStream()
        InflaterInputStream(ByteArrayInputStream(compressed)).use { input ->
            val block=ByteArray(4096)
            while(true) {
                val n=input.read(block);if(n<0) break
                require(buffer.size()+n<=2097152);buffer.write(block,0,n)
            }
        }
        values=RecipeJson.objectMap(buffer.toByteArray().toString(Charsets.UTF_8))
        require(values["model"]=="finder_resources_v1")
    }
    @Suppress("UNCHECKED_CAST")
    fun section(key: String): ByteArray=RecipeJson.text(values[key] as? Map<String,Any?> ?: error("Missing resource section")).toByteArray(Charsets.UTF_8)
}
