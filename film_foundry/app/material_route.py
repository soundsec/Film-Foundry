"""Selectable entry/exit stages; optical relays are optional edges, not a fixed chain.

Frontends can retain each returned state/medium and submit another route. Each
family still owns its legal operations; no new chemistry lives in this module.
"""
from dataclasses import dataclass
from copy import deepcopy
import json
import numpy as np
from film_foundry.contracts.optical_cascade import CASCADE_KEY, validate_cascade
from film_foundry.core.material_family import MediumBody, ProductGroup, ViewingConditions
from film_foundry.core.states import DevelopedNegative
from film_foundry.core.film_process import FilmProcessState


@dataclass(frozen=True)
class RouteResult:
    endpoint: str
    value: object
    # An observed image retains its forming medium; it is not the new authority.
    medium: object = None
    cascade: dict | None = None
    encoding: str | None = None


def run_material_route(source, *, receiver=None, stop='medium', config=None, material=None,
                       operations=(), relay=None, source_body_id=None, result_body_id=None, result_body_role=None,
                       source_name='', input_encoding='srgb', seed=0, cascade=None,
                       view=ViewingConditions()):
    """Start at an image, latent/family state, or finished medium.

    receiver='film' or a registered family selects a *new* material only for
    image/finished-medium starts. A latent/state continues its frozen material.
    receiver=None on a finished medium observes/returns it without re-exposure.
    stop='state' permits a family to pause after any supplied legal operation;
    stop='medium' finishes it; stop='image' observes the finished medium.
    Film observations use config.scanner, other families use view. Neither is
    used as the input to an optical relay, which reads the material itself.
    """
    from film_foundry.model.config import DarkroomConfig
    from film_foundry.core.engine import expose_image_to_latent, expose_linear_to_latent, develop_negative_from_latent
    from film_foundry.core.color import srgb_to_linear, linear_to_srgb
    from film_foundry.app import material_families as families
    from film_foundry.app.optical_cascade import expose_on_film, expose_on_family, select_body, _parent_record
    if stop not in ('state', 'medium', 'image'):
        raise ValueError('Unknown material route endpoint')
    if input_encoding not in ('srgb', 'linear_rgb'):
        raise ValueError('Declare srgb or linear_rgb input')
    if result_body_id is not None and result_body_role is not None:
        raise ValueError('Select an output body by identity or role, not both')
    config = config if config is not None else DarkroomConfig()
    trace = validate_cascade(cascade) if cascade is not None else None
    finished = isinstance(source, (DevelopedNegative, MediumBody, ProductGroup))
    if relay is not None and not (finished and receiver is not None):
        raise ValueError('An optical relay applies only between a finished medium and a new receiver')
    if material is not None and (not (finished or isinstance(source,np.ndarray)) or receiver in (None,'film')):
        raise ValueError('Material definitions apply only to a new family receiver')
    if not isinstance(operations,(tuple,list)) or any(not isinstance(op,dict) or not isinstance(op.get('operation'),str) or not op['operation'] for op in operations):
        raise ValueError('Family operations must be an ordered array of operation objects')
    state_family = families.state_family(source)
    if finished and receiver is None:
        if stop == 'state' or operations:
            raise ValueError('A finished source requires a receiver for another forming process')
        medium = source
        parent = _parent_record(source)
        trace = validate_cascade(parent) if parent is not None else None
    else:
        if isinstance(source, FilmProcessState):
            if receiver not in (None, 'film') or material is not None:
                raise ValueError('A latent must continue its frozen material before optical re-exposure')
            state = source.copy()
            receiver = 'film'
        elif state_family is not None:
            if receiver not in (None, state_family) or material is not None:
                raise ValueError('A process state must continue its own material family')
            state = source
            receiver = state_family
        else:
            if receiver is None:
                raise ValueError('Choose a receiver for an image or a new exposure')
            if receiver != 'film' and material is None:
                raise ValueError('Supply the selected family material definition')
            if finished:
                if receiver == 'film':
                    state, trace = expose_on_film(source, config, relay, body_id=source_body_id,
                        source_name=source_name, rng=np.random.default_rng(seed))
                else:
                    state = expose_on_family(source, receiver, material, relay,
                        body_id=source_body_id, source_name=source_name, seed=seed)
            elif isinstance(source, np.ndarray):
                if receiver == 'film':
                    expose = expose_image_to_latent if input_encoding == 'srgb' else expose_linear_to_latent
                    state = expose(source, config, rng=np.random.default_rng(seed))
                else:
                    image = srgb_to_linear(source) if input_encoding == 'srgb' else source
                    from uuid import uuid4
                    state = families.family(receiver).expose(image, material, group_id=str(uuid4()), seed=seed)
                    from film_foundry.app.material_process_record import record_initial_exposure
                    state = record_initial_exposure(families.binding(receiver)[0],state)
            else:
                raise ValueError('Unsupported route starting state')
        if receiver == 'film':
            if operations:
                raise ValueError('Film baths belong to config.chemistry, not family operations')
            if stop == 'state':
                return RouteResult(stop, state, cascade=trace)
            medium = develop_negative_from_latent(state, config, rng=np.random.default_rng(seed))
            if trace is not None:
                medium.metadata['film_process_model'][CASCADE_KEY] = deepcopy(trace)
        else:
            family = families.family(receiver)
            from film_foundry.app.material_process_record import record_transition
            module=families.binding(receiver)[0]
            for operation in operations:
                parameters = dict(operation)
                action = parameters.pop('operation')
                candidate = family.process(state, action, **parameters)
                state = record_transition(module,state,candidate,action,parameters,strict=False)
            trace = json.loads(state.process_json).get(CASCADE_KEY)
            if stop == 'state':
                return RouteResult(stop, state, cascade=deepcopy(trace))
            medium = family.finish(state)
    if stop == 'medium':
        return RouteResult(stop, medium, medium, trace)
    if isinstance(medium, DevelopedNegative):
        from film_foundry.core.scan_runtime import scan_medium_output
        image = scan_medium_output(medium, config).output_srgb
    else:
        if result_body_role is not None:
            bodies=medium.bodies if isinstance(medium,ProductGroup) else (medium,)
            matches=[b for b in bodies if b.role==result_body_role]
            if len(matches)!=1:raise ValueError('Output role must identify exactly one finished body')
            body=matches[0]
        else:body = select_body(medium, result_body_id or (source_body_id if receiver is None else None))
        image = linear_to_srgb(families.observe(body, view))
    return RouteResult(stop, image, medium, trace, 'display_srgb')


