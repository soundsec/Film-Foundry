"""Immutable float32 storage for independent material states and bodies.

Read-only flags alone do not prove ownership: a mutable base may still change.
Bytes-backed arrays can be shared safely; all other inputs are detached once.
No registry, image hashing, platform objects or mutable ownership token is used.
"""
import numpy as np


def freeze_array(value):
    array=np.asarray(value,dtype=np.float32)
    base=array
    while isinstance(base,np.ndarray):base=base.base
    while isinstance(base,memoryview):base=base.obj
    if not array.flags.writeable and isinstance(base,bytes):return array
    return np.frombuffer(array.tobytes(order='C'),dtype=np.float32).reshape(array.shape)
