package com.filmfoundry.interchange

/** Material aperture plus optional 135 gate baffles. Panorama keeps the full 135 output. */
data class StillFrameFormat(val format: String,val panorama: Boolean,val sourceWidth: Int,val sourceHeight: Int,
    val left: Int,val top: Int,val width: Int,val height: Int,val model: String=MODEL) {
    fun metadata(): Map<String,Any?> = mapOf("model" to model,"format" to format,"panorama" to panorama,
        "source_width" to sourceWidth,"source_height" to sourceHeight,"left" to left,"top" to top,"width" to width,"height" to height)
    /** Uncovered rectangle in the stored pixel grid. Baffles are always the top and bottom rows. */
    fun openArea(): IntArray {
        if(!panorama) return intArrayOf(0,0,width,height)
        val open=(width/(if(model==MODEL) (36.0/13.0) else 2.4)).toInt().coerceIn(1,height)
        return intArrayOf(0,(height-open)/2,width,open)
    }
    companion object {
        const val LEGACY_MODEL="phone_still_gate_v1"
        const val PREVIOUS_MODEL="phone_still_gate_v2"
        const val V3_MODEL="phone_still_gate_v3"
        const val V4_MODEL="phone_still_gate_v4"
        const val MODEL="phone_still_gate_v5"
        val roll120Frames=mapOf("6x4.5" to 16,"6x6" to 12,"6x7" to 10,"6x9" to 8)
        val longEdgeMm=mapOf("135" to 36.0,"half" to 24.0,"6x4.5" to 56.0,"6x6" to 56.0,"6x7" to 68.5,"6x9" to 82.6)
        fun is120(format: String)=format in roll120Frames
        fun capacity(format: String,length: Int): Int = when(format) {
            "135" -> length.also { require(it in listOf(12,24,36)) }
            "half" -> length.also { require(it in listOf(12,24,36)) }*2
            in roll120Frames -> roll120Frames.getValue(format).also { require(length == 120) }
            else -> error("unknown film format")
        }
        fun validateRoll(capacity: Int,settings: Map<String,Any?>) {
            val declared=settings["format"] ?: return
            val format=declared as? String ?: error("invalid film format")
            val number=settings["stock_length"] as? Number
            require(!settings.containsKey("stock_length") || number != null)
            val length=number?.toInt()?.also { require(it.toDouble() == number.toDouble()) }
                ?: when(format) { "135" -> capacity;"half" -> capacity/2;in roll120Frames -> 120;else -> error("unknown film format") }
            require(capacity == capacity(format,length)) { "roll capacity differs from stock format" }
        }
        fun create(format: String,panorama: Boolean,width: Int,height: Int,model: String=MODEL): StillFrameFormat {
            require(width > 0 && height > 0 && format in listOf("legacy","135","half")+roll120Frames.keys)
            require(model in listOf(MODEL,V4_MODEL,V3_MODEL,PREVIOUS_MODEL,LEGACY_MODEL))
            require(format !in listOf("6x4.5","6x7","6x9") || model in listOf(MODEL,V4_MODEL,V3_MODEL))
            require(!panorama || format == "135") { "panorama baffles require 135 film" }
            if(format=="half" && model in listOf(PREVIOUS_MODEL,V3_MODEL)) {
                val parent=create("135",false,width,height,model)
                val w=if(width>=height) maxOf(1,parent.width/2) else parent.width
                val h=if(width>=height) parent.height else maxOf(1,parent.height/2)
                return StillFrameFormat(format,false,width,height,parent.left+(parent.width-w)/2,parent.top+(parent.height-h)/2,w,h,model)
            }
            // Film dimensions fix shape and physical scale, not a second crop of the sensor grid.
            val ratio=when(format) { "135" -> 1.5;"half" -> if(model==LEGACY_MODEL) 4.0/3.0 else 3.0/4.0;"6x4.5" -> 56.0/41.5;"6x6" -> 1.0;"6x7" -> 68.5/56.0;"6x9" -> 82.6/56.0;else -> maxOf(width,height).toDouble()/minOf(width,height) }
            val target=if(width >= height) ratio else 1.0/ratio
            val w=if(width.toDouble()/height > target) (height*target).toInt().coerceIn(1,width) else width
            val h=if(width.toDouble()/height > target) height else (width/target).toInt().coerceIn(1,height)
            return StillFrameFormat(format,panorama,width,height,(width-w)/2,(height-h)/2,w,h,model)
        }
        fun read(raw: Any?,width: Int,height: Int): StillFrameFormat {
            if(raw == null) return StillFrameFormat("legacy",false,width,height,0,0,width,height)
            val map=raw as? Map<*,*> ?: error("invalid frame aperture")
            require(map["model"] in listOf(MODEL,V4_MODEL,V3_MODEL,PREVIOUS_MODEL,LEGACY_MODEL) && map["panorama"] is Boolean)
            val expected=create(map["format"] as String,map["panorama"] as Boolean,width,height,map["model"] as String)
            require(MovieMasterCodec.snapshot(expected.metadata()) == MovieMasterCodec.snapshot(map.entries.associate { it.key.toString() to it.value })) { "frame aperture differs from source" }
            return expected
        }
    }
}
