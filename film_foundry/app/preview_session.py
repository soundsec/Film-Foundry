"""Bounded, UI-free preview reuse across observation-only edits."""

import copy
from dataclasses import asdict
from pathlib import Path
from threading import Lock

import numpy as np

from film_foundry.core.engine import develop_negative, scan_medium_output
from film_foundry.core.source_image import load_formation_image, exposure_preview
from film_foundry.core.preview import resize_to_long_edge
from film_foundry.app.workflow import rng_for_develop
from film_foundry.core.file_cache import file_revision


class PreviewSession:
    """One decoded frame and one deterministic formed medium; no disk cache.

    Random runs still form afresh. Fixed/path runs reuse their medium only when
    every formation setting and the source revision match. No image hashing.
    """

    def __init__(self):
        self._lock = Lock()
        self._source_key = None
        self._image = None
        self._formation_key = None
        self._medium = None
        self._rendered_key = self._rendered = None

    def clear(self):
        with self._lock:
            self._source_key = self._image = self._formation_key = self._medium = None
            self._rendered_key = self._rendered = None

    def render(self, path: Path, config, *, long_edge: int, path_seed_offset=None):
        with self._lock:
            path = Path(path).resolve()
            source_key = (file_revision(path), long_edge, config.input_icc_policy)
            if source_key != self._source_key:
                self._medium = self._formation_key = self._image = self._source_key = None
                image, color = load_formation_image(path, input_icc_policy=config.input_icc_policy)
                self._encoding = color["encoding"]
                image = resize_to_long_edge(image, long_edge)
                image.setflags(write=False)
                self._image, self._source_key = image, source_key
            return self._render(path, config, path_seed_offset)

    def render_target(self, kind, config, *, long_edge=480, comparison_seed=13579):
        """Same film pipeline, synthetic source and stable comparison identity."""
        from film_foundry.app.diagnostic_targets import diagnostic_target, target_identity
        config = copy.deepcopy(config)
        config.seed_strategy = 'fixed'
        config.random_seed = comparison_seed
        with self._lock:
            key = target_identity(kind, long_edge)
            if key != self._source_key:
                self._medium = self._formation_key = None
                self._image = diagnostic_target(kind, long_edge=long_edge)
                self._image.setflags(write=False)
                self._encoding = 'srgb'
                self._source_key = key
            return self._render(Path('diagnostic_' + kind), config, None)

    def _render(self, path, config, path_seed_offset):
        rendered_key = (self._source_key, asdict(config), path_seed_offset)
        reusable = config.seed_strategy in {'fixed', 'path'}
        if reusable and rendered_key == self._rendered_key:
            return self._rendered[0].copy(), self._rendered[1].copy(), True
        values = asdict(config)
        values['path_seed_offset'] = path_seed_offset
        # Only the established observation bank is excluded. Unknown/new
        # formation fields automatically invalidate the cache.
        for key in ("scanner", "output", "debug_output", "save_sidecar", "comparison_grid"):
            values.pop(key)
        for key in ("print_contrast", "print_exposure_ev"):
            values["look"].pop(key)
        reusable = config.seed_strategy in {"fixed", "path"}
        hit = reusable and values == self._formation_key and self._medium is not None
        if not hit:
            self._medium = self._formation_key = None
            medium = develop_negative(self._image, config, input_encoding=self._encoding, rng=rng_for_develop(path, config, path_seed_offset=path_seed_offset))
            if reusable:
                # Preview needs observation only, never Inspector history.
                for name in ("linear_input", "after_mtf", "after_halation"):
                    setattr(medium, name, np.empty((0, 0, 3), dtype=np.float32))
                if medium.optical_density_rgb is not None:
                    medium.density_cmy = np.empty((0, 0, 3), dtype=np.float32)
                    medium.density_grain = np.empty((0, 0, 3), dtype=np.float32)
                    storage = dict(medium.metadata.get("stage_storage", {}))
                    storage.update({
                        "profile": "scan_optical_only_v1",
                        "layer_masters": "discarded_after_optical_compose",
                        "optical_master": "resident_authoritative",
                        "reversible_full_path": "clear preview cache and form again",
                    })
                    medium.metadata["stage_storage"] = storage
                self._medium, self._formation_key = medium, copy.deepcopy(values)
        else:
            medium = self._medium
        scanned = scan_medium_output(medium, config, config.scanner.interpretation_mode)
        source = exposure_preview(self._image, self._encoding)
        result = scanned.output_srgb
        if reusable:
            self._rendered_key = rendered_key
            self._rendered = (source.copy(), result.copy())
        return source, result, hit
