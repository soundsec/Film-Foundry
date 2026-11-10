"""Editable material starting points composed entirely from shared film fields.

No runtime dispatch by design name, scanner look, extra per-pixel pass or brand
calibration. The resolved fields are frozen by the ordinary exposure snapshot.
"""
from copy import deepcopy
from dataclasses import replace
from film_foundry.model.config import FilmStockConfig
from film_foundry.contracts.spectral_exposure import make_historical_exposure_profile
from film_foundry.contracts.process_events import validate_process_response

DESIGNS = {
    'panchromatic': ('全色银像负片', 'Panchromatic silver negative'),
    'orthochromatic': ('正色银像负片', 'Orthochromatic silver negative'),
    'blue_sensitive': ('蓝敏银像负片', 'Blue-sensitive silver negative'),
    'red_weighted_visible': ('红敏倾向黑白（可见光）', 'Red-weighted B&W (visible light)'),
    'document': ('高反差文献片', 'High-contrast document film'),
    'silver_reversal': ('银像黑白反转片', 'Silver B&W reversal'),
    'chromogenic_mono': ('染料型黑白负片', 'Chromogenic monochrome negative'),
    'color_negative': ('彩色负片', 'Colour negative'),
    'color_reversal': ('彩色反转片', 'Colour reversal'),
}
TRAITS = {
    'emulsion': {'standard':('标准乳剂','Standard emulsion'), 'soft':('易膨胀乳剂','Soft emulsion'), 'hardened':('较强硬化乳剂','Hardened emulsion')},
    'grain': {'standard':('标准颗粒','Standard grain'), 'fine':('细颗粒','Fine grain'), 'coarse':('粗颗粒','Coarse grain')},
    'support': {'native':('原生片基','Native support'), 'clear':('中性透明片基','Neutral clear support'), 'blue':('蓝调透明片基','Blue-tinted support')},
    'backing': {'standard':('标准防光晕','Standard antihalation'), 'strong':('强防光晕附加层','Strong antihalation layer'), 'absent':('无防光晕附加层','No antihalation layer')},
    'age': {'fresh':('新鲜材料','Fresh material'), 'aged':('老化材料','Aged material')},
}

def compose_material(config, design, *, emulsion='standard', grain='standard',
                     support='native', backing='standard', age='fresh'):
    """Replace material only, preserving selected chemistry/observer/resources.

    A monochrome chromogenic material uses three identical sensitivity/dye
    channels in the existing colour-coupling model. It is not an output desaturate.
    Explicit non-native chemistry remains available as a deliberate cross-process.
    """
    if design not in DESIGNS:raise ValueError('Unknown material design')
    selections=dict(emulsion=emulsion,grain=grain,support=support,backing=backing,age=age)
    for axis,value in selections.items():
        if value not in TRAITS[axis]:raise ValueError(f'Unknown {axis} trait')
    result=deepcopy(config);film=FilmStockConfig(name=DESIGNS[design][1])
    mono=design not in ('color_negative','color_reversal','chromogenic_mono')
    monochrome_image=mono or design=='chromogenic_mono'
    reversal=design in ('silver_reversal','color_reversal')
    film.color_process='monochrome' if mono else 'color'
    if design=='chromogenic_mono':film.color_process='chromogenic_monochrome'
    film.medium_process='reversal' if mono and reversal else 'slide' if reversal else 'negative'
    film.image_polarity='positive' if reversal else 'negative'
    if monochrome_image:
        sensitivity=design if design in ('panchromatic','orthochromatic','blue_sensitive','red_weighted_visible') else 'panchromatic'
        film.spectral_exposure_profile=make_historical_exposure_profile(sensitivity)
        film.hd_gamma=(.66,)*3;film.density_min=(.08,)*3;film.density_max=(1.95,)*3
        film.log_exposure_toe=(-2.2,)*3;film.log_exposure_shoulder=(.48,)*3
        film.film_base_density_rgb=(.04,)*3;film.clear_support_density_rgb=(.04,)*3
        film.retained_halide_density_rgb=(.82,)*3
        film.dye_absorption_matrix=((1.,0.,0.),(0.,1.,0.),(0.,0.,1.))
        film.halation_color=(1.,)*3;film.halation_layer_return_weights=(1.,)*3
        film.granularity_sigma=(.028,)*3
        film.degradation_layer_balance=(.95,)*3;film.degradation_fog_density_rgb=(.12,)*3
    if design=='chromogenic_mono':
        # Each dye component absorbs neutrally, including its own grain field.
        film.dye_absorption_matrix=((1/3,)*3,)*3
        profile=film.spectral_exposure_profile
        film.spectral_exposure_profile=replace(profile,layer_sensitivity=profile.layer_sensitivity*3,layer_gain=profile.layer_gain*3)
    if design=='document':
        film.hd_gamma=(1.8,)*3;film.hd_toe_width=.08;film.hd_shoulder_width=.10
        film.granularity_sigma=(.012,)*3;film.emulsion_blur_radius=.0006
    if reversal:
        film.film_base_density_rgb=(.035,)*3;film.clear_support_density_rgb=(.035,)*3
    response=validate_process_response(None)
    if emulsion=='soft':response.update(initial_hardening=.1,relaxation_s=50.,yield_strain=.015)
    elif emulsion=='hardened':response.update(initial_hardening=.85,relaxation_s=18.,yield_strain=.04)
    film.process_response=validate_process_response(response)
    if grain!='standard':
        amount,radius=(.55,.65) if grain=='fine' else (1.8,1.6)
        film.granularity_sigma=tuple(v*amount for v in film.granularity_sigma)
        film.grain_density_correlation_radius*=radius
    if support!='native':
        base=(.035,)*3 if support=='clear' else (.09,.055,.025)
        # Keep a chromogenic colour mask separate from the support change.
        mask=tuple(max(0.,v-floor) for v,floor in zip(film.film_base_density_rgb,film.clear_support_density_rgb))
        film.clear_support_density_rgb=base
        film.film_base_density_rgb=tuple(a+b for a,b in zip(base,mask))
    if backing=='strong':
        film.halation_strength=.025;film.auxiliary_layer_amount=1.;film.auxiliary_layer_density_rgb=(.35,)*3
    elif backing=='absent':
        film.halation_strength=.32;film.auxiliary_layer_amount=0.;film.auxiliary_layer_density_rgb=(0.,)*3
    if age=='aged':film.material_degradation=.55
    result.film=film;result.mode='bw_negative' if mono else 'color_negative'
    result.medium='film_'+film.medium_process
    result.enable_subtractive=not mono
    return result
