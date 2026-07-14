"""Resolve Film Foundry's user-visible processing modes."""

from __future__ import annotations

from half_frame_darkroom.model.config import DarkroomConfig


EXECUTION_MODES = ("quality", "scaled_fast", "reduced_fast")


def resolve_execution_mode(
    config: DarkroomConfig,
    *,
    scaled_override: bool = False,
) -> str:
    """Return the effective mode, including legacy ``fast_mode`` support."""
    if scaled_override:
        return "scaled_fast"
    mode = str(config.processing.execution_mode).strip().lower()
    if mode not in EXECUTION_MODES:
        raise ValueError(
            "processing.execution_mode must be 'quality', 'scaled_fast', or "
            "'reduced_fast'."
        )
    if mode == "quality" and bool(config.fast_mode):
        return "reduced_fast"
    return mode


def processing_long_edge(
    config: DarkroomConfig,
    *,
    scaled_override: bool = False,
) -> int | None:
    """Resolve frame size for a file-based develop/full run."""
    mode = resolve_execution_mode(config, scaled_override=scaled_override)
    if mode == "scaled_fast":
        return config.output.preview_long_edge
    return config.output.render_long_edge


def uses_reduced_implementation(config: DarkroomConfig) -> bool:
    """Whether documented lower-order internal approximations are allowed."""
    return resolve_execution_mode(config) == "reduced_fast"
