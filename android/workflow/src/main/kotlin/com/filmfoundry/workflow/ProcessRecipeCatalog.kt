package com.filmfoundry.workflow

import com.filmfoundry.interchange.*

data class NamedProcessRecipe(val id: String,val label: String,val recipe: Map<String,Any?>)

/** Application-private process definitions, separate from film/scanner presets and medium files. */
class ProcessRecipeCatalog(private val store: RecipeCatalogStore) {
    private val entries=linkedMapOf<String,NamedProcessRecipe>()
    init {
        store.readCatalog()?.let { bytes ->
            require(bytes.size <= 512*1024)
            val root=RecipeJson.objectMap(bytes.toString(Charsets.UTF_8));require(root["model"] == "phone_process_recipes_v1")
            val rows=root["entries"] as? List<*> ?: error("Invalid process catalog");require(rows.size <= 128)
            rows.forEach { value ->
                val row=value as? Map<*,*> ?: error("Invalid process entry")
                val id=row["id"] as? String ?: error("Missing process id");require(id.matches(Regex("[a-zA-Z0-9_-]{1,80}")) && id !in entries)
                val label=row["label"] as? String ?: error("Missing process name");require(label.isNotBlank() && label.length <= 64)
                val decoded=RecipeShareCodec.decode(row["share"]);require(decoded.keys == setOf("develop"))
                val recipe=(decoded["develop"] as Map<*,*>).entries.associate { it.key.toString() to it.value }
                entries[id]=NamedProcessRecipe(id,label,RecipeDrafts.process(recipe))
            }
        }
    }
    fun list()=entries.values.toList()
    @Synchronized fun save(id: String,label: String,recipe: Map<String,Any?>) {
        require(id.matches(Regex("[a-zA-Z0-9_-]{1,80}")) && label.isNotBlank() && label.length <= 64)
        val replacement=entries.toMutableMap();replacement[id]=NamedProcessRecipe(id,label.trim(),RecipeDrafts.process(recipe));require(replacement.size <= 128)
        persist(replacement);entries.clear();entries.putAll(replacement)
    }
    @Synchronized fun remove(id: String) {
        require(id in entries);val replacement=entries-id;persist(replacement);entries.remove(id)
    }
    private fun persist(values: Map<String,NamedProcessRecipe>) {
        val payload=mapOf("model" to "phone_process_recipes_v1","entries" to values.values.map { mapOf("id" to it.id,"label" to it.label,"share" to RecipeShareCodec.encode(mapOf("develop" to it.recipe))) })
        val bytes=RecipeJson.text(payload).toByteArray(Charsets.UTF_8);require(bytes.size <= 512*1024);store.writeCatalog(bytes)
    }
}
