"""Reduced plate bath kinetics. Normalized equivalents, not chemical recipes."""
import numpy as np
from film_foundry.core import sheet_common as s

FIELDS={'halide','removed_halide','sensitized_silver'}


def validate(fields):
    if fields['sensitivity'] is not fields['halide'] and not np.array_equal(fields['sensitivity'],fields['halide']):
        raise ValueError('Plate sensitivity must follow residual surface halide')
    if not np.allclose(fields['halide']+fields['particles']+fields['removed_halide']+fields.get('particles_removed',0),fields['sensitized_silver'],atol=2e-6,rtol=0):
        raise ValueError('Plate photosensitive surface pool is not conserved')


def fixing(state,*,seconds,activity,temperature_c,coverage):
    s.require(state,'developed','fixing','fixed');s.number(coverage,'coverage');s.number(activity,'activity',0,100)
    fraction=s.rate(seconds,state.material.fixing_seconds,activity*coverage,temperature_c)
    fields=dict(state.fields);removed=fields['halide']*fraction
    fields['halide']=fields['halide']-removed
    fields['removed_halide']=fields['removed_halide']+removed
    fields['sensitivity']=fields['halide']
    stage='fixed' if np.max(fields['halide'])<=1e-4 else 'fixing'
    return s.change(state,'fix',fields,stage,seconds,activity=activity,temperature_c=temperature_c,coverage=coverage)


def gilding(state,*,amount,seconds,activity,temperature_c,coverage):
    s.require(state,'fixed','gilding','gilded');s.number(amount,'amount');s.number(coverage,'coverage');s.number(activity,'activity',0,100)
    fraction=s.rate(seconds,state.material.gilding_seconds,activity*coverage,temperature_c)
    fields=dict(state.fields)
    # Gold binds preferentially around image particles. Amount is a bath target,
    # not newly injected gold on every call; repeated processing cannot erase it.
    target=amount*(.25+.75*fields['particles'])
    gap=np.maximum(target-fields['gold'],0)
    fields['gold']=fields['gold']+gap*fraction
    stage='gilded' if np.max(np.maximum(target-fields['gold'],0))<=1e-4 else 'gilding'
    return s.change(state,'gild',fields,stage,seconds,amount=amount,activity=activity,temperature_c=temperature_c,coverage=coverage)
