"""Presentation of the common film precision profile; no frontend policy."""
from film_foundry.core.storage_precision import film_precision_profile
from film_foundry.ui.curve_editor import text


def film_storage_label(quality,policy):
    profile=film_precision_profile(quality,policy)
    if profile['policy']=='full':return text('全高精度 · FP32 原值','Full precision · original FP32 values')
    bits=profile['cold_fraction_bits']
    return text(f"随质量档 · 密度分层 {bits} 位尾数",f'Follow quality · {bits}-bit fraction in density layers')


PRECISION_TOOLTIP=(
    '保存精度：兼容密度分层低档FP16容器、7位尾数；中档FP16、10位尾数；高档FP32容器、15位尾数。独立纹理FP16，权威光学母版和反应库存FP32。全高保存保留当前FP32原值，不恢复此前损失。计算精度：贴边液面低/中用FP32，高用FP64；窄液面/极短时间窗保FP64，可在运行设置中固定FP64。扫描算法和成片编码质量独立。',
    'Storage: compatibility density layers use FP16 with 7 fraction bits in draft, FP16 with 10 in standard, FP32 with 15 in high. Independent textures use FP16; optics and chemistry retain FP32. Full storage keeps current FP32 values, not previously lost information. Computation: edge wet fronts use FP32 in draft/standard, FP64 in high; narrow fronts/short windows retain FP64. Runtime settings can force FP64. Scanner algorithms and image encoding quality remain independent.')
