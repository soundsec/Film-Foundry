"""One effect-intent adapter; execution still uses the normal environment step."""
import math


def warm_shift_parameters(*,strength=0.,enabled=True,light_protection=1.,ambient_light=1.):
    if type(enabled) is not bool or not math.isfinite(strength) or not 0<=strength<=1:
        raise ValueError('Invalid warm-shift intent')
    # Linear normalized thermal imbalance. RGB and total developed density are
    # not linear: temperature also accelerates transfer and changes finite yield.
    return {'temperature_c':20.+20.*strength if enabled else 20.,
            'light_protection':light_protection,'ambient_light':ambient_light,'solar_warming':0.}


WARM_SHIFT_COUPLINGS=('reaction_speed','channel_transfer_loss','reaction_stain')


def reagent_shortage_parameters(*,strength=0.,enabled=True):
    """Linear shortage of supplied paste; the normal spread step does the work."""
    if type(enabled) is not bool or not math.isfinite(strength) or not 0<=strength<=1:
        raise ValueError('Invalid reagent shortage intent')
    return {'reagent_load':1.-strength if enabled else 1.}


REAGENT_SHORTAGE_COUPLINGS=('effective_coverage','reaction_speed','maximum_image_conversion',
                          'donor_development','alkali_to_neutralizer_ratio','backing_visibility')
