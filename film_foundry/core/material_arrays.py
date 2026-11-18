"""Immutable float32 storage for independent material states and bodies.

Read-only flags alone do not prove ownership: a mutable base may still change.
Bytes-backed arrays can be shared safely; all other inputs are detached once.
Range checks may reuse scalar results for immutable storage only. This cache
holds weak references, never pixel copies or mutable ownership tokens.
"""
from collections import OrderedDict
from threading import RLock
import weakref
import numpy as np

_bounds=OrderedDict()
_bounds_lock=RLock()
_BOUNDS_LIMIT=512


def _immutable_storage(array):
    base=array
    while isinstance(base,(np.ndarray,memoryview)):
        base=base.base if isinstance(base,np.ndarray) else base.obj
    return not array.flags.writeable and isinstance(base,bytes)


def array_bounds(value):
    """Exact extrema; reuse only the same immutable array view, not equal values.

    A read-only view of caller-owned mutable memory is deliberately uncached.
    View geometry/dtype are checked as well: NumPy permits metadata changes
    even when the underlying bytes are immutable. No content hashes are used.
    """
    array=np.asarray(value)
    from .execution_topology import reference_execution_enabled
    cacheable=not reference_execution_enabled() and _immutable_storage(array)
    if not cacheable:return float(np.min(array)),float(np.max(array))
    key=id(array)
    signature=(array.dtype.str,array.shape,array.strides,array.__array_interface__['data'][0])
    with _bounds_lock:
        record=_bounds.get(key)
        if record is not None and record[0]() is array and record[1]==signature:
            _bounds.move_to_end(key)
            return record[2]
    result=float(np.min(array)),float(np.max(array))
    def discard(ref):
        with _bounds_lock:
            previous=_bounds.get(key)
            if previous is not None and previous[0] is ref:_bounds.pop(key,None)
    with _bounds_lock:
        _bounds[key]=(weakref.ref(array,discard),signature,result)
        if len(_bounds)>_BOUNDS_LIMIT:_bounds.popitem(last=False)
    return result


def freeze_array(value):
    array=np.asarray(value,dtype=np.float32)
    if _immutable_storage(array):return array
    return np.frombuffer(array.tobytes(order='C'),dtype=np.float32).reshape(array.shape)
