"""Opt-in wet emulsion ownership, independent from support/backing layers."""
import math

MODEL = 'wet_emulsion_integrity_v1'
MODEL_V2 = 'wet_emulsion_integrity_v2'
SHEAR_RATE_DEFAULT = .15
DEFAULTS = {'model': MODEL, 'adhesion': .35, 'wet_weakening': .5,
            'damage_weakening': .7, 'wear_rate_s': .2}
RANGES = {'adhesion': (0., 2.), 'wet_weakening': (0., 1.),
          'damage_weakening': (0., 1.), 'wear_rate_s': (0., 10.)}
OWNED_POOLS = {'bound_residue_mg_m2':'emulsion_residue_mg_m2',
               'surface_silver_mg_m2':'emulsion_silver_mg_m2',
               'surface_halide_mg_m2':'emulsion_halide_mg_m2',
               'backing_front_mg_m2':'emulsion_carbon_mg_m2'}
FIELDS = ('emulsion_coverage', *OWNED_POOLS.values())
SPECTRAL_MODEL = 'emulsion_area_mixture_v1'
SPECTRAL_COVERAGE_KEY = 'spectral_emulsion_coverage'
SPECTRAL_COVERED_KEY = 'spectral_covered_density_rgb'
EMULSION_COMPONENTS = (0, 1, 2, 3, 4, 5, 7)


def validate_emulsion_integrity(raw):
    if raw is None:
        return None
    defaults=dict(DEFAULTS)
    if isinstance(raw,dict) and raw.get('model')==MODEL_V2:defaults['shear_rate_s']=SHEAR_RATE_DEFAULT
    if not isinstance(raw, dict) or set(raw)-set(defaults):
        raise ValueError('Unknown emulsion integrity parameters')
    value = {**defaults, **raw}
    if value['model'] not in (MODEL,MODEL_V2):
        raise ValueError('Unsupported emulsion integrity model')
    ranges={**RANGES,**({'shear_rate_s':(0.,10.)} if value['model']==MODEL_V2 else {})}
    for name, (low, high) in ranges.items():
        number = value[name]
        if isinstance(number, bool) or not isinstance(number, (int, float)) or not math.isfinite(number) or not low <= number <= high:
            raise ValueError(f'{name} must be finite and between {low} and {high}')
        value[name] = float(number)
    return value
