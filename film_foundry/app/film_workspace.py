"""Portable film sources and complete recipes, never a mid-bath checkpoint.

Original bytes are copied once, without decoding or recompressing images. The
manifest owns only its listed files; replacement refuses foreign content.
"""
from copy import deepcopy
from dataclasses import dataclass
import json
from pathlib import Path
import shutil

from film_foundry.app.source_seed import normalize_offsets, offset_for, validate_offset
from film_foundry.core.atomic_io import atomic_output_directory, atomic_write_json, strict_json_load
from film_foundry.core.engine import seed_from_path
from film_foundry.core.frame_identity import normalize_frame_identity
from film_foundry.model.config import DarkroomConfig
from film_foundry.model.session import SESSION_KIND, SESSION_VERSION

MODEL = 'film_workspace_v1'
MODES = ('full', 'develop', 'expose', 'develop_latent', 'scan')


@dataclass(frozen=True)
class FilmWorkspace:
    session: dict
    sources: tuple[Path, ...]
    active: int = 0
    layout: str = 'compact'
    source_names: tuple[str, ...] = ()


def _session(value):
    if not isinstance(value, dict) or value.get('kind') != SESSION_KIND or value.get('version') != SESSION_VERSION:
        raise ValueError('Invalid film session')
    if value.get('pipeline_mode') not in MODES or not isinstance(value.get('config'), dict):
        raise ValueError('Invalid film operation or recipe')
    DarkroomConfig.from_dict(value['config'])
    normalize_frame_identity(value.get('frame_identity'))
    return deepcopy(value)


def snapshot_film_workspace(session, sources, *, active=0, layout='compact', source_names=None):
    payload = _session(session)
    original_paths = tuple(Path(p) for p in sources)
    paths = tuple(p.absolute() for p in original_paths)
    if not paths or len(set(paths)) != len(paths):
        raise ValueError('Select distinct film sources before saving')
    if type(active) is not int or not 0 <= active < len(paths):
        raise ValueError('Invalid active source')
    if layout not in ('compact', 'studio'):
        raise ValueError('Invalid film workspace layout')
    offsets = normalize_offsets(payload.get('path_seed_offsets'))
    payload['path_seed_offsets'] = {
        str(p.resolve()): offset_for(p, offsets) if offset_for(p, offsets) is not None else seed_from_path(original, 0)
        for p, original in zip(paths, original_paths)
    }
    names = tuple(_filename(name) for name in source_names) if source_names is not None else tuple(p.name for p in paths)
    if len(names) != len(paths):
        raise ValueError('Invalid film source names')
    return FilmWorkspace(payload, paths, active, layout, names)


def _inside(path, root):
    if path.resolve() != path.absolute() or not path.resolve().is_relative_to(root.resolve()):
        raise ValueError('Film workspace links are not supported')
    return path


def _filename(value):
    # One generated filename, never an absolute path, stream or directory traversal.
    if (not isinstance(value, str) or not value or Path(value).name != value
            or any(c in value for c in '/\\:') or value in ('.', '..')
            or value.endswith((' ', '.'))):
        raise ValueError('Invalid film source filename')
    return value


def _read_package(root):
    _inside(root, root)
    file = _inside(root / 'workspace.json', root)
    if file.stat().st_size > 16 * 1024 * 1024:
        raise ValueError('Film workspace metadata is too large')
    manifest = strict_json_load(file)
    if not isinstance(manifest, dict) or manifest.get('model') != MODEL:
        raise ValueError('Not a supported film workspace')
    _session(manifest.get('session'))
    entries = manifest.get('sources')
    if not isinstance(entries, list) or not entries:
        raise ValueError('Film workspace has no sources')
    active = manifest.get('active')
    if type(active) is not int or not 0 <= active < len(entries) or manifest.get('layout') not in ('compact', 'studio'):
        raise ValueError('Invalid film workspace presentation')
    expected = set()
    for index, entry in enumerate(entries):
        if not isinstance(entry, dict) or validate_offset(entry.get('seed_offset')) is None:
            raise ValueError('Invalid film source identity')
        name = _filename(entry.get('file'))
        if name != f'{index + 1:06d}_{_filename(entry.get("name"))}':
            raise ValueError('Invalid ordered film source')
        names = [name]
        if entry.get('sidecar') is not None:
            if entry['sidecar'] != name + '.json':
                raise ValueError('Invalid film source sidecar')
            names.append(entry['sidecar'])
        for name in names:
            if name.casefold() in expected:
                raise ValueError('Duplicate film source filename')
            expected.add(name.casefold())
            if not _inside(root / 'sources' / name, root).is_file():
                raise ValueError('Film source is missing')
    directory = _inside(root / 'sources', root)
    if ({p.name for p in root.iterdir()} != {'workspace.json', 'sources'}
            or {p.name.casefold() for p in directory.iterdir()} != expected):
        raise ValueError('Workspace contains unrelated files; choose another save location')
    return manifest


