"""Bounded, atomic GPU advances for finite peel/integral transfer inventories.

Small rate preparation preserves NumPy's FP32 promotion rules. All four time
intervals and their FP64 quadrature execute in one kernel per tile. No partial
device result is committed to caller arrays; any failure returns to the CPU.
"""
import os
import numpy as np
from . import desktop_compute as compute
from .execution_topology import reference_execution_enabled
from .gpu_kernels import source

CHANGED=('remaining','immobile','received','developed_donor','transfer_loss',
         'developer_available','developer_used','stain_density','alkali_remaining',
         'alkali_neutralized','neutralizer_remaining','neutralization_clock')
MIN_PIXELS=262144
_cuda_kernel=None
_calls={'cuda':0,'opencl':0}


def status():return dict(_calls)


def prepare_rates(original,thermal,reaction_seconds,inhibitor_sensitivity,
                  illumination,yields,elapsed,seconds,acid_capacity,acid_seconds):
    from .transfer_environment import RESIDUE_RGB
    coverage=original['coverage'];contact=original['contact']
    rate=thermal/reaction_seconds*original['channel_activity']*(coverage*contact*np.exp(-3*inhibitor_sensitivity*original['inhibitor']))[...,None]
    yield_fraction=np.clip(yields,0,1)*original['transfer_yield']
    light=illumination*10**(-original['inhibitor'][...,None]*RESIDUE_RGB)
    if original['remaining'].shape[-1]==1:light=light.mean(axis=-1,keepdims=True)
    before=np.clip(original['arrival_seconds']-elapsed,0,seconds)
    after_light=light*(.04+.6*(1-coverage))[...,None]
    timer_rate=thermal*contact
    acid_rate=coverage*acid_capacity/acid_seconds*timer_rate
    return rate,light,after_light,before,timer_rate,acid_rate,yield_fraction


def try_advance(fields,rate_parameters,delay,acid_seconds,neutralizing,nodes,weights,stain):
    h,w,channels=fields['remaining'].shape
    if (h*w<MIN_PIXELS or reference_execution_enabled()
            or os.environ.get('FILM_FOUNDRY_TRANSFER_GPU')=='0'):
        return None
    backend=compute.selected_backend()
    if backend=='cpu':return None
    if backend=='opencl':
        from . import opencl_compute as cl_compute
        lock=cl_compute._lock
    else:lock=compute._lock
    with lock:
        try:
            if backend=='opencl':
                if not cl_compute.available() or not cl_compute._device.get('fp64'):return None
                runtime=cl_compute._runtime
            else:
                if compute._failure is not None:return None
                runtime=compute._get_runtime()
            result=_execute(backend,runtime,fields,rate_parameters,delay,acid_seconds,
                            neutralizing,np.concatenate((nodes,weights,stain)).astype(np.float64))
            if any(not np.isfinite(a).all() or np.min(a)<0 for a in result.values()):
                raise ValueError('Invalid finite transfer GPU output')
            _calls[backend]+=1
            return result
        except Exception as exc:
            (cl_compute.reject if backend=='opencl' else compute.reject)(f'{type(exc).__name__}: {exc}')
            return None


def _execute(backend,runtime,fields,parameters,delay,acid_seconds,neutralizing,quad):
    global _cuda_kernel
    h,w,channels=fields['remaining'].shape
    result={k:np.empty_like(fields[k]) for k in CHANGED}
    sizes=[channels]*7+[3]+[1]*4+[channels]*3+[1]*3
    offsets=np.cumsum([0]+sizes[:-1],dtype=np.int32)
    output_width=int(sum(sizes[:12]))
    if backend=='cuda':
        cp,_,stream,pool=runtime
        if _cuda_kernel is None:
            _cuda_kernel=cp.RawKernel(source('transfer_advance','cuda'),'transfer_advance',options=('--fmad=false',))
        tile_pixels=262144
    else:
        cl,context,queue,kernels,tile_pixels=runtime
        if 'transfer_advance' not in kernels:
            program=cl.Program(context,source('transfer_advance','opencl')).build()
            kernels['transfer_advance']=cl.Kernel(program,'transfer_advance')
        # Packed buffer has more planes than the ordinary RGB kernels.
        tile_pixels=min(65536,max(1,int(tile_pixels)*72//(sum(sizes)*4)))
    rows=max(1,tile_pixels//w)
    device_inputs=device_output=None
    try:
        for start in range(0,h,rows):
            stop=min(start+rows,h);count=(stop-start)*w
            local={k:v[start:stop] for k,v in fields.items()}
            rate_values=prepare_rates(local,*parameters)
            packed=np.concatenate([v.ravel() for v in
                [local[k] for k in CHANGED]+list(rate_values[:6])]).astype(np.float32,copy=False)
            yields=np.ascontiguousarray(rate_values[-1],dtype=np.float64)
            args=(np.float64(parameters[6]),np.float64(delay),
                  np.float64(acid_seconds),np.int32(neutralizing),np.int32(channels),np.int32(count))
            output=np.empty(output_width*count,np.float32)
            if backend=='cuda':
                with stream,cp.cuda.using_allocator(pool.malloc):
                    device_inputs=[cp.asarray(v) for v in (packed,offsets,yields,quad)]
                    device_output=cp.empty_like(output)
                    _cuda_kernel(((count+127)//128,),(128,),(*device_inputs,*args,device_output))
                    device_output.get(out=output,stream=stream,blocking=True)
            else:
                buffers=[];target=None
                try:
                    for value in (packed,offsets,yields,quad):
                        buffers.append(cl.Buffer(context,cl.mem_flags.READ_ONLY|cl.mem_flags.COPY_HOST_PTR,hostbuf=value))
                    target=cl.Buffer(context,cl.mem_flags.WRITE_ONLY,size=output.nbytes)
                    kernels['transfer_advance'](queue,(count,),None,*buffers,*args,target)
                    cl.enqueue_copy(queue,output,target,is_blocking=True)
                finally:
                    queue.finish()
                    for buffer in buffers:buffer.release()
                    if target is not None:target.release()
            for index,key in enumerate(CHANGED):
                result[key][start:stop]=output[offsets[index]*count:(offsets[index]+sizes[index])*count].reshape(result[key][start:stop].shape)
        return result
    finally:
        if backend=='cuda':
            stream.synchronize();device_inputs=device_output=None
            if pool.free_bytes()>128*1024*1024:pool.free_all_blocks()
