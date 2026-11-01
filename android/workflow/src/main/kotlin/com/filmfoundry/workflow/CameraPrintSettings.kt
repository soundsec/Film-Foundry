package com.filmfoundry.workflow

/** Shooting-time edits own only the imprint; body leaks and gate dirt remain frozen. */
object CameraPrintSettings {
    fun normalizeText(text: String)=com.filmfoundry.kernel.PhoneImprintFont.normalize(text)
    fun validText(text: String)=com.filmfoundry.kernel.PhoneImprintFont.valid(text)
    fun update(camera: Map<String,Any?>,text: String,style: String,side: String,brightness: Double?=null,x: Double?=null,y: Double?=null): Map<String,Any?> {
        val normalized=normalizeText(text)
        require(validText(normalized)) { "Use up to 24 English letters, digits, spaces or ASCII punctuation" }
        require(style in listOf("none","laser_dot","optical_amber","lab_edge") && side in listOf("top","bottom")) { "Invalid imprint style or position" }
        val placement=mutableMapOf<String,Any?>()
        if(brightness!=null) { require(brightness.isFinite() && brightness in 0.0..2.0);placement["imprint_brightness"]=brightness }
        if(x!=null || y!=null) {
            require(x!=null && y!=null && x.isFinite() && y.isFinite() && x in 0.0..1.0 && y in 0.0..1.0)
            placement["imprint_x"]=x;placement["imprint_y"]=y
        }
        return frozenRecipe(camera+placement+mapOf("imprint" to normalized,"imprint_style" to style,"imprint_side" to side))
    }
}
