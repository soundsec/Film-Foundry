"""Freeze a shared incident identity before dispatch, recording or preview.

This is batch coordination, not a second set of numerical defaults. Fixed/path
requests and declared rolls are replayable. Random batches sample once; the
prepared recipe is persisted by the ordinary session/manifest writers.
"""
import copy
import secrets
import zlib
from pathlib import Path


def new_incident_seed():
    return secrets.randbits(32)


def prepare_incident_config(config, *, roll_id=None, source=None, fallback_seed=None):
    if config.chemistry.incident_identity_seed is not None:
        return config
    config=copy.deepcopy(config)
    base=0 if config.random_seed is None else int(config.random_seed) & 0xFFFFFFFF
    if roll_id:
        # CRC only maps an identity string to a seed; no image/file checksums.
        seed=zlib.crc32(str(roll_id).encode("utf-8"),base)
    elif config.seed_strategy == "fixed":
        seed=base
    elif config.seed_strategy == "path" and source is not None:
        path=Path(source).resolve()
        scope=path.parent if path.is_file() else path
        seed=zlib.crc32(str(scope).encode("utf-8"),base)
    else:
        seed=new_incident_seed() if fallback_seed is None else int(fallback_seed)
    config.chemistry.incident_identity_seed=seed
    return config
