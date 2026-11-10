"""Resident CUDA multilayer grain; original CPU RNG and CPU fallback.

Filtering, local radius blending, normalization, subpixel translation and
channel mixing stay on device. No reduced scale/model or random replacement.
"""
from copy import deepcopy
import os
import cv2
import numpy as np
from . import desktop_compute as compute
from .execution_topology import reference_execution_enabled
from .random_fields import standard_normal_float32, fill_standard_normal_float32

_calls = 0
_shift_kernel = None
_SHIFT = r'''
__device__ int reflect_grain(int x,int n) {
 if(n==1)return 0;int period=2*n-2;x=x%period;
 if(x<0)x+=period;return x<n?x:period-x;
}
extern "C" __global__ void grain_shift(const float* src,float* dst,int w,int h,
 int tx,int ty,float mix,int channel,int add) {
 int i=blockIdx.x*blockDim.x+threadIdx.x;if(i>=w*h)return;
 int xx=(i%w)*32+tx,yy=(i/w)*32+ty;
 int x=xx>>5,y=yy>>5;float fx=(xx&31)/32.f,fy=(yy&31)/32.f;
 int x0=reflect_grain(x,w),x1=reflect_grain(x+1,w);
 int y0=reflect_grain(y,h),y1=reflect_grain(y+1,h);
 float value=src[y0*w+x0]*((1.f-fy)*(1.f-fx));
 value+=src[y0*w+x1]*((1.f-fy)*fx);
 value+=src[y1*w+x0]*(fy*(1.f-fx));
 value+=src[y1*w+x1]*(fy*fx);
 value*=mix;
 if(add)dst[i*3+channel]+=value;else dst[i*3+channel]=value;
}
'''


def status():
    return _calls


def _normalize(cp, field):
    field -= cp.mean(field, dtype=cp.float32)
    deviation = cp.sqrt(cp.mean(cp.square(field), dtype=cp.float32))
    field /= cp.maximum(deviation, cp.float32(1e-6))
    return field


def _blur(cp, field, sigma, kernel):
    h, w = field.shape
    size = int(round(sigma*8+1)) | 1
    weights = cp.asarray(cv2.getGaussianKernel(size, sigma, cv2.CV_32F).ravel(), blocking=True)
    temporary = cp.empty_like(field); result = cp.empty_like(field)
    args = (weights, np.int32(size//2), np.int32(w), np.int32(h))
    grid = ((field.size+255)//256,)
    kernel(grid, (256,), (field, temporary, *args, np.int32(0)))
    kernel(grid, (256,), (temporary, result, *args, np.int32(1)))
    return result


def _local(cp, field, sigma, plan, kernel):
    if plan is None:
        return _blur(cp, field, sigma, kernel)
    result = None
    for index, anchor in enumerate(plan.anchors):
        sample = _normalize(cp, _blur(cp, field, sigma*anchor, kernel))
        if plan.weights:
            sample *= cp.asarray(np.ascontiguousarray(plan.weights[index]), blocking=True)
        if result is None: result = sample
        else: result += sample
    return result


def _translation(value):
    # OpenCV affine translation: 10-bit coordinate accumulation, 5-bit
    # interpolation table, with the same rounding bias before the shift.
    return np.int32((int(np.rint(-float(np.float32(value))*1024.))+16)>>5)


def _run(cp, rng, shape, sigma, shared_mix, plans, kernel):
    global _shift_kernel
    if _shift_kernel is None:
        _shift_kernel = cp.RawKernel(_SHIFT, 'grain_shift', options=('--fmad=false',))
    h, w = shape; count = h*w
    max_shift = float(np.clip(sigma*.42, .35, 2.25))
    layer_mix = float(np.sqrt(max(1.-shared_mix*shared_mix, 0.)))
    host = standard_normal_float32(rng, shape)
    shared = _normalize(cp, _local(cp, cp.asarray(host, blocking=True), sigma, plans[0], kernel))
    mixed = cp.empty((*shape, 3), cp.float32)
    grid = ((count+255)//256,)
    for channel in range(3):
        dx, dy = rng.uniform(-max_shift, max_shift, 2)
        _shift_kernel(grid, (256,), (shared, mixed, np.int32(w), np.int32(h),
                      _translation(dx), _translation(dy), np.float32(shared_mix), np.int32(channel), np.int32(0)))
        fill_standard_normal_float32(rng, host)
        layer = _normalize(cp, _local(cp, cp.asarray(host, blocking=True), sigma, plans[channel+1], kernel))
        dx, dy = rng.uniform(-max_shift, max_shift, 2)
        _shift_kernel(grid, (256,), (layer, mixed, np.int32(w), np.int32(h),
                      _translation(dx), _translation(dy), np.float32(layer_mix), np.int32(channel), np.int32(1)))
    return _normalize(cp, mixed)


def try_multilayer(rng, shape, sigma, shared_mix, out, radius_response, radius_plans):
    global _calls
    if (reference_execution_enabled() or not isinstance(rng, np.random.Generator)
            or shape[0]*shape[1] < 1048576
            or os.environ.get('FILM_FOUNDRY_GRAIN_RESIDENT') == '0'
            or compute.selected_backend() != 'cuda'
            or (radius_response is not None and radius_plans is None)):
        return None
    if out is not None and (out.shape != (*shape,3) or out.dtype != np.float32
                           or not out.flags.c_contiguous or not out.flags.writeable):
        return None
    plans = radius_plans if radius_response is not None else (None,)*4
    if len(plans) != 4 or any(not .01 <= sigma*anchor <= 96.
                             for plan in plans for anchor in (plan.anchors if plan else (1.,))):
        return None
    with compute._lock:
        if compute._failure is not None: return None
        state = deepcopy(rng.bit_generator.state)
        device_result = None
        try:
            cp, _, stream, pool = compute._get_runtime()
            # Resident fields require memory; do not evict other applications
            # or lower resolution when the available budget is insufficient.
            if cp.cuda.runtime.memGetInfo()[0] < shape[0]*shape[1]*4*14 + 64*1024*1024:
                return None
            if compute._blur_kernel is None:
                compute._blur_kernel = cp.RawKernel(compute._BLUR_SOURCE, 'grain_blur', options=('--fmad=false',))
            with stream, cp.cuda.using_allocator(pool.malloc):
                device_result = _run(cp, rng, shape, sigma, float(np.clip(shared_mix,0.,1.)), plans, compute._blur_kernel)
                result = device_result.get(stream=stream, blocking=True)
            if not np.isfinite(result).all(): raise ValueError('Non-finite resident grain result')
            if out is not None:
                np.copyto(out, result); result = out
            _calls += 1
            return result
        except Exception as exc:
            rng.bit_generator.state = state
            compute.reject(f'{type(exc).__name__}: {exc}')
            return None
        finally:
            if compute._runtime is not None:
                # A failed stream must not mask the exception already handled
                # above or prevent the caller from running its CPU fallback.
                try:
                    compute._runtime[2].synchronize()
                except Exception as exc:
                    if compute._failure is None:
                        compute.reject(f'{type(exc).__name__}: {exc}')
                device_result = None
                try:
                    compute._runtime[3].free_all_blocks()
                except Exception as exc:
                    if compute._failure is None:
                        compute.reject(f'{type(exc).__name__}: {exc}')
