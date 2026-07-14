"""Reusable process-program topologies for silver-halide film."""

from __future__ import annotations

from half_frame_darkroom.core.film_process.operators import (
    FilmProcessAction,
    FilmProcessProgram,
    FilmProcessStep,
)


def black_and_white_negative_program() -> FilmProcessProgram:
    return FilmProcessProgram(
        key="bw_negative",
        output_polarity="negative",
        compatible_interpreters=("negative_scan",),
        steps=(
            FilmProcessStep(FilmProcessAction.DEVELOP_SILVER, label="exposure_selective_development"),
            FilmProcessStep(FilmProcessAction.FIX_HALIDE, label="fix_remaining_halide"),
            FilmProcessStep(FilmProcessAction.REMOVE_AUXILIARY, label="remove_auxiliary_layers"),
        ),
    )


def color_negative_program(bleach_strength: float = 1.0) -> FilmProcessProgram:
    """Color-negative topology; lowering bleach strength produces bleach bypass."""
    return FilmProcessProgram(
        key="color_negative" if bleach_strength >= 1.0 else "color_negative_bleach_bypass",
        output_polarity="negative",
        compatible_interpreters=("negative_scan",),
        steps=(
            FilmProcessStep(FilmProcessAction.DEVELOP_COLOR, label="color_coupling_development"),
            FilmProcessStep(
                FilmProcessAction.BLEACH_SILVER,
                strength=float(bleach_strength),
                label="silver_bleach",
            ),
            FilmProcessStep(FilmProcessAction.FIX_HALIDE, label="fix_remaining_halide"),
            FilmProcessStep(FilmProcessAction.DESTROY_DYE, label="cross_process_dye_stability"),
            FilmProcessStep(FilmProcessAction.REMOVE_AUXILIARY, label="remove_auxiliary_layers"),
        ),
    )


def black_and_white_reversal_program() -> FilmProcessProgram:
    return FilmProcessProgram(
        key="bw_reversal",
        output_polarity="positive",
        compatible_interpreters=("positive_transparency_scan",),
        steps=(
            FilmProcessStep(FilmProcessAction.DEVELOP_SILVER, label="first_development"),
            FilmProcessStep(FilmProcessAction.REMOVE_SILVER, label="remove_first_silver_image"),
            FilmProcessStep(FilmProcessAction.ACTIVATE_REMAINING_HALIDE, label="reversal_activation"),
            FilmProcessStep(FilmProcessAction.DEVELOP_SILVER, label="second_development"),
            FilmProcessStep(FilmProcessAction.FIX_HALIDE, label="final_fix"),
            FilmProcessStep(FilmProcessAction.REMOVE_AUXILIARY, label="remove_auxiliary_layers"),
        ),
    )


def color_reversal_program() -> FilmProcessProgram:
    """Color reversal keeps both silver images until the final bleach/fix.

    Unlike B&W reversal, removing the first silver image before reversal
    activation is not required. The first development has already consumed its
    halide; final bleaching can remove silver from both developments together.
    """
    return FilmProcessProgram(
        key="color_reversal",
        output_polarity="positive",
        compatible_interpreters=("positive_transparency_scan",),
        steps=(
            FilmProcessStep(FilmProcessAction.DEVELOP_SILVER, label="first_development"),
            FilmProcessStep(FilmProcessAction.ACTIVATE_REMAINING_HALIDE, label="reversal_activation"),
            FilmProcessStep(FilmProcessAction.DEVELOP_COLOR, label="color_development"),
            FilmProcessStep(FilmProcessAction.BLEACH_SILVER, label="silver_bleach"),
            FilmProcessStep(FilmProcessAction.FIX_HALIDE, label="final_fix"),
            FilmProcessStep(FilmProcessAction.DESTROY_DYE, label="cross_process_dye_stability"),
            FilmProcessStep(FilmProcessAction.REMOVE_AUXILIARY, label="remove_auxiliary_layers"),
        ),
    )
