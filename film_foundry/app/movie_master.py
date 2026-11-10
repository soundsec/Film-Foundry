"""Material movie orchestration. No movie encoding or editing lives here."""
from __future__ import annotations

from contextlib import contextmanager
import copy
from dataclasses import asdict
from fractions import Fraction
import json
import os
from pathlib import Path
import secrets
import time
from uuid import uuid4

import numpy as np

from film_foundry.app.control import WorkflowControl
from film_foundry.app.incident_batch import prepare_incident_config
from film_foundry.app.master_workspace import master_workspace, check_space
from film_foundry.contracts.movie_master import MANIFEST, MODEL, normalize_master
from film_foundry.core.atomic_io import atomic_savez, atomic_write_json, strict_json_load
from film_foundry.core.engine import expose_image_to_latent, develop_negative_from_latent
from film_foundry.core.execution import processing_long_edge
from film_foundry.core.frame_identity import attach_frame_identity
from film_foundry.core.latent_io import load_exposed_latent, save_exposed_latent, latent_identity_from_film
from film_foundry.core.negative_io import load_developed_medium_for_scan
from film_foundry.core.preview import resize_to_long_edge
from film_foundry.core.runtime_validate import _validate_output_runtime
from film_foundry.core.scan_runtime import scan_negative
from film_foundry.core.states import developed_medium_metadata
from film_foundry.model.config import DarkroomConfig


@contextmanager
def _new_master(output: Path, control: WorkflowControl):
    output = Path(output).resolve()
    if output.exists():
        raise FileExistsError('A new material master needs a new output directory: '+str(output))
    with master_workspace(output.parent,control) as pending:
        (pending/'frames').mkdir()
        yield pending
        control.check_cancel()
        check_space(pending)
        if output.exists():
            raise FileExistsError(output)
        for attempt in range(4):
            try:
                os.rename(pending,output)
                break
            except PermissionError as exc:
                # Windows indexers can briefly hold a just-written directory.
                # Keep publication atomic; never fall back to copying into target.
                if os.name != 'nt' or getattr(exc,'winerror',None) not in {5,32} or attempt == 3:
                    raise
                control.check_cancel()
                time.sleep(.025*(attempt+1))


def read_master(path: Path) -> tuple[Path,dict]:
    path = Path(path).resolve()
    if not path.is_dir() and path.name != MANIFEST:
        raise ValueError('Select a material master directory or its master.json.')
    root = path if path.is_dir() else path.parent
    document = normalize_master(strict_json_load(root/MANIFEST))
    for frame in document['frames']:
        target = (root/frame['path']).resolve()
        if not target.is_relative_to(root) or not target.is_file() or target.stat().st_size == 0:
            raise ValueError('Movie master contains a missing or escaping material frame.')
    return root,document


def _seed(config, fallback=None):
    value = config.random_seed if config.random_seed is not None else fallback
    if value is None:
        value = secrets.randbits(32)
    if isinstance(value,bool) or not isinstance(value,int) or not 0 <= value <= 0xffffffff:
        raise ValueError('Material movie seed must be an unsigned 32-bit integer.')
    return value


def _frame_rng(seed,index,phase):
    return np.random.default_rng(np.random.SeedSequence([seed,index,phase]))


