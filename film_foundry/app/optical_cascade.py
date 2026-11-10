"""A finished medium illuminates another material; each stage stays independent."""
from copy import deepcopy
import json
import math
from pathlib import Path
from uuid import uuid4
import numpy as np
from film_foundry.contracts.optical_cascade import OpticalRelay, CASCADE_KEY, CASCADE_MODEL, validate_cascade
from film_foundry.core.material_family import MediumBody, ProductGroup, ViewingConditions
from film_foundry.core.states import DevelopedNegative
from film_foundry.core.optical_relay import transmission_radiance, relay_dose


def select_body(source, body_id=None):
    if isinstance(source, ProductGroup):
        bodies = source.bodies if body_id is None else tuple(b for b in source.bodies if b.body_id == body_id)
        if len(bodies) != 1:
            raise ValueError('Select exactly one source body from the product group')
        return bodies[0]
    if body_id is not None and (not isinstance(source, MediumBody) or source.body_id != body_id):
        raise ValueError('Source body identity does not match')
    if not isinstance(source, (DevelopedNegative, MediumBody)):
        raise ValueError('Optical relay requires a finished medium, not a latent or unfinished state')
    return source


def _view(relay):
    def direction(degrees):
        angle = math.radians(degrees)
        return (math.sin(angle), 0., math.cos(angle))
    return ViewingConditions(illuminant_rgb=relay.illuminant_rgb, background_rgb=relay.background_rgb,
        light_direction=direction(relay.light_angle), view_direction=direction(relay.view_angle))


def project_medium(source, relay=None, *, body_id=None):
    """Return relative linear RGB dose; never call a scanner or encode sRGB.

    The current relay deliberately uses RGB optics, also for spectral archives.
    It does not claim a wavelength-resolved emitter-to-receiver integration.
    """
    relay = OpticalRelay.from_value(relay)
    body = select_body(source, body_id)
    if isinstance(body, DevelopedNegative):
        if body.optical_density_rgb is None:
            raise ValueError('Optical relay requires a saved authoritative optical master')
        if relay.mode == 'transmission':
            if body.view_mode != 'transmissive':
                raise ValueError('Source does not declare transmission observation')
            image = transmission_radiance(body.optical_density_rgb, relay.illuminant_rgb)
        else:
            from film_foundry.core.film_surface_optics import observe_surface
            image = observe_surface(body, _view(relay))
    else:
        if (relay.mode == 'transmission') != body.usable_negative:
            raise ValueError('Use transmission for a usable donor negative, reflection for an opaque body')
        from film_foundry.app.material_families import observe
        image = observe(body, _view(relay))
    return relay_dose(image, relay)


def _parent_record(source):
    if isinstance(source, DevelopedNegative):
        return source.metadata.get('film_process_model', {}).get(CASCADE_KEY)
    if isinstance(source, ProductGroup):
        return json.loads(source.provenance_json).get('process', {}).get(CASCADE_KEY)
    return None


def source_reference(source, relay, *, body_id=None, source_name=''):
    """Small provenance snapshot for an already-projected exposure image."""
    body = select_body(source, body_id)
    parent = _parent_record(source)
    descriptor = {'name': Path(source_name).name if source_name else '', 'encoding': 'relative_linear_rgb'}
    if isinstance(body, DevelopedNegative):
        descriptor.update(kind='film', family=body.medium_family, polarity=body.image_polarity,
            observation='optical_density_rgb' if relay.mode == 'transmission' else 'deposited_silver_rgb_scattering_v1',
            spectral_source=body.spectral_medium is not None)
        if body.metadata.get('frame_identity'):
            descriptor['frame_identity'] = deepcopy(body.metadata['frame_identity'])
    else:
        descriptor.update(kind='body', family=body.family, group_id=body.group_id, body_id=body.body_id,
                          role=body.role, observation=body.observation_model)
    return {'source':descriptor,'parent':validate_cascade(parent) if parent is not None else None}


def record_reference(reference, relay, target, shape):
    """Append provenance without reprojecting or needing the upstream pixels."""
    if not isinstance(reference,dict) or set(reference)!={'source','parent'} or not isinstance(reference['source'],dict):
        raise ValueError('Invalid optical source reference')
    descriptor=deepcopy(reference['source']);parent=reference['parent']
    steps = validate_cascade(parent)['steps'] if parent is not None else []
    if len(steps) >= 16:
        raise ValueError('Optical cascade is limited to 16 recorded stages')
    steps.append({'id': str(uuid4()), 'source': descriptor, 'target': target, 'relay': relay.as_dict(),
                  'output_shape': list(shape[:2]), 'dose_units': 'relative_rgb_seconds',
                  'spectral_transport': 'rgb_reduced', 'field_mapping': 'same_extent_resampled'})
    return validate_cascade({'model': CASCADE_MODEL, 'steps': steps})


