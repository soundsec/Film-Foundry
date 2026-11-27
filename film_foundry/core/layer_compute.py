"""Bounded pointwise device maps for exposure and material layers."""
import os
from contextlib import ExitStack

import numpy as np

from . import desktop_compute as compute
from .computation_control import checkpoint
from .execution_topology import reference_execution_enabled
from .gpu_kernels import source


MIN_PIXELS = 131072
TILE_PIXELS = 262144
_kernels = {}


def eligible(image, switch, input_channels=3):
    return (image.ndim == 3 and image.shape[-1] == input_channels and image.dtype == np.float32
            and image.size // input_channels >= MIN_PIXELS and not reference_execution_enabled()
            and os.environ.get(switch) != '0' and compute.selected_backend() != 'cpu')


def _tiles(height, width, limit):
    cols = min(width, limit)
    rows = max(1, limit // cols)
    for y in range(0, height, rows):
        for x in range(0, width, cols):
            yield slice(y, min(y + rows, height)), slice(x, min(x + cols, width))


def _cuda(table, capacity, channels, name, input_channels=3):
    cp, _, stream, pool = compute._get_runtime()
    if name not in _kernels:
        _kernels[name] = cp.RawKernel(source(name, 'cuda'), name, options=('--fmad=false',))
    kernel = _kernels[name]
    with stream, cp.cuda.using_allocator(pool.malloc):
        basis = cp.asarray(table)
        src = cp.empty((capacity, input_channels), cp.float32)
        dst = cp.empty((capacity, channels), cp.float32)

    def dispatch(tile, out):
        count = tile.size // input_channels
        src[:count].set(tile.reshape(count, input_channels), stream=stream)
        kernel(((count + 255) // 256,), (256,),
               (src, basis, np.int32(len(table)), np.int32(channels), np.int32(count), dst), stream=stream)
        dst[:count].get(out=out.reshape(count, channels), stream=stream, blocking=True)
    return dispatch


def _opencl(runtime, table, capacity, channels, resources, name, input_channels=3):
    cl, context, queue, kernels, _ = runtime
    if name not in kernels:
        kernels[name] = cl.Kernel(cl.Program(context, source(name, 'opencl')).build(), name)
    flags = cl.mem_flags
    src = cl.Buffer(context, flags.READ_ONLY, size=capacity * input_channels * 4)
    resources.callback(src.release)
    basis = cl.Buffer(context, flags.READ_ONLY | flags.COPY_HOST_PTR, hostbuf=table)
    resources.callback(basis.release)
    dst = cl.Buffer(context, flags.WRITE_ONLY, size=capacity * channels * 4)
    resources.callback(dst.release)

    def dispatch(tile, out):
        count = tile.size // input_channels
        cl.enqueue_copy(queue, src, tile, is_blocking=False)
        kernels[name](queue, (count,), None, src, basis, np.int32(len(table)),
                      np.int32(channels), np.int32(count), dst)
        cl.enqueue_copy(queue, out, dst, is_blocking=True)
    return dispatch


def run(image, rows, channels, name, counts):
    """Dispatch an eligible scalar or RGB map and return its complete host result."""
    backend = compute.selected_backend()
    if backend == 'cpu' or not np.isfinite(rows).all():
        return None
    limit = TILE_PIXELS
    if backend == 'opencl':
        from . import opencl_compute as device
        if not device.available():
            return None
        limit = min(limit, device._runtime[-1])
    else:
        device = compute
    height, width = image.shape[:2]
    capacity = min(height * width, limit)
    with device._lock, ExitStack() as resources:
        if device._failure is not None:
            return None
        checkpoint()
        try:
            dispatch = (_cuda(rows, capacity, channels, name, image.shape[-1]) if backend == 'cuda' else
                        _opencl(device._runtime, rows, capacity, channels, resources, name, image.shape[-1]))
        except Exception as exc:
            device.reject(f'{type(exc).__name__}: {exc}')
            return None
        result = np.empty((height, width, channels), np.float32)
        for region in _tiles(height, width, limit):
            checkpoint()
            tile = np.ascontiguousarray(image[region])
            # fmax would swallow a source NaN. Defer exceptional data to NumPy.
            if not np.isfinite(tile).all():
                return None
            try:
                dispatch(tile, result[region])
            except Exception as exc:
                device.reject(f'{type(exc).__name__}: {exc}')
                return None
            if not np.isfinite(result[region]).all():
                return None
        counts[backend] += 1
    return result
