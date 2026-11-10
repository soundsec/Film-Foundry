"""Reduced layer ownership: two supports and removal, independent of optics.

Fractions partition one frozen source layer. A layer's original chemical amount
is provenance; live amounts are source * ownership. Removal never recreates it.
"""
import numpy as np


def validate(first,second):
    if first.ndim!=2 or first.shape!=second.shape:
        raise ValueError('Invalid layer ownership geometry')
    if np.any(first<0) or np.any(second<0) or np.any(first+second>1.000002):
        raise ValueError('Layer ownership exceeds the original layer')


def retained(source,fraction):
    return source*fraction[...,None] if source.ndim==3 else source*fraction


def remove(ownership,fraction):
    return ownership*(1-fraction)


def removed(first,second):
    return np.maximum(1-first-second,0)
