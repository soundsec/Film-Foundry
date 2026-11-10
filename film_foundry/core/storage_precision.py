"""Storage precision by field role; computation and authoritative optics stay full.

FP16 is a standard NumPy dtype, not a custom pixel encoding. The caller must
declare independent appearance/cold fields. Unknown fields are retained exactly.
"""
import numpy as np


def precision_policy(value):
    if value not in ('auto','full'):raise ValueError('Storage precision must be auto or full')
    return value


def pack_fields(fields, *, half_fields=(), binary_fields=(), policy='auto'):
    precision_policy(policy)
    result=dict(fields); changed={}
    if policy=='full':return result,changed
    for key in half_fields:
        if key not in fields:continue
        value=fields[key]
        if value.dtype!=np.float32 or not value.size:continue
        # Preserve unusual legacy ranges instead of clipping or overflowing.
        low=float(np.min(value));high=float(np.max(value))
        if not np.isfinite(low) or not np.isfinite(high) or low < -65504 or high > 65504:continue
        result[key]=value.astype(np.float16);changed[key]='float16'
    for key in binary_fields:
        if key not in fields:continue
        value=fields[key]
        if value.dtype==np.float32 and np.all((value==0)|(value==1)):
            result[key]=value.astype(np.uint8);changed[key]='uint8_exact'
    return result,changed


def film_precision_profile(quality='standard',policy='auto'):
    """Effective fraction bits and standard storage containers, not new dtypes."""
    precision_policy(policy)
    aliases={'draft':'draft','preview':'draft','standard':'standard',
             'high':'high','full':'high','native':'high'}
    quality=str(quality).strip().lower()
    if quality not in aliases:raise ValueError('Unsupported film quality')
    quality=aliases[quality]
    dtype,bits=('float32',23) if policy=='full' else {
        'draft':('float16',7),'standard':('float16',10),'high':('float32',15)}[quality]
    return {'model':'film_storage_precision_v2','quality':quality,'policy':policy,
            'cold_storage_dtype':dtype,'cold_fraction_bits':bits,
            'appearance_storage_dtype':'float32' if policy=='full' else 'float16',
            'optical_master':'float32'}


def round_float_fraction(value,fraction_bits,storage_dtype='float32'):
    """Round FP32 fraction bits, ties to even, into a standard NPY container.

    Bounded chunks avoid a full-size integer scratch array. Overflow retains the
    original field; no clipping or mutation is permitted. FP16 subnormals retain
    their normal absolute-error limit rather than a relative-error promise.
    """
    if type(fraction_bits) is not int or not 0<=fraction_bits<=23:raise ValueError('Fraction bits must be 0..23')
    if storage_dtype not in ('float16','float32'):raise ValueError('Unsupported float storage')
    if storage_dtype=='float16' and fraction_bits>10:raise ValueError('FP16 supports at most 10 explicit fraction bits')
    source=np.asarray(value)
    if source.dtype!=np.float32:raise ValueError('Expected FP32 source')
    if not source.size:return source
    low=float(np.min(source));high=float(np.max(source))
    if not np.isfinite(low) or not np.isfinite(high):return source
    if storage_dtype=='float16' and (low < -65504 or high > 65504):return source
    if fraction_bits==23:return source
    if fraction_bits==10 and storage_dtype=='float16':return source.astype(np.float16)
    output=np.empty(source.shape,dtype=storage_dtype)
    flat=source.ravel();target=output.reshape(-1);shift=23-fraction_bits
    mask=np.uint32((0xffffffff << shift)&0xffffffff)
    bias=np.uint32((1 << (shift-1))-1)
    with np.errstate(over='ignore',invalid='ignore'):
        for start in range(0,source.size,262144):
            stop=min(source.size,start+262144);words=flat[start:stop].view(np.uint32)
            rounded=(words+bias+((words>>shift)&np.uint32(1)))&mask
            target[start:stop]=rounded.view(np.float32)
            if not np.isfinite(target[start:stop]).all():return source
    return output


def pack_film_fields(fields,policy='auto',*,quality='standard'):
    # Modern scans use optical_density_rgb, and spectral relighting uses the
    # spectral component amounts. Both remain FP32. CMY/grain are cold layer
    # masters (diagnostics/creative separation), never the live chemical pools.
    profile=film_precision_profile(quality,policy)
    result,changed=pack_fields(fields,half_fields=('process_surface_emulsion_texture',),
        binary_fields=('spectral_emulsion_coverage','process_surface_emulsion_coverage'),policy=policy)
    if policy=='full' or 'optical_density_rgb' not in fields:return result,changed
    for key in ('density_cmy','density_grain'):
        if key not in fields or fields[key].dtype!=np.float32:continue
        rounded=round_float_fraction(fields[key],profile['cold_fraction_bits'],profile['cold_storage_dtype'])
        if rounded is fields[key]:continue
        result[key]=rounded
        changed[key]='float16' if profile['quality']=='standard' else f"{profile['cold_storage_dtype']}_fraction{profile['cold_fraction_bits']}"
    return result,changed


def film_precision_record(changed,quality,policy,previous=None):
    record=film_precision_profile(quality,policy)
    record['fields']=dict(changed)
    previous=previous or {}
    record['source_quantized']=bool(previous.get('source_quantized') or
        any(value!='uint8_exact' for value in previous.get('fields',{}).values()))
    return record
