package com.filmfoundry.app.ui

import com.filmfoundry.workflow.PhoneRecipeCapabilities

internal fun capabilityText(shell: MobileShell,report: PhoneRecipeCapabilities.Report): String {
    val warnings=report.warnings
    if(warnings.isEmpty()) return ""
    val labels=mapOf(
        "duration_seconds" to ("曝光时长与互易律" to "Exposure duration and reciprocity"),
        "carbon_backing" to ("碳背层状态" to "Carbon backing state"),
        "time_response" to ("材料时效响应" to "Material time response"),
        "process_steps" to ("逐浴工艺步骤" to "Ordered process steps"),
        "process_events" to ("浴间事故与表面处理" to "Process events and surface treatments"),
        "bath_overrides" to ("逐浴条件覆盖" to "Per-bath overrides"),
        "effect_intent" to ("效果期望映射" to "Effect intent mapping"),
        "latent_wait" to ("潜影等待" to "Latent waiting"),
        "post_process_light" to ("处理后受光" to "Post-process light"),
        "formation_history_model" to ("桌面局部形成史" to "Desktop local formation history"),
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
        label+when(entry.status) {
            PhoneRecipeCapabilities.Status.UNSUPPORTED -> shell.copy("：当前不支持，需更换设置。"," — unsupported; choose another setting.")
            PhoneRecipeCapabilities.Status.OMITTED -> shell.copy("：轻量形成中省略；设置已保留。"," — omitted in lightweight formation; setting retained.")
            else -> shell.copy("：手机版暂不支持；设置已保留。"," — not yet supported on mobile; setting retained.")
        }
    }
}
