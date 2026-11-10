"""Optional desktop compute kernels. CPU is the default and always the fallback.

No material state or GUI objects enter this boundary. One serialized private
CUDA stream bounds concurrent device allocations; no global CuPy pool is changed.
"""
import os
import threading
import warnings
import sys
import numpy as np

from .gpu_kernels import source as kernel_source

_lock=threading.RLock()
_runtime=None
_failure=None
_calls=0
_blur_calls=0
_blur_kernel=None
_cuda_device=None

_SOURCE=kernel_source('dye','cuda')
_spectral_kernel=None
_spectral_calls=0
_auto_backend=None
_paper_kernel=None
_paper_calls=0
_contact_kernels={}
_edge_calls=0
_noise_kernel=None
_noise_calls=0
_rotary_calls=0



def status():
    with _lock:
        requested=os.environ.get('FILM_FOUNDRY_COMPUTE','cpu').lower()
        selected=_auto_backend if requested=='auto' else requested
        opencl=_opencl_status()
        development=sys.modules.get('film_foundry.core.development_compute')
        grain=sys.modules.get('film_foundry.core.grain_compute')
        active=('cuda' if selected=='cuda' and _runtime is not None and _failure is None else
                'opencl' if selected=='opencl' and opencl is not None and opencl['initialized'] and opencl['failure'] is None else 'cpu')
        return dict(requested=os.environ.get('FILM_FOUNDRY_COMPUTE','cpu'),
                    initialized=_runtime is not None, failure=_failure, calls=_calls,blur_calls=_blur_calls,
                    spectral_calls=_spectral_calls, paper_calls=_paper_calls, rotary_calls=_rotary_calls, edge_contact_calls=_edge_calls, noise_calls=_noise_calls, auto_backend=_auto_backend,
                    development_calls=development.status() if development is not None else {'cuda':0,'opencl':0},
                    resident_grain_calls=grain.status() if grain is not None else 0,
                    active_backend=active, cuda_device=_cuda_device, opencl=opencl)


def _get_runtime():
    global _runtime, _cuda_device
    if _runtime is None:
        import cupy as cp
        _runtime=(cp,cp.RawKernel(_SOURCE,'dye',options=('--fmad=false',)),
                  cp.cuda.Stream(non_blocking=True),cp.cuda.MemoryPool())
        name=cp.cuda.runtime.getDeviceProperties(cp.cuda.Device().id)['name']
        _cuda_device=name.decode() if isinstance(name,bytes) else str(name)
    return _runtime


def _opencl_status():
    module = sys.modules.get('film_foundry.core.opencl_compute')
    return module.status() if module is not None else None


def selected_backend():
    """Explicit selection, or availability preference CUDA -> OpenCL -> CPU.

    Auto is not a speed prediction. Failed execution always falls back to CPU.
    """
    global _auto_backend, _failure
    requested = os.environ.get('FILM_FOUNDRY_COMPUTE', 'cpu').lower()
    if requested in ('cpu', 'cuda', 'opencl'):
        return requested
    if requested != 'auto':
        return 'cpu'
    with _lock:
        if _auto_backend is None:
            if _failure is None:
                try:
                    cp, _, stream, _ = _get_runtime()
                    if cp.cuda.runtime.getDeviceCount() > 0:
                        stream.synchronize()
                        _auto_backend = 'cuda'
                except Exception as exc:
                    _failure = f'{type(exc).__name__}: {exc}'
            if _auto_backend is None:
                from . import opencl_compute
                _auto_backend = 'opencl' if opencl_compute.available() else 'cpu'
        return _auto_backend


