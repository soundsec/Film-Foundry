"""Read-only film task context from explicit provenance, never reconstructed pixels.

This is application navigation data. Formation and scan execution remain in the
public workflow API. Only JSON descriptors are read from NPZ archives here.
"""
from copy import deepcopy
from pathlib import Path
import zipfile

import numpy as np

from film_foundry.app.catalog_assets import import_record, path_key
from film_foundry.core.atomic_io import strict_json_load, strict_json_loads
from film_foundry.core.latent_io import LATENT_JSON_KEY, LATENT_KIND, LATENT_STAGE


def _metadata(path):
    with np.load(path, allow_pickle=False) as archive:
        key = LATENT_JSON_KEY if LATENT_JSON_KEY in archive.files else 'developed_medium_metadata'
        if key not in archive.files:return {}
        value = strict_json_loads(str(archive[key].item()))
        if not isinstance(value, dict):raise ValueError('Invalid film metadata')
        return value


def available_latent(path):
    """Check the declared latent stage; the execution loader validates pools later."""
    path = Path(path)
    if not path.is_file() or path.suffix.lower() != '.npz':return False
    try:
        value = _metadata(path)
        return value.get('kind') == LATENT_KIND and value.get('stage') == LATENT_STAGE and not value.get('consumed', False)
    except (ValueError, OSError, KeyError, zipfile.BadZipFile):return False


def _reference(value, parent):
    if not isinstance(value, str) or not value.strip():return None
    path = Path(value)
    return path.resolve() if path.is_absolute() else (parent / path).resolve()


def film_task_context(path, catalog=None):
    """Resolve only recorded direct parents. A sibling latent is not evidence."""
    path = Path(path).resolve()
    metadata = _metadata(path)
    kind = 'latent' if metadata.get('kind') == LATENT_KIND else 'medium'
    sidecar_path = path.with_suffix(path.suffix + '.json')
    sidecar = strict_json_load(sidecar_path) if sidecar_path.is_file() else {}
    if not isinstance(sidecar, dict):raise ValueError('Invalid film sidecar')
    record = None; stack_id = None; parents = []
    if catalog is not None:
        for stack in catalog.results:
            for run in stack['runs']:
                if path_key(run['medium']) != path_key(path):continue
                stack_id = stack['id']
                if record is None:record = deepcopy(run)
                parent = _reference(run.get('source'), path.parent)
                if parent and parent != path:parents.append(parent)
        if stack_id is None:
            stack_id = next((stack['id'] for stack in catalog.results
                             if any(path_key(run.get('source','')) == path_key(path) for run in stack['runs'])), None)
    if record is None:
        record = import_record(path)
        parent = _reference(record.get('source'), path.parent)
        if parent and parent != path:parents.append(parent)
    parent = _reference(sidecar.get('input_path'), sidecar_path.parent)
    if parent and parent != path:
        parents.append(parent)
        if not record.get('source') or path_key(record['source']) == path_key(path):
            record['source'] = str(parent)
            # A scan imported before its formation record points to itself.
            # This is now a recovered formation record, not a fresh scan update.
            if record.get('command') == 'scan':record.pop('command')
    parents = list(dict.fromkeys(parents))
    continuation=metadata.get('film_process_continuation')
    if isinstance(continuation,dict):
        parent=_reference(continuation.get('source_path'),path.parent)
        if parent and parent!=path and parent not in parents:parents.append(parent)
    latents = [str(p) for p in parents if available_latent(p)] if kind == 'medium' else []
    config = sidecar.get('config')
    return {'path': str(path), 'kind': kind, 'metadata': metadata,
            'config': deepcopy(config) if isinstance(config, dict) else None,
            'parents': [str(p) for p in parents], 'latents': latents,
            'record': record, 'stack_id': stack_id}


def related_film_stack(path, catalog):
    """Find a known source, checkpoint or medium without walking the filesystem."""
    key=path_key(path)
    if not key:return None
    return next((stack for stack in catalog.results
                 if any(key in (path_key(run.get('source')),path_key(run['medium']))
                        for run in stack['runs'])),None)


def redevelopment_config(context, current):
    """Start from the recorded recipe while retaining this machine's execution/output choices."""
    from film_foundry.model.config import DarkroomConfig
    stored = context.get('config')
    if not stored:return deepcopy(current)
    config = DarkroomConfig.from_dict(stored)
    config.processing = deepcopy(current.processing)
    config.fast_mode = current.fast_mode
    config.output = deepcopy(current.output)
    return config


def scan_version_context(path, catalog=None):
    """Find a saved scan's recipe and explicit medium; never infer from its pixels.

    Full photo jobs record the medium in their result manifest, while separate
    scans record it in the sidecar. Both must refer to this particular image.
    """
    path = Path(path).resolve()
    context = {'path': str(path), 'medium': None, 'config': None, 'problem': None}
    sidecar = path.with_suffix(path.suffix + '.json')
    if not path.is_file():return dict(context, problem='missing_image')
    if not sidecar.is_file():return dict(context, problem='missing_record')
    try:
        value = strict_json_load(sidecar)
        if not isinstance(value, dict) or value.get('kind') != 'ScannedPositive':
            return dict(context, problem='unsupported_record')
        config = value.get('config')
        if not isinstance(config, dict) or not isinstance(config.get('scanner'), dict) or not config['scanner']:
            return dict(context, problem='missing_recipe')
        # Validate the saved recipe before a frontend replaces any current work.
        from film_foundry.model.config import DarkroomConfig
        DarkroomConfig.from_dict(config)
        parents = []
        recorded = value.get('paths') if isinstance(value.get('paths'), dict) else {}
        for key in ('scan_source_path', 'negative_path'):
            parent = _reference(value.get(key) or recorded.get(key), sidecar.parent)
            if parent:
                parents.append(parent)
                break  # The actual scan source takes precedence over an input alias.
        if not parents:
            if catalog is not None:
                for stack in catalog.results:
                    for run in stack['runs']:
                        if any(path_key(p) == path_key(path) for p in run.get('artifacts', {}).values()):
                            parents.append(Path(run['medium']).resolve())
            if not parents:
                try:record = import_record(path)
                except ValueError:record = None
                if record:parents.append(Path(record['medium']).resolve())
        parents = list(dict.fromkeys(parents))
        if len(parents) != 1:return dict(context, problem='missing_medium' if not parents else 'ambiguous_medium')
        medium = parents[0]
        if not medium.is_file():return dict(context, problem='missing_medium')
        from film_foundry.app.catalog_assets import inspect_archive
        if medium.suffix.lower() != '.npz' or inspect_archive(medium).get('kind') != 'medium':
            return dict(context, problem='unsupported_source')
        return dict(context, medium=str(medium), config=deepcopy(config))
    except (ValueError, TypeError, OSError, KeyError, zipfile.BadZipFile) as exc:
        return dict(context, problem='invalid_record', detail=str(exc))


def rescan_config(context, current):
    """Restore observation and encoding, keeping this machine's execution policy."""
    from film_foundry.model.config import DarkroomConfig
    if context.get('problem') or not context.get('config'):
        raise ValueError('The scan recipe or its source medium is unavailable')
    config = DarkroomConfig.from_dict(context['config'])
    config.processing = deepcopy(current.processing)
    config.fast_mode = current.fast_mode
    for key in ('encode_tile_rows', 'encode_tile_threshold_megapixels', 'medium_npz_compression',
                'material_storage_precision', 'medium_preview_long_edge', 'save_scanner_raw',
                'export_layer_pack', 'export_transparent_plate', 'export_plate_set'):
        setattr(config.output, key, deepcopy(getattr(current.output, key)))
    return config
