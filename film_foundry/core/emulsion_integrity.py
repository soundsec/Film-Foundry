"""Film emulsion loss. Coverage is area ownership, not reduced density.

All imaging layers leave together; the support and back coating stay. The
ordered pool executor owns chemistry and shear; this module observes ownership.
"""
import math
import numpy as np
from film_foundry.contracts.emulsion_integrity import EMULSION_COMPONENTS,OWNED_POOLS


def adhesion_threshold(fields,material,hardening,wet_seconds,hydration_s):
    hydrated = -math.expm1(-wet_seconds/hydration_s)
    threshold = material['adhesion']*(1+1.5*hardening)*(1-material['wet_weakening']*hydrated)
    threshold = threshold*(1-material['damage_weakening']*fields['emulsion_damage'])
    return threshold


def wear(fields, material, event, contact, hardening, wet_seconds, hydration_s):
    coverage = fields['emulsion_coverage']
    threshold=adhesion_threshold(fields,material,hardening,wet_seconds,hydration_s)
    stress = np.maximum(event['pressure']*np.clip(contact, 0., 1.)-threshold, 0.)
    loss = -np.expm1(-stress*material['wear_rate_s']*event['duration_s'])
    removed = coverage*loss
    coverage -= removed
    # Remove existing deposits with the substrate they were attached to.
    # Later events can contaminate the newly bare front surface again.
    displaced = {}
    for key, owned_key in OWNED_POOLS.items():
        if key not in fields:
            continue
        amount = fields[owned_key]*loss
        fields[owned_key] -= amount
        fields[key] -= amount
        displaced[key] = float(np.mean(amount, dtype=np.float64))
        if key == 'backing_front_mg_m2':
            fields['backing_removed_mg_m2'] += amount
    return {'removed_area_fraction': float(np.mean(removed, dtype=np.float64)), 'carried_deposits_mg_m2': displaced}


def update_deposit_ownership(fields, before, *, carbon_handled=False):
    """New deposition is spread over bare and coated areas; removal is pro rata.

Carbon washing explicitly handles its simultaneous removal/redeposition, so
its net change must not be mistaken for a single addition or subtraction.
"""
    for key, old in before.items():
        if key=='backing_front_mg_m2' and carbon_handled:continue
        current=fields[key];owned=fields[OWNED_POOLS[key]]
        removal=np.clip(np.divide(current,old,out=np.ones_like(old),where=old>0),0.,1.)
        owned*=removal
        owned+=np.maximum(current-old,0.)*fields['emulsion_coverage']
        np.minimum(owned,current,out=owned)


def mix_density(intact, support, coverage):
    """Subpixel holes mix transmission. Exact endpoints avoid log round trips."""
    support = np.broadcast_to(np.asarray(support, np.float32), intact.shape)
    from .execution_topology import reference_execution_enabled
    if not reference_execution_enabled():
        # Intact pixels need no optical calculation; bare pixels are exactly
        # the support. Refine only fractional coverage at damaged boundaries.
        partial = (coverage != 0.) & (coverage != 1.)
        if np.count_nonzero(partial) < coverage.size // 2:
            result = np.array(intact, dtype=np.float32, copy=True)
            bare = coverage == 0.
            result[bare] = support[bare]
            if np.any(partial):
                base = support[partial]
                c = coverage[partial, None]
                relative = np.maximum(intact[partial] - base, 0.)
                transmission = (1-c)+c*np.power(10., -relative)
                result[partial] = base-np.log10(np.maximum(transmission, np.float32(1e-35)))
            return result
    coverage = coverage[..., None]
    relative = np.maximum(intact-support, 0.)
    transmission = (1-coverage)+coverage*np.power(10., -relative)
    result = support-np.log10(np.maximum(transmission, np.float32(1e-35)))
    return np.where(coverage == 1., intact, np.where(coverage == 0., support, result)).astype(np.float32, copy=False)


def form_optical_ownership(config, fields, audit, optical, grain_density,
                           spectral_components, spectral_overlay, process_contract, *, retain_layers,coverage_override=None):
    """One row pass over formed sources before the final surface is added.

Pool totals in the preceding process trace describe completed chemical baths.
Spectral sources stay frozen: actual emulsion amount = source * coverage.
"""
    coverage = fields.get('emulsion_coverage') if coverage_override is None else coverage_override
    if coverage is None or np.min(coverage) == 1.:
        return None, None
    from film_foundry.core.process_operations import sample_control_rows
    from film_foundry.core.film_process.integration import _base_and_mask_density
    from film_foundry.core.derived_cache import pseudoinverse_3x3
    from film_foundry.core.media import is_monochrome_material
    support, _ = _base_and_mask_density(config.film)
    auxiliary = process_contract['pool_totals']['auxiliary_remaining']
    support = np.asarray(support, np.float32)+np.asarray(config.film.auxiliary_layer_density_rgb, np.float32)*auxiliary
    h,w = optical.shape[:2]
    sampled_coverage = (coverage_override if coverage_override is not None else np.empty((h,w), np.float32)) if spectral_components is not None else None
    inverse = pseudoinverse_3x3(config.film.dye_absorption_matrix) if retain_layers else None
    ordered=audit.get('primary_emulsion_model')=='ordered_pool_removal_v1'
    removed = np.zeros(9, np.float64)
    for start in range(0,h,max(1,500000//w)):
        stop = min(h,start+max(1,500000//w))
        c = sample_control_rows(coverage,(h,w),start,stop) if coverage_override is None else coverage_override[start:stop]
        original = optical[start:stop]
        changed = mix_density(original,support,c)
        if retain_layers:
            delta = changed-original
            proxy = delta.mean(axis=-1,keepdims=True) if is_monochrome_material(config) else np.einsum('...r,lr->...l',delta,inverse)
            grain_density[start:stop] += proxy
            np.maximum(grain_density[start:stop],0.,out=grain_density[start:stop])
        optical[start:stop] = changed
        if sampled_coverage is not None:
            sampled_coverage[start:stop] = c
            for index in (() if ordered else EMULSION_COMPONENTS):
                removed[index] += np.sum(spectral_components[start:stop,...,index]*(1-c),dtype=np.float64)
    audit['ownership'] = {'model':'emulsion_area_ownership_v1', 'support_density_rgb':support.tolist(),
        'chemical_pool_totals_stage':'after_ordered_processing' if ordered else 'before_terminal_mechanical_handling',
        'coverage_semantics':'actual_imaging_material_is_source_times_coverage',
        'coordinate_displacement':audit.get('deformation',{}).get('model',False)}
    if sampled_coverage is not None:
        if not ordered:audit['ownership']['removed_spectral_component_sums'] = removed.tolist()
        audit['ownership']['spectral_observation_execution'] = 'cpu_bounded_rows'
    if ordered:audit['ownership']['removed_primary_pools']={key.removeprefix('emulsion_removed_'):value for key,value in process_contract['pool_totals'].items() if key.startswith('emulsion_removed_')}
    if coverage_override is not None:
        audit['ownership']['coverage_source']='full_resolution_primary_state'
        audit['ownership']['outside_primary_pools']={key.removeprefix('emulsion_outside_'):value for key,value in process_contract['pool_totals'].items() if key.startswith('emulsion_outside_')}
    # Existing legacy density accidents are on the emulsion before handling.
    # Subsequent explicit surface events get their own outside density overlay.
    return sampled_coverage, spectral_overlay
