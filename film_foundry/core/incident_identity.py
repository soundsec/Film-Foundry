"""Independent random streams for explicitly identified roll/batch defects."""
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


def validate_incident_seed(seed):
    if seed is None:
        return
    if isinstance(seed, bool) or int(seed) != seed or not 0 <= int(seed) <= 0xFFFFFFFF:
        raise ValueError("develop.incident_identity_seed must be an unsigned 32-bit integer or null")


def incident_rng(recipe, frame_rng, component):
    seed = getattr(recipe, "incident_identity_seed", None)
    if seed is None:
        return frame_rng
    validate_incident_seed(seed)
    return np.random.default_rng(int(seed) ^ INCIDENT_SALTS[component])
