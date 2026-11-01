"""Local catalog entitlement and a small offline puzzle, outside recipe codecs.

The XOR notation is an Easter egg, not encryption or a licensing mechanism.
An unlocked catalog stays unlocked when the puzzle phrase is replaced.
"""
from __future__ import annotations

import json
import os
import re
import secrets
from functools import lru_cache
from pathlib import Path

from film_foundry.app.paths import app_root
from film_foundry.core.atomic_io import atomic_write_json
from film_foundry.app.finder_resources import payload

_SEAL_KEY = b"FilmFoundry:edge"


@lru_cache(maxsize=1)
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


def _read_state() -> dict:
    try:
        data = json.loads(state_path().read_text(encoding="utf-8"))
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


@lru_cache(maxsize=64)
def _materialize(kind: str, key: str, state_file: str) -> Path:
    entries=definition()["entries"]
    field={"film":"material_id","scanner":"scanner_id"}[kind]
    index=next(i for i,e in enumerate(entries) if e[field]==key)
    # Compatibility paths for existing editors and explicit recipe references.
    # Only requested definitions are materialized, outside all preset catalogs.
    path=Path(state_file).parent/".resources"/"finder"/kind/f"{index:02d}.json"
    atomic_write_json(path,payload()["desktop"][kind][key])
    return path


def preset_path(kind: str, key: str) -> Path | None:
    return _materialize(kind,key,str(state_path())) if key in collection_ids(kind) else None


def matching_scanner(material_key: str) -> str | None:
    return next((e["scanner_id"] for e in definition()["entries"] if e["material_id"] == material_key), None)


def localized_name(kind: str, key: str, language: str) -> str | None:
    field = {"film": "material_id", "scanner": "scanner_id"}.get(kind)
    if not field:
        return None
    entry = next((e for e in definition()["entries"] if e[field] == key), None)
    if entry is None:
        return None
    prefix = "scanner_label_" if kind == "scanner" else "label_"
    return entry[prefix + ("zh" if language.startswith("zh") else "en")]
