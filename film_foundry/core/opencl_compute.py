"""Optional cross-vendor GPU backend, with crash-isolated driver qualification.

Only GPU devices are selected. An unavailable FP64 unit disables spectral work,
not RGB work. No driver paths, registry edits, GUI objects or material state.
"""
import json
import subprocess
import sys
import threading
import tempfile
import warnings
from pathlib import Path

import numpy as np

from .gpu_kernels import source

_lock = threading.RLock()
_runtime = None
_failure = None
_device = None
_probe_failures = []
_calls = {'dye': 0, 'spectral': 0, 'paper_response':0, 'rotary_contact':0, 'edge_contact':0, 'coordinate_noise':0}


def status():
    with _lock:
        return dict(initialized=_runtime is not None, failure=_failure,
                    device=_device, skipped_platforms=list(_probe_failures), **_calls)


def device_state():
    """Read the last published device without waiting for a tile or probing."""
    device = _device
    return (device['name'] if device else None), _failure


def reject(reason):
    global _failure
    with _lock:
        _failure = str(reason)
        warnings.warn('OpenCL disabled; using CPU: ' + _failure, RuntimeWarning, stacklevel=2)


def _create_runtime(platform_index, identity=None, *, report=lambda stage: None):
    report('import PyOpenCL')
    import pyopencl as cl
    candidates = []
    report('enumerate OpenCL platforms')
    platform = cl.get_platforms()[platform_index]
    report(f'read name of platform {platform_index}')
    name = platform.name
    report('enumerate GPU devices on ' + name)
    devices = platform.get_devices(device_type=cl.device_type.GPU)
    for index, device in enumerate(devices):
        report(f'read GPU {index} properties on {name}')
        if not device.available or not device.compiler_available:
            continue
        info = dict(platform_index=platform_index, platform=name, vendor=device.vendor, name=device.name,
                    driver=device.driver_version, fp64='cl_khr_fp64' in device.extensions.split(),
                    compute_units=device.max_compute_units)
        try:
            info['discrete'] = not device.host_unified_memory
        except cl.LogicError:
            info['discrete'] = False
        if identity is None or info == identity:
            candidates.append((device, info))
    if not candidates:
        raise RuntimeError('No available OpenCL GPU/compiler matching qualification')
    device, info = max(candidates, key=lambda item: _device_rank(item[1]))
    report('create GPU context for ' + info['name'])
    context = cl.Context([device])
    queue = cl.CommandQueue(context)
    kernels = {}
    for name in ('dye', 'paper_response', 'spectral') if info['fp64'] else ('dye','paper_response'):
        report('build ' + name + ' on ' + info['name'])
        program = cl.Program(context, source(name, 'opencl')).build()
        kernels[name] = cl.Kernel(program, name)
    # Bound each tile by driver allocation limits and a fraction of device RAM.
    tile_pixels = min(262144, int(device.max_mem_alloc_size)//72, int(device.global_mem_size)//384)
    if tile_pixels < 256:
        raise RuntimeError('Insufficient OpenCL device memory')
    return (cl, context, queue, kernels, tile_pixels), info


def _execute(runtime, name, values, tables, parameters, overlay=None):
    cl, context, queue, kernels, tile_pixels = runtime
    height, width = values.shape[:2]
    result = np.empty((height, width, 3), np.float32)
    flat = result.reshape(-1, 3)
    # Row tiles avoid an entire extra host copy for strided input arrays.
    rows = max(1, tile_pixels // width)
    buffers = []
    try:
        for table in tables:
            buffers.append(cl.Buffer(context, cl.mem_flags.READ_ONLY | cl.mem_flags.COPY_HOST_PTR,
                                     hostbuf=np.ascontiguousarray(table)))
        for start in range(0, height, rows):
            tile = np.ascontiguousarray(values[start:start+rows], dtype=np.float32)
            count = tile.size // values.shape[-1]
            src = cl.Buffer(context, cl.mem_flags.READ_ONLY | cl.mem_flags.COPY_HOST_PTR, hostbuf=tile)
            dst = cl.Buffer(context, cl.mem_flags.WRITE_ONLY, size=count*12)
            extra = None
            try:
                if name == 'spectral':
                    extra_data = (np.zeros(1, np.float32) if overlay is None else
                                  np.ascontiguousarray(overlay[start:start+rows], dtype=np.float32))
                    extra = cl.Buffer(context, cl.mem_flags.READ_ONLY | cl.mem_flags.COPY_HOST_PTR, hostbuf=extra_data)
                    args = (src, buffers[0], buffers[1], extra, buffers[2], *parameters, np.int32(count), dst)
                else:
                    args = (src, *buffers, *parameters, np.int32(count), dst)
                kernels[name](queue, (count,), None, *args)
                cl.enqueue_copy(queue, flat[start*width:start*width+count], dst, is_blocking=True)
            finally:
                queue.finish()
                src.release()
                dst.release()
                if extra is not None:
                    extra.release()
    finally:
        queue.finish()
        for buffer in buffers:
            buffer.release()
    if not np.isfinite(result).all():
        raise ValueError('Non-finite OpenCL output')
    return result


def _run_probe(target):
    frozen = getattr(sys, 'frozen', False)
    command = [sys.executable]
    if not frozen:
        command.extend(('-m', 'film_foundry.core.opencl_compute'))
    # A windowed executable has no Python stdout. Flush each stage to a private
    # file so a native access violation still leaves a useful diagnostic.
    with tempfile.TemporaryDirectory(prefix='film-opencl-') as directory:
        journal = Path(directory) / 'result'
        journal.touch()
        try:
            child = subprocess.run([*command, '--opencl-probe', str(journal), str(target)],
                                   capture_output=True, timeout=30,
                                   cwd=None if frozen else Path(__file__).resolve().parents[2],
                                   creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
            error = f'0x{child.returncode & 0xffffffff:08X}' if child.returncode else None
        except subprocess.TimeoutExpired:
            error = 'TimeoutExpired (30 s)'
        lines = journal.read_text(encoding='utf-8').splitlines()
    if error or not lines or not lines[-1].startswith('FF_OPENCL:'):
        detail = lines[-1] if lines else 'start isolated probe'
        raise RuntimeError(f'OpenCL probe failed ({error or "no result"}): {detail}')
    return json.loads(lines[-1].partition(':')[2])


def _device_rank(info):
    return info['fp64'], info['discrete'], info['compute_units']


def _qualify():
    """Probe each ICD separately; one defective platform cannot veto the others."""
    count = _run_probe('count')
    devices = []
    _probe_failures.clear()
    for index in range(count):
        try:
            device = _run_probe(index)
        except RuntimeError as exc:
            _probe_failures.append(f'platform {index}: {exc}')
            continue
        if device['fp64'] and device['discrete']:
            return device
        devices.append(device)
    if not devices:
        raise RuntimeError(' / '.join(_probe_failures) or 'No OpenCL platforms')
    return max(devices, key=_device_rank)


def available():
    global _runtime, _device
    with _lock:
        if _failure is not None:
            return False
        if _runtime is None:
            try:
                identity = _qualify()
                _runtime, _device = _create_runtime(identity['platform_index'], identity)
            except Exception as exc:
                reject(f'{type(exc).__name__}: {exc}')
        return _runtime is not None and _failure is None


def run(name, values, tables, parameters, overlay=None):
    with _lock:
        if not available() or name not in _runtime[3]:
            return None
        try:
            result = _execute(_runtime, name, values, tables, parameters, overlay)
            _calls[name] += 1
            return result
        except Exception as exc:
            reject(f'{type(exc).__name__}: {exc}')
            return None


def run_rotary(shape,start,stop,plan,parameters,*,name='rotary_contact'):
    with _lock:
        if not available() or (name!='edge_contact_f32' and not _device['fp64']):return None
        cl,context,queue,kernels,tile_pixels=_runtime
        table=target=None
        try:
            if name not in kernels:
                program=cl.Program(context,source(name,'opencl')).build()
                kernels[name]=cl.Kernel(program,name)
            height,width=shape;result=np.empty((stop-start,width),np.float32)
            table=cl.Buffer(context,cl.mem_flags.READ_ONLY|cl.mem_flags.COPY_HOST_PTR,hostbuf=plan)
            rows=max(1,tile_pixels//width)
            for row in range(start,stop,rows):
                end=min(row+rows,stop);count=(end-row)*width
                target=cl.Buffer(context,cl.mem_flags.WRITE_ONLY,size=count*4)
                kernels[name](queue,(count,),None,table,np.int32(len(plan)),
                    np.int32(height),np.int32(width),np.int32(row),np.int32(count),*parameters,target)
                cl.enqueue_copy(queue,result[row-start:end-start],target,is_blocking=True)
                target.release();target=None
            if not np.isfinite(result).all():raise ValueError('Non-finite rotary contact GPU result')
            _calls[name]=_calls.get(name,0)+1
            return result
        except Exception as exc:
            reject(f'{type(exc).__name__}: {exc}')
            return None
        finally:
            try:
                queue.finish()
                if table is not None:table.release()
                if target is not None:target.release()
            except Exception:
                pass


def run_coordinate_noise(start,stop,width,height):
    with _lock:
        if not available():return None
        cl,context,queue,kernels,tile_pixels=_runtime;target=None
        try:
            name='coordinate_noise'
            if name not in kernels:
                program=cl.Program(context,source(name,'opencl')).build()
                kernels[name]=cl.Kernel(program,name)
            result=np.empty((stop-start,width,3),np.float32);rows=max(1,tile_pixels//width)
            for row in range(start,stop,rows):
                end=min(stop,row+rows);count=(end-row)*width
                target=cl.Buffer(context,cl.mem_flags.WRITE_ONLY,size=count*12)
                kernels[name](queue,(count,),None,np.int32(row),np.int32(width),np.int32(height),np.int32(count),target)
                cl.enqueue_copy(queue,result[row-start:end-start],target,is_blocking=True)
                target.release();target=None
            _calls[name]+=1;return result
        except Exception as exc:
            reject(f'{type(exc).__name__}: {exc}');return None
        finally:
            try:queue.finish()
            except Exception:pass
            if target is not None:
                try:target.release()
                except Exception:pass


def _probe(target, report):
    # Only the short-lived qualification process handles native loader faults.
    # Leave the desktop process and machine-wide Windows reporting settings alone.
    if sys.platform == 'win32':
        import ctypes
        kernel = ctypes.windll.kernel32
        kernel.SetErrorMode(kernel.GetErrorMode() | 0x0001 | 0x0002)
        kernel.WerSetFlags(0x0020)  # WER_FAULT_REPORTING_NO_UI
    if target == 'count':
        report('import PyOpenCL')
        import pyopencl as cl
        report('enumerate OpenCL platforms')
        return len(cl.get_platforms())
    runtime, info = _create_runtime(int(target), report=report)
    report('execute qualification kernels on ' + info['name'])
    values = np.array([[[.2, .5, 1.], [2., 0., .3]]], np.float32)
    identity = np.eye(3, dtype=np.float32)
    actual = _execute(runtime, 'dye', values,
                      [identity, np.zeros(3, np.float32), identity, identity, np.ones(3, np.float32)],
                      [np.float32(0), np.int32(1)])
    np.testing.assert_array_equal(actual, values)
    actual=_execute(runtime,'paper_response',np.full((1,2,3),.18,np.float32),
                    [np.array([3,1,np.log(.18),2.2,0],np.float32)],[])
    np.testing.assert_allclose(actual,.5,rtol=2e-6,atol=2e-6)
    if info['fp64']:
        values = np.full((1, 2, 9), .125, np.float32)
        absorbance = np.full((9, 2), .25, np.float64)
        weights = np.full((3, 2), .5, np.float64)
        basis = np.array([[1., 0., 0.], [0., 1., 0.]])
        overlay = np.full((1, 2, 3), -.1, np.float32)
        actual = _execute(runtime, 'spectral', values, [absorbance, weights, basis],
                          [np.int32(2), np.int32(1)], overlay)
        np.testing.assert_allclose(actual, 10.**(-(.125*.25*9+float(overlay[0, 0, 0]))), rtol=1e-7, atol=0)
    return info


def probe_main(path, target):
    with Path(path).open('w', encoding='utf-8') as output:
        def report(message):
            print(message, file=output, flush=True)
        try:
            result = _probe(target, report)
        except Exception as exc:
            report(f'{type(exc).__name__}: {exc}')
            return 1
        report('FF_OPENCL:' + json.dumps(result))
        return 0


if __name__ == '__main__':
    if len(sys.argv) == 4 and sys.argv[1] == '--opencl-probe':
        raise SystemExit(probe_main(sys.argv[2], sys.argv[3]))
    raise SystemExit('Use run_compute_desktop --compute opencl --diagnose')
