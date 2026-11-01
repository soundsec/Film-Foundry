package com.filmfoundry.platform

/** Output encoding only. These controls never change the final optical medium. */
object ScanDelivery {
    val keys=setOf("output_format","output_bits","jpeg_quality")
    fun format(recipe: Map<String,Any?>)=ScanImageFormat.valueOf((recipe["scanner"] as? Map<*,*>)?.get("output_format") as? String ?: "PNG")
    fun bits(recipe: Map<String,Any?>): Int {
        val value=(recipe["scanner"] as? Map<*,*>)?.get("output_bits") as? Number
        require(value==null || value.toDouble()==value.toInt().toDouble() && value.toInt() in listOf(8,16))
        return when(format(recipe)) { ScanImageFormat.JPEG->8;ScanImageFormat.TIFF->16;else->value?.toInt() ?: 16 }
    }
    fun jpegQuality(recipe: Map<String,Any?>): Int {
        val v=(recipe["scanner"] as? Map<*,*>)?.get("jpeg_quality") as? Number ?: return 95
        require(v.toDouble()==v.toInt().toDouble() && v.toInt() in 1..100);return v.toInt()
    }
    fun allowed(format: ScanImageFormat,bits: Int): Set<ScanImageFormat> = when {
        format==ScanImageFormat.JPEG -> setOf(ScanImageFormat.JPEG)
        bits<16 -> setOf(ScanImageFormat.PNG,ScanImageFormat.JPEG)
        else -> ScanImageFormat.entries.toSet()
    }
    fun requireExport(saved: SavedScan,target: ScanImageFormat) {
        require(target in allowed(saved.format,saved.precisionBits)) { "不能提高既有成片的数据质量；请从来源底片重新扫描 / Rescan the source film for a higher-quality format" }
    }
}
