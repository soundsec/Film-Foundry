"""Local catalog entitlement and a small offline puzzle, outside recipe codecs.

The XOR notation is an Easter egg, not encryption or a licensing mechanism.
An unlocked catalog stays unlocked when the puzzle phrase is replaced.
"""
from __future__ import annotations

import json
import os
import re
import secrets
from collections import OrderedDict
from threading import RLock
from pathlib import Path

from film_foundry.app.paths import app_root
from film_foundry.core.atomic_io import atomic_write_json
from film_foundry.app.finder_resources import payload, resource_path
from film_foundry.core.file_cache import FileRevisionCache, file_revision

_SEAL_KEY = b"FilmFoundry:edge"


def definition() -> dict:
    return payload()["collection"]


def seal_phrase(phrase: str) -> str:
    raw = phrase.encode("utf-8")
    if not raw or len(raw) > 256 or phrase != phrase.strip() or any(ord(c) < 32 for c in phrase):
        raise ValueError("Use 1–256 UTF-8 bytes without surrounding whitespace or control characters.")
    return bytes(b ^ _SEAL_KEY[i % len(_SEAL_KEY)] for i, b in enumerate(raw)).hex().upper()


def _phrase_bytes() -> bytes:
    sealed = bytes.fromhex(definition()["sealed_phrase"])
    return bytes(b ^ _SEAL_KEY[i % len(_SEAL_KEY)] for i, b in enumerate(sealed))


def puzzle_cipher(identity: str) -> str:
    if re.fullmatch(r"[0-9A-F]{12}", identity) is None:
        raise ValueError("Invalid installation ID")
    key = identity.encode("ascii")
    return bytes(b ^ key[i % len(key)] for i, b in enumerate(_phrase_bytes())).hex().upper()


def state_path() -> Path:
    override = os.environ.get("FILM_FOUNDRY_SEASONAL_STATE")
    return Path(override) if override else app_root() / "user_presets/seasonal_unlock.json"


_state_cache = FileRevisionCache(8)


def _read_state() -> dict:
    try:
        data = _state_cache.get(state_path(), lambda p: json.loads(p.read_text(encoding="utf-8")))
        if data.get("model") == "seasonal_unlock_v1" and re.fullmatch(r"[0-9A-F]{12}", data.get("identity", "")):
            return data
    except (OSError, ValueError, TypeError, AttributeError):
        pass
    return {}


def installation_state() -> dict:
    data = _read_state()
    if not data:
        data = {"model": "seasonal_unlock_v1", "identity": secrets.token_hex(6).upper(), "unlocked": False}
        atomic_write_json(state_path(), data)
    return data


def is_unlocked() -> bool:
    return _read_state().get("unlocked") is True


def try_unlock(text: str) -> bool:
    # UI dispatches here before its ordinary FFC2 parser. No recipe is mutated.
    if not text or len(text) > 256:
        return False
    try:
        matches = seal_phrase(text.strip()) == definition()["sealed_phrase"]
    except ValueError:
        return False
    if not matches:
        return False
    data = installation_state()
    data["unlocked"] = True
    atomic_write_json(state_path(), data)
    return True


def collection_ids(kind: str) -> frozenset[str]:
    field = {"film": "material_id", "scanner": "scanner_id"}.get(kind)
    return frozenset(item[field] for item in definition()["entries"]) if field else frozenset()


def visible_builtin_names(names: set[str], kind: str) -> set[str]:
    hidden=collection_ids(kind)
    return (names | hidden) if is_unlocked() else names - hidden


_materialized = OrderedDict()
_materialize_lock = RLock()


def _materialize(kind: str, key: str, state_file: str) -> Path:
    entries=definition()["entries"]
    field={"film":"material_id","scanner":"scanner_id"}[kind]
    index=next(i for i,e in enumerate(entries) if e[field]==key)
    # Compatibility paths for existing editors and explicit recipe references.
    # Only requested definitions are materialized, outside all preset catalogs.
    path=Path(state_file).parent/".resources"/"finder"/kind/f"{index:02d}.json"
    identity = (kind, key, str(path.resolve()))
    with _materialize_lock:
        resource_revision = file_revision(resource_path())
        try: current = file_revision(path)
        except OSError: current = None
        if _materialized.get(identity) != (resource_revision, current):
            expected = payload()["desktop"][kind][key]
            try: unchanged = current is not None and json.loads(path.read_text(encoding='utf-8')) == expected
            except (OSError, ValueError): unchanged = False
            if not unchanged:atomic_write_json(path,expected)
            _materialized[identity] = (resource_revision, file_revision(path))
        _materialized.move_to_end(identity)
        while len(_materialized) > 64:
            _materialized.popitem(last=False)
    return path


def preset_path(kind: str, key: str) -> Path | None:
    return _materialize(kind,key,str(state_path())) if key in collection_ids(kind) else None


def matching_scanner(material_key: str) -> str | None:
    return next((e["scanner_id"] for e in definition()["entries"] if e["material_id"] == material_key), None)


def localized_description(kind: str, key: str, language: str) -> str:
    if key not in collection_ids(kind):return ''
    season = key.split('_')[1]
    descriptions = {
        'spring': ('柔和高光、偏粉的细颗粒与较宽的层间光晕。', 'Soft highlights, pink-tinted fine grain and broader layer-selective halation.'),
        'summer': ('暖色高光、较清晰的边缘与紧凑的琥珀色光晕。', 'Warm highlights, clearer edges and compact amber halation.'),
        'autumn': ('浓烈金黄与火红，橙黄溢彩、绿转橄榄，保留冷色对照；配套扫描进一步增强秋叶色彩。', 'Intense golds and fiery reds, radiant oranges and olive greens with cool colour contrast; the companion scan intensifies autumn foliage colours.'),
        'winter': ('冷色层次、较细颗粒与很轻的高光扩散。', 'Cool separation, finer grain and restrained highlight spread.'),
    }
    zh, en = descriptions[season]
    if kind == 'film':
        zh += ' 材料定义含独立储存响应，只有设置储存历史才会产生老化；开启联动后自动匹配专属扫描，也可独立选择中性扫描。'
        en += ' Storage response activates only with explicit storage history. Linking selects its companion scan; neutral observation remains available independently.'
    return en if language.startswith('en') else zh


def localized_name(kind: str, key: str, language: str) -> str | None:
    field = {"film": "material_id", "scanner": "scanner_id"}.get(kind)
    if not field:
        return None
    entry = next((e for e in definition()["entries"] if e[field] == key), None)
    if entry is None:
        return None
    prefix = "scanner_label_" if kind == "scanner" else "label_"
    return entry[prefix + ("zh" if language.startswith("zh") else "en")]
