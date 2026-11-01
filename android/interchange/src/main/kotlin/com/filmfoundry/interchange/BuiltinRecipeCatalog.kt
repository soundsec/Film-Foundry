package com.filmfoundry.interchange

import org.json.JSONObject
import org.json.JSONArray

data class BuiltinRecipe(val id: String,val labelZh: String,val labelEn: String,val source: String,val recipe: Map<String,Any?>)
data class BuiltinRecipeCatalog(val materials: List<BuiltinRecipe>,val scanners: List<BuiltinRecipe>,val dailyMaterialIds: Set<String>?=null) {
    fun dailyMaterials(selected: String?=null)=materials.filter { dailyMaterialIds==null || it.id in dailyMaterialIds || it.id==selected }
    fun dailyScanners(selected: String?=null)=scanners.filter { it.id !in setOf("reference_soft_tone_scan","reference_deep_tone_scan") || it.id==selected }
    companion object {
        fun read(bytes: ByteArray): BuiltinRecipeCatalog {
            require(bytes.size <= 512*1024)
            val root=JSONObject(bytes.toString(Charsets.UTF_8));require(root.getString("model") == "phone_builtin_recipes_v1")
            fun values(value: Any?): Any?=when(value) {
                is JSONObject -> value.keys().asSequence().associateWith { values(value.get(it)) }
                is JSONArray -> (0 until value.length()).map { values(value.get(it)) }
                JSONObject.NULL -> null
                else -> value
            }
            fun group(key: String,section: String): List<BuiltinRecipe> {
                val array=root.getJSONArray(key);require(array.length() in 1..64)
                return (0 until array.length()).map { index ->
                    val entry=array.getJSONObject(index)
                    @Suppress("UNCHECKED_CAST") val recipe=values(entry.getJSONObject("recipe")) as Map<String,Any?>
                    require(recipe.keys == setOf(section) && recipe[section] is Map<*,*>)
                    BuiltinRecipe(entry.getString("id").also { require(it.matches(Regex("[a-z0-9_]{1,80}"))) },
                        entry.getString("label_zh"),entry.getString("label_en"),entry.getString("source"),MovieMasterCodec.snapshot(recipe))
                }.also { require(it.map { e -> e.id }.distinct().size == it.size) }
            }
            val materials=group("materials","film")
            val ids=root.optJSONArray("daily_material_ids")?.let { array -> (0 until array.length()).map { array.getString(it) }.toSet() }
            require(ids==null || ids.isNotEmpty() && ids.all { id -> materials.any { it.id==id } })
            return BuiltinRecipeCatalog(materials,group("scanners","scanner"),ids)
        }
    }
}
