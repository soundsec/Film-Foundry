"""Header-only stage/operation queries for CLI and future desktop frontends."""
from film_foundry.contracts.formation_history import capabilities,LEGACY
from film_foundry.core.film_process.revision import preparation_updates


def describe_formation_execution(config):
    """Recipe-only execution capabilities, without exposing a UI dependency."""
    from film_foundry.core.implementation_plan import resolve_implementation_plan
    from film_foundry.core.basic_formation import OMITTED_FIELDS
    plan = resolve_implementation_plan(config)
    inactive = ['chemistry.' + key for key in OMITTED_FIELDS] if plan.basic_formation_only else []
    if plan.basic_formation_only:
        inactive += ['chemistry.process_events']
    if not plan.spatial_chemistry_enabled:
        inactive += ['chemistry.development_adjacency_strength', 'chemistry.development_adjacency_radius',
                     'chemistry.dir_process_effectiveness', 'film.dir_diffusion_radius', 'film.dir_material_response_matrix']
    return {**plan.as_dict(), 'basic_formation_only': plan.basic_formation_only,
            'local_grain_response_enabled': plan.local_grain_response_enabled,
            'inactive_fields': inactive, 'observation_changes_formation': False}


def describe_process_capabilities(metadata,baths):
    boundary=metadata['boundary'];cursor=boundary['next_step'];total=len(boundary['binding']['program']['steps'])
    formation=capabilities(metadata['config']['chemistry'].get('formation_history_model',LEGACY))
    from film_foundry.model.config import DarkroomConfig
    from film_foundry.core.implementation_plan import resolve_implementation_plan
    plan=resolve_implementation_plan(DarkroomConfig.from_dict(metadata['config']))
    if plan.basic_formation_only:
        formation['limits'].extend(['process_events_omitted','secondary_bath_reactions_omitted','basic_stain_and_fog_only'])
    if not plan.local_grain_response_enabled:
        formation['local_response']=False
        formation['limits'].extend(['grain_moments_omitted','first_developer_global_grain_response',
                                   'development_adjacency_and_dir_omitted'])
        for stage in formation['stages']:
            if stage['id']=='bath_formation':stage['writes']=['chemical_fog_history'] if formation['history_enabled'] else []
            if stage['id']=='grain_response':
                stage['reads']=['first_developer_response'];stage['writes']=['global_grain_response']
        for field in formation['fields']:field['resolution']='frame'
    return {'model':'film_process_capabilities_v1',
        'execution_implementation':plan.as_dict(),
        'stages':[
            {'id':'preparation','state':'complete','checkpoint':True},
            {'id':'reactions','state':'complete' if cursor==total else 'pending' if cursor==0 else 'in_progress',
             'completed_steps':cursor,'total_steps':total,'checkpoint':True},
            {'id':'finalization','state':'ready' if cursor==total else 'pending','checkpoint':False},
            {'id':'observation','state':'requires_final_medium','read_only':True}],
        'operations':{'continue':True,'finish_processing':True,'finalize':cursor==total,'revise':any(b['editable'] for b in baths),
                      'replace_material':False,'reorder_baths':False,'edit_events':False},
        'baths':[{'id':f'bath:{b["bath"]}','bath':b['bath'],'editable':b['editable'],'reason':b['reason'],
                  'refreshes':sorted(preparation_updates(metadata,{b['bath']})) if b['editable'] else []}
                 for b in baths],
        'formation':formation,
        'contracts':{'mutable_inventory':'reaction_checkpoint','derived_grain_fields':'finalization_only',
                     'observation_changes_formation':False}}
