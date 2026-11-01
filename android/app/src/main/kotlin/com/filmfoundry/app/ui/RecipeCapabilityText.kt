package com.filmfoundry.app.ui

import com.filmfoundry.workflow.PhoneRecipeCapabilities

internal fun capabilityText(shell: MobileShell,report: PhoneRecipeCapabilities.Report): String {
    val warnings=report.warnings
    if(warnings.isEmpty()) return ""
    val labels=mapOf(
        "dir_material_response_matrix" to ("DIR 层间响应" to "DIR interlayer response"),
        "development_adjacency_strength" to ("首显邻接" to "Development adjacency"),
        "light_piping_strength" to ("片基光导" to "Base light piping"),
        "silver_grain_strength" to ("独立银粒" to "Separate silver grain"),
        "emulsion_highlight_spread_strength" to ("乳剂层间高光扩散" to "Emulsion highlight spread"),
        "halation_strength" to ("所选 Halation 模型" to "Selected halation model"),
        "negative_channel_matrix" to ("负片通道矩阵" to "Negative channel matrix"),
        "negative_channel_gamma" to ("负片通道 Gamma" to "Negative channel gamma"),
        "print_gamma" to ("印相 Gamma" to "Print gamma"),
        "print_color_bias" to ("印相通道增益" to "Print channel gain"),
        "print_color_shift" to ("印相颜色偏移" to "Print color shift"),
        "highlight_color_bias" to ("高光通道增益" to "Highlight channel gain"))
    return shell.copy("手机版支持提醒：","Phone capability notes:")+"\n"+warnings.joinToString("\n") { entry ->
        val key=entry.path.substringAfterLast('.');val label=labels[key]?.let { shell.copy(it.first,it.second) } ?: key
        label+if(entry.status==PhoneRecipeCapabilities.Status.UNSUPPORTED) shell.copy("：当前不支持，需更换设置。"," — unsupported; choose another setting.")
            else shell.copy("：手机版暂不支持；设置已保留。"," — not yet supported on mobile; setting retained.")
    }
}
