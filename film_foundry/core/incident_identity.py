"""Independent random streams for explicitly identified roll/batch defects."""
from dataclasses import replace
from pathlib import Path
import zlib

import numpy as np

# Separate streams keep one defect's geometry stable when another is toggled.
INCIDENT_SALTS = {
    "light_leak": 0x1EA50101,
    "process_variation": 0xBA7C0101,
    "uneven_development": 0xBA7C0102,
    "chemical_stain": 0xBA7C0103,
    "silver_plating": 0xBA7C0104,
    "reticulation": 0xBA7C0105,
}


FRAME_COMPONENTS = frozenset(("chemical_stain", "silver_plating", "reticulation"))


def frame_token(identity):
    """Prefer the numbered slot so optional display IDs cannot move a defect."""
    if not identity:
        return None
    if "frame_index" in identity:
        return zlib.crc32(f"index:{identity['frame_index']}".encode("ascii"))
    if "frame_id" in identity:
        return zlib.crc32(("id:" + identity["frame_id"]).encode("utf-8"))
    return None


def bind_incident_frame(config, *, identity=None, stored_seed=None, path=None, source_offset=None):
    if config.chemistry.incident_pattern_scope == "roll":
        return config
    seed = frame_token(identity)
    if seed is None:
        seed = config.chemistry.incident_frame_seed
    if seed is None:
        seed = stored_seed
    if seed is None and path is not None:
        from film_foundry.core.engine import seed_from_path
        seed = source_offset if source_offset is not None else seed_from_path(Path(path).resolve(), 0)
    if seed == config.chemistry.incident_frame_seed:
        return config
    return replace(config, chemistry=replace(config.chemistry, incident_frame_seed=seed))


def local_incident_seed(recipe, roll_seed):
    frame = getattr(recipe, "incident_frame_seed", None)
    if frame is None or getattr(recipe, "incident_pattern_scope", "frame") == "roll":
        return roll_seed
    return int(np.random.SeedSequence([int(roll_seed or 0), frame, 0x4652414D]).generate_state(1)[0])


def validate_incident_seed(seed, name="incident_identity_seed"):
    if seed is None:
        return
    if isinstance(seed, bool) or int(seed) != seed or not 0 <= int(seed) <= 0xFFFFFFFF:
        raise ValueError(f"develop.{name} must be an unsigned 32-bit integer or null")


def incident_rng(recipe, frame_rng, component):
    seed = getattr(recipe, "incident_identity_seed", None)
    if component in FRAME_COMPONENTS:
        seed = local_incident_seed(recipe, seed)
    if seed is None:
        return frame_rng
    validate_incident_seed(seed)
    return np.random.default_rng(int(seed) ^ INCIDENT_SALTS[component])