def run_material_route_file(input_path, output_path, *, start, receiver=None, stop='medium',
                            config=None, material_parameters=None, operations=(), relay=None,
                            source_body_id=None, result_body_id=None, result_body_role=None, seed=0, control=None,
                            view=ViewingConditions()):
    """Reference file frontend, with explicit start/stop and separately saved media.

    An image endpoint that forms a new medium also writes a sibling formed
    archive. The image never replaces that authoritative intermediate.
    """
    from pathlib import Path
    from film_foundry.app.material_image_io import save_observation
    from film_foundry.model.config import DarkroomConfig
    from film_foundry.app import material_families as families
    from film_foundry.app.optical_cascade import load_source
    from film_foundry.core.latent_io import load_exposed_latent, save_exposed_latent, latent_identity_from_film, LATENT_SUFFIX
    from film_foundry.core.medium_export import save_developed_medium_at_path,developed_medium_output_paths
    from film_foundry.core.atomic_io import atomic_path_set
    from dataclasses import asdict
    input_path=Path(input_path); output_path=Path(output_path)
    if input_path.resolve()==output_path.resolve():
        raise ValueError('Route output must be separate from the source')
    if stop not in ('state','medium','image'):
        raise ValueError('Unknown material route endpoint')
    if stop=='image' and output_path.suffix.lower() not in ('.png','.tif','.tiff'):
        raise ValueError('Route observation output must be PNG or TIFF')
    if stop!='image' and output_path.suffix.lower()!='.npz':
        raise ValueError('Route material/state output must be NPZ')
    config=config if config is not None else DarkroomConfig()
    def stage(name):
        if control is not None:
            control.check_cancel()
            control.report('route',name)
    stage('loading');trace=None;latent_metadata=None;input_color=None
    if start=='image':
        from film_foundry.core.source_image import load_formation_image
        source,input_color=load_formation_image(input_path,input_icc_policy=config.input_icc_policy)
    elif start=='latent':
        source,latent_metadata=load_exposed_latent(input_path);trace=latent_metadata.get(CASCADE_KEY)
    elif start=='medium':
        source=load_source(input_path,reflection=True)
    elif start=='state':
        source=families.load(input_path)
        if isinstance(source,ProductGroup):
            raise ValueError('Choose medium as the start for final products')
    else:raise ValueError('Unknown route starting kind')
    material=families.material_definition(receiver,material_parameters) if receiver not in (None,'film') and start in ('image','medium') else None
    if material_parameters is not None and material is None:
        raise ValueError('Material parameters apply only to a new family receiver')
    stage('exposing' if start=='image' or receiver is not None and start=='medium' else 'developing')
    result=run_material_route(source,receiver=receiver,stop=stop,config=config,material=material,
                              input_encoding=input_color["encoding"] if input_color else "srgb",
        operations=operations,relay=relay,source_body_id=source_body_id,result_body_id=result_body_id,result_body_role=result_body_role,
        source_name=input_path.name,seed=seed,cascade=trace,view=view)
    # Continuing an exposure keeps its identity. Optical re-exposure deliberately
    # starts a new identity and only refers to the parent in the cascade record.
    if latent_metadata is not None and isinstance(result.medium,DevelopedNegative):
        from film_foundry.core.frame_identity import attach_frame_identity
        attach_frame_identity(result.medium.metadata,latent_metadata.get('frame_identity'))
        if isinstance(latent_metadata.get('capture_incident_identity'),dict):
            result.medium.metadata['film_process_model']['capture_incident_identity']=deepcopy(latent_metadata['capture_incident_identity'])
    capture = input_color or (latent_metadata or {}).get('input_color')
    if capture and isinstance(result.medium, DevelopedNegative):
        result.medium.metadata['input_color'] = deepcopy(capture)
    stage('saving')
    def save_medium(medium,path):
        if path.resolve()==input_path.resolve():
            raise ValueError('Formed companion must not replace the source')
        if isinstance(medium,DevelopedNegative):
            save_developed_medium_at_path(input_path,path,medium,config,resolved_seed=seed)
        else:families.save(path,medium)
    medium_path=None
    if stop=='state':
        if isinstance(result.value,FilmProcessState):
            if not output_path.name.endswith(LATENT_SUFFIX):
                raise ValueError('Film state output requires .darkroom_latent.npz')
            identity=latent_identity_from_film(config.film)
            if capture:identity["input_color"]=deepcopy(capture)
            if latent_metadata is not None:
                for key in ('frame_identity','capture_incident_identity'):
                    if key in latent_metadata:identity[key]=deepcopy(latent_metadata[key])
            if result.cascade is not None:identity[CASCADE_KEY]=result.cascade
            save_exposed_latent(output_path,result.value,identity=identity)
        else:families.save(output_path,result.value)
    elif stop=='medium':
        save_medium(result.value,output_path);medium_path=output_path
    else:
        observe_only=start=='medium' and receiver is None
        if observe_only:
            medium_path=input_path
        else:
            suffix='.darkroom_positive.npz' if isinstance(result.medium,DevelopedNegative) and result.medium.image_polarity=='positive' else '.darkroom_negative.npz' if isinstance(result.medium,DevelopedNegative) else '.ffmaterial.npz'
            medium_path=output_path.with_name(output_path.stem+'.formed'+suffix)
        details={'model':'material_route_observation_v1','medium_name':medium_path.name,
                 'encoding':result.encoding,'cascade':result.cascade,
                 'scanner':asdict(config.scanner) if isinstance(result.medium,DevelopedNegative) else None,
                 'view':asdict(view) if not isinstance(result.medium,DevelopedNegative) else None}
        targets=[output_path,output_path.with_suffix(output_path.suffix+'.json')]
        if not observe_only:
            targets.extend(developed_medium_output_paths(medium_path) if isinstance(result.medium,DevelopedNegative) else (medium_path,))
        source_path=input_path.resolve()
        for target in targets:
            resolved=target.resolve()
            if resolved==source_path or resolved in source_path.parents:
                raise ValueError('Route output set must not replace its source or a directory containing it')
        with atomic_path_set(targets):
            if not observe_only:save_medium(result.medium,medium_path)
            stage('saving')
            save_observation(result.value,output_path,details,bit_depth=config.output.bit_depth,protected_paths=(input_path,medium_path))
            if control:control.check_cancel()
    # Once publication succeeds, cancellation belongs to the next operation.
    from film_foundry.app.control import WorkflowCancelled
    try:stage('completed')
    except WorkflowCancelled:pass
    return {'endpoint':stop,'output_path':str(output_path),'medium_path':str(medium_path) if medium_path else None}
