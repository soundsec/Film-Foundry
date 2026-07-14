"""Conservative resource planning for full-frame Film Foundry processing.

The estimator is deliberately separate from the image operators.  It never
changes resolution, precision, process programs, or scan interpretation.  Its
job is only to make the cost of the current non-tiled implementation visible
before a large source is decoded.
"""

from __future__ import annotations

from dataclasses import dataclass
import warnings


_RGB_FLOAT32_BYTES_PER_PIXEL = 3 * 4
_MIB = 1024 * 1024


def dimensions_for_long_edge(width: int, height: int, long_edge: int | None) -> tuple[int, int]:
    """Return the processing dimensions used by ``resize_to_long_edge``."""
    width = int(width)
    height = int(height)
    if width <= 0 or height <= 0:
        raise ValueError("Image dimensions must be positive.")
    if long_edge is None:
        return width, height
    long_edge = int(long_edge)
    if long_edge <= 0:
        raise ValueError("Long edge must be positive or None.")
    current = max(width, height)
    if current <= long_edge:
        return width, height
    scale = long_edge / current
    return max(1, round(width * scale)), max(1, round(height * scale))


@dataclass(frozen=True, slots=True)
class PipelineMemoryEstimate:
    """Planning estimate for the current full-frame, non-tiled pipeline."""

    source_width: int
    source_height: int
    work_width: int
    work_height: int
    retained_state_bytes: int
    planning_peak_bytes: int
    retention_policy: str
    decoder_reduced: bool = False
    comfort_zone_megapixels: float = 30.0
    model_version: str = "full_frame_v2"

    @property
    def source_megapixels(self) -> float:
        return self.source_width * self.source_height / 1_000_000.0

    @property
    def work_megapixels(self) -> float:
        return self.work_width * self.work_height / 1_000_000.0

    @property
    def retained_state_mib(self) -> float:
        return self.retained_state_bytes / _MIB

    @property
    def planning_peak_mib(self) -> float:
        return self.planning_peak_bytes / _MIB

    @property
    def support_tier(self) -> str:
        if self.work_megapixels <= self.comfort_zone_megapixels:
            return "comfort"
        return "best_effort_large_frame"

    def as_dict(self) -> dict[str, object]:
        return {
            "model_version": self.model_version,
            "source": {
                "width": self.source_width,
                "height": self.source_height,
                "megapixels": self.source_megapixels,
            },
            "work": {
                "width": self.work_width,
                "height": self.work_height,
                "megapixels": self.work_megapixels,
            },
            "retention_policy": self.retention_policy,
            "decoder_reduced": self.decoder_reduced,
            "retained_state_mib": self.retained_state_mib,
            "planning_peak_mib": self.planning_peak_mib,
            "comfort_zone_megapixels": self.comfort_zone_megapixels,
            "support_tier": self.support_tier,
            "notes": [
                "Conservative planning estimate, not a measured process RSS value.",
                "No automatic resizing or operator substitution is performed.",
                "Frames above the comfort zone remain best-effort support.",
            ],
        }