def _record(source, relay, target, shape, *, body_id=None, source_name=''):
    return record_reference(source_reference(source,relay,body_id=body_id,source_name=source_name),relay,target,shape)


def expose_on_film(source, config, relay=None, *, body_id=None, source_name='', rng=None):
    """Return an independent frozen latent plus its cascade record, before baths."""
    from film_foundry.core.engine import expose_linear_to_latent
    relay = OpticalRelay.from_value(relay)
    body = select_body(source, body_id)
    shape = relay.output_shape or (body.frame_shape[:2] if isinstance(body, DevelopedNegative) else next(iter(body.fields.values())).shape[:2])
    record = _record(source, relay, {'kind': 'film', 'family': config.film.medium_family,
                    'receiver_exposure_ev': config.look.exposure_ev}, shape, body_id=body_id, source_name=source_name)
    image = project_medium(source, relay, body_id=body_id)
    # relay_dose already multiplied photons by this time. Pass the same
    # clock separately for reciprocity, replacing a stale photo-workspace clock.
    config = deepcopy(config)
    config.exposure.duration_seconds = relay.exposure_seconds or None
    state = expose_linear_to_latent(image, config, rng=rng)
    return state, record


def expose_on_family(source, family_name, material, relay=None, *, body_id=None, source_name='', seed=0):
    """Existing instant/peel/plate receivers retain their own process vocabulary."""
    from film_foundry.app.material_families import family
    relay = OpticalRelay.from_value(relay)
    body = select_body(source, body_id)
    shape = relay.output_shape or (body.frame_shape[:2] if isinstance(body, DevelopedNegative) else next(iter(body.fields.values())).shape[:2])
    group_id = str(uuid4())
    record = _record(source, relay, {'kind': 'family', 'family': family_name, 'group_id': group_id, 'seed': seed},
                     shape, body_id=body_id, source_name=source_name)
    state = family(family_name).expose(project_medium(source, relay, body_id=body_id), material, group_id=group_id, seed=seed)
    from film_foundry.app.material_families import binding
    from film_foundry.app.material_process_record import record_initial_exposure
    state = record_initial_exposure(binding(family_name)[0],state)
    return state.with_annotations(**{CASCADE_KEY:record})


def load_source(path, *, reflection=False):
    from film_foundry.core.negative_io import load_developed_medium_for_scan
    from film_foundry.app.material_families import load
    with np.load(path, allow_pickle=False) as archive:
        is_family = 'manifest' in archive.files
    if is_family:
        source = load(path)
        if not isinstance(source, ProductGroup):
            raise ValueError('Save final products before using them as an optical source')
        return source
    return load_developed_medium_for_scan(path, include_process_surface=reflection)


def source_choices(path):
    """Read small archive descriptors for a chooser, without loading image pools."""
    with np.load(path,allow_pickle=False) as archive:
        if 'manifest' not in archive.files:
            if 'optical_density_rgb' not in archive.files:raise ValueError('Choose a finished optical medium')
            return ({'body_id':None,'role':'film','mode':'transmission'},)
        manifest=json.loads(str(archive['manifest']))
    if manifest.get('kind')!='products':raise ValueError('Choose final products; processing states cannot be optical sources')
    return tuple({'body_id':body['body_id'],'role':body['role'],
                  'mode':'transmission' if body['usable_negative'] else 'reflection'} for body in manifest['bodies'])


def expose_medium_file(source_path, output_path, config, relay=None, *, body_id=None, frame_identity=None, control=None):
    """Commit a second exposure through the ordinary shared latent archive."""
    from film_foundry.app.workflow import rng_and_resolved_seed_for_develop
    from film_foundry.core.latent_io import save_exposed_latent, latent_identity_from_film, LATENT_SUFFIX
    from film_foundry.core.frame_identity import attach_frame_identity
    source_path = Path(source_path); output_path = Path(output_path)
    if not output_path.name.endswith(LATENT_SUFFIX) or source_path.resolve() == output_path.resolve():
        raise ValueError('Choose a separate .darkroom_latent.npz output')
    relay = OpticalRelay.from_value(relay)
    def stage(name):
        if control is not None:
            control.check_cancel(); control.report('reexpose', name)
    stage('loading')
    source = load_source(source_path, reflection=relay.mode == 'reflection')
    stage('exposing')
    rng, seed = rng_and_resolved_seed_for_develop(source_path, config)
    latent, record = expose_on_film(source, config, relay, body_id=body_id, source_name=source_path.name, rng=rng)
    record['steps'][-1]['target']['resolved_seed'] = seed
    identity = latent_identity_from_film(config.film)
    identity[CASCADE_KEY] = record
    attach_frame_identity(identity, frame_identity)
    stage('saving')
    return save_exposed_latent(output_path, latent, identity=identity)
