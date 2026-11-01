"""Current desktop camera identities, independent of film and processing batches.

The profile is application state. Prepared recipes carry its explicit seeds so
headless replay never needs this local file. No numerical defaults live here.
"""
from __future__ import annotations

import copy
from dataclasses import dataclass
import json
import os
from pathlib import Path
import secrets
import tempfile

from film_foundry.app.paths import app_root


@dataclass(frozen=True)
class CameraIdentity:
    structure_seed: int
    gate_seed: int

    def __post_init__(self):
        for value in (self.structure_seed, self.gate_seed):
            if type(value) is not int or not 0 <= value <= 0xFFFFFFFF:
                raise ValueError("Camera identities must be unsigned 32-bit integers")


def preserve_legacy_camera_identity(config):
    """Make an imported common identity explicit, preserving its original streams."""
    common=config.chemistry.incident_identity_seed
    if common is None:
        return config
    from film_foundry.core.incident_identity import INCIDENT_SALTS
    return prepare_camera_config(config,CameraIdentity(int(common) ^ INCIDENT_SALTS["light_leak"],int(common)))


def prepare_camera_config(config, identity: CameraIdentity):
    """Explicit imported recipe identities take priority; never edit the caller."""
    chemistry = config.chemistry
    if chemistry.light_leak_identity_seed is not None and chemistry.exposure_occlusion_identity_seed is not None:
        return config
    prepared = copy.deepcopy(config)
    if chemistry.light_leak_identity_seed is None:
        prepared.chemistry.light_leak_identity_seed = identity.structure_seed
    if chemistry.exposure_occlusion_identity_seed is None:
        prepared.chemistry.exposure_occlusion_identity_seed = identity.gate_seed
    return prepared


class CameraProfileStore:
    def __init__(self, path: Path | None = None):
        override = os.environ.get("FILM_FOUNDRY_CAMERA_SETTINGS")
        self.path = Path(path) if path is not None else Path(override) if override else app_root() / "user_presets" / "desktop_camera.json"

    def current(self) -> CameraIdentity:
        if not self.path.exists():
            identity = CameraIdentity(secrets.randbits(32), secrets.randbits(32))
            self.save(identity)
            return identity
        if self.path.stat().st_size > 4096:
            raise ValueError("Camera profile is too large; existing file preserved")
        value = json.loads(self.path.read_text(encoding="utf-8-sig"))
        if not isinstance(value, dict) or set(value) != {"version", "structure_seed", "gate_seed"} or type(value["version"]) is not int or value["version"] != 1:
            raise ValueError("Unsupported camera profile; existing file preserved")
        return CameraIdentity(value["structure_seed"], value["gate_seed"])

    def save(self, identity: CameraIdentity):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        pending = None
        try:
            with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=self.path.parent,
                                             prefix=".desktop_camera_", suffix=".tmp", delete=False) as output:
                pending = Path(output.name)
                json.dump({"version": 1, "structure_seed": identity.structure_seed, "gate_seed": identity.gate_seed}, output)
                output.flush()
                os.fsync(output.fileno())
            os.replace(pending, self.path)
        finally:
            if pending is not None and pending.exists():
                pending.unlink()

    def replace(self, *, structure: bool = False, gate: bool = False) -> CameraIdentity:
        previous = self.current()
        changed = CameraIdentity(secrets.randbits(32) if structure else previous.structure_seed,
                                 secrets.randbits(32) if gate else previous.gate_seed)
        self.save(changed)
        return changed
