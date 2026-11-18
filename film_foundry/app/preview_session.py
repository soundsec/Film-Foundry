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
        self._latent = None
        self._formation_key = None
        self._medium = None
        self._rendered_key = self._rendered = None
        self._profile = None

    def clear(self):
        with self._lock:
            self._source_key = self._image = self._latent = self._formation_key = self._medium = None
            self._rendered_key = self._rendered = self._profile = None

    def clear_probe(self):
        with self._lock:
            self._profile = None
            if self._rendered_key is not None and self._rendered_key[-1]:
                self._rendered_key = self._rendered = None

    def _photo(self, path, policy, long_edge):
        source_key = ('photo', file_revision(path), long_edge, policy)
        if source_key != self._source_key:
            self._source_key = self._image = self._medium = self._formation_key = self._latent = self._profile = None
            self._rendered_key = self._rendered = None
            image, color = load_formation_image(path, input_icc_policy=policy)
            image = resize_to_long_edge(image, long_edge)
            image.setflags(write=False)
            self._image, self._encoding = image, color["encoding"]
            self._source_key = source_key
        return self._image, self._encoding

    def photo(self, path, policy, long_edge):
        with self._lock:
            return self._photo(path, policy, long_edge)

    def latent_copy(self, path, long_edge):
        from film_foundry.core.latent_io import load_exposed_latent
        from film_foundry.app.preview_inputs import preview_latent
        with self._lock:
            key = ('latent', file_revision(path), long_edge)
            if key != self._source_key:
                latent, _ = load_exposed_latent(path)
                if long_edge is not None:
                    latent = preview_latent(latent, long_edge)
                self._image = self._medium = self._formation_key = self._profile = None
                self._rendered_key = self._rendered = None
                self._latent, self._source_key = latent, key
            return self._latent.copy()

    def _develop(self,path,config,path_seed_offset):
        return develop_negative(self._image,config,input_encoding=self._encoding,
            rng=rng_for_develop(path,config,path_seed_offset=path_seed_offset))

    def form_photo(self,path,config,*,long_edge,path_seed_offset=None):
        """Form a private preview medium for inspecting its capture and changing observation."""
        with self._lock:
            path=Path(path).resolve()
            self._photo(path,config.input_icc_policy,long_edge)
            return self._develop(path,config,path_seed_offset)

    def render(self, path: Path, config, *, long_edge: int, path_seed_offset=None):
        with self._lock:
            path = Path(path).resolve()
            self._photo(path, config.input_icc_policy, long_edge)
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
                self._medium = self._formation_key = self._latent = self._profile = None
                self._rendered = self._rendered_key = None
                self._image = diagnostic_target(kind, long_edge=long_edge)
                self._image.setflags(write=False)
                self._encoding = 'srgb'
                self._source_key = key
            return self._render(Path('diagnostic_' + kind), config, None)

    def _render(self, path, config, path_seed_offset):
        from film_foundry.core.formation_observer import observer
        probe = observer.get()
        rendered_key = (self._source_key, asdict(config), path_seed_offset, probe is not None)
        reusable = config.seed_strategy in {'fixed', 'path'}
        if reusable and rendered_key == self._rendered_key:
            if probe is not None:
                probe.scan_geometry = self._scan_geometry
                probe.use_profile(self._profile)
                probe.medium(self._medium)
                probe.expected_pixels = self._image.shape[0]*self._image.shape[1]
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
        if probe is not None and self._profile is None:
            hit = False
        if not hit:
            self._rendered = self._rendered_key = None
            self._profile = None
            self._medium = self._formation_key = None
            medium = self._develop(path,config,path_seed_offset)
            if probe is not None:
                self._profile = probe.profile()
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
        if probe is not None:
            probe.use_profile(self._profile)
            probe.medium(medium)
            probe.expected_pixels = self._image.shape[0]*self._image.shape[1]
        scanned = scan_medium_output(medium, config, config.scanner.interpretation_mode)
        source = exposure_preview(self._image, self._encoding)
        result = scanned.output_srgb
        if reusable:
            self._scan_geometry = probe.scan_geometry if probe is not None else None
            self._rendered_key = rendered_key
            self._rendered = (source.copy(), result.copy())
        return source, result, hit
