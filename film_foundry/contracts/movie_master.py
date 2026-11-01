"""Application-level material sequence, referencing unchanged per-frame NPZ media.

No codecs, numerical runtime, UI, or paths on the calling machine live here.
"""
from __future__ import annotations

from fractions import Fraction
import json
from pathlib import PurePosixPath

MODEL = 'material_movie_master_v1'
MANIFEST = 'master.json'
STAGES = ('exposed_latent', 'developed_medium')


def normalize_master(value: object) -> dict:
    if not isinstance(value, dict) or value.get('model') != MODEL:
        raise ValueError('Unsupported material movie master model.')
    required = {'model','master_id','roll_id','stage','width','height','seed','recipe','frames'}
    if required - value.keys() or value.keys() - (required | {'source','source_master_id'}):
        raise ValueError('Movie master fields are missing or unknown.')
    for key in ('master_id','roll_id'):
        if not isinstance(value[key],str) or not value[key].strip():
            raise ValueError(f'Movie master {key} must be a nonempty string.')
    if value['stage'] not in STAGES:
        raise ValueError('Movie master must contain latent or developed material.')
    for key in ('width','height','seed'):
        number = value[key]
        if isinstance(number,bool) or not isinstance(number,int) or number < (0 if key == 'seed' else 1):
            raise ValueError(f'Invalid movie master {key}.')
        if key != 'seed' and number > 0x7fffffff:
            raise ValueError('Movie dimensions exceed the shared 32-bit range.')
    if value['seed'] > 0xffffffff or not isinstance(value['recipe'],dict):
        raise ValueError('Invalid movie master seed or recipe.')
    if 'source' in value and not isinstance(value['source'],dict):
        raise ValueError('Movie master source must be an object.')
    if 'source_master_id' in value and (not isinstance(value['source_master_id'],str) or not value['source_master_id'].strip()):
        raise ValueError('Movie source master identity must be a nonempty string.')
    frames = value['frames']
    if not isinstance(frames,list) or not frames:
        raise ValueError('Movie master must contain at least one material frame.')
    previous = None
    paths = set()
    for index, frame in enumerate(frames,1):
        if not isinstance(frame,dict) or set(frame) != {'index','path','pts','time_base_num','time_base_den','duration'}:
            raise ValueError('Invalid movie frame fields.')
        for key in ('index','pts','time_base_num','time_base_den'):
            if isinstance(frame[key],bool) or not isinstance(frame[key],int):
                raise ValueError('Movie frame timing and index must be integers.')
            if not -(1 << 63) <= frame[key] < (1 << 63):
                raise ValueError('Movie frame timing is outside the shared signed 64-bit range.')
        if frame['index'] != index or index > 0x7fffffff or frame['time_base_num'] <= 0 or frame['time_base_den'] <= 0:
            raise ValueError('Movie frame index/time base is invalid.')
        duration = frame['duration']
        if duration is not None and (isinstance(duration,bool) or not isinstance(duration,int) or not 0 <= duration < (1 << 63)):
            raise ValueError('Movie frame duration must be a nonnegative integer or null.')
        path = frame['path']
        if not isinstance(path,str) or '\\' in path or ':' in path:
            raise ValueError('Movie frame path must be relative POSIX syntax.')
        parts = PurePosixPath(path).parts
        suffixes = ('.darkroom_latent.npz',) if value['stage'] == 'exposed_latent' else ('.darkroom_negative.npz','.darkroom_positive.npz')
        if str(PurePosixPath(path)) != path or len(parts) != 2 or parts[0] != 'frames' or any(p in {'','.', '..'} for p in parts) or not path.endswith(suffixes) or path in paths:
            raise ValueError('Movie frame must reference a unique local NPZ in frames/.')
        paths.add(path)
        timestamp = Fraction(frame['pts']*frame['time_base_num'],frame['time_base_den'])
        if previous is not None and timestamp <= previous:
            raise ValueError('Movie presentation timestamps must be strictly increasing.')
        previous = timestamp
    # Validate JSON representability/finite values; return an independently owned tree.
    return json.loads(json.dumps(value,ensure_ascii=False,allow_nan=False))
