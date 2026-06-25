"""Media pipeline registry.

This module keeps medium-specific dispatch out of the UI and entry scripts.
Only the electronic negative pipeline is implemented today.  Other media are
registered as explicit placeholders so future work can add handlers without
turning engine.py into a pile of conditionals.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

import numpy as np

from half_frame_darkroom.core.media import config_medium_process, normalized_process
from half_frame_darkroom.core.states import DevelopedMedium, RenderedPositive
from half_frame_darkroom.model.config import DarkroomConfig


DevelopHandler = Callable[[np.ndarray, DarkroomConfig, np.random.Generator | None, bool], DevelopedMedium]
ScanHandler = Callable[[DevelopedMedium, DarkroomConfig | None, bool], RenderedPositive]


@dataclass(frozen=True, slots=True)
class MediaPipeline:
    key: str
    label: str
    input_polarity: str
    output_polarity: str
    develop: DevelopHandler
    scan: ScanHandler


def unsupported_develop(image_srgb: np.ndarray, config: DarkroomConfig, rng: np.random.Generator | None, prepared: bool) -> DevelopedMedium:
    process = config_medium_process(config)
    raise NotImplementedError(
        f"Medium process '{process}' is registered but not implemented yet. "
        "The current stable engine supports electronic negative formation only."
    )


def unsupported_scan(negative: DevelopedMedium, config: DarkroomConfig | None, prepared: bool) -> RenderedPositive:
    process = config_medium_process(config or DarkroomConfig())
    raise NotImplementedError(
        f"Medium process '{process}' scan/render is registered but not implemented yet. "
        "The current stable engine supports negative inversion scanning only."
    )


_PIPELINES: dict[str, MediaPipeline] = {}
_NEGATIVE_KEYS = ("negative", "color_negative", "bw_negative", "film_negative")
_FUTURE_PIPELINES = (
    ("slide", "Slide / Reversal Film"),
    ("reversal", "Reversal Process"),
    ("instant", "Instant Film"),
    ("direct_positive", "Direct Positive Material"),
    ("daguerreotype", "Historic Plate"),
)


def _placeholder_pipeline(key: str, label: str, input_polarity: str = "positive", output_polarity: str = "positive") -> MediaPipeline:
    return MediaPipeline(
        key=key,
        label=label,
        input_polarity=input_polarity,
        output_polarity=output_polarity,
        develop=unsupported_develop,
        scan=unsupported_scan,
    )


def install_placeholder_media_pipelines() -> None:
    """Install visible placeholders so the registry is useful before engine import."""
    if _PIPELINES:
        return
    for key in _NEGATIVE_KEYS:
        register_media_pipeline(
            _placeholder_pipeline(
                key,
                "Electronic Negative",
                input_polarity="positive",
                output_polarity="negative",
            )
        )
    for key, label in _FUTURE_PIPELINES:
        register_media_pipeline(_placeholder_pipeline(key, label))
    register_media_pipeline(_placeholder_pipeline("unsupported", "Unsupported Medium", "unknown", "unknown"))


def register_media_pipeline(pipeline: MediaPipeline) -> None:
    _PIPELINES[normalized_process(pipeline.key)] = pipeline


def get_media_pipeline(config: DarkroomConfig) -> MediaPipeline:
    install_placeholder_media_pipelines()
    key = config_medium_process(config)
    pipeline = _PIPELINES.get(key)
    if pipeline is not None:
        return pipeline
    if key.endswith("_negative"):
        return _PIPELINES["negative"]
    return _PIPELINES.get(key, _PIPELINES["unsupported"])


def registered_media_processes() -> tuple[str, ...]:
    install_placeholder_media_pipelines()
    return tuple(sorted(_PIPELINES))


def install_default_media_pipelines(
    negative_develop: DevelopHandler,
    negative_scan: ScanHandler,
) -> None:
    install_placeholder_media_pipelines()
    for key in _NEGATIVE_KEYS:
        register_media_pipeline(
            MediaPipeline(
                key=key,
                label="Electronic Negative",
                input_polarity="positive",
                output_polarity="negative",
                develop=negative_develop,
                scan=negative_scan,
            )
        )