def expose_movie_frames(frames, output: Path, config: DarkroomConfig, *, source: dict | None=None,
                        control: WorkflowControl | None=None) -> Path:
    """Freeze timestamped SDR RGB input frames to an undeveloped material master."""
    control = control or WorkflowControl()
    seed = _seed(config)
    runtime = prepare_incident_config(copy.deepcopy(config),fallback_seed=seed)
    runtime.random_seed = seed
    runtime.seed_strategy = 'fixed'
    roll_id = str(uuid4())
    manifest = dict(model=MODEL,master_id=roll_id,roll_id=roll_id,stage='exposed_latent',
                    width=0,height=0,seed=seed,recipe=asdict(runtime),frames=[])
    if source is not None:
        manifest['source'] = copy.deepcopy(source)
    previous = None
    with _new_master(output,control) as pending:
        for index,frame in enumerate(frames,1):
            control.check_cancel()
            check_space(pending)
            rgb = resize_to_long_edge(frame.rgb,processing_long_edge(runtime))
            height,width = rgb.shape[:2]
            if index == 1:
                manifest.update(width=width,height=height)
            if (height,width) != (manifest['height'],manifest['width']):
                raise ValueError('Changing frame dimensions require a separate material master.')
            timestamp = Fraction(frame.pts*frame.time_base_num,frame.time_base_den)
            if previous is not None and timestamp <= previous:
                raise ValueError('Movie presentation timestamps must be strictly increasing.')
            control.report('movie_expose','exposing')
            latent = expose_image_to_latent(rgb,runtime,rng=_frame_rng(seed,index,0))
            identity = latent_identity_from_film(runtime.film)
            identity['capture_incident_identity'] = {'model':'roll_batch_incident_identity_v1',
                'seed':runtime.chemistry.incident_identity_seed}
            identity['frame_identity'] = dict(roll_id=roll_id,frame_index=index)
            relative = f'frames/{index:08d}.darkroom_latent.npz'
            control.check_cancel()
            save_exposed_latent(pending/relative,latent,identity=identity)
            check_space(pending)
            manifest['frames'].append(dict(index=index,path=relative,pts=frame.pts,time_base_num=frame.time_base_num,
                time_base_den=frame.time_base_den,duration=frame.duration))
            previous = timestamp
            del latent,rgb
        control.check_cancel()
        atomic_write_json(pending/MANIFEST,normalize_master(manifest))
    return Path(output).resolve()


def import_video_master(source: Path, output: Path, config: DarkroomConfig, *, control=None) -> Path:
    from film_foundry.app.movie_ingest import decode_video
    source,output = Path(source).resolve(),Path(output).resolve()
    if not source.is_file():
        raise FileNotFoundError(source)
    if source == output or source.is_relative_to(output):
        raise ValueError('Material master cannot contain or overwrite the input video.')
    frames = decode_video(source)
    from film_foundry.app.workflow import rng_and_resolved_seed_for_develop
    _,resolved_seed = rng_and_resolved_seed_for_develop(source,config)
    frozen = copy.deepcopy(config)
    frozen.random_seed = resolved_seed
    frozen.seed_strategy = 'fixed'
    try:
        return expose_movie_frames(frames,output,frozen,control=control,source={
            'kind':'video','name':source.name,'color_ingest':'sdr_bt709_or_srgb_v1_unspecified_assumed_bt709',
            'audio':'not_part_of_material_master'})
    finally:
        frames.close()


def _save_optical(path,medium,config):
    # Existing NPZ fields and metadata. Keep CMY compatibility masters alongside
    # authoritative optical density and optional final spectral components.
    payload = {key:np.asarray(getattr(medium,key),dtype=np.float32) for key in
        ('density_cmy','density_grain','optical_density_rgb')}
    metadata = developed_medium_metadata(medium)
    if 'capture' in medium.metadata:
        metadata['capture'] = copy.deepcopy(medium.metadata['capture'])
    payload['developed_medium_metadata'] = np.asarray(json.dumps(metadata,ensure_ascii=False,allow_nan=False))
    if medium.clear_base_optical_density_rgb is not None:
        payload['clear_base_optical_density_rgb'] = np.asarray(medium.clear_base_optical_density_rgb,dtype=np.float32)
    if medium.spectral_medium is not None:
        payload['spectral_component_amounts'] = medium.spectral_medium.component_amounts
        if medium.spectral_medium.emulsion_coverage is not None:payload['spectral_emulsion_coverage']=medium.spectral_medium.emulsion_coverage
        if medium.spectral_medium.covered_density_rgb is not None:payload['spectral_covered_density_rgb']=medium.spectral_medium.covered_density_rgb
        if medium.spectral_medium.post_process_density_rgb is not None:
            payload['spectral_post_process_density_rgb'] = medium.spectral_medium.post_process_density_rgb
    from film_foundry.core.storage_precision import pack_film_fields,film_precision_record
    payload,stored_fields=pack_film_fields(payload,config.output.material_storage_precision,quality=config.processing.quality_mode)
    metadata['storage_precision']=film_precision_record(stored_fields,config.processing.quality_mode,
        config.output.material_storage_precision,metadata.get('storage_precision'))
    payload['developed_medium_metadata']=np.asarray(json.dumps(metadata,ensure_ascii=False,allow_nan=False))
    atomic_savez(path,compressed=str(config.output.medium_npz_compression).strip().lower() == 'compressed',compression_level=1,**payload)


