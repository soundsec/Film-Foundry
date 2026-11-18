package com.filmfoundry.interchange

import java.io.InputStream
import java.io.OutputStream
import java.util.Collections

/** Same products envelope as desktop .ffmaterial.npz; never a film-negative archive. */
class MaterialBody(val family: String,val schemaVersion: Int,val groupId: String,val bodyId: String,
    val role: String,val observationModel: String,fields: Map<String,FloatNd>,
    val usableNegative: Boolean=false,val presentationJson: String="{}") {
    private val stored=fields.mapValues { it.value.copy() }
    val width: Int
    val height: Int
    val fieldNames: Set<String> get()=stored.keys.toSet()
    init {
        require(family in MaterialProductArchive.FAMILIES && schemaVersion>0 && groupId.isNotBlank() && bodyId.isNotBlank())
        require(stored.isNotEmpty());val shape=stored.values.first().shape
        require(shape.size in 2..3);height=shape[0];width=shape[1]
        for((name,v) in stored) require(name.matches(Regex("[a-z][a-z0-9_]*")) && v.shape.size in 2..3 && v.shape[0]==height && v.shape[1]==width && v.values.all { it.isFinite() })
        RecipeJson.objectMap(presentationJson)
    }
    fun fields()=stored.mapValues { it.value.copy() }
    /** Bounded, detached row access for observers; no mutable view of material state. */
    fun rows(start: Int,count: Int): Map<String,FloatNd> {
        require(start>=0 && count>0 && count<=height-start)
        return stored.mapValues { (_,v)->
            val stride=width*(if(v.shape.size==3)v.shape[2] else 1)
            FloatNd(v.shape.copyOf().also { it[0]=count },v.values.copyOfRange(start*stride,(start+count)*stride))
        }
    }
}

class MaterialProducts(val groupId: String,bodies: List<MaterialBody>,val provenanceJson: String) {
    val bodies: List<MaterialBody> = Collections.unmodifiableList(bodies.toList())
    init {
        require(groupId.isNotBlank() && bodies.isNotEmpty() && bodies.size<=16)
        require(bodies.all { it.groupId==groupId } && bodies.map { it.bodyId }.toSet().size==bodies.size)
        RecipeJson.objectMap(provenanceJson)
    }
}

object MaterialProductArchive {
    val FAMILIES=setOf("peel_apart_transfer_v1","integral_instant_v1","daguerreotype_surface_v1",
        "photographic_paper_v1","wet_collodion_v1","cyanotype_v1","platinum_palladium_v1")

    fun write(products: MaterialProducts,output: OutputStream,precision: MaterialStoragePrecision.Policy=MaterialStoragePrecision.Policy.AUTO) {
        val arrays=linkedMapOf<String,NpyValue>()
        val converted=linkedMapOf<String,List<String>>()
        val bodies=products.bodies.mapIndexed { i,b ->
            val fields=b.fields();val names=fields.keys.associateWith { "body_${i}_$it" }
            fields.forEach { (key,value)->arrays[names.getValue(key)]=MaterialStoragePrecision.encode(b.family,key,value,precision) }
            converted[b.bodyId]=fields.keys.filter { (arrays[names.getValue(it)] as NpyValue.Float32).storageBits==16 }
            mapOf("family" to b.family,"schema_version" to b.schemaVersion,"group_id" to b.groupId,
                "body_id" to b.bodyId,"role" to b.role,"observation_model" to b.observationModel,
                "usable_negative" to b.usableNegative,"presentation_json" to b.presentationJson,"fields" to names)
        }
        val sourceProvenance=RecipeJson.objectMap(products.provenanceJson)
        val previous=sourceProvenance["phone_storage_precision"] as? Map<*,*>
        val previousFields=(previous?.get("quantization_history") ?: previous?.get("float16_fields")) as? Map<*,*>
        val history=converted.mapValues { (body,fields)-> (fields+((previousFields?.get(body) as? List<*>)?.filterIsInstance<String>().orEmpty())).distinct().sorted() }
        val provenance=sourceProvenance+("phone_storage_precision" to mapOf(
            "model" to "appearance_float16_v1","policy" to precision.name.lowercase(),"float16_fields" to converted,
            "arithmetic" to "float32","quantization_history" to history))
        arrays["manifest"]=NpyValue.UnicodeScalar(RecipeJson.text(mapOf("kind" to "products","version" to 1,
            "group_id" to products.groupId,"provenance_json" to RecipeJson.text(provenance),"bodies" to bodies)))
        NpzFile.write(arrays,output)
    }

    fun read(input: InputStream,maxBytes: Long=256L*1024*1024): MaterialProducts {
        require(maxBytes>0)
        val arrays=NpzFile.readChunks(input,{true},NpzReadLimits(maxMembers=256,maxTotalBytes=maxBytes,
            maxStringBytes=262144,maxRetainedBytes=maxBytes),null)
        val meta=RecipeJson.objectMap((arrays["manifest"] as? NpyValue.UnicodeScalar)?.text ?: error("Missing material manifest"))
        require(meta["kind"]=="products" && (meta["version"] as? Number)?.toInt()==1) { "Only formed product archives are supported" }
        val group=meta["group_id"] as String
        val descriptors=meta["bodies"] as? List<*> ?: error("Missing products")
        require(descriptors.size in 1..16)
        val used=mutableSetOf("manifest")
        val bodies=descriptors.map { value ->
            val b=value as Map<*,*>;val names=b["fields"] as Map<*,*>
            val fields=names.entries.associate { (key,name)->
                require(key is String && name is String && used.add(name))
                key to ((arrays[name] as? NpyValue.Float32)?.array ?: error("Missing numeric material field"))
            }
            MaterialBody(b["family"] as String,(b["schema_version"] as Number).toInt(),b["group_id"] as String,
                b["body_id"] as String,b["role"] as String,b["observation_model"] as String,fields,
                b["usable_negative"] as Boolean,b["presentation_json"] as String)
        }
        require(used==arrays.keys) { "Undeclared material fields" }
        return MaterialProducts(group,bodies,meta["provenance_json"] as String)
    }
}
