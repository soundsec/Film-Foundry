"""Three independent silver exposures and one registered additive observation."""
from copy import deepcopy
from dataclasses import asdict, dataclass
from pathlib import Path
from tempfile import TemporaryDirectory

import numpy as np

from film_foundry.app.material_image_io import save_observation
from film_foundry.contracts.lens_filters import lens_filter
from film_foundry.core.color import linear_to_srgb, luminance, srgb_to_linear
from film_foundry.core.file_cache import file_revision
from film_foundry.core.computation_control import checkpoint
from film_foundry.core.engine import develop_negative, scan_medium_output
from film_foundry.core.media import is_monochrome_material
from film_foundry.core.medium_export import save_developed_medium_at_path
from film_foundry.core.negative_io import load_developed_medium_for_scan
from film_foundry.core.preview import resize_to_long_edge
from film_foundry.core.source_image import load_formation_image


CHANNELS = ("red", "green", "blue")


def _row_blocks(shape):
    height, width = shape[:2]
    rows = max(1, 65536 // width)
    for top in range(0, height, rows):
        checkpoint()
        yield slice(top, min(top + rows, height))


def _observation_plane(scanned):
    plane = np.empty(scanned.shape[:2], np.float32)
    for block in _row_blocks(plane.shape):
        rgb = scanned[block]
        first = rgb[..., 0]
        if np.array_equal(first, rgb[..., 1]) and np.array_equal(first, rgb[..., 2]):
            plane[block] = first
        else:
            gray = linear_to_srgb(luminance(srgb_to_linear(rgb)), consume_input=True)
            neutral = (first == rgb[..., 1]) & (first == rgb[..., 2])
            np.copyto(gray, first, where=neutral)
            plane[block] = gray
    return plane


def separation_config(config):
    from film_foundry.app.workflow import film_preset_path, develop_preset_path, recommended_standard_develop_preset
    from film_foundry.model.config import DarkroomConfig
    cfg = deepcopy(config)
    if not is_monochrome_material(cfg):
        stock = DarkroomConfig.from_json(film_preset_path("monochrome_fine_negative"))
        bath = DarkroomConfig.from_json(develop_preset_path(recommended_standard_develop_preset(stock)))
        cfg.film, cfg.chemistry = stock.film, bath.chemistry
        cfg.medium, cfg.mode = "film_negative", "bw_negative"
    return cfg


@dataclass
class Separations:
    planes: list
    scale: float
    sources: tuple
    revisions: tuple

    def compose(self, offsets, gains):
        """Offsets are output-space translations in full-size input pixels."""
        if np.shape(offsets) != (3, 2) or not np.issubdtype(np.asarray(offsets).dtype, np.integer):
            raise ValueError("Use three X/Y offsets in whole source pixels")
        if np.shape(gains) != (3,) or not np.isfinite(gains).all() or np.max(np.abs(gains)) > 5:
            raise ValueError("Use three channel exposures between -5 and +5 EV")
        shifts = [(round(x * self.scale), round(y * self.scale)) for x, y in offsets]
        left, top = max(x for x, _ in shifts), max(y for _, y in shifts)
        right = min(x + plane.shape[1] for (x, _), plane in zip(shifts, self.planes))
        bottom = min(y + plane.shape[0] for (_, y), plane in zip(shifts, self.planes))
        if right <= left or bottom <= top:
            raise ValueError("The three plates have no common area")
        result = np.empty((bottom - top, right - left, 3), np.float32)
        for i, ((x, y), plane, gain) in enumerate(zip(shifts, self.planes, gains)):
            checkpoint()
            region = plane[top - y:bottom - y, left - x:right - x]
            if not gain:
                result[..., i] = region
                continue
            factor = 2.0 ** gain
            for block in _row_blocks(region.shape):
                light = srgb_to_linear(region[block])
                light *= factor
                result[block, :, i] = linear_to_srgb(light, consume_input=True)
        return result


def prepare_separations(paths, config, *, seed, compensate=True, preview_edge=0, output_dir=None,
                        published_dir=None, progress=lambda stage: None):
    paths = tuple(Path(p) for p in paths)
    if len(paths) not in (1, 3) or any(not p.is_file() for p in paths):
        raise ValueError("Select one photograph or three developed plates")
    revisions = tuple(map(file_revision, paths))
    if output_dir is not None and preview_edge:
        raise ValueError("Plate exports require full resolution")
    generated = len(paths) == 1
    if generated and not is_monochrome_material(config):
        raise ValueError("Select silver black-and-white film for separation exposures")
    cfg = deepcopy(config)
    scale, planes = 1., []
    if generated:
        image, info = load_formation_image(paths[0], input_icc_policy=cfg.input_icc_policy)
        original_edge = max(image.shape[:2])
        if preview_edge and original_edge > preview_edge:
            image = resize_to_long_edge(image, preview_edge)
            scale = max(image.shape[:2]) / original_edge
        # The three frames share decoded linear light, never their random stream.
        if info["encoding"] != "linear_rgb":
            image = srgb_to_linear(image)
    for i, channel in enumerate(CHANNELS):
        checkpoint()
        progress(channel)
        if generated:
            cfg.exposure.lens_filter = lens_filter(channel, compensate)
            frame_seed = int(np.random.SeedSequence([seed, i]).generate_state(1)[0])
            medium = develop_negative(image, cfg, rng=np.random.default_rng(frame_seed), input_encoding="linear_rgb")
            if output_dir is not None:
                suffix = ".darkroom_positive.npz" if medium.image_polarity == "positive" else ".darkroom_negative.npz"
                name = channel + suffix
                save_developed_medium_at_path(paths[0], Path(output_dir) / name, medium, cfg,
                                              resolved_seed=frame_seed, archive_only=True,
                                              published_path=Path(published_dir or output_dir) / name)
        else:
            medium = load_developed_medium_for_scan(paths[i])
            if medium.color_system not in ("silver_bw", "silver_on_color_material", "monochrome", "monochrome_dye"):
                raise ValueError(f"Expected a monochrome developed plate: {paths[i].name}")
        scanned = scan_medium_output(medium, cfg, cfg.scanner.interpretation_mode).output_srgb
        del medium
        if not generated and preview_edge:
            edge = max(scanned.shape[:2])
            if i == 0:
                scale = min(1., preview_edge / edge)
            if scale < 1.:
                scanned = resize_to_long_edge(scanned, max(1, round(scale * edge)))
        # A separation is a scalar observation. Use display luminance when
        # the chosen scanner tones its neutral silver image.
        planes.append(_observation_plane(scanned))
        del scanned
    if tuple(map(file_revision, paths)) != revisions:
        raise ValueError("Source changed during processing. Reload the plates.")
    return Separations(planes, scale, paths, revisions)


def export_separations(destination, paths, config, *, seed, compensate, offsets, gains, progress=lambda stage: None,
                       expected_revisions=None):
    """Publish the complete new folder together, including the reusable negatives."""
    destination = Path(destination)
    paths = tuple(paths)
    if destination.exists():
        raise FileExistsError(destination)
    if expected_revisions is not None and tuple(map(file_revision, paths)) != expected_revisions:
        raise ValueError("Source changed since preview. Generate the preview again.")
    with TemporaryDirectory(dir=destination.parent, prefix=".film-separations-") as tmp:
        work = Path(tmp) / "result"
        work.mkdir()
        plates = prepare_separations(paths, config, seed=seed, compensate=compensate, output_dir=work,
                                     published_dir=destination, progress=progress)
        if expected_revisions is not None and plates.revisions != expected_revisions:
            raise ValueError("Source changed since preview. Generate the preview again.")
        image = plates.compose(offsets, gains)
        save_observation(image, work / "tricolour.png", {
            "model": "silver_separations_v1", "channels": CHANNELS, "sources": [p.name for p in plates.sources],
            "offset_xy": offsets, "channel_ev": gains, "scanner": asdict(config.scanner),
            "enable_subtractive": config.enable_subtractive, "print_contrast": config.look.print_contrast,
            "print_exposure_ev": config.look.print_exposure_ev,
            **({"seed": seed, "filter_compensation": compensate} if len(plates.sources) == 1 else {}),
        }, bit_depth=16)
        checkpoint()
        work.rename(destination)
    return destination
