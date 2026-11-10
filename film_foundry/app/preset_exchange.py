"""Selective recipe exchange and user library, independent of any frontend.

Processes embed their solutions. Saving a solution to the user's library is
optional and never changes the process's reaction capability or dependencies.
"""
from copy import deepcopy
from dataclasses import asdict
import json
from pathlib import Path

from film_foundry.contracts.config_share import (
    decode_config_share, decode_share_document, encode_config_share, encode_share_document,
)
from film_foundry.contracts.data_kernel import CONFIG_SHARE_KIND
from film_foundry.contracts.bath_solution import validate_solution
from film_foundry.model.config import DarkroomConfig, DEVELOP_LOOK_FIELDS, SCAN_LOOK_FIELDS
from film_foundry.core.atomic_io import atomic_write_json
from film_foundry.app.paths import app_root, preset_root
from film_foundry.core.file_cache import FileRevisionCache

COLLECTION_KIND = 'film_foundry_preset_collection'
KINDS = ('film', 'solution', 'develop', 'scanner', 'capture')
FIELDS = {
    'film': {'film', 'mode', 'medium'},
    'develop': {'develop', 'look', 'enable_grain', 'enable_halation', 'enable_mtf', 'process_control_mode'},
    'scanner': {'scanner', 'look', 'enable_subtractive'},
    'capture': {'exposure', 'film_border'},
}
_files=FileRevisionCache(128)


def preset_document(path):
    return _files.get(path,lambda p:json.loads(p.read_text(encoding='utf-8-sig')),namespace='preset-exchange')


def is_scoped_process(path):
    data=preset_document(path)
    metadata=data.get('preset') if isinstance(data,dict) else None
    return isinstance(metadata,dict) and metadata.get('kind')=='develop'


def checked_item(item):
    if not isinstance(item, dict) or set(item) != {'kind', 'name', 'payload'}:
        raise ValueError('Invalid preset entry')
    kind, name, payload = item['kind'], item['name'], item['payload']
    if kind not in KINDS or not isinstance(name, str) or not name.strip() or len(name) > 120:
        raise ValueError('Invalid preset type or name')
    if any(ord(c) < 32 for c in name) or not isinstance(payload, dict):
        raise ValueError('Invalid preset name or parameters')
    if kind == 'solution':
        validate_solution(payload, payload.get('action'))
    else:
        if not payload or set(payload) - FIELDS[kind]:
            raise ValueError(f'Parameters do not belong to {kind}')
        look = payload.get('look', {})
        allowed = DEVELOP_LOOK_FIELDS if kind == 'develop' else SCAN_LOOK_FIELDS if kind == 'scanner' else ()
        if not isinstance(look, dict) or set(look) - set(allowed):
            raise ValueError('Look parameters belong to another preset category')
        # Exercise the existing contract; retain sparse fields instead of
        # filling them with defaults that would overwrite unselected values.
        DarkroomConfig.from_dict(payload)
    return deepcopy(item)


def split_recipe(payload, name='Shared'):
    """Old recipe codes remain selectable, even if they contain sparse values."""
    result = []
    for kind in ('film', 'develop', 'scanner', 'capture'):
        # Historical process mode selects topology; it is not a material preset.
        if kind == 'film' and 'film' not in payload:continue
        values = {key: deepcopy(value) for key, value in payload.items() if key in FIELDS[kind] and key != 'look'}
        if kind in ('develop', 'scanner'):
            keys = DEVELOP_LOOK_FIELDS if kind == 'develop' else SCAN_LOOK_FIELDS
            look = {key: value for key, value in payload.get('look', {}).items() if key in keys}
            if look:values['look'] = deepcopy(look)
        if kind=='develop' and 'develop' in values and any(key in payload for key in ('mode','film')):
            recipe=DarkroomConfig.from_dict(payload)
            if recipe.chemistry.program_key=='auto' and recipe.chemistry.process_steps is None:
                from film_foundry.core.film_process.recipe import resolve_program_key
                values['develop']['program_key']=resolve_program_key(recipe.chemistry,mode=recipe.mode,material_process=recipe.film.medium_process)
        if values:
            title = values.get('film', {}).get('name') or values.get('develop', {}).get('developer_name') or name
            result.append(checked_item({'kind': kind, 'name': str(title)[:120], 'payload': values}))
    return result


