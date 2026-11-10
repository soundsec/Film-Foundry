"""Versioned formation reductions; observation is independent of these IDs."""

LEGACY_REDUCED_PROFILE = "builtin_reduced_fast_v1"
CONTACT_REDUCED_PROFILE = "builtin_reduced_fast_v2"
DETAILED_REDUCED_PROFILE = "builtin_reduced_fast_v3"
CURRENT_REDUCED_PROFILE = "builtin_reduced_fast_v4"
REDUCED_PROFILES = (CURRENT_REDUCED_PROFILE, DETAILED_REDUCED_PROFILE, CONTACT_REDUCED_PROFILE, LEGACY_REDUCED_PROFILE)


def validate_reduced_profile(value):
    if value not in REDUCED_PROFILES:
        raise ValueError("processing.reduced_profile is not recognized.")
    return value
