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
NEGATIVE_TRANSPARENCY_INTERPRETERS = ("negative_scan",)
POSITIVE_TRANSPARENCY_INTERPRETERS = ("positive_transparency_scan",)
REFLECTIVE_INTERPRETERS = ("reflective_scan",)
PLATE_INTERPRETERS = ("plate_view",)


def normalized_process(value: str | None) -> str:
    return str(value or "negative").strip().lower().replace("-", "_").replace(" ", "_")


def config_medium_process(config: DarkroomConfig) -> str:
    """Return the material's declared/native process identity."""
    film_process = normalized_process(getattr(config.film, "medium_process", ""))
    if film_process:
        return film_process
    return normalized_process(getattr(config, "medium", "film_negative"))


def config_development_process(config: DarkroomConfig) -> str:
    """Return the process-program result used for development dispatch.

    Explicit reduced film programs are authoritative.  ``auto`` preserves old
    preset behavior, except that a legacy develop preset explicitly declaring
    reversal/slide may still route a negative material through reversal.
    """
    program_key = normalized_process(getattr(config.chemistry, "program_key", "auto"))
    if program_key in {"bw_reversal", "color_reversal"}:
        return "reversal"
    if program_key in {
        "bw_negative",
        "color_negative",
        "color_negative_bleach_bypass",
    }:
        return "negative"
    recipe_process = normalized_process(getattr(config.chemistry, "medium_process", ""))
    if recipe_process in {"slide", "reversal", "positive"}:
        return recipe_process
    return config_medium_process(config)


def is_negative_process(config: DarkroomConfig) -> bool:
    process = config_medium_process(config)
    return process in NEGATIVE_PROCESSES or process.endswith("_negative")


def is_positive_process(config: DarkroomConfig) -> bool:
    return config_medium_process(config) in POSITIVE_PROCESSES


def is_monochrome_process(config: DarkroomConfig) -> bool:
    """Return whether the selected program produces a monochrome silver image."""
    mode = normalized_process(getattr(config, "mode", ""))
    program = normalized_process(getattr(config.chemistry, "program_key", "auto"))
    color_process = normalized_process(getattr(config.film, "color_process", ""))
    return (
        program in {"bw_negative", "bw_reversal"}
        or mode in {"bw_negative", "bw_positive", "monochrome"}
        or color_process in {"bw", "black_white", "monochrome"}
    )


def is_monochrome_material(config: DarkroomConfig) -> bool:
    """Return material identity without inferring it from the process program."""
    color_process = normalized_process(getattr(config.film, "color_process", ""))
    return color_process in {"bw", "black_white", "monochrome"}


def developed_medium_contract(config: DarkroomConfig) -> dict[str, object]:
    """Return the interpreter contract for the developed medium.

    The process owns this contract. Render/scanner presets should interpret the
    returned developed material instead of redefining what the process produced.
    Only the electronic negative contract is implemented today; future media use
    the same keys when they become real pipelines.
    """
    process = config_development_process(config)
    family = normalized_process(getattr(config.film, "medium_family", "film"))
    color_process = normalized_process(getattr(config.film, "color_process", "color"))
    is_mono = is_monochrome_process(config)
    material_is_mono = is_monochrome_material(config)
    native_process = config_medium_process(config)
    clear_base_material = material_is_mono or native_process in {"slide", "reversal", "positive"}

    if process in NEGATIVE_PROCESSES or process.endswith("_negative"):
        return {
            "medium_family": family,
            "medium_process": "negative",
            "image_polarity": "negative",
            "view_mode": "transmissive",
            "base_type": "clear_base" if clear_base_material else "orange_mask",
            "color_system": (
                "silver_bw"
                if material_is_mono
                else ("silver_on_color_material" if is_mono else "color_negative_dye")
            ),
            "compatible_interpreters": NEGATIVE_TRANSPARENCY_INTERPRETERS,
        }

    if process in {"slide", "reversal", "positive"}:
        return {
            "medium_family": family,
            "medium_process": process,
            "image_polarity": "positive",
            "view_mode": "transmissive",
            "base_type": "clear_base" if clear_base_material else "orange_mask",
            "color_system": (
                "silver_bw"
                if material_is_mono
                else ("silver_on_color_material" if is_mono else "positive_dye")
            ),
            "compatible_interpreters": POSITIVE_TRANSPARENCY_INTERPRETERS,
        }

    if process in {"instant", "direct_positive"}:
        return {
            "medium_family": family,
            "medium_process": process,
            "image_polarity": "positive",
            "view_mode": "reflective",
            "base_type": "paper_base",
            "color_system": "instant_dye" if process == "instant" else "direct_positive",
            "compatible_interpreters": REFLECTIVE_INTERPRETERS,
        }

    if process == "daguerreotype":
        return {
            "medium_family": family,
            "medium_process": process,
            "image_polarity": "positive",
            "view_mode": "angle_reflective",
            "base_type": "metal_plate",
            "color_system": "metallic_silver",
            "compatible_interpreters": PLATE_INTERPRETERS,
        }

    return {
        "medium_family": family,
        "medium_process": process,
        "image_polarity": "unknown",
        "view_mode": "unknown",
        "base_type": "unknown",
        "color_system": "unknown",
        "compatible_interpreters": (),
    }