def current_items(config):
    from film_foundry.app.workflow import recipe_share_payload
    from film_foundry.core.film_process.recipe import resolve_program_key
    payload = recipe_share_payload(config)
    payload['exposure'] = asdict(config.exposure)
    # A standalone process must not select a different standard program just
    # because the recipient has a different material. Cross-processing remains
    # deliberate. This resolves a declaration, not a second numerical path.
    recipe = DarkroomConfig.from_dict(payload)
    if recipe.chemistry.program_key == 'auto' and recipe.chemistry.process_steps is None:
        payload['develop']['program_key'] = resolve_program_key(
            recipe.chemistry, mode=recipe.mode, material_process=recipe.film.medium_process)
    payload['process_control_mode'] = config.process_control_mode
    result = split_recipe(payload, 'Current')
    result.extend(solution_items(payload.get('develop', {})))
    return result


def solution_items(recipe):
    result = []
    for key in ('process_steps', 'bath_overrides'):
        for index, row in enumerate(recipe.get(key) or ()):
            solution = row.get('solution')
            if solution and not any(item['payload'] == solution for item in result):
                result.append(checked_item({'kind': 'solution',
                    'name': str(row.get('label') or f"{solution['action']} {index+1}"), 'payload': solution}))
    return result


def encode_items(items):
    entries = [checked_item(item) for item in items]
    if not 1 <= len(entries) <= 64:raise ValueError('Select 1–64 presets')
    for item in entries:_require_portable(item['payload'])
    return encode_share_document({'kind': COLLECTION_KIND, 'version': 1, 'items': entries})


def read_items(code):
    envelope = decode_share_document(code)
    if envelope.get('kind') == CONFIG_SHARE_KIND:
        payload = decode_config_share(code)
        items = split_recipe(payload)
        items.extend(solution_items(payload.get('develop', {})))
        return items
    if (envelope.get('kind') != COLLECTION_KIND or envelope.get('version') != 1
            or set(envelope) != {'kind', 'version', 'items'}):
        raise ValueError('Unsupported preset collection')
    items = envelope['items']
    if not isinstance(items, list) or not 1 <= len(items) <= 64:raise ValueError('Invalid preset collection')
    return [checked_item(item) for item in items]


def legacy_code(items):
    """An optional old-client recipe; solutions alone cannot be made a recipe."""
    payload = {}
    kinds = set()
    for raw in items:
        item = checked_item(raw);kind = item['kind']
        if kind == 'solution' or kind in kinds:raise ValueError('Legacy recipe accepts one preset per category, without standalone solutions')
        kinds.add(kind)
        values = deepcopy(item['payload']);values.pop('process_control_mode', None)
        _require_portable(values)
        _merge(payload, values)
    if not payload:raise ValueError('Select at least one preset')
    return encode_config_share(payload)


def _require_portable(payload):
    if payload.get('develop', {}).get('light_leak_mask_path'):
        raise ValueError('该流程引用外部漏光遮罩。FFC2 不携带图像文件；请取消分享此流程，或改用内置遮罩后再分享。 / External leak masks cannot be embedded in FFC2.')


def _merge(target, source):
    for key, value in source.items():
        if isinstance(value, dict) and isinstance(target.get(key), dict):_merge(target[key], value)
        else:target[key] = deepcopy(value)


def apply_items(config, items):
    """Only selected fields change; files, quality, seeds and output stay local."""
    data = asdict(config);data['develop'] = data.pop('chemistry')
    kinds = set()
    for raw in items:
        item = checked_item(raw);kind = item['kind']
        if kind == 'solution':raise ValueError('Choose a bath in the process editor to apply a solution')
        if kind in kinds:raise ValueError('Only one preset per category can be applied at a time')
        kinds.add(kind)
        _merge(data, item['payload'])
    return DarkroomConfig.from_dict(data)


