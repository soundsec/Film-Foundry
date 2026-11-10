"""Remove actual film-family material at its scheduled bath boundary."""
from copy import copy
import numpy as np

POOLS=('halide','metallic_silver','coupler','dye','bleached_halide')


def remove_emulsion(state,target):
    target=np.asarray(target,np.float32)
    before=state.emulsion_coverage
    if before is None:
        before=np.ones(state.halide.shape[:-1],np.float32)
    if target.shape!=before.shape or not np.isfinite(target).all() or np.any(target<0) or np.any(target>before+1e-6):
        raise ValueError('Emulsion coverage may only decrease at its scheduled operation')
    if np.array_equal(target,before):return 0.
    ratio=np.divide(target,before,out=np.ones_like(target),where=before>0)
    np.clip(ratio,0.,1.,out=ratio)
    retained_fraction=ratio[...,None]
    if state.formation_history is not None:
        for value in state.formation_history.values(): value *= retained_fraction
    ledger=dict(state.emulsion_removed or {});silver=0.
    for name in POOLS:
        pool=getattr(state,name)
        if pool is None:continue
        # Keep the small survivor accurate; subtraction of almost the whole
        # pool would lose its conditional optical source near zero coverage.
        remaining=pool*retained_fraction
        removed=pool-remaining;pool[:]=remaining
        amount=float(np.sum(removed,dtype=np.float64));ledger[name]=ledger.get(name,0.)+amount
        if name in ('halide','metallic_silver','bleached_halide'):silver+=amount
    state.emulsion_coverage=target.copy();state.emulsion_removed=ledger
    # Activation is a fraction of the remaining halide, not another mass pool.
    state.developability[state.halide<=0]=0.
    return silver


def optical_sources(state):
    """Per-covered-area source amounts for existing optical adapters.

This shallow carrier is private and never published as a chemical state.
Zero-coverage pixels have no recoverable image source.
"""
    source=copy(state);coverage=state.emulsion_coverage[...,None]
    for name in POOLS:
        pool=getattr(state,name)
        if pool is not None:setattr(source,name,np.divide(pool,coverage,out=np.zeros_like(pool),where=coverage>0))
    return source
