package com.filmfoundry.app.session

import com.filmfoundry.interchange.RecipeJson

/** A portable offline riddle, not a cryptographic access-control system. */
class SeasonalPuzzle(bytes: ByteArray) {
    private val sealed: ByteArray
    private val sealKey="FilmFoundry:edge".toByteArray(Charsets.UTF_8)
    init {
        val definition=RecipeJson.objectMap(bytes.toString(Charsets.UTF_8))
        require(definition["model"]=="seasonal_collection_v1" && definition["codec"]=="xor_utf8_identity_hex_v1")
        sealed=unhex(definition["sealed_phrase"] as String)
        require(sealed.size in 1..256)
    }
    private fun phrase()=ByteArray(sealed.size) { (sealed[it].toInt() xor sealKey[it%sealKey.size].toInt()).toByte() }
    fun matches(value: String): Boolean = value.trim().toByteArray(Charsets.UTF_8).contentEquals(phrase())
    fun cipher(identity: String): String {
        require(identity.matches(Regex("[0-9A-F]{12}")))
        val key=identity.toByteArray(Charsets.US_ASCII);val raw=phrase()
        return hex(ByteArray(raw.size) { (raw[it].toInt() xor key[it%key.size].toInt()).toByte() })
    }
    companion object {
        fun unhex(value: String): ByteArray {
            require(value.length%2==0 && value.matches(Regex("[0-9A-Fa-f]+")))
            return ByteArray(value.length/2) { value.substring(it*2,it*2+2).toInt(16).toByte() }
        }
        fun hex(value: ByteArray)=value.joinToString("") { "%02X".format(it.toInt() and 255) }
    }
}
