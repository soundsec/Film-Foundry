package com.filmfoundry.interchange

import java.util.Collections
import org.json.JSONObject

/** Optional latent binding, reusing the existing FFC2 film object, never bath controls. */
object MaterialSnapshot {
    private fun freeze(value: Any?): Any? = when(value) {
        is Map<*,*> -> {
            require(value.keys.all { it is String }) { "Material parameter keys must be strings" }
            Collections.unmodifiableMap(value.entries.associate { it.key as String to freeze(it.value) })
        }
        is List<*> -> Collections.unmodifiableList(value.map(::freeze))
        is Number -> value.also { require(it.toDouble().isFinite()) { "Material parameters must be finite" } }
        null,is String,is Boolean -> value
        else -> throw IllegalArgumentException("Material parameters must contain JSON values")
    }

    fun capture(film: Map<String,Any?>): Map<String,Any?> {
        @Suppress("UNCHECKED_CAST")
        val detached=freeze(film) as Map<String,Any?>
        require(JSONObject(detached).toString().toByteArray(Charsets.UTF_8).size<=262144) { "Material snapshot is too large" }
        return Collections.unmodifiableMap(mapOf("model" to DataKernel.MATERIAL_SNAPSHOT_MODEL,"film" to detached))
    }

    fun film(value: Any?): Map<String,Any?> {
        val envelope=value as? Map<*,*> ?: throw IllegalArgumentException("Material snapshot must be an object")
        require(envelope.keys==setOf("model","film") && envelope["model"]==DataKernel.MATERIAL_SNAPSHOT_MODEL) {
            "Unsupported material snapshot fields or model"
        }
        val film=envelope["film"] as? Map<*,*> ?: throw IllegalArgumentException("Material snapshot film must be an object")
        require(film.keys.all { it is String })
        @Suppress("UNCHECKED_CAST")
        return capture(film as Map<String,Any?>)["film"] as Map<String,Any?>
    }

    fun resolve(recipe: Map<String,Any?>,metadata: Map<String,Any?>): Map<String,Any?> {
        if(!metadata.containsKey(DataKernel.MATERIAL_SNAPSHOT_KEY)) return recipe
        val source=film(metadata[DataKernel.MATERIAL_SNAPSHOT_KEY])
        // Replace the entire object: absent source parameters use this backend's
        // defaults, never values from whichever unrelated stock is selected now.
        return recipe+("film" to source)
    }

    fun binding(metadata: Map<String,Any?>)=mapOf("model" to DataKernel.MATERIAL_SNAPSHOT_MODEL,
        "source" to if(metadata.containsKey(DataKernel.MATERIAL_SNAPSHOT_KEY)) "latent_snapshot" else "caller_legacy",
        "unspecified_parameters" to "receiver_model_defaults")+
        if(metadata.containsKey(DataKernel.MATERIAL_SNAPSHOT_KEY)) mapOf("snapshot" to metadata[DataKernel.MATERIAL_SNAPSHOT_KEY]) else emptyMap()
}