def develop_movie_master(source: Path, output: Path, config: DarkroomConfig | None=None, *, control=None) -> Path:
    """Derive a new optical master; no source latent file is consumed or rewritten."""
    control = control or WorkflowControl()
    root,original = read_master(source)
    if original['stage'] != 'exposed_latent':
        raise ValueError('Development requires an undeveloped material master.')
    output = Path(output).resolve()
    if output.is_relative_to(root) or root.is_relative_to(output):
        raise ValueError('Derived master must be separate from its source master.')
    frozen = DarkroomConfig.from_dict(original['recipe'])
    runtime = copy.deepcopy(config if config is not None else frozen)
    runtime.film = copy.deepcopy(frozen.film)
    runtime.mode = frozen.mode
    runtime.enable_subtractive = frozen.enable_subtractive
    from film_foundry.app.workflow import rng_and_resolved_seed_for_develop
    _,seed = rng_and_resolved_seed_for_develop(root/MANIFEST,runtime)
    runtime = prepare_incident_config(runtime,fallback_seed=seed)
    runtime.random_seed = seed
    runtime.seed_strategy = 'fixed'
    _validate_output_runtime(runtime)
    document = dict(model=MODEL,master_id=str(uuid4()),roll_id=original['roll_id'],stage='developed_medium',
        width=original['width'],height=original['height'],seed=seed,recipe=asdict(runtime),
        source_master_id=original['master_id'],frames=[])
    with _new_master(output,control) as pending:
        for item in original['frames']:
            control.check_cancel()
            check_space(pending)
            state,metadata = load_exposed_latent(root/item['path'])
            expected = dict(roll_id=original['roll_id'],frame_index=item['index'])
            identity = metadata.get('frame_identity') or {}
            if any(identity.get(key) != value for key,value in expected.items()) or state.developability.shape[:2] != (original['height'],original['width']):
                raise ValueError('Latent frame identity or dimensions disagree with the master.')
            control.report('movie_develop','developing')
            medium = develop_negative_from_latent(state,runtime,rng=_frame_rng(seed,item['index'],1))
            attach_frame_identity(medium.metadata,identity)
            for key in ('capture','capture_incident_identity'):
                if key in metadata:
                    if key == 'capture':
                        medium.metadata[key] = copy.deepcopy(metadata[key])
                    else:
                        medium.metadata['film_process_model'][key] = copy.deepcopy(metadata[key])
            suffix = 'positive' if medium.image_polarity == 'positive' else 'negative'
            relative = f"frames/{item['index']:08d}.darkroom_{suffix}.npz"
            control.check_cancel()
            _save_optical(pending/relative,medium,runtime)
            check_space(pending)
            document['frames'].append(dict(item,path=relative))
            del medium,state
        control.check_cancel()
        atomic_write_json(pending/MANIFEST,normalize_master(document))
    return output


def observe_master_frame(source: Path, index: int, config: DarkroomConfig | None=None):
    """Read one final frame. No directory/NPZ write, no per-frame auto levels."""
    root,document = read_master(source)
    if document['stage'] != 'developed_medium':
        raise ValueError('Observation requires a developed optical master.')
    if isinstance(index,bool) or not isinstance(index,int) or not 1 <= index <= len(document['frames']):
        raise ValueError('Movie frame index is out of range.')
    item = document['frames'][index-1]
    medium = load_developed_medium_for_scan(root/item['path'])
    expected = dict(roll_id=document['roll_id'],frame_index=index)
    identity = medium.metadata.get('frame_identity') or {}
    if any(identity.get(key) != value for key,value in expected.items()) or medium.optical_density_rgb.shape[:2] != (document['height'],document['width']):
        raise ValueError('Optical frame identity or dimensions disagree with the master.')
    runtime = copy.deepcopy(config if config is not None else DarkroomConfig.from_dict(document['recipe']))
    runtime.scanner.scan_normalize = False
    from film_foundry.core.engine import configure_scan_interpretation
    configure_scan_interpretation(runtime,'positive' if medium.image_polarity == 'positive' else 'negative')
    return scan_negative(medium,runtime)
