"""Conservative optical microstructure on archived, normalized material pools.

The chemical pools remain the bulk development result. These effective local
amounts describe grain in the derived spectral medium, without inventing a
spectrum from RGB density or adding material to an absent component.
"""

import numpy as np

from film_foundry.core.silver_grain import apply_metallic_silver_grain


SPECTRAL_GRAIN_MODEL = "conservative_normalized_pool_grain_v1"
_POOL_SOFTENING = np.float32(0.05)


def apply_spectral_silver_grain(amounts, plan):
    """Reuse the coordinate field on normalized silver, in bounded row buffers.

    Unlike the legacy RGB path, the density-response proxy here is the actual
    normalized silver amount. No RGB absorption coefficient is inferred.
    """
    if plan is None:
        return False
    applied = False
    for start in range(0, amounts.shape[0], 128):
        stop = min(start + 128, amounts.shape[0])
        silver = amounts[start:stop, ..., 3]
        delta = np.zeros(silver.shape + (3,), dtype=np.float32)
        if apply_metallic_silver_grain(delta, silver, plan, row_offset=start):
            silver += delta[..., 0]
            applied = True
    return applied


def apply_spectral_emulsion_grain(amounts, layer_delta, density_range, *, independent_silver):
    """Project the existing layer-noise realization into normalized pool units.

    delta/range is a statistical amplitude conversion, not an absorption fit.
    The amount/(amount+0.05) gate leaves empty material exactly empty and
    suppresses unbounded relative changes near zero. Final global normalization
    must run after all tiles so tile boundaries cannot change the material sum.
    """
    normalized_delta = layer_delta / np.asarray(density_range, dtype=np.float32).reshape(1, 1, 3)
    for component in range(4):
        if component == 3 and independent_silver:
            continue
        pool = amounts[..., component]
        jitter = (normalized_delta[..., component] if component < 3
                  else np.mean(normalized_delta, axis=-1, dtype=np.float32))
        relative = jitter / (pool + _POOL_SOFTENING)
        np.maximum(relative, -0.90, out=relative)
        pool *= 1.0 + relative


def conserve_spectral_grain_totals(amounts, totals):
    """Restore each dye/silver frame total after positive local redistribution."""
    for component, target in enumerate(totals):
        pool = amounts[..., component]
        current = np.sum(pool, dtype=np.float64)
        if target > 0.0 and current > 0.0:
            pool *= float(target / current)
