"""Intensification supply intent compiles to the same finite charging step."""
import math

COUPLINGS=('maximum_added_silver','density_and_contrast','existing_image_centres',
           'background_nucleation','washout_residue','negative_positive_viewing')


def silver_supply_parameters(*,strength=.25,enabled=True):
    if type(enabled) is not bool or isinstance(strength,bool) or not math.isfinite(strength) or not 0<=strength<=1:
        raise ValueError('Invalid silver supply intent')
    return {'silver_load':float(strength) if enabled else 0.}
