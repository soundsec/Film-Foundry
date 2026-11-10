"""Portable path identities at the application boundary, independent of pixels."""
from pathlib import Path


def validate_offset(value):
    if value is not None and (type(value) is not int or not 0 <= value < 2**32):
        raise ValueError('Invalid source path seed offset')
    return value


def normalize_offsets(value):
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise ValueError('Source path seed offsets must be an object')
    result = {}
    for path, offset in value.items():
        if not isinstance(path, str) or not path or offset is None:
            raise ValueError('Invalid source path seed binding')
        result[str(Path(path).resolve())] = validate_offset(offset)
    return result


def offset_for(path, offsets):
    return validate_offset((offsets or {}).get(str(Path(path).resolve())))


def source_seed_override(config, offset):
    """Resolve only path mode; a later user base-seed edit still takes effect.

    An offset is the existing path-derived seed with base zero. This does not
    read/hash any source bytes, reseed incident identities, or freeze random mode.
    """
    validate_offset(offset)
    if offset is None or str(config.seed_strategy).lower() != 'path':
        return None
    return (offset + int(config.random_seed or 0)) % 2**32