def library_root():
    return app_root() / 'user_presets'


def save_items(items, *, root=None):
    """Keep selected entries as separate JSON presets; never overwrite a name."""
    from film_foundry.app.workflow import safe_preset_stem
    root = Path(root) if root is not None else library_root()
    entries = [checked_item(item) for item in items]
    paths = []
    try:
        for item in entries:
            folder = root / item['kind'];folder.mkdir(parents=True, exist_ok=True)
            stem = safe_preset_stem(item['name'])[:90] or 'shared'
            path = folder / f'{stem}.json';suffix = 2
            while path.exists():
                path = folder / f'{stem}_{suffix}.json';suffix += 1
            if item['kind'] == 'solution':
                value = {'kind': 'film_foundry_bath_solution', 'version': 1, 'name': item['name'], 'solution': item['payload']}
            else:value = {**item['payload'], 'preset': {'kind': item['kind'], 'version': 1, 'name': item['name']}}
            atomic_write_json(path, value);paths.append(path)
    except Exception:
        for path in paths:path.unlink(missing_ok=True)
        raise
    return paths


def read_preset(path, kind):
    data = preset_document(path)
    if not isinstance(data,dict):raise ValueError('Preset must be an object')
    if kind == 'solution':
        if data.get('kind') != 'film_foundry_bath_solution' or data.get('version') != 1:raise ValueError('Invalid solution preset')
        return checked_item({'kind': kind, 'name': data.get('name'), 'payload': data.get('solution')})
    if 'chemistry' in data:data['develop'] = data.pop('chemistry')
    entries = split_recipe(data, Path(path).stem)
    entry = next((item for item in entries if item['kind'] == kind), None)
    if entry is None:raise ValueError('Preset has no parameters for this category')
    metadata=data.get('preset')
    if isinstance(metadata,dict) and metadata.get('kind')==kind:
        entry['name']=metadata.get('name',entry['name']);checked_item(entry)
    # Old process presets used mode to select an automatic program. Freeze that
    # selection while keeping the receiver's material untouched.
    if kind == 'develop':
        from film_foundry.core.film_process.recipe import resolve_program_key
        config = DarkroomConfig.from_dict(data)
        if config.chemistry.program_key == 'auto' and config.chemistry.process_steps is None:
            entry['payload'].setdefault('develop', {})['program_key'] = resolve_program_key(
                config.chemistry, mode=config.mode, material_process=config.film.medium_process)
    return entry


def library_records(*, root=None):
    root = Path(root) if root is not None else library_root()
    records = []
    for kind in KINDS:
        folders = [(root / kind, False)]
        if kind in ('film', 'develop', 'scanner'):folders.insert(0, (preset_root() / kind, True))
        for folder, builtin in folders:
            for path in sorted(folder.glob('*.json')):
                try:entry = read_preset(path, kind)
                except (OSError, ValueError, TypeError) as exc:
                    records.append({'kind':kind,'name':path.stem,'path':path,'builtin':builtin,'error':str(exc)});continue
                records.append({**entry, 'path':path, 'builtin':builtin})
    from film_foundry.app.seasonal_collection import collection_ids,preset_path,is_unlocked
    for kind in ('film','scanner'):
        for key in sorted(collection_ids(kind)) if is_unlocked() else ():
            path=preset_path(kind,key)
            if path is None:continue
            try:entry=read_preset(path,kind)
            except (OSError,ValueError,TypeError):continue
            if kind=='scanner':entry['name']=key
            records.append({**entry,'path':path,'builtin':True,'key':key})
    return records


def remove_user_preset(record, *, root=None):
    root = (Path(root) if root is not None else library_root()).resolve()
    path = Path(record['path']).resolve()
    if record.get('builtin') or path.parent != root / record['kind'] or path.suffix != '.json':
        raise ValueError('Only a user preset can be removed')
    path.unlink()