def try_spectral(values, absorbance, weights, overlay, basis):
    """Fused 9-component projection, transmission and all-band RGB integration.

    Float64 arithmetic, original bands, bounded tiles, no wavelength volume.
    None means the unchanged CPU implementation must execute.
    """
    global _spectral_kernel, _spectral_calls
    if values.shape[0]*values.shape[1] < 65536 or values.dtype != np.float32:
        return None
    backend = selected_backend()
    if backend == 'cpu':
        return None
    parameters = [np.int32(absorbance.shape[1]), np.int32(overlay is not None)]
    if backend == 'opencl':
        from . import opencl_compute
        return opencl_compute.run('spectral', values, [absorbance, weights, basis], parameters, overlay)
    with _lock:
        if _failure is not None:
            return None
        source = target = extra = tables = None
        try:
            cp, _, stream, pool = _get_runtime()
            if _spectral_kernel is None:
                _spectral_kernel = cp.RawKernel(kernel_source('spectral', 'cuda'), 'spectral', options=('--fmad=false',))
            height, width = values.shape[:2]
            result = np.empty((height, width, 3), np.float32)
            rows = max(1, 262144//width)
            with stream, cp.cuda.using_allocator(pool.malloc):
                tables = [cp.asarray(np.ascontiguousarray(v, dtype=np.float64)) for v in (absorbance, weights, basis)]
                for start in range(0, height, rows):
                    source = cp.asarray(np.ascontiguousarray(values[start:start+rows]))
                    extra = cp.asarray(np.zeros(1, np.float32) if overlay is None else np.ascontiguousarray(overlay[start:start+rows]))
                    count = source.size//9
                    target = cp.empty((source.shape[0], width, 3), dtype=cp.float32)
                    _spectral_kernel(((count+255)//256,), (256,),
                                     (source, tables[0], tables[1], extra, tables[2], *parameters, np.int32(count), target))
                    target.get(out=result[start:start+rows], stream=stream, blocking=True)
                stream.synchronize()
            if not np.isfinite(result).all():
                raise ValueError('Non-finite spectral GPU result')
            _spectral_calls += 1
            return result
        except Exception as exc:
            reject(f'{type(exc).__name__}: {exc}')
            return None
        finally:
            if _runtime is not None:
                try:
                    _runtime[2].synchronize()
                    source = target = extra = tables = None
                    _runtime[3].free_all_blocks()
                except Exception:
                    pass


def reject(reason):
    global _failure
    with _lock:
        if selected_backend()=='opencl':
            from . import opencl_compute
            opencl_compute.reject(reason)
            return
        _failure=str(reason)
        warnings.warn('CUDA disabled; using CPU: '+_failure,RuntimeWarning,stacklevel=2)


def try_rotary_contact(shape,start,stop,duration,event,window,intervals):
    plan=np.asarray([(a,b,c,d,e,np.cos(f),np.sin(f)) for a,b,c,d,e,f in intervals],np.float64)
    parameters=(event['immersion_fraction'],event['rotation_rpm'],duration*window[0],duration*window[1],
                event.get('liquid_level_gradient',0.),event.get('meniscus_retention_s',0.))
    return try_contact_plan('rotary_contact',shape,start,stop,plan,parameters)


def try_contact_plan(name,shape,start,stop,plan,parameters):
    """Selected-precision contact plan; no chemical pools, CPU fallback."""
    global _rotary_calls,_edge_calls
    height,width=shape
    if (stop-start)*width<65536:return None
    backend=selected_backend()
    if backend=='cpu':return None
    dtype=np.float32 if name=='edge_contact_f32' else np.float64
    plan=np.asarray(plan,dtype=dtype)
    parameters=tuple(dtype(v) for v in parameters)
    if backend=='opencl':
        from . import opencl_compute
        return opencl_compute.run_rotary(shape,start,stop,plan,parameters,name=name)
    with _lock:
        if _failure is not None:return None
        table=target=None
        try:
            cp,_,stream,pool=_get_runtime()
            if name not in _contact_kernels:
                _contact_kernels[name]=cp.RawKernel(kernel_source(name,'cuda'),name,options=('--fmad=false',))
            kernel=_contact_kernels[name]
            result=np.empty((stop-start,width),np.float32)
            rows=max(1,1048576//width)
            with stream,cp.cuda.using_allocator(pool.malloc):
                table=cp.asarray(plan)
                for row in range(start,stop,rows):
                    end=min(row+rows,stop);count=(end-row)*width
                    target=cp.empty((end-row,width),cp.float32)
                    kernel(((count+255)//256,),(256,),
                        (table,np.int32(len(plan)),np.int32(height),np.int32(width),np.int32(row),np.int32(count),*parameters,target))
                    target.get(out=result[row-start:end-start],stream=stream,blocking=True)
                stream.synchronize()
            if not np.isfinite(result).all():raise ValueError('Non-finite rotary contact GPU result')
            _rotary_calls+=1
            if name.startswith('edge_contact'):_edge_calls+=1
            return result
        except Exception as exc:
            reject(f'{type(exc).__name__}: {exc}')
            return None
        finally:
            if _runtime is not None:
                try:
                    _runtime[2].synchronize();table=target=None;_runtime[3].free_all_blocks()
                except Exception:pass


def try_coordinate_noise(start,stop,width,height):
    """Same coordinate dither v2, generated on device without image uploads."""
    global _noise_kernel,_noise_calls
    if (stop-start)*width<65536:return None
    backend=selected_backend()
    if backend=='cpu':return None
    if backend=='opencl':
        from . import opencl_compute
        return opencl_compute.run_coordinate_noise(start,stop,width,height)
    with _lock:
        if _failure is not None:return None
        target=None
        try:
            cp,_,stream,pool=_get_runtime()
            if _noise_kernel is None:
                _noise_kernel=cp.RawKernel(kernel_source('coordinate_noise','cuda'),'coordinate_noise',options=('--fmad=false',))
            output=np.empty((stop-start,width,3),np.float32);rows=max(1,262144//width)
            with stream,cp.cuda.using_allocator(pool.malloc):
                for row in range(start,stop,rows):
                    end=min(stop,row+rows);count=(end-row)*width
                    target=cp.empty((end-row,width,3),cp.float32)
                    _noise_kernel(((count+255)//256,),(256,),
                        (np.int32(row),np.int32(width),np.int32(height),np.int32(count),target))
                    target.get(out=output[row-start:end-start],stream=stream,blocking=True)
                stream.synchronize()
            _noise_calls+=1
            return output
        except Exception as exc:
            reject(f'{type(exc).__name__}: {exc}');return None
        finally:
            if _runtime is not None:
                try:
                    _runtime[2].synchronize();target=None;_runtime[3].free_all_blocks()
                except Exception:pass


def try_paper_response(values,parameters):
    """Fused RGB sensitivity/mixing/logistic/fog response, no material objects."""
    global _paper_kernel,_paper_calls
    if values.shape[0]*values.shape[1]<262144 or values.dtype!=np.float32:return None
    backend=selected_backend()
    if backend=='cpu':return None
    if backend=='opencl':
        from . import opencl_compute
        return opencl_compute.run('paper_response',values,[parameters],[])
    with _lock:
        if _failure is not None:return None
        src=dst=table=None
        try:
            cp,_,stream,pool=_get_runtime()
            if _paper_kernel is None:
                _paper_kernel=cp.RawKernel(kernel_source('paper_response','cuda'),'paper_response',options=('--fmad=false',))
            height,width=values.shape[:2];output=np.empty((height,width,3),np.float32)
            rows=max(1,1048576//width)
            with stream,cp.cuda.using_allocator(pool.malloc):
                table=cp.asarray(parameters)
                for start in range(0,height,rows):
                    src=cp.asarray(np.ascontiguousarray(values[start:start+rows]));dst=cp.empty_like(src)
                    count=src.size//3
                    _paper_kernel(((count+255)//256,),(256,),(src,table,np.int32(count),dst))
                    dst.get(out=output[start:start+rows],stream=stream,blocking=True)
                stream.synchronize()
            if not np.isfinite(output).all():raise ValueError('Non-finite paper response')
            _paper_calls+=1
            return output
        except Exception as exc:
            reject(f'{type(exc).__name__}: {exc}')
            return None
        finally:
            if _runtime is not None:
                try:
                    _runtime[2].synchronize();src=dst=table=None
                    if _runtime[3].free_bytes()>128*1024*1024:_runtime[3].free_all_blocks()
                except Exception:pass


def try_dye(density,absorption,base,strength,legacy,overlap=None,weighted=None,response=None):
    """Return accelerated output or None. Opt-in, >=256K pixels, bounded 1M tiles."""
    global _runtime,_failure,_calls
    if density.shape[0]*density.shape[1]<262144:
        return None
    backend=selected_backend()
    if backend=='cpu' or not np.isfinite(density).all():return None
    if backend=='opencl':
        from . import opencl_compute
        tables=[np.asarray(v,dtype=np.float32) for v in
                (absorption,base,overlap if overlap is not None else np.eye(3),
                 weighted if weighted is not None else np.eye(3),response if response is not None else np.ones(3))]
        return opencl_compute.run('dye',density,tables,[np.float32(strength),np.int32(legacy)])
    with _lock:
        if _failure is not None:return None
        try:
            cp,kernel,stream,pool=_get_runtime()
            output=np.empty_like(density,dtype=np.float32)
            rows=max(1,1048576//density.shape[1])
            with stream,cp.cuda.using_allocator(pool.malloc):
                tables=[cp.asarray(np.asarray(v,dtype=np.float32)) for v in
                        (absorption,base,overlap if overlap is not None else np.eye(3),
                         weighted if weighted is not None else np.eye(3),response if response is not None else np.ones(3))]
                for start in range(0,len(density),rows):
                    source=cp.asarray(np.ascontiguousarray(density[start:start+rows]))
                    target=cp.empty_like(source);count=source.size//3
                    kernel(((count+255)//256,),(256,),
                           (source,*tables,np.float32(strength),np.int32(legacy),np.int32(count),target))
                    target.get(out=output[start:start+rows],stream=stream,blocking=True)
                stream.synchronize()
            if not np.isfinite(output).all():raise ValueError('CUDA dye result is non-finite')
            _calls+=1
            return output
        except Exception as exc:
            _failure=f'{type(exc).__name__}: {exc}'
            warnings.warn('CUDA disabled; using CPU: '+_failure,RuntimeWarning,stacklevel=2)
            return None
        finally:
            if _runtime is not None:
                # Exceptions may leave queued work. Drain before freeing cached blocks.
                try:
                    _runtime[2].synchronize()
                    source=target=tables=None
                    _runtime[3].free_all_blocks()
                except Exception:
                    pass


_BLUR_SOURCE=r'''
__device__ int reflected(int x,int n) {
 if(x>=0 && x<n)return x;
 if(n==1)return 0;int period=2*n-2;x=x%period;
 if(x<0)x+=period;return x<n?x:period-x;
}
extern "C" __global__ void grain_blur(const float* src,float* dst,const float* k,
 int radius,int width,int height,int vertical) {
 int i=blockIdx.x*blockDim.x+threadIdx.x;if(i>=width*height)return;
 int x=i%width,y=i/width;float value=0;
 for(int j=-radius;j<=radius;j++) {
   int p=vertical?reflected(y+j,height)*width+x:y*width+reflected(x+j,width);
   value+=src[p]*k[j+radius];
 } dst[i]=value;
}
'''


def try_grain_blur(field,sigma):
    """Float32 separable Gaussian, original OpenCV weights and reflect101 edges.

    CPU supplies the unchanged random field. Overlapped tiles preserve the full
    convolution footprint; no smaller work image, new random stream or FFT wrap.
    """
    global _blur_kernel,_blur_calls
    if field.ndim!=2 or field.dtype!=np.float32 or field.size<1048576 or not 2.5<=sigma<=96:return None
    # Small-field pipelines still lose to transfer overhead. Large native fields
    # are qualified separately, without changing their size or random stream.
    if os.environ.get('FILM_FOUNDRY_GRAIN_CUDA')=='0':return None
    if field.size<8388608 and os.environ.get('FILM_FOUNDRY_EXPERIMENTAL_GRAIN_CUDA')!='1':return None
    if selected_backend()!='cuda':return None
    if not np.isfinite(field).all():return None
    import cv2
    size=int(round(sigma*8+1))|1;radius=size//2
    weights=cv2.getGaussianKernel(size,sigma,cv2.CV_32F).ravel()
    with _lock:
        if _failure is not None:return None
        source=temporary=target=device_weights=None
        try:
            cp,_,stream,pool=_get_runtime()
            if _blur_kernel is None:
                _blur_kernel=cp.RawKernel(_BLUR_SOURCE,'grain_blur',options=('--fmad=false',))
            height,width=field.shape;rows=max(1,(4194304 if sigma>16 else 1048576)//width)
            output=np.empty(field.shape,np.float32)
            with stream,cp.cuda.using_allocator(pool.malloc):
                device_weights=cp.asarray(weights)
                for start in range(0,height,rows):
                    stop=min(height,start+rows);a=max(0,start-radius);b=min(height,stop+radius)
                    source=cp.asarray(np.ascontiguousarray(field[a:b]))
                    temporary=cp.empty_like(source);target=cp.empty_like(source)
                    args=(device_weights,np.int32(radius),np.int32(width),np.int32(b-a))
                    grid=((source.size+255)//256,)
                    _blur_kernel(grid,(256,),(source,temporary,*args,np.int32(0)))
                    _blur_kernel(grid,(256,),(temporary,target,*args,np.int32(1)))
                    target[start-a:stop-a].get(out=output[start:stop],stream=stream,blocking=True)
                stream.synchronize()
            if not np.isfinite(output).all():raise ValueError('Non-finite Gaussian output')
            # Validate corners and a tile seam using original CPU convolution.
            for y,x in ((0,0),(min(rows,height-1),width//2),(height-1,width-1)):
                a=max(0,y-radius);b=min(height,y+radius+8)
                l=max(0,x-radius);r=min(width,x+radius+8)
                cpu=cv2.GaussianBlur(np.ascontiguousarray(field[a:b,l:r]),(0,0),sigmaX=sigma,sigmaY=sigma)
                ch=min(8,height-y);cw=min(8,width-x)
                if not np.allclose(output[y:y+ch,x:x+cw],cpu[y-a:y-a+ch,x-l:x-l+cw],rtol=2e-6,atol=2e-6):
                    raise ValueError('Gaussian kernel exceeded CPU tolerance')
            _blur_calls+=1
            return output
        except Exception as exc:
            reject(f'{type(exc).__name__}: {exc}')
            return None
        finally:
            if _runtime is not None:
                try:
                    _runtime[2].synchronize()
                    source=temporary=target=device_weights=None
                    # Repeated grain passes reuse bounded device allocations.
                    # Freeing after every small blur costs more than the convolution.
                    if _runtime[3].free_bytes()>64*1024*1024:
                        _runtime[3].free_all_blocks()
                except Exception:
                    pass
