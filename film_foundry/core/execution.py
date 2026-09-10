"""Resolve Film Foundry's user-visible processing modes."""

from __future__ import annotations

from dataclasses import dataclass

from film_foundry.model.config import DarkroomConfig


EXECUTION_MODES = ("quality", "scaled_fast", "reduced_fast")


@dataclass(frozen=True, slots=True)
class ExecutionAxes:
    """Three orthogonal controls resolved for one processing request."""

    scaling_enabled: bool
    lower_order_enabled: bool
    quality_mode: str

    @property
    def label(self) -> str:
        if self.scaling_enabled and self.lower_order_enabled:
            return "scaled_reduced_fast"
        if self.scaling_enabled:
            return "scaled_fast"
        if self.lower_order_enabled:
            return "reduced_fast"
        return "quality"


def resolve_execution_axes(
    config: DarkroomConfig,
    *,
    scaled_override: bool = False,
) -> ExecutionAxes:
    """Resolve scaling, model reduction and quality as independent axes."""

    mode = str(config.processing.execution_mode).strip().lower()
    if mode not in EXECUTION_MODES:
        raise ValueError(
            "processing.execution_mode must be 'quality', 'scaled_fast', or "
            "'reduced_fast'."
        )
    quality_mode = str(config.processing.quality_mode).strip().lower()
    return ExecutionAxes(
        scaling_enabled=bool(scaled_override or mode == "scaled_fast"),
        lower_order_enabled=bool(config.fast_mode or mode == "reduced_fast"),
        quality_mode=quality_mode,
    )


def resolve_execution_mode(
    config: DarkroomConfig,
    *,
    scaled_override: bool = False,
) -> str:
    """Return a compatibility label for the resolved orthogonal axes."""

    return resolve_execution_axes(
        config,
        scaled_override=scaled_override,
    ).label


def processing_long_edge(
    config: DarkroomConfig,
    *,
    scaled_override: bool = False,
) -> int | None:
    """Resolve frame size for a file-based develop/full run."""
    axes = resolve_execution_axes(config, scaled_override=scaled_override)
    if axes.scaling_enabled:
        return config.output.preview_long_edge
    return config.output.render_long_edge


def uses_reduced_implementation(config: DarkroomConfig) -> bool:
    """Whether documented lower-order internal approximations are allowed."""
    return resolve_execution_axes(config).lower_order_enabled
