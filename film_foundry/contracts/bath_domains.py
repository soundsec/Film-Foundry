"""Declared targets of the existing operators, shared by UI and provenance.

This inventory is not a second implementation or a chemical recipe registry.
"""
BATH_DOMAINS={
    'develop_silver':('image_emulsion',('halide','metallic_silver'),('将可显影卤化银转为成像银；不生成染料。','Convert developable halide into image silver; no dye coupling.')),
    'develop_color':('image_emulsion',('halide','metallic_silver','coupler','dye'),('显影银并消耗偶合剂形成染料，受剩余材料限制。','Develop silver and consume coupler to form dye, bounded by available material.')),
    'activate_remaining_halide':('latent_state',('developability',),('活化剩余卤化银；属于二次曝光或化学活化步骤，不是显影液风格。','Activate remaining halide; re-exposure/chemical activation, not a developer style.')),
    'bleach_silver':('image_emulsion',('metallic_silver','bleached_halide'),('将金属银转为可定影银盐；不是直接去除染料或色罩。','Convert metallic silver to fixable salt; does not directly remove dye or mask.')),
    'fix_halide':('image_emulsion',('halide','bleached_halide'),('去除未显影及漂白后的银盐；保留未漂白成像银和染料。','Remove undeveloped and bleached silver salts; preserve metallic silver and dye.')),
    'remove_silver':('image_emulsion',('metallic_silver',),('直接去银的降阶步骤，用于黑白反转流程。','Reduced direct silver removal, used in B&W reversal.')),
    'destroy_dye':('image_emulsion',('dye',),('染料池损失，区分明确损伤和工艺不匹配。','Dye-pool loss from explicit damage or process mismatch.')),
    'bleach_mask_dye':('mask_and_image_dye',('masking_coupler','dye'),('实验性色罩去除及伴随染料损伤，与漂白成像银分开。','Experimental mask removal and associated dye damage, separate from silver bleach.')),
    'remove_auxiliary':('auxiliary_layer',('auxiliary',),('去除附加层；目前是统一附加层池，不是完整去碳刷洗模型。','Remove the auxiliary pool; not a full remjet scrubbing model.')),
}

def bath_domain_trace():
    return {action:{'domain':value[0],'targets':list(value[1])} for action,value in BATH_DOMAINS.items()}
