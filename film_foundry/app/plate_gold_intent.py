"""Gilding intent maps to the same finite gold loading operation."""
import math

COUPLINGS=('finite_gold_supply','surface_tint','image_particle_adhesion',
           'gilding_time_and_target','unbound_gold_residue','no_sulfide_protection')


def gold_supply_parameters(*,strength=.5,enabled=True):
    if type(enabled) is not bool or isinstance(strength,bool) or not math.isfinite(strength) or not 0<=strength<=1:
        raise ValueError('Invalid gold supply intent')
    return {'gold_load':float(strength) if enabled else 0.}
