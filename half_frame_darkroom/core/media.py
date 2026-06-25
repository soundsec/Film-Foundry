"""Media-family helpers.

The current engine is implemented for electronic negatives, but the public
configuration needs room for slide film, instant materials, direct-positive
paper, and historic plates.  Keep these checks centralized so future branches
do not scatter string comparisons through the engine.
"""

from __future__ import annotations

from half_frame_darkroom.model.config import DarkroomConfig


NEGATIVE_PROCESSES = {"negative", "color_negative", "bw_negative", "film_negative"}
POSITIVE_PROCESSES = {"positive", "slide", "reversal", "direct_positive", "instant", "daguerreotype"}


def normalized_process(value: str | None) -> str:
    return str(value or "negative").strip().lower().replace("-", "_").replace(" ", "_")


def config_medium_process(config: DarkroomConfig) -> str:
    film_process = normalized_process(getattr(config.film, "medium_process", ""))
    if film_process:
        return film_process
    return normalized_process(getattr(config, "medium", "film_negative"))


def is_negative_process(config: DarkroomConfig) -> bool:
    process = config_medium_process(config)
    return process in NEGATIVE_PROCESSES or process.endswith("_negative")


def is_positive_process(config: DarkroomConfig) -> bool:
    return config_medium_process(config) in POSITIVE_PROCESSES


def is_monochrome_process(config: DarkroomConfig) -> bool:
    mode = normalized_process(getattr(config, "mode", ""))
    color_process = normalized_process(getattr(config.film, "color_process", ""))
    return mode in {"bw_negative", "bw_positive", "monochrome"} or color_process in {"bw", "black_white", "monochrome"}