def save_film_workspace(path, workspace):
    target = Path(path).absolute()
    if target.suffix.lower() != '.fffilm':
        raise ValueError('Choose a .fffilm directory package')
    _inside(target, target)
    if target.exists():
        _read_package(target)
    workspace = snapshot_film_workspace(workspace.session, workspace.sources, active=workspace.active,
        layout=workspace.layout, source_names=workspace.source_names or None)
    payload = deepcopy(workspace.session)
    offsets = payload.pop('path_seed_offsets')
    # Complete settings are authoritative; historical preset paths/run outputs
    # must never become dependencies or write destinations on another computer.
    payload.pop('run', None); payload.pop('batch', None)
    payload['paths'] = {'input': '', 'developed_medium': '', 'negative': '', 'output': ''}
    payload['presets'] = {}
    payload.setdefault('ui_options', {})['scan_output_near_source'] = False
    manifest = {'model': MODEL, 'session': payload, 'active': workspace.active,
                'layout': workspace.layout, 'sources': []}
    from film_foundry.app.workflow import is_scanner_raw_tiff
    with atomic_output_directory(target) as staging:
        directory = staging / 'sources'; directory.mkdir()
        for index, source in enumerate(workspace.sources):
            if not source.is_file():
                raise ValueError(f'Film source is missing: {source.name}')
            # Prefixes retain explicit order and prevent equal basenames colliding.
            name = _filename(f'{index + 1:06d}_{workspace.source_names[index]}')
            before = source.stat()
            shutil.copy2(source, directory / name)
            after = source.stat()
            if (before.st_size, before.st_mtime_ns) != (after.st_size, after.st_mtime_ns):
                raise ValueError(f'Film source changed while saving: {source.name}')
            entry = {'file': name, 'name': workspace.source_names[index], 'seed_offset': offset_for(source, offsets)}
            sidecar = source.with_suffix(source.suffix + '.json')
            if is_scanner_raw_tiff(source) and sidecar.is_file():
                shutil.copy2(sidecar, directory / (name + '.json'))
                entry['sidecar'] = name + '.json'
            manifest['sources'].append(entry)
        # Validate JSON before the atomic directory writer touches the old package.
        encoded = json.dumps(manifest, ensure_ascii=False, allow_nan=False)
        if len(encoded.encode('utf-8')) > 16 * 1024 * 1024:
            raise ValueError('Film workspace metadata is too large')
        atomic_write_json(staging / 'workspace.json', manifest)
    return target


def load_film_workspace(path):
    root = Path(path).absolute()
    if root.name == 'workspace.json':
        root = root.parent
    manifest = _read_package(root)
    payload = deepcopy(manifest['session'])
    sources = tuple(root / 'sources' / entry['file'] for entry in manifest['sources'])
    active = manifest['active']; mode = payload['pipeline_mode']
    source = sources[active] if len(sources) == 1 or mode == 'full' else root / 'sources'
    paths = payload['paths'] = {'input': '', 'developed_medium': '', 'negative': '',
        'output': str(root.parent / (root.stem + '-results'))}
    if mode in ('full', 'develop', 'expose'):
        paths['input'] = str(source)
    else:
        paths['developed_medium'] = paths['negative'] = str(source)
    payload['path_seed_offsets'] = {str(p.resolve()): entry['seed_offset'] for p, entry in zip(sources, manifest['sources'])}
    payload.setdefault('ui_options', {})['scan_output_near_source'] = False
    return FilmWorkspace(payload, sources, active, manifest['layout'], tuple(entry['name'] for entry in manifest['sources']))


def start_film_workspace_run(workspace, output, *, control=None, on_result=None):
    """Create a durable run plan; the existing batch resume API executes it."""
    from film_foundry.app.replay_batch import load_resume_plan, run_resume_batch
    from film_foundry.app.workflow import create_run_directory, output_path_for, latent_path_for, planned_developed_path
    from film_foundry.core.media import developed_medium_contract
    from film_foundry.model.session import save_session
    if control: control.check_cancel()
    payload = _session(workspace.session)
    config = DarkroomConfig.from_dict(payload['config']); mode = payload['pipeline_mode']
    root, run_id = create_run_directory(Path(output), mode)
    destination = root / ('latent' if mode == 'expose' else 'medium' if mode in ('develop', 'develop_latent') else 'images')
    destination.mkdir()
    items = []
    polarity = developed_medium_contract(config)['image_polarity']
    for index, source in enumerate(workspace.sources):
        if mode == 'full': target = destination / f'{index + 1:06d}' / ('scan.' + config.output.format.lower().lstrip('.'))
        elif mode == 'scan': target = output_path_for(source, destination, config.output.format)
        elif mode == 'expose': target = latent_path_for(source, destination)
        else: target = planned_developed_path(source, destination, image_polarity=polarity)
        items.append({'input': str(source), 'output': str(target), 'status': 'pending', 'elapsed_seconds': 0.})
    payload['run'] = {'id': run_id, 'directory': str(root), 'actual_output': str(destination),
        'status': 'pending', 'inputs': [str(p) for p in workspace.sources], 'selection_layout': 'numbered'}
    payload['batch'] = {'operation': mode, 'status': 'pending', 'items': items}
    manifest = root / 'film_foundry_run.json'; save_session(manifest, payload)
    return manifest, run_resume_batch(load_resume_plan(manifest), control=control, on_result=on_result)
