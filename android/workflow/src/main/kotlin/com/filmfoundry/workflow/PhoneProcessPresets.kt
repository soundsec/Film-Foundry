package com.filmfoundry.workflow

/** Named creative routes; source material/base stays owned by the exposure roll. */
data class PhoneProcessPreset(val id: String,val labelZh: String,val labelEn: String,val recipe: Map<String,Any?>)

object PhoneProcessPresets {
    val entries=listOf(
        PhoneProcessPreset("phone_native_8","标准冲洗 · 自动匹配胶卷（推荐）","Standard · match this film (recommended)",mapOf("time_min" to 8.0,"chemistry" to "native")),
        PhoneProcessPreset("phone_native_4","轻冲洗","Light development",mapOf("time_min" to 4.0,"chemistry" to "native")),
        PhoneProcessPreset("phone_native_12","充分显影","Extended development",mapOf("time_min" to 12.0,"chemistry" to "native")),
        PhoneProcessPreset("phone_cross_8","彩负交叉反转（旧快捷）","Color negative cross reversal (legacy shortcut)",mapOf("time_min" to 8.0,"chemistry" to "cross")),
        PhoneProcessPreset("phone_color_negative","彩色负片工艺","Color negative process",mapOf("program_key" to "color_negative","time_min" to 8.0)),
        PhoneProcessPreset("phone_bw_negative","黑白负片工艺","B&W negative process",mapOf("program_key" to "bw_negative","time_min" to 8.0)),
        PhoneProcessPreset("phone_color_reversal","彩色反转工艺","Color reversal process",mapOf("program_key" to "color_reversal","time_min" to 8.0,"chemistry" to "native")),
        PhoneProcessPreset("phone_bw_reversal","黑白反转工艺","B&W reversal process",mapOf("program_key" to "bw_reversal","time_min" to 8.0,"chemistry" to "native")),
        PhoneProcessPreset("phone_negative_reversal","彩色负片 → 正冲 · 保留片基","Color negative → reversal · retain base",mapOf("program_key" to "color_reversal","time_min" to 8.0,"chemistry" to "cross")),
        PhoneProcessPreset("phone_negative_clear_reversal","彩色反转工艺 · 片基漂白","Color reversal · base-mask bleaching",mapOf("program_key" to "color_reversal","time_min" to 8.0,"chemistry" to "cross","mask_bleach_completion" to 1.0)),
        PhoneProcessPreset("phone_bw_negative_reversal","黑白负片 · 反转冲洗","B&W negative → reversal",mapOf("program_key" to "bw_reversal","time_min" to 8.0,"chemistry" to "cross")),
        PhoneProcessPreset("phone_slide_negative","彩色反转片 → 负冲（实验）","Color reversal stock → negative (experimental)",mapOf("program_key" to "color_negative","time_min" to 8.0,"chemistry" to "cross")),
        PhoneProcessPreset("phone_fine_grain","微粒药液 · 常规","Fine-grain bath · native",mapOf("program_key" to "auto","developer_type" to "fine_grain","fixer_type" to "standard","time_min" to 9.0)),
        PhoneProcessPreset("phone_adjacency_acutance","邻接显影 · 轮廓与颗粒","Adjacency development · edges and grain",mapOf("program_key" to "auto","developer_type" to "high_acutance","time_min" to 8.0,"agitation" to .65,"development_adjacency_strength" to .5,"development_adjacency_radius" to .0025)),
        PhoneProcessPreset("phone_compensating","补偿显影 · 静置倾向","Compensating · low agitation",mapOf("program_key" to "auto","developer_type" to "compensating","time_min" to 10.0,"agitation" to .5)),
        PhoneProcessPreset("phone_push_bath","迫冲药液 · 粗粒硬调","Push bath · coarse / hard",mapOf("program_key" to "auto","developer_type" to "push","push_stops" to 1.0,"time_min" to 12.0)),
        PhoneProcessPreset("phone_silver_bypass","彩色负片工艺 · 留银（跳过银漂白）","Color negative · retain silver (skip silver bleach)",mapOf("program_key" to "color_negative_bleach_bypass","time_min" to 8.0)),
        PhoneProcessPreset("phone_exhausted_bath","衰退药液 · 实验事故","Exhausted bath · experimental incidents",mapOf("program_key" to "auto","developer_type" to "exhausted","fixer_exhaustion" to .4,"time_min" to 8.0))
    )
    private val compatibility=setOf("phone_native_4","phone_native_12","phone_cross_8","phone_negative_reversal","phone_bw_negative_reversal","phone_slide_negative")
    fun visible(selected: String?=null)=entries.filter { it.id !in compatibility || it.id==selected }
}
