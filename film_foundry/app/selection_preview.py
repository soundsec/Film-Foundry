"""Small reference recipes for catalog browsing; never mutate a work session."""
from copy import deepcopy
from pathlib import Path
from dataclasses import asdict
from io import BytesIO
import numpy as np

from film_foundry import __version__
from film_foundry.app.preview_session import PreviewSession
from film_foundry.app.diagnostic_targets import diagnostic_target
from film_foundry.model.config import DarkroomConfig, MONO_LEGACY_EXPOSURE, merge_config_presets


class SelectionPreviewSession:
    """One source / formed film cache, owned by the serial thumbnail worker.

    Material choices use their standard process. Process and scanner choices
    use the other banks from the caller's snapshot, composed by the same public
    preset contract as CLI. These are references, not adoption simulations.
    """
    def __init__(self, *, bank='material', base=None, source=None, long_edge=240, cache=None):
        self.cache = cache
        self.bank=bank;self.base=deepcopy(base or DarkroomConfig())
        self.source=Path(source) if source else None;self.long_edge=long_edge
        self.film=PreviewSession();self._linear=None;self._source_revision=None;self._comparison_revision=None

    def check_source(self):
        from film_foundry.core.file_cache import file_revision
        if self.source is None:raise ValueError('No photograph selected')
        revision=file_revision(self.source)
        if self._comparison_revision is None:self._comparison_revision=revision
        elif self._comparison_revision!=revision:raise ValueError('Source changed; reopen the selector')

    @staticmethod
    def preset_path(bank, key):
        from film_foundry.app import workflow
        return getattr(workflow, bank + "_preset_path")(key)

    def film_config(self, record):
        from film_foundry.app import workflow
        from film_foundry.app.preset_pairing import recommended_scanner
        bank='film' if self.bank=='material' else self.bank
        preset=DarkroomConfig.from_json(self.preset_path(bank, record['key']))
        if bank=='film':
            develop=DarkroomConfig.from_json(self.preset_path('develop',workflow.recommended_standard_develop_preset(preset)))
            config=merge_config_presets(preset,develop_config=develop)
            scanner=DarkroomConfig.from_json(self.preset_path('scanner',recommended_scanner(record['key'],config)))
            config=merge_config_presets(preset,scanner,develop)
        else:
            config=merge_config_presets(self.base,preset if bank=='scanner' else self.base,
                                       preset if bank=='develop' else self.base)
        config.seed_strategy='fixed';config.random_seed=13579
        config.save_sidecar=False;config.debug_output=False;config.comparison_grid=False
        return config

    def render(self, record, use_source, control):
        control.check_cancel()
        target = "color24" if use_source == "color24" else "astronaut"
        use_source = use_source is True
        if use_source:self.check_source()
        from film_foundry.app.thumbnail_cache import ThumbnailCache
        from PIL import Image
        cache=self.cache if self.cache is not None else ThumbnailCache()
        config=self.film_config(record) if record.get('family','film')=='film' else None
        recipe = asdict(config) if config is not None else record['material']
        # Old factory thumbnails used the same uncropped scan. Omit only the
        # identity settings so those read-only examples remain usable.
        if config is not None:
            # Serialize the effective formation choices in the existing cache
            # key layout so equivalent factory samples remain reusable.
            recipe['processing'].pop('layered_response')
            recipe['processing'].pop('independent_coating_grain')
            recipe['film']['coating_stack'] = config.formation_film().coating_stack
            if recipe['film'].get('coating_stack') is None:
                recipe['film'].pop('coating_stack', None)
            if recipe['exposure']['rear_optics'] is None:
                del recipe['exposure']['rear_optics']
            if recipe['exposure']['film_side'] == 'base':
                recipe['rear_exposure_revision'] = 3
            if recipe['exposure']['film_side'] == 'emulsion':
                del recipe['exposure']['film_side']
            if recipe['exposure']['lens_filter'] is None:
                del recipe['exposure']['lens_filter']
            for name,default in (('crop_rect',None),('straighten_degrees',0.),('base_sample_rect',None),('base_reference_rgb',None)):
                if recipe['scanner'][name]==default:del recipe['scanner'][name]
        family = record.get('family','film')
        revision = {'plate':'selection-plate-view-v3', 'wet':'selection-process-v2',
                    'peel':'selection-process-v2', 'platinum':'selection-pt-clear-v2'}.get(family, 'selection-v1')
        if config is not None:
            from film_foundry.core.media import is_monochrome_material
            model = recipe['film'].pop('monochrome_exposure_model')
            if is_monochrome_material(config) and (config.film.spectral_exposure_profile is not None or model != MONO_LEGACY_EXPOSURE):
                revision += '-silver-rgb-exposure-v1'
        reference_print = not use_source and target == 'astronaut' and family in ('paper','cyanotype','platinum')
        if reference_print:
            revision += '-contact-photo-v1'
        key=cache.key(dict(version=revision,engine=__version__,family=family,
            recipe=recipe,
            source=self._comparison_revision if use_source else target,edge=self.long_edge,seed=13579))
        cached,generation=cache.lookup(key)
        if not cached and self.cache is None and not use_source and target == "astronaut" and self.long_edge == 240:
            from film_foundry.app.thumbnail_cache import factory_thumbnail
            cached = factory_thumbnail(key)
        if cached:
            try:
                with Image.open(BytesIO(cached)) as image:
                    if image.mode!='RGB' or max(image.size)>self.long_edge:raise ValueError('Invalid thumbnail')
                    pixels=np.array(image)
                control.check_cancel()
                if use_source:self.check_source()
                return pixels
            except (OSError,ValueError):pass
        if config is not None:
            if use_source:
                if self.source is None:raise ValueError('No photograph selected')
                _,result,_=self.film.render(self.source,config,long_edge=self.long_edge)
            else:_,result,_=self.film.render_target(target,config,long_edge=self.long_edge)
        else:
            from film_foundry.app.material_lab_session import MaterialLabSession
            from film_foundry.core.color import srgb_to_linear
            session=MaterialLabSession(record['family'])
            if use_source:
                from film_foundry.app.material_image_io import load_exposure_image
                from film_foundry.core.file_cache import file_revision
                if self.source is None:raise ValueError('No photograph selected')
                revision=file_revision(self.source)
                if self._source_revision!=revision:
                    self._linear=None
                    self._linear,_=load_exposure_image(self.source,self.long_edge)
                    self._source_revision=revision
                session.image=self._linear.copy()
            else:
                session.image=srgb_to_linear(diagnostic_target(target,long_edge=self.long_edge))
                if reference_print:
                    from film_foundry.app.material_reference import example_exposure
                    session.image=example_exposure(session.image,family,record['material'])
            session.run_standard(deepcopy(record['material']),seed=13579,control=control)
            if reference_print:
                control.check_cancel()
                session.process('dry', {'seconds':3600.})
            control.check_cancel();result=session.preview()
        control.check_cancel()
        if use_source:self.check_source()
        if not np.isfinite(result).all():raise ValueError('Non-finite reference preview')
        from film_foundry.core.preview import resize_to_long_edge
        result=resize_to_long_edge(result,self.long_edge)
        pixels=np.ascontiguousarray(np.clip(result*255+.5,0,255).astype(np.uint8))
        stream=BytesIO();Image.fromarray(pixels).save(stream,format='PNG')
        control.check_cancel();cache.put(key,stream.getvalue(),generation)
        return pixels

    def clear(self):
        self.film.clear();self._linear=None;self._source_revision=None
