"""Fused development kinetics and conversion, without mutable material objects.

All intermediate rates, active/inactive shares and fog fractions stay on the
device. Only three fields needed by the CPU inventory/history stage return.
The caller commits nothing until the complete result is available.
"""
import os
import numpy as np

from . import desktop_compute as compute
from .execution_topology import reference_execution_enabled
from .gpu_kernels import source

_cuda_kernel = None
_calls = {'cuda': 0, 'opencl': 0}


def eligible(halide):
    return (halide.ndim == 3 and halide.dtype == np.float32
            and halide.shape[0]*halide.shape[1] >= 262144
            and not reference_execution_enabled()
            and os.environ.get('FILM_FOUNDRY_DEVELOPMENT_GPU') != '0'
            and compute.selected_backend() != 'cpu')


def status():
    return dict(_calls)


def _local_array(value, shape):
    if value is None:
        return np.ones(1, np.float32), 0
    value = np.asarray(value, dtype=np.float32)
    if value.shape == shape[:-1] or value.shape == (*shape[:-1], 1):
        return value.reshape(shape[:-1]), 1
    if value.shape == shape:
        return value, 2
    raise ValueError('Development local field must match the image or layer shape')


def try_conversion(halide, activation, rates, fog_rates, local, fog_local,
                   exponent, compensation, fog_coefficient):
    if not eligible(halide):
        return None
    local, local_mode = _local_array(local, halide.shape)
    fog_local, fog_mode = _local_array(fog_local, halide.shape)
    tables = np.concatenate((np.asarray(rates).ravel(), np.asarray(fog_rates).ravel())).astype(np.float32)
    parameters = (np.int32(halide.shape[-1]), np.int32(local_mode), np.int32(fog_mode),
                  np.float32(exponent), np.float32(compensation), np.float32(fog_coefficient))
    backend = compute.selected_backend()
    if backend == 'cpu':
        return None
    if backend == 'opencl':
        from . import opencl_compute
        lock = opencl_compute._lock
    else:
        lock = compute._lock
    with lock:
        try:
            if backend == 'opencl':
                if not opencl_compute.available():
                    return None
                result = _opencl(opencl_compute._runtime, halide, activation, local, fog_local, tables, parameters)
            else:
                if compute._failure is not None:
                    return None
                result = _cuda(halide, activation, local, fog_local, tables, parameters)
            if not np.isfinite(result).all():
                raise ValueError('Non-finite development GPU output')
            _calls[backend] += 1
            return tuple(result)
        except Exception as exc:
            if backend == 'opencl':
                opencl_compute.reject(f'{type(exc).__name__}: {exc}')
            else:
                compute.reject(f'{type(exc).__name__}: {exc}')
            return None


def _tiles(halide, activation, local, fog_local, pixel_limit):
    height, width = halide.shape[:2]
    rows = max(1, pixel_limit//width)
    for start in range(0, height, rows):
        stop = min(start+rows, height)
        yield start, stop, [np.ascontiguousarray(v if v.ndim == 1 else v[start:stop])
                            for v in (halide, activation, local, fog_local)]


def _cuda(halide, activation, local, fog_local, tables, parameters):
    global _cuda_kernel
    cp, _, stream, pool = compute._get_runtime()
    result = np.empty((3, *halide.shape), np.float32)
    device_inputs = device_output = device_tables = None
    try:
        if _cuda_kernel is None:
            _cuda_kernel = cp.RawKernel(source('development_conversion', 'cuda'),
                                        'development_conversion', options=('--fmad=false',))
        with stream, cp.cuda.using_allocator(pool.malloc):
            device_tables = cp.asarray(tables)
            for start, stop, inputs in _tiles(halide, activation, local, fog_local, 1048576):
                device_inputs = [cp.asarray(v) for v in inputs]
                count = inputs[0].size
                device_output = cp.empty((3, *inputs[0].shape), cp.float32)
                _cuda_kernel(((count+255)//256,), (256,),
                             (*device_inputs, device_tables, *parameters, np.int32(count), device_output))
                # Each plane is contiguous; avoid a host interleave/deinterleave.
                for index in range(3):
                    device_output[index].get(out=result[index, start:stop], stream=stream, blocking=True)
        return result
    finally:
        stream.synchronize()
        device_inputs = device_output = device_tables = None
        if pool.free_bytes() > 128*1024*1024:
            pool.free_all_blocks()


def _opencl(runtime, halide, activation, local, fog_local, tables, parameters):
    cl, context, queue, kernels, tile_pixels = runtime
    name = 'development_conversion'
    if name not in kernels:
        program = cl.Program(context, source(name, 'opencl')).build()
        kernels[name] = cl.Kernel(program, name)
    result = np.empty((3, *halide.shape), np.float32)
    table = None
    try:
        table = cl.Buffer(context, cl.mem_flags.READ_ONLY | cl.mem_flags.COPY_HOST_PTR, hostbuf=tables)
        for start, stop, inputs in _tiles(halide, activation, local, fog_local, tile_pixels):
            buffers = []
            output = None
            try:
                for value in inputs:
                    buffers.append(cl.Buffer(context, cl.mem_flags.READ_ONLY | cl.mem_flags.COPY_HOST_PTR, hostbuf=value))
                count = inputs[0].size
                output = cl.Buffer(context, cl.mem_flags.WRITE_ONLY, size=count*12)
                kernels[name](queue, (count,), None, *buffers, table, *parameters, np.int32(count), output)
                for index in range(3):
                    cl.enqueue_copy(queue, result[index, start:stop], output,
                                    src_offset=index*count*4, is_blocking=True)
            finally:
                queue.finish()
                for buffer in buffers:
                    buffer.release()
                if output is not None:
                    output.release()
        return result
    finally:
        queue.finish()
        if table is not None:
            table.release()
