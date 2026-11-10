"""Portable recipe-parameter share code.

This is configuration identity, not a developed medium and not a preset
filename. If both sides already have the JSON file, share that file. The
codec carries the actual film / develop / scanner / look values so a custom
setup can be copied as one string. It uses only the standard library so
Android can reimplement it without importing the desktop runtime.
"""

from __future__ import annotations

import base64
import json
import zlib
from collections.abc import Mapping

from film_foundry.contracts.data_kernel import (
    CONFIG_SHARE_FIELDS,
    CONFIG_SHARE_KIND,
    CONFIG_SHARE_PREFIX,
    CONFIG_SHARE_VERSION,
)

_MAX_DECODED_BYTES = 262144
_MAX_INPUT_CHARS = 524288
_ALLOWED_FIELDS = frozenset(CONFIG_SHARE_FIELDS)
_FIELD_ALIASES = {"chemistry": "develop"}
_TOKEN_CHARS = frozenset(
    "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789_-"
)


def _jsonable(value: object) -> object:
    if value is None or isinstance(value, (bool, int, float, str)):
        if isinstance(value, float) and (
            value != value or value in {float("inf"), float("-inf")}
        ):
            raise ValueError("config share cannot contain NaN or Infinity.")
        return value
    if isinstance(value, Mapping):
        return {str(key): _jsonable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonable(item) for item in value]
    raise ValueError(f"config share cannot encode {type(value).__name__}.")


_OBJECT_FIELDS = frozenset({"film", "exposure", "develop", "scanner", "look", "film_border"})


def _normalize_payload(config: object) -> dict[str, object]:
    if not isinstance(config, Mapping):
        raise ValueError("config share payload must be an object.")
    payload: dict[str, object] = {}
    for raw_key, value in config.items():
        key = _FIELD_ALIASES.get(str(raw_key), str(raw_key))
        if key not in _ALLOWED_FIELDS:
            raise ValueError(f"config share rejects unknown field {raw_key!r}.")
        if key in payload:
            raise ValueError(f"config share field {key!r} was supplied twice.")
        converted = _jsonable(value)
        if key in _OBJECT_FIELDS and not isinstance(converted, dict):
            raise ValueError(f"config share {key} must be an object.")
        payload[key] = converted
    return payload


def encode_config_share(config: Mapping[str, object]) -> str:
    """Encode recipe parameter values as one copyable string."""
    envelope = {
        "kind": CONFIG_SHARE_KIND,
        "version": CONFIG_SHARE_VERSION,
        "config": _normalize_payload(config),
    }
    return encode_share_document(envelope)


def encode_share_document(envelope: Mapping[str, object]) -> str:
    """Common bounded FFC2 transport; document semantics belong to its codec."""
    raw = json.dumps(
        envelope,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
        allow_nan=False,
    ).encode("utf-8")
    if len(raw) > _MAX_DECODED_BYTES:
        raise ValueError("config share payload is too large.")
    token = base64.urlsafe_b64encode(zlib.compress(raw, 9)).decode("ascii").rstrip("=")
    return f"{CONFIG_SHARE_PREFIX}{token}"


def decode_config_share(text: object) -> dict[str, object]:
    """Decode an FFC2 share code into a recipe parameter object."""
    envelope = decode_share_document(text)
    if envelope.get("kind") != CONFIG_SHARE_KIND:
        raise ValueError("This FFC2 document is a preset collection; open it in Materials & presets.")
    if envelope.get("version") != CONFIG_SHARE_VERSION:
        raise ValueError(f"unsupported config share version {envelope.get('version')!r}.")
    return _normalize_payload(envelope.get("config"))


def decode_share_document(text: object) -> dict[str, object]:
    """Decode the bounded transport without guessing how a document is applied."""
    source = str(text or "")
    if len(source) > _MAX_INPUT_CHARS:
        raise ValueError("config share payload is too large.")
    raw = "".join(source.split()).lstrip("\ufeff")
    if raw.startswith("FFC1:"):
        raise ValueError(
            "preset-name share codes are not supported; share the JSON file "
            "or encode the parameter values."
        )
    if not raw.startswith(CONFIG_SHARE_PREFIX):
        raise ValueError(
            f"config share must start with {CONFIG_SHARE_PREFIX!r}."
        )
    body = raw[len(CONFIG_SHARE_PREFIX) :]
    if not body:
        raise ValueError("config share payload is empty.")
    if "/" in body or "\\" in body:
        raise ValueError(
            "config share must carry parameter values, not preset names."
        )
    if any(char not in _TOKEN_CHARS for char in body):
        raise ValueError("config share token is not url-safe base64.")
    padded = body + ("=" * ((4 - len(body) % 4) % 4))
    try:
        blob = base64.urlsafe_b64decode(padded.encode("ascii"))
        decoder = zlib.decompressobj()
        decoded = decoder.decompress(blob, max_length=_MAX_DECODED_BYTES)
        if decoder.unconsumed_tail or not decoder.eof:
            raise ValueError("config share payload is too large.")
        if decoder.unused_data:
            raise ValueError("Trailing compressed data in config share.")
    except (ValueError, zlib.error) as exc:
        if str(exc) == "config share payload is too large.":
            raise
        raise ValueError("config share payload is not valid compressed JSON.") from exc
    try:
        envelope = json.loads(decoded.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError("config share payload is not valid JSON.") from exc
    if not isinstance(envelope, dict):
        raise ValueError("config share envelope must be an object.")
    return _jsonable(envelope)
