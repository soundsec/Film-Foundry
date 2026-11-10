"""Frozen-recipe, per-reaction continuation over the normal film pipeline.

New boundaries own main reaction pools, the wet surface and carried inhibitor,
plus reusable spatial fields and their random-stream boundary. Each checkpoint
has a fixed program; explicit eligible future-bath revisions create a new file.
Finished observation uses the normal writer.
"""
from copy import deepcopy
from dataclasses import asdict, dataclass
from pathlib import Path
from uuid import uuid4
import numpy as np

from film_foundry.core.film_process.continuation import FilmProcessRun, FilmProcessBoundary, FilmProcessPaused, MODEL
from film_foundry.core.film_process.checkpoint_io import load_checkpoint, save_checkpoint
from film_foundry.core.film_process.operators import FilmProcessStepReport
from film_foundry.core.engine import develop_negative_from_latent
from film_foundry.model.config import DarkroomConfig


@dataclass(frozen=True)
class FilmProcessWorkResult:
    output_path: Path
    status: str
    next_step: int
    total_steps: int
    artifacts: dict


def _rng(value):
    # Application creation uses PCG64. No class lookup or pickle from a file.
    if not isinstance(value, dict) or value.get('bit_generator') != 'PCG64':
        raise ValueError('Unsupported checkpoint random generator')
    rng = np.random.default_rng(); rng.bit_generator.state = deepcopy(value)
    return rng


def _boundary(metadata, state):
    value = metadata['boundary']
    if value['binding'].get('model') != MODEL:
        raise ValueError('Unsupported reaction boundary model')
    cursor = value['next_step']; steps = value['binding']['program']['steps']
    if type(cursor) is not int or not 0 <= cursor <= len(steps):
        raise ValueError('Invalid saved reaction cursor')
    trace = tuple(FilmProcessStepReport(**row) for row in value['trace'])
    # Premature fixing may emit one additional report per completed operator.
    if not cursor <= len(trace) <= 2 * cursor:
        raise ValueError('Reaction trace does not match the saved cursor')
    return FilmProcessBoundary(state, cursor, trace, value['binding'], metadata.get('surface_state'),
        metadata.get('inhibitor_state'),metadata.get('spatial_preparation'))


def describe_work(path):
    from film_foundry.core.film_process.checkpoint_io import inspect_checkpoint
    metadata = inspect_checkpoint(path)
    boundary = _boundary(metadata, None)
    steps = boundary.binding['program']['steps']
    from film_foundry.app.film_process_revision import revision_options
    from film_foundry.app.film_process_capabilities import describe_process_capabilities
    baths=revision_options(metadata)
    return {'run_id': metadata['run_id'], 'next_step': boundary.next_step,
        'total_steps': len(steps), 'steps': steps, 'status': metadata['status'],
        'scope': metadata['scope'], 'source_name': metadata['source_name'],
        'baths':baths,'bath_revisions':metadata.get('bath_revisions',[]),
        'capabilities':describe_process_capabilities(metadata,baths)}


def start_work(source, output, config, *, stop_after=0, control=None, path_seed_offset=None):
    from film_foundry.core.latent_io import load_exposed_latent
    from film_foundry.app.workflow import prepare_develop_runtime_config, rng_and_resolved_seed_for_develop
    source = Path(source).resolve()
    if control: control.check_cancel()
    latent, identity = load_exposed_latent(source)
    runtime, _ = prepare_develop_runtime_config(config)
    rng, seed = rng_and_resolved_seed_for_develop(source, runtime, path_seed_offset=path_seed_offset)
    metadata = {'run_id': str(uuid4()), 'config': asdict(runtime), 'rng_initial': deepcopy(rng.bit_generator.state),
        'resolved_seed': seed, 'source_name': source.name, 'identity': identity,
        'scope': 'frozen_program_wet_history_v5',
        'preparation': 'restore_spatial_fields_rng_and_wet_inventories'}
    return _run(source, output, latent, runtime, metadata, None, stop_after, control)


def continue_work(source, output, *, stop_after=None, control=None, reuse_preparation=True):
    source = Path(source).resolve()
    if control: control.check_cancel()
    metadata, latent, state = load_checkpoint(source)
    if metadata.get('scope') not in ('frozen_program_primary_pools_v1','frozen_program_primary_and_surface_v2','frozen_program_wet_inventory_v3','frozen_program_prepared_inventory_v4','frozen_program_wet_history_v5'):
        raise ValueError('Unsupported film continuation scope')
    if metadata['scope']!='frozen_program_primary_pools_v1' and metadata.get('surface_state') is None:
        raise ValueError('Missing surface inventory in film checkpoint')
    if metadata['scope'] in ('frozen_program_wet_inventory_v3','frozen_program_prepared_inventory_v4','frozen_program_wet_history_v5') and metadata.get('inhibitor_state') is None:
        raise ValueError('Missing inhibitor inventory in film checkpoint')
    if metadata['scope'] in ('frozen_program_prepared_inventory_v4','frozen_program_wet_history_v5') and metadata.get('spatial_preparation') is None:
        raise ValueError('Missing spatial preparation in film checkpoint')
    if metadata['scope']=='frozen_program_wet_history_v5' and metadata['surface_state'].get('model')!='film_surface_boundary_v2':
        raise ValueError('Missing continuous wet history in film checkpoint')
    runtime = DarkroomConfig.from_dict(metadata['config'])
    return _run(source, output, latent, runtime, metadata, _boundary(metadata, state), stop_after, control,
        reuse_preparation=reuse_preparation)


