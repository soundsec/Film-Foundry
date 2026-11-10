"""Save a revised future-bath checkpoint without advancing its material."""
from copy import deepcopy
from dataclasses import asdict
from pathlib import Path
from uuid import uuid4

from film_foundry.core.film_process.revision import locked_baths,RevisedPreparation,validate_program_revision,rebind_wet_state
from film_foundry.core.film_process.checkpoint_io import load_checkpoint,save_checkpoint,SUFFIX
from film_foundry.core.film_process.continuation import FilmProcessRun,FilmProcessPaused
from film_foundry.app.film_process_work import FilmProcessWorkResult,_rng
from film_foundry.model.config import DarkroomConfig


def _authored_config(metadata):
    config=DarkroomConfig.from_dict(metadata['config'])
    if config.process_control_mode=='principle':
        # The actual condition compiler can omit a bypassed bath and append a
        # fixed compatibility operation. Its source numbering is authoritative.
        from film_foundry.core.process_controls import condition_driven_config
        config=condition_driven_config(config)
    return config


def revision_options(metadata):
    if metadata.get('scope') not in ('frozen_program_prepared_inventory_v4','frozen_program_wet_history_v5'):return []
    reasons=locked_baths(metadata)
    rows=metadata['surface_state']['definition']['baths']
    config=_authored_config(metadata);recipe=config.chemistry
    # Show authored conditions, before any per-frame variation/projection.
    from film_foundry.core.process_operations import source_bath_rows
    from film_foundry.core.film_process.revision import _program
    authored=source_bath_rows(config,_program(metadata['boundary']['binding']['program']))
    from film_foundry.contracts.bath_solution import ACTIONS
    mapped=len(authored)==len(rows) and all(a['action']==b['action'] for a,b in zip(authored,rows))
    return [dict(bath=i,row=deepcopy(authored[i-1] if mapped else row),reason=reasons.get(i,'') if mapped else 'source_mapping',
        editable=mapped and i not in reasons and row['action'] in ACTIONS,
        defaults={key:getattr(recipe,key) for key in ('time_min','temperature_c','concentration','agitation','agitation_time_s','bath_transfer_s','formation_history_model')})
        for i,row in enumerate(rows,1)]


def revise_work(source,output,updates,*,control=None):
    source=Path(source).resolve();output=Path(output).resolve()
    if source==output or not str(output).lower().endswith(SUFFIX):
        raise ValueError('Save revised baths to a separate .ffprocess.npz checkpoint')
    if control:control.check_cancel()
    meta,latent,state=load_checkpoint(source)
    options={r['bath']:r for r in revision_options(meta)}
    if not options:raise ValueError('Future bath editing requires a v4 or v5 process checkpoint')
    from film_foundry.contracts.bath_overrides import validate_bath_overrides
    updates=validate_bath_overrides(updates)
    if not updates:raise ValueError('Choose at least one future bath')
    selected={row['bath'] for row in updates}
    for row in updates:
        option=options.get(row['bath'])
        if option is None or not option['editable']:
            reason='unknown' if option is None else option['reason']
            raise ValueError(f'Bath {row["bath"]} is locked ({reason}): completed/in-progress baths and already-used spatial dependencies cannot change')
        if row['action']!=option['row']['action']:raise ValueError('Keep the source bath action and order')
    config=_authored_config(meta)
    overrides={row['bath']:row for row in config.chemistry.bath_overrides or ()}
    overrides.update({row['bath']:row for row in updates})
    config.chemistry.bath_overrides=tuple(overrides[i] for i in sorted(overrides))
    preparation=RevisedPreparation(meta,selected)
    request=FilmProcessRun(stop_after=0,include_surface=True,include_inhibitor=True,preparation=preparation,
        track_wet_history=meta['scope']=='frozen_program_wet_history_v5')
    from film_foundry.core.engine import develop_negative_from_latent
    if control:control.report('film-process','starting',0.)
    try:
        develop_negative_from_latent(latent.copy(),config,rng=_rng(meta['rng_initial']),process_run=request)
    except FilmProcessPaused as paused:candidate=paused.boundary
    else:raise ValueError('Revised program did not expose its initial boundary')
    if control:control.check_cancel()
    old=meta['boundary'];cursor=old['next_step'];new=candidate.binding
    validate_program_revision(old['binding'],new,cursor,selected)
    old_config=meta['spatial_preparation']['binding']['config']
    new_config=candidate.preparation['binding']['config']
    kwargs=dict(old_config=old_config,new_config=new_config,
        old_program=old['binding']['program'],new_program=new['program'],cursor=cursor,changed=selected,shape=latent.halide.shape)
    surface=rebind_wet_state(meta['surface_state'],candidate.surface,**kwargs)
    inhibitor=rebind_wet_state(meta['inhibitor_state'],candidate.inhibitor,inhibitor=True,**kwargs)
    revision=dict(model='film_future_bath_revision_v1',revision_id=str(uuid4()),next_step=cursor,
        baths=sorted(selected),before=[options[i]['row'] for i in sorted(selected)],after=deepcopy(list(updates)),
        refreshed_preparation=sorted(preparation.rebuild_fields))
    payload=dict(meta,config=asdict(config),status='revised',boundary=dict(old,binding=new),
        bath_revisions=[*meta.get('bath_revisions',[]),revision])
    for key in ('surface_state','inhibitor_state','spatial_preparation'):payload.pop(key,None)
    if control:control.check_cancel()
    save_checkpoint(output,payload,latent,state,surface=surface,inhibitor=inhibitor,preparation=candidate.preparation)
    return FilmProcessWorkResult(output,'revised',cursor,len(new['program']['steps']),{'checkpoint':str(output)})
