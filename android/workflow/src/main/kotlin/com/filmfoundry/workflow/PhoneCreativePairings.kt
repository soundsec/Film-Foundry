package com.filmfoundry.workflow

import com.filmfoundry.interchange.RecipeJson

/** Complete optional starting recipes. Existing rolls and independent definitions are never changed. */
data class PhoneCreativePairing(val id: String,val labelZh: String,val labelEn: String,val materialId: String,
    val summaryZh: String,val summaryEn: String,val recipe: Map<String,Any?>)

object PhoneCreativePairings {
    fun read(bytes: ByteArray): List<PhoneCreativePairing> {
        require(bytes.size<=262144)
        val root=RecipeJson.objectMap(bytes.toString(Charsets.UTF_8))
        require(root["model"]=="phone_creative_pairings_v1")
        val entries=root["entries"] as? List<*> ?: error("Missing pairings");require(entries.size in 1..32)
        return entries.map { raw ->
            val row=raw as? Map<*,*> ?: error("Invalid pairing")
            fun text(key: String)=(row[key] as? String)?.also { require(it.isNotBlank() && it.length<=240) } ?: error("Missing $key")
            val recipe=(row["recipe"] as? Map<*,*>)?.entries?.associate { it.key.toString() to it.value } ?: error("Missing recipe")
            require(recipe.keys==setOf("film","develop","scanner") && recipe.values.all { it is Map<*,*> })
            PhoneCreativePairing(text("id"),text("label_zh"),text("label_en"),text("material_id"),text("summary_zh"),text("summary_en"),frozenRecipe(recipe))
        }.also { require(it.map { e->e.id }.distinct().size==it.size) }
    }
}
