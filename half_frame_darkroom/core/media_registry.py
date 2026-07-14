"""Media pipeline registry.

This module keeps medium-specific dispatch out of UI and entry scripts. The
registry exposes only implemented or actively experimental workflows.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

import numpy as np

from half_frame_darkroom.core.media import config_development_process, config_medium_process, normalized_process
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
    status: str = "development"
    medium_family: str = "film"
    medium_process: str = "negative"
    default_interpreter: str = "negative_scan"
    compatible_interpreters: tuple[str, ...] = ("negative_scan",)
    description: str = ""
    material_editor_script: str | None = None
    develop_editor_script: str | None = None
    scanner_editor_script: str | None = None


def unsupported_develop(
    image_srgb: np.ndarray,
    config: DarkroomConfig,
    rng: np.random.Generator | None,
    prepared: bool,
) -> DevelopedMedium:
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
_EXPERIMENTAL_PIPELINES = (
    ("slide", "Slide / Reversal Film", "film", "slide", "positive", "positive", "实验中的彩色/黑白反转正片形成与透明片扫描。"),
    ("reversal", "Reversal Process", "film", "reversal", "positive", "positive", "实验中的银盐反转程序与正片最终介质。"),
)


def _interpreters_for_process(process: str) -> tuple[str, ...]:
    process = normalized_process(process)
    if process in {"slide", "reversal", "positive"}:
        return ("positive_transparency_scan",)
    if process in {"instant", "direct_positive"}:
        return ("reflective_scan",)
    if process == "daguerreotype":
        return ("plate_view",)
    if process in _NEGATIVE_KEYS or process.endswith("_negative"):
        return ("negative_scan",)
    return ()


def _pipeline_stub(
    key: str,
    label: str,
    input_polarity: str = "positive",
    output_polarity: str = "positive",
    *,
    status: str = "development",
    medium_family: str = "film",
    medium_process: str | None = None,
    default_interpreter: str | None = None,
    compatible_interpreters: tuple[str, ...] | None = None,
    description: str = "",
    material_editor_script: str | None = None,
    develop_editor_script: str | None = None,
    scanner_editor_script: str | None = None,
) -> MediaPipeline:
    process = medium_process or key
    interpreters = compatible_interpreters
    if interpreters is None:
        interpreters = _interpreters_for_process(process)
    if default_interpreter is None:
        default_interpreter = interpreters[0] if interpreters else "unsupported"
    return MediaPipeline(
        key=key,
        label=label,
        input_polarity=input_polarity,
        output_polarity=output_polarity,
        develop=unsupported_develop,
        scan=unsupported_scan,
        status=status,
        medium_family=medium_family,
        medium_process=process,
        default_interpreter=default_interpreter,
        compatible_interpreters=interpreters,
        description=description,
        material_editor_script=material_editor_script,
        develop_editor_script=develop_editor_script,
        scanner_editor_script=scanner_editor_script,
    )


def install_builtin_pipeline_stubs() -> None:
    """Install current built-in identities before engine handlers are attached."""
    if _PIPELINES:
        return
    for key in _NEGATIVE_KEYS:
        register_media_pipeline(
            _pipeline_stub(
                key,
                "Negative Film Toolkit",
                input_polarity="positive",
                output_polarity="negative",
                medium_family="film",
                medium_process="negative",
                description="当前稳定的彩色/黑白负片工具包。",
                status="stable",
                material_editor_script="film_foundry.tools.run_film_material_editor",
                develop_editor_script="film_foundry.tools.run_develop_process_editor",
                scanner_editor_script="film_foundry.tools.run_scanner_render_editor",
            )
        )
    for key, label, family, process, input_polarity, output_polarity, description in _EXPERIMENTAL_PIPELINES:
        register_media_pipeline(
            _pipeline_stub(
                key,
                label,
                input_polarity=input_polarity,
                output_polarity=output_polarity,
                medium_family=family,
                medium_process=process,
                description=description,
                status="experimental",
            )
        )
    register_media_pipeline(_pipeline_stub("unsupported", "Unsupported Medium", "unknown", "unknown"))


def register_media_pipeline(pipeline: MediaPipeline) -> None:
    _PIPELINES[normalized_process(pipeline.key)] = pipeline


def get_media_pipeline(config: DarkroomConfig) -> MediaPipeline:
    """Compatibility lookup by material identity; prefer stage-specific APIs."""
    install_builtin_pipeline_stubs()
    key = config_medium_process(config)
    pipeline = _PIPELINES.get(key)
    if pipeline is not None:
        return pipeline
    if key.endswith("_negative"):
        return _PIPELINES["negative"]
    return _PIPELINES.get(key, _PIPELINES["unsupported"])


def get_develop_pipeline(config: DarkroomConfig) -> MediaPipeline:
    """Select formation pipeline from the explicit process program/result."""
    install_builtin_pipeline_stubs()
    key = config_development_process(config)
    pipeline = _PIPELINES.get(key)
    if pipeline is not None:
        return pipeline
    if key.endswith("_negative"):
        return _PIPELINES["negative"]
    return _PIPELINES["unsupported"]


def get_scan_pipeline(medium: DevelopedMedium) -> MediaPipeline:
    """Select observation pipeline from the developed medium, not its history."""
    install_builtin_pipeline_stubs()
    key = normalized_process(getattr(medium, "medium_process", ""))
    pipeline = _PIPELINES.get(key)
    if pipeline is not None:
        return pipeline
    if key.endswith("_negative") or str(getattr(medium, "image_polarity", "")).lower() == "negative":
        return _PIPELINES["negative"]
    return _PIPELINES["unsupported"]


def registered_media_processes() -> tuple[str, ...]:
    install_builtin_pipeline_stubs()
    return tuple(sorted(_PIPELINES))


def registered_media_toolkits() -> tuple[MediaPipeline, ...]:
    install_builtin_pipeline_stubs()
    preferred = (
        "negative",
        "slide",
        "reversal",
        "instant",
        "direct_positive",
        "daguerreotype",
    )
    seen: set[str] = set()
    ordered: list[MediaPipeline] = []
    for key in preferred:
        pipeline = _PIPELINES.get(key)
        if pipeline is not None:
            ordered.append(pipeline)
            seen.add(key)
    for key in sorted(_PIPELINES):
        if key not in seen and key not in _NEGATIVE_KEYS and key != "unsupported":
            ordered.append(_PIPELINES[key])
    return tuple(ordered)


def install_default_media_pipelines(
    negative_develop: DevelopHandler,
    negative_scan: ScanHandler,
    positive_transparency_develop: DevelopHandler | None = None,
    positive_transparency_scan: ScanHandler | None = None,
) -> None:
    install_builtin_pipeline_stubs()
    for key in _NEGATIVE_KEYS:
        register_media_pipeline(
            MediaPipeline(
                key=key,
                label="Negative Film Toolkit",
                input_polarity="positive",
                output_polarity="negative",
                develop=negative_develop,
                scan=negative_scan,
                status="stable",
                medium_family="film",
                medium_process="negative",
                default_interpreter="negative_scan",
                compatible_interpreters=("negative_scan",),
                description="当前稳定的彩色/黑白负片工具包。",
                material_editor_script="film_foundry.tools.run_film_material_editor",
                develop_editor_script="film_foundry.tools.run_develop_process_editor",
                scanner_editor_script="film_foundry.tools.run_scanner_render_editor",
            )
        )
    if positive_transparency_develop is not None and positive_transparency_scan is not None:
        for key, label in (("slide", "Slide / Reversal Film"), ("reversal", "Reversal Process")):
            register_media_pipeline(
                MediaPipeline(
                    key=key,
                    label=label,
                    input_polarity="positive",
                    output_polarity="positive",
                    develop=positive_transparency_develop,
                    scan=positive_transparency_scan,
                    status="experimental",
                    medium_family="film",
                    medium_process=key,
                    default_interpreter="positive_transparency_scan",
                    compatible_interpreters=("positive_transparency_scan",),
                    description="Experimental positive transparency prototype. Produces a viewable transmissive positive without negative inversion.",
                    material_editor_script="film_foundry.tools.run_film_material_editor",
                    develop_editor_script="film_foundry.tools.run_develop_process_editor",
                    scanner_editor_script="film_foundry.tools.run_positive_scanner_editor",
                )
            )
