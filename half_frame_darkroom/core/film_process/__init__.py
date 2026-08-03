"""Reduced-order film material state-transition framework.

This package models silver-halide film processes as selective transformations
of a small set of material pools.  It intentionally does not model named
laboratory standards or microscopic chemistry.  Negative film, reversal film,
bleach bypass, and cross processing can share the same operators while keeping
their material definitions, reagents, programs, and editors separate.
"""

from half_frame_darkroom.core.film_process.model import (
    FilmFinalMedium,
    FilmProcessState,
    ReducedFilmMaterial,
    compose_optical_density_rgb,
)
from half_frame_darkroom.core.film_process.integration import (
    ReducedBwDevelopment,
    ReducedColorDevelopment,
    develop_bw_negative_reduced,
    develop_bw_reversal_reduced,
    develop_color_negative_reduced,
    develop_color_reversal_reduced,
    shape_positive_density_fraction,
    shape_positive_dye_chroma,
)
from half_frame_darkroom.core.film_process.operators import (
    CompatibilityProfile,
    FilmProcessAction,
    FilmProcessProgram,
    FilmProcessResult,
    FilmProcessStep,
    apply_process_program,
)
from half_frame_darkroom.core.film_process.programs import (
    black_and_white_negative_program,
    black_and_white_reversal_program,
    color_negative_program,
    color_reversal_program,
)
from half_frame_darkroom.core.film_process.recipe import (
    FILM_PROCESS_PROGRAM_KEYS,
    process_program_payload,
    program_from_develop_recipe,
    resolve_program_key,
)

__all__ = [
    "CompatibilityProfile",
    "FilmFinalMedium",
    "FilmProcessAction",
    "FilmProcessProgram",
    "FilmProcessResult",
    "FilmProcessState",
    "FilmProcessStep",
    "FILM_PROCESS_PROGRAM_KEYS",
    "ReducedFilmMaterial",
    "ReducedBwDevelopment",
    "ReducedColorDevelopment",
    "apply_process_program",
    "compose_optical_density_rgb",
    "black_and_white_negative_program",
    "black_and_white_reversal_program",
    "color_negative_program",
    "color_reversal_program",
    "develop_bw_negative_reduced",
    "develop_bw_reversal_reduced",
    "develop_color_negative_reduced",
    "develop_color_reversal_reduced",
    "shape_positive_density_fraction",
    "shape_positive_dye_chroma",
    "process_program_payload",
    "program_from_develop_recipe",
    "resolve_program_key",
]
