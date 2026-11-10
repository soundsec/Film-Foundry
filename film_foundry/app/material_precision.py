"""Opt-out mixed-precision storage policy for experimental materials.

Arithmetic and chemical inventories remain float32. Only selected independent
appearance fields are stored as IEEE float16; NumPy/ZIP remain the file formats.
"""
POLICIES=('auto','full','float32','appearance_float16_v1')
# Deliberately excludes coating, contact, coverage, chemistry and density fields:
# they participate in inventories, thresholds or subsequent exposure.
APPEARANCE_FIELDS={
    'peel_apart_transfer_v1':frozenset(('paper_texture','backing_texture','paper')),
    'integral_instant_v1':frozenset(('paper_texture','backing_texture','paper')),
    'daguerreotype_surface_v1':frozenset(('polish_x','polish_y','microtexture')),
    'photographic_paper_v1':frozenset(('paper',)),
    'wet_collodion_v1':frozenset(('surface',)),
    'cyanotype_v1':frozenset(('paper',)),
    'platinum_palladium_v1':frozenset(('paper',)),
}


def storage_fields(fields,family,precision='auto'):
    if precision not in POLICIES:raise ValueError('Unsupported material storage precision')
    from film_foundry.core.storage_precision import pack_fields
    result,changed=pack_fields(fields,half_fields=APPEARANCE_FIELDS.get(family,()),
        policy='full' if precision in ('float32','full') else 'auto')
    return result,sorted(changed)
