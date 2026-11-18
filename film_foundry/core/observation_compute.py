"""Optional numerical optics/encoding kernels with atomic CPU fallback.

Fields remain immutable host-owned material data. Only the optical result is
returned; observers never write back into formation pools.
"""
import os
import numpy as np
from . import desktop_compute as compute
from .execution_topology import reference_execution_enabled
from .gpu_kernels import source

MIN_PIXELS=262144
_kernels={}
_calls={}
FP64={'observe_plate','observe_instant'}


def status():return dict(_calls)


def eligible(shape):
    return (shape[0]*shape[1]>=MIN_PIXELS and not reference_execution_enabled()
        and os.environ.get('FILM_FOUNDRY_OBSERVATION_GPU')!='0'
        and compute.selected_backend()!='cpu')


def run(name,arrays,parameters,*,kind=0,channels=3):
    shape=arrays[0].shape[:2]
    if not eligible(shape):return None
    # The simple peel observer is memory/transfer bound on the measured GPU.
    # Keep its kernel available for explicit hardware qualification, while the
    # finite transfer reaction continues to use its independent GPU path.
    if name=='observe_peel' and os.environ.get('FILM_FOUNDRY_EXPERIMENTAL_PEEL_OBSERVER')!='1':return None
    backend=compute.selected_backend()
    if backend=='opencl':
        from . import opencl_compute as oc
        lock=oc._lock
    else:lock=compute._lock
    with lock:
        try:
            if backend=='opencl':
                if not oc.available() or (name in FP64 and not oc._device['fp64']):return None
                runtime=oc._runtime
            else:
                if compute._failure is not None:return None
                runtime=compute._get_runtime()
            result=_run(backend,runtime,name,arrays,parameters,kind,channels)
            finite=np.isfinite(result).all()
            if not finite and name=='encode_srgb' and np.isnan(arrays[0]).any():
                # Preserve the CPU encoder's NaN propagation without treating
                # caller data as a device fault or disabling other GPU work.
                return None
            if not finite or np.min(result)<0:raise ValueError('Invalid GPU optical output')
            key=backend+':'+name;_calls[key]=_calls.get(key,0)+1
            return result
        except Exception as exc:
            (oc.reject if backend=='opencl' else compute.reject)(f'{type(exc).__name__}: {exc}')
            return None


def _run(backend,runtime,name,arrays,parameters,kind,channels):
    h,w=arrays[0].shape[:2]
    unique=[];offsets=[];sizes=[];plane_count=0
    for array in arrays:
        alias=next((index for index,value in enumerate(unique) if value is array),None)
        if alias is None:
            offsets.append(plane_count);unique.append(array)
            size=1 if array.ndim==2 else array.shape[-1];sizes.append(size);plane_count+=size
        else:offsets.append(sum(sizes[:alias]))
    offsets=np.asarray(offsets,np.int32)
    params=np.asarray(parameters,dtype=np.float64 if name in FP64 else np.float32)
    result=np.empty((h,w,3),np.float32)
    if backend=='cuda':
        cp,_,stream,pool=runtime
        if name not in _kernels:_kernels[name]=cp.RawKernel(source(name,'cuda'),name,options=('--fmad=false',))
        limit=524288
    else:
        cl,context,queue,kernels,limit=runtime
        if name not in kernels:
            kernels[name]=cl.Kernel(cl.Program(context,source(name,'opencl')).build(),name)
        limit=min(limit,max(1,limit*72//(4*sum(sizes))))
    rows=max(1,limit//w);inputs=target=None
    try:
        for start in range(0,h,rows):
            stop=min(start+rows,h);count=(stop-start)*w
            packed=np.concatenate([a[start:stop].ravel() for a in unique]).astype(np.float32,copy=False)
            arguments=(np.int32(kind),np.int32(channels),np.int32(count))
            if backend=='cuda':
                with stream,cp.cuda.using_allocator(pool.malloc):
                    inputs=[cp.asarray(a) for a in (packed,offsets,params)]
                    target=cp.empty((stop-start,w,3),cp.float32)
                    _kernels[name](((count+255)//256,),(256,),(*inputs,*arguments,target))
                    target.get(out=result[start:stop],stream=stream,blocking=True)
            else:
                buffers=[];output=None
                try:
                    buffers=[cl.Buffer(context,cl.mem_flags.READ_ONLY|cl.mem_flags.COPY_HOST_PTR,hostbuf=a) for a in (packed,offsets,params)]
                    output=cl.Buffer(context,cl.mem_flags.WRITE_ONLY,size=count*12)
                    kernels[name](queue,(count,),None,*buffers,*arguments,output)
                    cl.enqueue_copy(queue,result[start:stop],output,is_blocking=True)
                finally:
                    queue.finish()
                    for buffer in buffers:buffer.release()
                    if output is not None:output.release()
        return result
    finally:
        if backend=='cuda':
            stream.synchronize();inputs=target=None
            if pool.free_bytes()>128*1024*1024:pool.free_all_blocks()


def sheet(body,material,view,kind):
    """Return unilluminated sheet optics; caller keeps its validation/framing."""
    f=body.fields;m=material
    if not eligible(next(iter(f.values())).shape):return None
    from .sheet_structure import gloss_factors
    if kind=='paper' and body.schema_version==2:
        keys=('silver','halide','residue','dye','paper','paper_moisture')
        parameters=[m.maximum_density,m.warmth,m.texture,m.drydown*(.2 if m.paper_base=='rc' else 1.),m.surface_gloss,*gloss_factors(view)]
        return run('observe_paper',[f[k] for k in keys],parameters,kind=int(m.kind=='color'))
    if kind in ('cyanotype','platinum') and body.schema_version==3:
        import math
        keys=('ferric','ferrous','spent_ferric' if kind=='platinum' else 'ferric',
              'platinum' if kind=='platinum' else 'blue_pigment','palladium' if kind=='platinum' else 'reduced_pigment',
              'residue','paper','fibers','paper_moisture')
        parameters=[m.maximum_density,math.log(10),m.texture,m.drydown*(.2 if m.paper_base=='rc' else 1.),m.surface_gloss,*gloss_factors(view)]
        return run('observe_iron',[f[k] for k in keys],parameters,kind=int(kind=='platinum'))
    if kind=='wet' and body.schema_version==4:
        keys=('silver','external_deposit','enhancement_deposit','halide','external_silver','enhancement_silver','residue','surface','varnish','roughness')
        parameters=[m.maximum_density,m.particle_reflectance,m.base_transmission,
                    *(gloss_factors(view) if not body.usable_negative else (0.,0.,0.))]
        return run('observe_wet',[f[k] for k in keys],parameters,kind=0 if body.usable_negative else 1 if m.support=='ambrotype' else 2)
    return None
