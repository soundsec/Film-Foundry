package com.filmfoundry.app.ui

import com.filmfoundry.platform.ScanImageFormat

/** Exact delivery values are independent of asynchronous Android selection callbacks. */
data class ScanOutputSettings(val format: String="PNG",val bits: Int=16,val jpegQuality: Int=100) {
    init { ScanImageFormat.valueOf(format);require(bits in listOf(8,16) && jpegQuality in 1..100) }
    val formatIndex get()=formats.indexOf(format)
    val qualityIndex get()=when(format) {
        "TIFF" -> 1
        "JPEG" -> jpegLevels.indexOf(jpegQuality).takeIf { it>=0 } ?: 3
        else -> if(bits==8) 0 else 1
    }
    val actualBits get()=when(format) { "JPEG"->8;"TIFF"->16;else->bits }
    fun select(formatIndex: Int,qualityIndex: Int): ScanOutputSettings {
        require(formatIndex in formats.indices && qualityIndex in 0..3)
        val nextFormat=formats[formatIndex]
        if(nextFormat!=format) return copy(format=nextFormat)
        if(qualityIndex==this.qualityIndex || nextFormat=="TIFF") return this
        if(nextFormat=="JPEG" && qualityIndex==3 || nextFormat=="PNG" && qualityIndex>1) return this
        return if(nextFormat=="JPEG") copy(jpegQuality=jpegLevels[qualityIndex])
            else copy(bits=if(qualityIndex==0) 8 else 16)
    }
    fun applyTo(draft: ScanEditorDraft)=draft.copy(outputFormat=format,outputBits=bits,jpegQuality=jpegQuality)
    companion object {
        val jpegLevels=listOf(40,70,100)
        val formats=listOf("JPEG","PNG","TIFF")
        fun from(draft: ScanEditorDraft)=ScanOutputSettings(draft.outputFormat,draft.outputBits,draft.jpegQuality)
    }
}
