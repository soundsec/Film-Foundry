"""Offline developer review of explicitly sourced, rendered film scans.

Not a runtime calibration service. Never interprets a positive film scan as
scene-linear exposure, downloads pictures, or changes a material definition.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageOps

from film_foundry.core.io_utils import load_image_with_color

LUMA_WEIGHTS = np.array([0.2126, 0.7152, 0.0722], dtype=np.float64)


def crop(image: np.ndarray, bounds: list[int]) -> np.ndarray:
    """Explicit half-open pixel rectangle; labels/gutters are not measurements."""
    x0, y0, x1, y1 = bounds
    height, width = image.shape[:2]
    if not (0 <= x0 < x1 <= width and 0 <= y0 < y1 <= height):
        raise ValueError(f"Invalid reference crop {bounds} for {width}x{height}")
    return image[y0:y1, x0:x1]


def brightness(image: np.ndarray) -> np.ndarray:
    # Matches the existing final-output tone control, not radiometric Y.
    return image.astype(np.float64) @ LUMA_WEIGHTS


def describe(image: np.ndarray) -> dict:
    y = brightness(image)
    q = np.percentile(y, [1, 5, 10, 50, 90, 95, 99])
    return {
        "shape": list(image.shape),
        "encoded_luma_quantiles": dict(zip(("p01", "p05", "p10", "p50", "p90", "p95", "p99"), q.tolist())),
        "p90_minus_p10": float(q[4] - q[2]),
        "channel_mean": np.mean(image, axis=(0, 1), dtype=np.float64).tolist(),
        "clipped_channel_fraction": float(np.mean(image >= 254.5 / 255.0)),
    }


def fit_relative_tone(base: np.ndarray, target: np.ndarray, percentiles: list[float]) -> list[list[float]]:
    """Scene-specific histogram relationship; deliberately no color fitting."""
    xs = np.percentile(brightness(base), percentiles)
    ys = np.percentile(brightness(target), percentiles)
    points = [[0.0, 0.0]]
    for x, y in zip(xs, ys):
        x, y = round(float(x), 6), round(float(y), 6)
        if points[-1][0] < x < 1.0:
            points.append([x, y])
    points.append([1.0, 1.0])
    return points


def review(catalog: dict, data_dir: Path, output_dir: Path) -> dict:
    if catalog.get("schema") != "film_scan_reference_review_v1":
        raise ValueError("Unsupported reference catalog")
    root = data_dir.resolve()
    images, colors = {}, {}
    # Filenames are fixed IDs, never URLs or arbitrary paths from a web page.
    for key in catalog["files"]:
        path = (root / f"{key}.jpg").resolve()
        if path.parent != root:
            raise ValueError("Reference ID escapes the local data directory")
        image, color = load_image_with_color(path)
        images[key], colors[key] = image, color
    result = {
        "schema": "rendered_film_scan_measurements_v1",
        "catalog_reviewed_on": catalog["reviewed_on"],
        "measurement_space": "output-encoded sRGB brightness proxy; not scene-linear luminance or film density",
        "color_assumptions": colors,
        "paired_frames": {}, "contrast_panels": {}, "exposure_strips": {}, "relative_tone_curves": {},
        "limits": ["No spatial registration or pixelwise Delta E", "No scene-linear or sensitometric recovery", "No grain/MTF measurements on resized JPEGs", "Tone fits have one scene; endpoints are fixed choices; unsupported ranges remain extrapolation"],
    }
    previews = []
    x0, y0, x1, y1 = catalog["paired_crop_fraction"]
    for frame in catalog["paired_frames"]:
        values = {}
        for scanner in ("n", "f"):
            key = f"pv_{scanner}_{frame}"
            image = images[key]
            h, w = image.shape[:2]
            region = crop(image, [round(w*x0), round(h*y0), round(w*x1), round(h*y1)])
            values[scanner] = describe(region)
            previews.append((key, region))
        result["paired_frames"][frame] = values
    panels = {}
    for key, rectangles in catalog["contrast_panels"].items():
        panels[key] = {}
        result["contrast_panels"][key] = {}
        for label, rectangle in zip(catalog["contrast_labels"], rectangles):
            region = crop(images[key], rectangle)
            panels[key][label] = region
            result["contrast_panels"][key][label] = describe(region)
            previews.append((f"{key}_{label}", region))
    for key, spec in catalog["exposure_strips"].items():
        measurements = []
        for ev, (left, right) in zip(spec["exposure_ev"], spec["panel_x"]):
            region = crop(images[key], [left, spec["photo_y"][0], right, spec["photo_y"][1]])
            measurements.append({"exposure_ev": ev, **describe(region)})
        result["exposure_strips"][key] = measurements
    spec = catalog["tone_fit"]
    base = panels[spec["source"]]["neutral"]
    for label in ("low", "high"):
        result["relative_tone_curves"][label] = {
            "tone_curve": fit_relative_tone(base, panels[spec["source"]][label], spec["percentiles"]),
            "source": spec["source"], "method": spec["method"], "support": spec["support"],
        }
    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "measurements.json").write_text(json.dumps(result, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    # Local inspection sheet only; never a bundled sample image.
    cell_w, cell_h, cols = 380, 450, 2
    sheet = Image.new("RGB", (cell_w*cols, cell_h*((len(previews)+cols-1)//cols)), "#303030")
    draw = ImageDraw.Draw(sheet)
    for index, (label, region) in enumerate(previews):
        thumb = Image.fromarray(np.rint(np.clip(region, 0, 1)*255).astype(np.uint8))
        thumb = ImageOps.contain(thumb, (cell_w-12, cell_h-30))
        left, top = index % cols * cell_w, index // cols * cell_h
        sheet.paste(thumb, (left+(cell_w-thumb.width)//2, top+25))
        draw.text((left+8, top+6), label, fill="white")
    sheet.save(output_dir / "measured_regions.jpg")
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("catalog", type=Path)
    parser.add_argument("data_dir", type=Path, help="Local ID.jpg files from the catalog; no network access")
    parser.add_argument("output_dir", type=Path)
    args = parser.parse_args()
    catalog = json.loads(args.catalog.read_text(encoding="utf-8"))
    result = review(catalog, args.data_dir, args.output_dir)
    for key, scans in result["paired_frames"].items():
        n, f = scans["n"], scans["f"]
        print(f"{key}: N/F p50={n['encoded_luma_quantiles']['p50']:.4f}/{f['encoded_luma_quantiles']['p50']:.4f}, span={n['p90_minus_p10']:.4f}/{f['p90_minus_p10']:.4f}")
    for key, scans in result["contrast_panels"].items():
        print(key, {label: round(value["p90_minus_p10"], 4) for label, value in scans.items()})
    print("Relative tone curves:", json.dumps(result["relative_tone_curves"]))


if __name__ == "__main__":
    main()
