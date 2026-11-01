package com.filmfoundry.app.ui

/** Vocabulary only. Program selection and reaction predictions come from workflow inspection. */
object ProcessPresentation {
    fun result(inspection: com.filmfoundry.workflow.RecipeDrafts.ProcessInspection,english: Boolean): String {
        if(!inspection.variableImage) return if(english) "May leave no recognizable image" else "可能没有可辨认影像"
        return (if("dye" in inspection.components) if(english) "Color" else "彩色" else if(english) "B&W" else "黑白")+
            (if(inspection.positive) if(english) " positive" else "正像" else if(english) " negative" else "负像")+
            if("dye" in inspection.components && "metallic_silver" in inspection.components) if(english) " · retained silver" else " · 留银" else ""
    }
    fun action(key: Any?,english: Boolean)=when(key) {
        "develop_silver"->if(english) "Silver development" else "黑白显影"
        "develop_color"->if(english) "Color development" else "彩色显影"
        "activate_remaining_halide"->if(english) "Reversal activation" else "反转激活"
        "bleach_silver"->if(english) "Silver bleach" else "银影漂白"
        "fix_halide"->if(english) "Fixing" else "定影"
        "remove_silver"->if(english) "Remove silver" else "去除银影"
        "destroy_dye"->if(english) "Dye loss" else "染料损伤"
        "bleach_mask_dye"->if(english) "Base-mask bleach" else "片基色罩漂除"
        "remove_auxiliary"->if(english) "Remove auxiliary layer" else "去除附加层"
        else->key?.toString().orEmpty()
    }
    fun program(key: String?,english: Boolean)=when(key) {
        "color_negative"->if(english) "Color negative process" else "彩色负片工艺"
        "bw_negative"->if(english) "B&W negative process" else "黑白负片工艺"
        "color_reversal"->if(english) "Color reversal process" else "彩色反转工艺"
        "bw_reversal"->if(english) "B&W reversal process" else "黑白反转工艺"
        "color_negative_bleach_bypass"->if(english) "Color negative · retain silver" else "彩色负片工艺 · 留银"
        "custom"->if(english) "Custom bath sequence" else "自由药浴组合"
        else->if(english) "Match this film" else "自动匹配胶卷"
    }
}
