"""Render full-pipeline scan previews for Film Foundry feature review.

This developer-facing tool calls the same ``process_array`` boundary as the
GUI.  It saves sRGB scans, amplified absolute-difference maps, and contact
sheets so visual direction can be judged on pixels, not only pytest status.
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass
import time
from pathlib import Path
from typing import Callable

import numpy as np

from film_foundry.core.atomic_io import atomic_output_directory, atomic_write_json
from film_foundry.core.engine import process_array
from film_foundry.core.io_utils import save_image
from film_foundry.model.config import DarkroomConfig, OutputConfig


ConfigMutation = Callable[[DarkroomConfig], None]


@dataclass(frozen=True, slots=True)
class VisualCase:
    key: str
    family: str
    compare_to: str | None
    description: str
    mutate: ConfigMutation


def build_review_scene(width: int, height: int) -> np.ndarray:
    """Build a deterministic photographic-ish sRGB chart for visual review."""

    width = int(width)
    height = int(height)
    if width < 320 or height < 240:
        raise ValueError("visual review scene requires at least 320x240 pixels")
    yy, xx = np.indices((height, width), dtype=np.float32)
    yn = yy / float(height - 1)
    xn = xx / float(width - 1)
    image = np.empty((height, width, 3), dtype=np.float32)

    sky = np.clip(1.0 - yn, 0.0, 1.0) ** 1.35
    image[..., 0] = 0.28 + 0.52 * sky
    image[..., 1] = 0.36 + 0.46 * sky
    image[..., 2] = 0.48 + 0.48 * sky

    ground = yn > 0.62
    image[ground, 0] = 0.16 + 0.14 * xn[ground]
    image[ground, 1] = 0.20 + 0.10 * xn[ground]
    image[ground, 2] = 0.12 + 0.08 * xn[ground]

    window = (yn > 0.18) & (yn < 0.42) & (xn > 0.62) & (xn < 0.88)
    image[window] = (0.98, 0.96, 0.88)

    cy, cx = 0.52 * height, 0.32 * width
    ry, rx = 0.16 * height, 0.11 * width
    face = ((yy - cy) / max(ry, 1.0)) ** 2 + ((xx - cx) / max(rx, 1.0)) ** 2 <= 1.0
    image[face, 0] = 0.78
    image[face, 1] = 0.56
    image[face, 2] = 0.44

    spec_y = int(round(0.24 * height))
    spec_x = int(round(0.74 * width))
    image[max(0, spec_y - 2): spec_y + 3, max(0, spec_x - 2): spec_x + 3] = (1.0, 1.0, 1.0)

    grating = (xn > 0.08) & (xn < 0.28) & (yn > 0.72) & (yn < 0.92)
    stripes = np.where((xx.astype(np.int32) // 3) % 2 == 0, 0.92, 0.12)
    image[grating, 0] = stripes[grating]
    image[grating, 1] = stripes[grating]
    image[grating, 2] = stripes[grating] * 0.85

    foliage = (yn > 0.58) & (yn < 0.78) & (xn > 0.78)
    image[foliage, 0] = 0.12 + 0.08 * np.sin(xx[foliage] * 0.35)
    image[foliage, 1] = 0.28 + 0.12 * np.sin(yy[foliage] * 0.22)
    image[foliage, 2] = 0.10

    np.clip(image, 0.0, 1.0, out=image)
    return image


def _identity(_config: DarkroomConfig) -> None:
    return None


def _set_chem(**values: object) -> ConfigMutation:
    def mutate(config: DarkroomConfig) -> None:
        for name, value in values.items():
            setattr(config.chemistry, name, value)

    return mutate


def _set_film(**values: object) -> ConfigMutation:
    def mutate(config: DarkroomConfig) -> None:
        for name, value in values.items():
            setattr(config.film, name, value)

    return mutate


def _set_border(**values: object) -> ConfigMutation:
    def mutate(config: DarkroomConfig) -> None:
        for name, value in values.items():
            setattr(config.film_border, name, value)

    return mutate


def _compose(*mutations: ConfigMutation) -> ConfigMutation:
    def mutate(config: DarkroomConfig) -> None:
        for item in mutations:
            item(config)

    return mutate


def _as_bw_negative(config: DarkroomConfig) -> None:
    config.mode = "bw_negative"
    config.medium = "film_negative"
    config.film.color_process = "monochrome"
    config.film.medium_process = "negative"
    config.film.image_polarity = "negative"
    config.chemistry.program_key = "bw_negative"
    config.chemistry.medium_process = "negative"


def _as_color_reversal(config: DarkroomConfig) -> None:
    config.mode = "color_reversal"
    config.medium = "film_reversal"
    config.film.medium_process = "slide"
    config.film.image_polarity = "positive"
    config.film.color_process = "color"
    config.chemistry.program_key = "color_reversal"
    config.chemistry.medium_process = "reversal"
    config.scanner.interpreter_key = "positive_transparency_scan"
    config.scanner.target_medium_process = "slide"
    config.scanner.input_polarity = "positive"
    config.scanner.output_polarity = "positive"
    config.scanner.scan_method = "positive_transparency"


def _as_bw_reversal(config: DarkroomConfig) -> None:
    _as_color_reversal(config)
    config.mode = "bw_reversal"
    config.film.color_process = "monochrome"
    config.chemistry.program_key = "bw_reversal"


def _as_bleach_bypass(config: DarkroomConfig) -> None:
    config.chemistry.program_key = "color_negative_bleach_bypass"
    config.chemistry.silver_retention = 0.85
    config.chemistry.silver_bleach_completion = 0.25


VISUAL_CASES: tuple[VisualCase, ...] = (
    VisualCase("color_negative", "program", None, "Standard color negative scan", _identity),
    VisualCase("bw_negative", "program", "color_negative", "Black-and-white negative scan", _as_bw_negative),
    VisualCase("color_reversal", "program", None, "Color reversal / positive transparency", _as_color_reversal),
    VisualCase("bw_reversal", "program", "color_reversal", "Black-and-white reversal", _as_bw_reversal),
    VisualCase("bleach_bypass", "program", "color_negative", "Color negative bleach bypass", _as_bleach_bypass),
    VisualCase(
        "leak_local_edge",
        "light_leak",
        "color_negative",
        "Local-edge light leak",
        _set_chem(light_leak_strength=0.72, light_leak_template="local_edge"),
    ),
    VisualCase(
        "leak_perimeter",
        "light_leak",
        "color_negative",
        "Perimeter light leak",
        _set_chem(light_leak_strength=0.72, light_leak_template="perimeter"),
    ),
    VisualCase(
        "leak_half_frame",
        "light_leak",
        "color_negative",
        "Half-frame light leak",
        _set_chem(light_leak_strength=0.72, light_leak_template="half_frame"),
    ),
    VisualCase(
        "leak_cone",
        "light_leak",
        "color_negative",
        "Cone light leak",
        _set_chem(light_leak_strength=0.72, light_leak_template="cone"),
    ),
    VisualCase(
        "occlusion_dust",
        "occlusion",
        "color_negative",
        "Pre-develop dust occlusion",
        _set_chem(exposure_occlusion_dust=0.95),
    ),
    VisualCase(
        "occlusion_hair",
        "occlusion",
        "color_negative",
        "Pre-develop hair occlusion",
        _set_chem(exposure_occlusion_hair=0.95),
    ),
    VisualCase(
        "occlusion_insect",
        "occlusion",
        "color_negative",
        "Pre-develop insect occlusion",
        _set_chem(exposure_occlusion_insect=1.0),
    ),
    VisualCase(
        "occlusion_debris",
        "occlusion",
        "color_negative",
        "Pre-develop debris occlusion",
        _set_chem(exposure_occlusion_debris=0.95),
    ),
    VisualCase(
        "occlusion_combined",
        "occlusion",
        "color_negative",
        "Combined dust/hair/insect/debris occlusion",
        _set_chem(
            exposure_occlusion_dust=0.75,
            exposure_occlusion_hair=0.80,
            exposure_occlusion_insect=0.85,
            exposure_occlusion_debris=0.75,
        ),
    ),
    VisualCase(
        "imprint_laser_dot",
        "imprint",
        "color_negative",
        "Laser-dot film-plane flash",
        _set_border(imprint_style="laser_dot", imprint_side="bottom", imprint_text="A12", imprint_time="12:00"),
    ),
    VisualCase(
        "imprint_optical_amber",
        "imprint",
        "color_negative",
        "Optical-amber film-plane flash",
        _set_border(imprint_style="optical_amber", imprint_side="bottom", imprint_text="FF", imprint_time="2026"),
    ),
    VisualCase(
        "imprint_lab_edge",
        "imprint",
        "color_negative",
        "Lab-edge film-plane flash",
        _set_border(imprint_style="lab_edge", imprint_side="top", imprint_text="LAB", imprint_time="09-05"),
    ),
    VisualCase(
        "chemical_stain",
        "process_accident",
        "color_negative",
        "Post-process chemical stain",
        _set_chem(chemical_stain=0.75),
    ),
    VisualCase(
        "uneven_development",
        "process_accident",
        "color_negative",
        "Uneven development",
        _set_chem(uneven_development=0.75),
    ),
    VisualCase(
        "silver_plating",
        "process_accident",
        "color_negative",
        "Surface silver plating",
        _set_chem(silver_plating=0.70),
    ),
    VisualCase(
        "reticulation",
        "process_accident",
        "color_negative",
        "Emulsion reticulation",
        _set_chem(reticulation_strength=0.85),
    ),
    VisualCase(
        "dye_damage",
        "process_accident",
        "color_negative",
        "Explicit dye-pool damage",
        _set_chem(dye_damage_strength=0.70),
    ),
    VisualCase(
        "no_grain",
        "material",
        "color_negative",
        "Grain disabled",
        lambda config: setattr(config, "enable_grain", False),
    ),
    VisualCase(
        "strong_halation",
        "material",
        "color_negative",
        "Stronger layer halation",
        _compose(
            _set_film(halation_strength=1.6, halation_return_model="layer_selective"),
            lambda config: setattr(config, "enable_halation", True),
        ),
    ),
    VisualCase(
        "emulsion_spread",
        "material",
        "color_negative",
        "Compact emulsion highlight spread",
        _set_film(emulsion_highlight_spread_strength=0.55, emulsion_highlight_spread_threshold=0.42),
    ),
    VisualCase(
        "light_piping",
        "material",
        "color_negative",
        "Base light piping from left edge",
        _set_film(light_piping_strength=0.45, light_piping_depth=0.18, light_piping_edge_mode="left"),
    ),
    VisualCase(
        "development_adjacency",
        "material",
        "color_negative",
        "First-development adjacency",
        _set_chem(development_adjacency_strength=0.80, development_adjacency_radius=0.018),
    ),
    VisualCase(
        "clear_base_border",
        "scan",
        "color_negative",
        "Scanner clear-base observation border",
        lambda config: setattr(config.scanner, "include_clear_base_border", True),
    ),
)


def _base_config(seed: int) -> DarkroomConfig:
    config = DarkroomConfig()
    config.processing.execution_mode = "quality"
    config.processing.quality_mode = "standard"
    config.seed_strategy = "fixed"
    config.random_seed = int(seed)
    config.enable_mtf = True
    config.enable_halation = True
    config.enable_grain = True
    config.debug_output = False
    config.comparison_grid = False
    config.scanner.scan_normalize = True
    config.look.exposure_ev = -0.15
    return config


def _hstack(images: list[np.ndarray]) -> np.ndarray:
    height = min(item.shape[0] for item in images)
    width = min(item.shape[1] for item in images)
    tiles = [np.clip(item[:height, :width], 0.0, 1.0) for item in images]
    return np.concatenate(tiles, axis=1)


def _contact_sheet(images: list[np.ndarray], columns: int = 3) -> np.ndarray:
    if not images:
        raise ValueError("contact sheet requires images")
    height = min(item.shape[0] for item in images)
    width = min(item.shape[1] for item in images)
    tiles = [np.clip(item[:height, :width], 0.0, 1.0) for item in images]
    columns = max(1, int(columns))
    rows: list[np.ndarray] = []
    for start in range(0, len(tiles), columns):
        row = list(tiles[start:start + columns])
        while len(row) < columns:
            row.append(np.zeros_like(tiles[0]))
        rows.append(np.concatenate(row, axis=1))
    return np.concatenate(rows, axis=0)


def _absdiff(sample: np.ndarray, reference: np.ndarray, gain: float = 6.0) -> np.ndarray:
    sample = np.asarray(sample, dtype=np.float32)
    reference = np.asarray(reference, dtype=np.float32)
    height = min(sample.shape[0], reference.shape[0])
    width = min(sample.shape[1], reference.shape[1])
    delta = np.abs(sample[:height, :width] - reference[:height, :width])
    return np.clip(delta * float(gain), 0.0, 1.0)


def _edge_energy(image: np.ndarray, band: int = 18) -> dict[str, float]:
    values = np.asarray(image, dtype=np.float32)
    height, width = values.shape[:2]
    band = max(2, min(int(band), height // 4, width // 4))
    return {
        "top": float(np.mean(values[:band])),
        "bottom": float(np.mean(values[-band:])),
        "left": float(np.mean(values[:, :band])),
        "right": float(np.mean(values[:, -band:])),
        "center": float(np.mean(values[band:-band, band:-band])),
        "mean": float(np.mean(values)),
    }


def run_visual_feature_audit(
    output_dir: Path,
    *,
    width: int = 960,
    height: int = 640,
    seed: int = 20260905,
) -> dict[str, object]:
    scene = build_review_scene(width, height)
    output = OutputConfig(format="png", bit_depth=8)
    scans: dict[str, np.ndarray] = {}
    reports: dict[str, object] = {}
    with atomic_output_directory(output_dir) as staging:
        scans_dir = staging / "scans"
        diffs_dir = staging / "diffs"
        sheets_dir = staging / "sheets"
        scans_dir.mkdir(parents=True, exist_ok=True)
        diffs_dir.mkdir(parents=True, exist_ok=True)
        sheets_dir.mkdir(parents=True, exist_ok=True)
        save_image(scene, scans_dir / "00_source.png", output)

        for case in VISUAL_CASES:
            config = _base_config(seed)
            case.mutate(config)
            started = time.perf_counter()
            scan = process_array(scene, config, rng=np.random.default_rng(seed))
            elapsed = time.perf_counter() - started
            scans[case.key] = scan
            save_image(scan, scans_dir / f"{case.key}.png", output)
            payload: dict[str, object] = {
                "family": case.family,
                "description": case.description,
                "elapsed_seconds": elapsed,
                "shape": [int(value) for value in scan.shape],
                "edge_energy": _edge_energy(scan),
                "mean": float(np.mean(scan)),
                "std": float(np.std(scan)),
            }
            if case.compare_to and case.compare_to in scans:
                reference = scans[case.compare_to]
                diff = _absdiff(scan, reference)
                save_image(diff, diffs_dir / f"{case.key}_vs_{case.compare_to}.png", output)
                pair = _hstack([reference, scan, diff])
                save_image(pair, diffs_dir / f"{case.key}_triplet.png", output)
                payload["mean_abs_delta"] = float(np.mean(np.abs(
                    scan[: reference.shape[0], : reference.shape[1]] - reference
                )))
                payload["compare_to"] = case.compare_to
            reports[case.key] = payload

        families: dict[str, list[np.ndarray]] = {}
        for case in VISUAL_CASES:
            families.setdefault(case.family, []).append(scans[case.key])
        for family, images in families.items():
            save_image(_contact_sheet(images, columns=min(3, len(images))), sheets_dir / f"{family}.png", output)

        summary = {
            "purpose": "full_feature_visual_review",
            "runtime": {
                "width": int(width),
                "height": int(height),
                "seed": int(seed),
                "quality_mode": "standard",
                "execution_mode": "quality",
            },
            "cases": reports,
            "notes": [
                "Images are scanned sRGB observations, not the FilmFinalMedium itself.",
                "Difference maps are amplified absolute RGB deltas against the named reference.",
                "Canonical quality path is used so reduced_fast does not hide formation direction.",
            ],
        }
        atomic_write_json(staging / "visual_feature_audit.json", summary)
    return summary


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--width", type=int, default=960)
    parser.add_argument("--height", type=int, default=640)
    parser.add_argument("--seed", type=int, default=20260905)
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path("outputs") / "2026-09-05_visual_feature_audit",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    summary = run_visual_feature_audit(
        args.output_dir,
        width=args.width,
        height=args.height,
        seed=args.seed,
    )
    print(f"wrote {args.output_dir}")
    print(f"cases {len(summary['cases'])}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
