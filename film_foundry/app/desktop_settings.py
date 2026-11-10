"""Local desktop workspace policy. No image ownership or numerical defaults live here."""
from copy import deepcopy
import os
from pathlib import Path
from time import time

from film_foundry.app.paths import app_root
from film_foundry.core.atomic_io import atomic_write_json, strict_json_load


def settings_path():
    return Path(os.environ.get('FILM_FOUNDRY_DESKTOP_SETTINGS',
        Path(os.environ.get('FILM_FOUNDRY_UI_SETTINGS', app_root()/'user_presets'/'desktop_ui.json')).with_name('desktop_workspace.json')))


def load_settings():
    defaults = dict(workspace=str(app_root()), source_folders=[], recent_work=[], leave_policy='ask',
                    fast_mode=False, quality='standard', expert=False, expert_mode='expected',
                    resize=False, long_edge=1600, sidebar_items={}, hidden_folders=[])
    path = settings_path()
    if path.is_file():
        value = strict_json_load(path)
        if not isinstance(value, dict):
            raise ValueError('Invalid desktop settings')
        defaults.update({k: v for k, v in value.items() if k in defaults})
    return defaults


def save_settings(value):
    if value['leave_policy'] not in ('ask', 'save', 'discard'):
        raise ValueError('Unknown work saving policy')
    if value['quality'] not in ('draft', 'standard', 'high') or value['expert_mode'] not in ('expected', 'principle'):
        raise ValueError('Unknown workspace default')
    if not 64 <= int(value['long_edge']) <= 65536:
        raise ValueError('Invalid formation edge')
    value = deepcopy(value)
    value['workspace'] = str(Path(value['workspace']).expanduser().resolve())
    value['source_folders'] = list(dict.fromkeys(str(Path(p).expanduser().resolve()) for p in value['source_folders']))
    active_folders = {sidebar_key('folder', p) for p in value['source_folders']}
    value['hidden_folders'] = [key for key in value.get('hidden_folders', []) if key not in active_folders]
    atomic_write_json(settings_path(), value)


def remember_work(path, kind, label=None):
    value = load_settings()
    path = str(Path(path).resolve())
    previous=next((r for r in value['recent_work'] if os.path.normcase(r['path'])==os.path.normcase(path)),{})
    recent = [r for r in value['recent_work'] if os.path.normcase(r['path']) != os.path.normcase(path)]
    value['recent_work'] = [dict(path=path, kind=kind, label=label or previous.get('label') or Path(path).stem, time=time()), *recent][:40]
    save_settings(value)


def work_directory():
    return Path(load_settings()['workspace'])/'sessions'


def sidebar_key(kind, path):
    return kind + ':' + os.path.normcase(str(Path(path).resolve()))


def sidebar_entry(kind, path, values=None):
    values = load_settings() if values is None else values
    return values['sidebar_items'].get(sidebar_key(kind, path), {})


def update_sidebar_entry(kind, path, *, label=None, locked=None, remove=False):
    """Manage navigation references and aliases, never rename or remove files."""
    if kind not in ('folder', 'work'):
        raise ValueError('Unknown sidebar entry')
    values = load_settings()
    key = sidebar_key(kind, path)
    entry = dict(values['sidebar_items'].get(key, {}))
    if entry.get('locked') and (label is not None or remove):
        raise ValueError('Unlock this sidebar entry first')
    if label is not None:
        label = label.strip()
        if not label:
            raise ValueError('A display name is required')
        entry['label'] = label
    if locked is not None:
        entry['locked'] = bool(locked)
    values['sidebar_items'][key] = entry
    if remove:
        if kind == 'folder':
            values['source_folders'] = [p for p in values['source_folders'] if sidebar_key(kind, p) != key]
            if key not in values['hidden_folders']:
                values['hidden_folders'].append(key)
        else:
            values['recent_work'] = [r for r in values['recent_work'] if sidebar_key(kind, r['path']) != key]
    save_settings(values)