def _run(source, output, latent, config, metadata, boundary, stop_after, control, *, reuse_preparation=True):
    from film_foundry.app.control import WorkflowCancelled
    from film_foundry.app.workflow import save_developed_medium_at_path
    from film_foundry.core.frame_identity import attach_frame_identity, frame_identity_from_mapping
    from film_foundry.contracts.optical_cascade import CASCADE_KEY, validate_cascade
    from film_foundry.core.film_process.checkpoint_io import SUFFIX
    output = Path(output).resolve()
    if output == source:
        raise ValueError('Continuation must preserve its input; choose a new output path')
    if stop_after is not None and not str(output).lower().endswith(SUFFIX):
        raise ValueError(f'Paused output requires {SUFFIX}')
    if stop_after is None and (output.suffix.lower() != '.npz' or str(output).lower().endswith(SUFFIX)):
        raise ValueError('Finished output requires a separate developed .npz path')
    initial_cursor = 0 if boundary is None else boundary.next_step
    progress = {'next_step': initial_cursor, 'total_steps': 0}
    def pause_requested(index, total):
        progress.update(next_step=index, total_steps=total)
        if control:
            try: control.report('film-process', 'developing', index / max(1, total))
            except WorkflowCancelled: return True
        return False
    from film_foundry.core.film_process.preparation import SpatialPreparation
    preparation=(SpatialPreparation(None if boundary is None else boundary.preparation,reuse=reuse_preparation)
        if metadata['scope'] in ('frozen_program_prepared_inventory_v4','frozen_program_wet_history_v5') else None)
    request = FilmProcessRun(boundary, stop_after, pause_requested,
        include_surface=metadata['scope']!='frozen_program_primary_pools_v1',
        include_inhibitor=metadata['scope'] in ('frozen_program_wet_inventory_v3','frozen_program_prepared_inventory_v4','frozen_program_wet_history_v5'),
        preparation=preparation,track_wet_history=metadata['scope']=='frozen_program_wet_history_v5')
    try:
        # The numerical adapter consumes a private latent when beginning. Keep
        # the frozen source for preparation on later resumes, including when
        # the first segment stops before any final-medium compaction.
        medium = develop_negative_from_latent(latent.copy(), config, rng=_rng(metadata['rng_initial']), process_run=request)
    except FilmProcessPaused as paused:
        metadata = dict(metadata, status=paused.reason, boundary={
            'next_step': paused.boundary.next_step, 'trace': [asdict(row) for row in paused.boundary.trace],
            'binding': paused.boundary.binding})
        # Cancellation also preserves a real last-completed boundary. Never
        # write checkpoint contents to a final-medium filename.
        target = output if str(output).lower().endswith(SUFFIX) else output.with_suffix(SUFFIX)
        if target == source:
            raise ValueError('Cancelled continuation must not overwrite its source checkpoint')
        metadata.pop('surface_state',None)
        metadata.pop('inhibitor_state',None)
        metadata.pop('spatial_preparation',None)
        save_checkpoint(target, metadata, latent, paused.boundary.state,surface=paused.boundary.surface,
            inhibitor=paused.boundary.inhibitor,preparation=paused.boundary.preparation)
        return FilmProcessWorkResult(target, paused.reason, progress['next_step'], progress['total_steps'], {'checkpoint': str(target)})
    identity = metadata['identity']
    if CASCADE_KEY in identity:
        medium.metadata['film_process_model'][CASCADE_KEY] = validate_cascade(identity[CASCADE_KEY])
    if isinstance(identity.get('capture_incident_identity'), dict):
        medium.metadata['film_process_model']['capture_incident_identity'] = deepcopy(identity['capture_incident_identity'])
    attach_frame_identity(medium.metadata, frame_identity_from_mapping(identity))
    medium.metadata['film_process_continuation'] = {'model': MODEL, 'run_id': metadata['run_id'],
        'resumed_from_step': initial_cursor, 'completed_steps': progress['total_steps'], 'scope': metadata['scope'],
        'bath_revisions':deepcopy(metadata.get('bath_revisions',[]))}
    paths = save_developed_medium_at_path(source, output, medium, config, resolved_seed=metadata['resolved_seed'])
    return FilmProcessWorkResult(output, 'completed', progress['next_step'], progress['total_steps'], paths)
