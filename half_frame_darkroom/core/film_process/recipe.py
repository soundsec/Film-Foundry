"""Map darkroom-facing recipe controls to reduced film process programs."""

from __future__ import annotations

from dataclasses import replace

import numpy as np

from half_frame_darkroom.core.development import build_effective_development
from half_frame_darkroom.core.film_process.operators import (
    FilmProcessAction,
    FilmProcessProgram,
    FilmProcessStep,
)
from half_frame_darkroom.core.film_process.programs import (
    black_and_white_negative_program,
    black_and_white_reversal_program,
    color_negative_program,
    color_reversal_program,
)
from half_frame_darkroom.model.config import DevelopRecipeConfig


FILM_PROCESS_PROGRAM_KEYS = (
    "auto",
    "legacy_density",
    "bw_negative",
    "color_negative",
    "color_negative_bleach_bypass",
    "bw_reversal",
    "color_reversal",
)


def _normalized(value: str | None) -> str:
    return str(value or "").strip().lower().replace("-", "_").replace(" ", "_")


def resolve_program_key(
    recipe: DevelopRecipeConfig,
    *,
    mode: str = "color_negative",
    material_process: str = "negative",
) -> str:
    """Resolve ``auto`` without treating the program name as the final state."""
    configured = _normalized(getattr(recipe, "program_key", "auto")) or "auto"
    if configured != "auto":
        if configured not in FILM_PROCESS_PROGRAM_KEYS:
            raise ValueError(f"Unsupported silver-halide film process program: {configured}")
        return configured
    normalized_mode = _normalized(mode)
    normalized_material_process = _normalized(material_process)
    monochrome = normalized_mode in {"bw", "bw_negative", "bw_positive", "monochrome"}
    reversal = normalized_material_process in {"slide", "reversal", "positive"} or "reversal" in normalized_mode
    if reversal:
        return "bw_reversal" if monochrome else "color_reversal"
    return "bw_negative" if monochrome else "color_negative"


def _base_program(key: str, recipe: DevelopRecipeConfig) -> FilmProcessProgram:
    if key == "bw_negative":
        return black_and_white_negative_program()
    if key == "color_negative":
        return color_negative_program()
    if key == "color_negative_bleach_bypass":
        return color_negative_program(bleach_strength=float(recipe.silver_bleach_completion))
    if key == "bw_reversal":
        return black_and_white_reversal_program()
    if key == "color_reversal":
        return color_reversal_program()
    raise ValueError(f"Unsupported silver-halide film process program: {key}")


def program_from_develop_recipe(
    recipe: DevelopRecipeConfig,
    *,
    mode: str = "color_negative",
    material_process: str = "negative",
    layer_count: int = 3,
) -> FilmProcessProgram:
    """Build an executable reduced program from existing recipe controls."""
    key = resolve_program_key(recipe, mode=mode, material_process=material_process)
    if key == "legacy_density":
        normalized_process = _normalized(material_process)
        positive = normalized_process in {"slide", "reversal", "positive"}
        return FilmProcessProgram(
            key="legacy_density",
            steps=(),
            output_polarity="positive" if positive else "negative",
            compatible_interpreters=(
                ("positive_transparency_scan",) if positive else ("negative_scan",)
            ),
        )
    program = _base_program(key, recipe)
    effective = build_effective_development(recipe)
    development_completion = float(np.clip(effective.progress_ratio, 0.0, 1.0))
    first_development_completion = development_completion * float(
        np.clip(recipe.first_development_completion, 0.0, 1.0)
    )
    second_development_completion = development_completion * float(
        np.clip(recipe.second_development_completion, 0.0, 1.0)
    )
    fixing_completion = float(
        np.clip(
            float(recipe.halide_fixing_completion) * (1.0 - effective.clearing_failure),
            0.0,
            1.0,
        )
    )
    bleach_completion = float(
        np.clip(
            float(recipe.silver_bleach_completion) * (1.0 - float(recipe.silver_retention)),
            0.0,
            1.0,
        )
    )
    configured_balance = tuple(float(np.clip(v, 0.0, 2.0)) for v in recipe.process_layer_balance)
    if int(layer_count) == 1:
        layer_balance = (float(np.mean(configured_balance)),)
    elif int(layer_count) == len(configured_balance):
        layer_balance = configured_balance
    else:
        layer_balance = tuple([1.0] * int(layer_count))
    dye_ratio = tuple(
        [float(np.clip(float(recipe.dye_coupling_efficiency), 0.0, 2.0))] * int(layer_count)
    )
    adjusted: list[FilmProcessStep] = []
    for step in program.steps:
        if step.action in {FilmProcessAction.DEVELOP_SILVER, FilmProcessAction.DEVELOP_COLOR}:
            stage_completion = development_completion
            if step.label == "first_development":
                stage_completion = first_development_completion
            elif step.label in {"second_development", "color_development"}:
                stage_completion = second_development_completion
            step = replace(
                step,
                strength=stage_completion,
                layer_selectivity=layer_balance,
                dye_coupling_ratio=dye_ratio if step.action == FilmProcessAction.DEVELOP_COLOR else None,
            )
        elif step.action == FilmProcessAction.ACTIVATE_REMAINING_HALIDE:
            step = replace(step, strength=float(np.clip(recipe.reversal_activation, 0.0, 1.0)))
        elif step.action == FilmProcessAction.REMOVE_SILVER:
            step = replace(step, strength=float(np.clip(recipe.first_silver_removal, 0.0, 1.0)))
        elif step.action == FilmProcessAction.BLEACH_SILVER:
            step = replace(step, strength=bleach_completion)
        elif step.action == FilmProcessAction.FIX_HALIDE:
            step = replace(step, strength=fixing_completion)
        elif step.action == FilmProcessAction.REMOVE_AUXILIARY:
            step = replace(step, strength=float(np.clip(recipe.auxiliary_removal, 0.0, 1.0)))
        adjusted.append(step)
    resolved_key = key
    if key == "color_negative" and bleach_completion < 1.0 - 1e-6:
        resolved_key = "color_negative_bleach_bypass"
    return replace(program, key=resolved_key, steps=tuple(adjusted))


def process_program_payload(program: FilmProcessProgram) -> dict[str, object]:
    return {
        "key": program.key,
        "output_polarity": program.output_polarity,
        "view_mode": program.view_mode,
        "compatible_interpreters": list(program.compatible_interpreters),
        "steps": [
            {
                "action": step.action.value,
                "label": step.label,
                "strength": float(step.strength),
                "layer_selectivity": (
                    None if step.layer_selectivity is None else list(step.layer_selectivity)
                ),
                "dye_coupling_ratio": (
                    None if step.dye_coupling_ratio is None else list(step.dye_coupling_ratio)
                ),
            }
            for step in program.steps
        ],
    }