def estimate_pipeline_memory(
    width: int,
    height: int,
    *,
    long_edge: int | None = None,
    diagnostic: bool = False,
    retain_development_stages: bool = False,
    comfort_zone_megapixels: float = 30.0,
    decoder_reduced: bool = False,
) -> PipelineMemoryEstimate:
    """Estimate memory before decoding a full-frame source.

    The peak coefficient includes NumPy temporaries observed in the current
    pointwise/convolution pipeline and intentionally errs on the safe side.
    It is a capacity-planning boundary, not a promise of exact RSS usage.
    """
    source_width = int(width)
    source_height = int(height)
    comfort_zone_megapixels = float(comfort_zone_megapixels)
    if not comfort_zone_megapixels > 0.0:
        raise ValueError("comfort_zone_megapixels must be positive.")
    work_width, work_height = dimensions_for_long_edge(
        source_width, source_height, long_edge
    )
    source_unit = source_width * source_height * _RGB_FLOAT32_BYTES_PER_PIXEL
    work_unit = work_width * work_height * _RGB_FLOAT32_BYTES_PER_PIXEL

    if diagnostic:
        # Five development masters plus retained scan diagnostics.
        retention_policy = "diagnostic_full_float32"
        retained_units = 13.0
        pipeline_peak_units = 26.0
    elif retain_development_stages:
        retention_policy = "production_full_development_float32"
        retained_units = 5.0
        pipeline_peak_units = 22.0
    else:
        # Two authoritative density masters remain FP32.  Three consumed
        # history images are cold FP16 storage (3 * 0.5 FP32 units).
        retention_policy = "production_mixed_precision_cold_history"
        retained_units = 3.5
        pipeline_peak_units = 16.0

    retained_state = round(work_unit * retained_units)
    # Header probing is cheap, but the current decoder still materializes the
    # complete source before an optional resize.  Model decode and resize peaks
    # separately from the processing peak and take the largest phase.
    if decoder_reduced and long_edge is not None:
        # Pillow JPEG draft decoding uses a power-of-two DCT reduction. The
        # decoded edge can be up to roughly twice the requested final edge.
        decode_width, decode_height = dimensions_for_long_edge(
            source_width,
            source_height,
            min(max(source_width, source_height), int(long_edge) * 2),
        )
        decode_unit = decode_width * decode_height * _RGB_FLOAT32_BYTES_PER_PIXEL
    else:
        decode_unit = source_unit
    decode_peak = decode_unit * 4
    resize_peak = decode_unit + work_unit
    pipeline_peak = round(work_unit * pipeline_peak_units)
    planning_peak = max(decode_peak, resize_peak, pipeline_peak)
    return PipelineMemoryEstimate(
        source_width=source_width,
        source_height=source_height,
        work_width=work_width,
        work_height=work_height,
        retained_state_bytes=retained_state,
        planning_peak_bytes=planning_peak,
        retention_policy=retention_policy,
        decoder_reduced=bool(decoder_reduced),
        comfort_zone_megapixels=comfort_zone_megapixels,
    )


def warn_outside_comfort_zone(estimate: PipelineMemoryEstimate) -> None:
    """Warn about best-effort support without changing the selected mode."""
    if estimate.support_tier == "comfort":
        return
    warnings.warn(
        f"{estimate.work_megapixels:.1f}MP processing is above Film Foundry's "
        f"current {estimate.comfort_zone_megapixels:.1f}MP comfort zone. The "
        "job remains enabled as best-effort large-frame support; no automatic "
        "resize or reduced implementation has been selected.",
        RuntimeWarning,
        stacklevel=2,
    )


def enforce_memory_budget(
    estimate: PipelineMemoryEstimate,
    budget_mb: float | None,
    policy: str = "warn",
) -> None:
    """Apply an explicit user memory boundary without changing image semantics."""
    if budget_mb is None:
        return
    budget_mb = float(budget_mb)
    if not budget_mb > 0:
        raise ValueError("processing.memory_budget_mb must be positive or None.")
    normalized_policy = str(policy).strip().lower()
    if normalized_policy not in {"allow", "warn", "error"}:
        raise ValueError(
            "processing.memory_budget_policy must be 'allow', 'warn', or 'error'."
        )
    if estimate.planning_peak_mib <= budget_mb or normalized_policy == "allow":
        return
    message = (
        f"Estimated pipeline peak {estimate.planning_peak_mib:.0f} MiB exceeds the "
        f"configured {budget_mb:.0f} MiB memory budget for "
        f"{estimate.work_width}x{estimate.work_height} processing. "
        "Choose preview/render_long_edge explicitly, raise the budget, or use "
        "policy='allow'. Film Foundry will not silently resize the image."
    )
    if normalized_policy == "error":
        raise MemoryError(message)
    warnings.warn(message, RuntimeWarning, stacklevel=2)
