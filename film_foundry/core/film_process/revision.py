"""Conservative dependencies for revising unentered film baths.

Only an explicit new checkpoint adopts the new program. Ordinary continuation
keeps its strict bindings. No past reaction is replayed to reconstruct pools.
"""
from copy import deepcopy
from dataclasses import asdict, is_dataclass
import json

from film_foundry.core.film_process.preparation import SpatialPreparation, binding_for
from film_foundry.core.film_process.bindings import canonical_wet_definition


def _json(value):
    def encode(v):
        if is_dataclass(v):return asdict(v)
        raise TypeError('Unsupported wet timeline value')
    return json.loads(json.dumps(value,default=encode,allow_nan=False))


def bath_number(step,index):
    return (index if step.get('bath_index') is None else step['bath_index'])+1


def preparation_targets(metadata):
    """Bath dependencies of the existing first-development estimators.

    Enabled-but-empty fields still have dependencies: changing a future bath
    can make a formerly uniform/zero source produce a spatial response.
    """
    steps=metadata['boundary']['binding']['program']['steps']
    preparation=metadata.get('spatial_preparation') or {}
    audits=preparation.get('audits',{})
    targets={}
    for key,actions in (('adjacency',('develop_silver','develop_color')),('dir',('develop_color',))):
        if not audits.get(key,{}).get('enabled'):continue
        target=next(((i,bath_number(s,i)) for i,s in enumerate(steps) if s['action'] in actions),None)
        if target is not None:targets[key]=target
    return targets


def locked_baths(metadata):
    """Small header-only policy; full program/wet-prefix checks happen on save."""
    steps=metadata['boundary']['binding']['program']['steps']
    cursor=metadata['boundary']['next_step'];reasons={}
    for i,step in enumerate(steps[:cursor]):
        if step.get('time_history') is None:
            reasons[bath_number(step,i)]='started'
    for key,(index,target) in preparation_targets(metadata).items():
        if index<cursor:
            for number in range(1,target+1):reasons.setdefault(number,key)
    # A completed program has already applied terminal handling/light.
    if cursor==len(steps):
        for i,s in enumerate(steps):reasons[bath_number(s,i)]='finished'
    return reasons


def preparation_updates(metadata,changed):
    targets=preparation_targets(metadata);cursor=metadata['boundary']['next_step']
    rebuild={key for key,(_,bath) in targets.items() if any(number<=bath for number in changed)}
    if any(targets[key][0]<cursor for key in rebuild):
        raise ValueError('Cannot replace spatial preparation already used by completed development')
    return frozenset(rebuild)


def _without_overrides(value):
    result=deepcopy(value);result['chemistry'].pop('bath_overrides',None);return result


class RevisedPreparation(SpatialPreparation):
    def __init__(self,metadata,changed):
        super().__init__(metadata['spatial_preparation'])
        self.rebuild_fields=preparation_updates(metadata,changed)

    def lookup(self,config,shape,program_kind,rng):
        old=self.record['binding'];new=binding_for(config,shape,program_kind)
        if (old['shape']!=new['shape'] or old['program_kind']!=new['program_kind']
            or _without_overrides(old['config'])!=_without_overrides(new['config'])):
            raise ValueError('Bath revision would change frozen material, global conditions or preparation')
        self.record=dict(self.record,binding=new)
        return super().lookup(config,shape,program_kind,rng)

    def capture(self,rng,*fields):
        # Both estimators are deterministic. Rebuilding their private estimate
        # must not resample common unevenness or move the final grain stream.
        if rng.bit_generator.state!=self.record['rng_after']:
            raise ValueError('Future spatial preparation changed the saved random stream')
        super().capture(rng,*fields)


def _normalized_steps(steps):
    result=deepcopy(steps)
    for i,step in enumerate(result):
        if step.get('bath_index') is None and step.get('time_history') is None:
            step['bath_index']=i
    return result


def validate_program_revision(old,new,cursor,changed):
    for key in ('model','material','compatibility'):
        if old[key]!=new[key]:raise ValueError('Bath revision would change material or global process compatibility')
    a=deepcopy(old['program']);b=deepcopy(new['program'])
    aa=_normalized_steps(a.pop('steps'));bb=_normalized_steps(b.pop('steps'))
    if a!=b:raise ValueError('Bath revision would change process polarity or interpretation')
    if aa[:cursor]!=bb[:cursor]:raise ValueError('Bath revision would change completed reactions')
    # Reactions outside the selected baths keep their complete definitions.
    unchanged=lambda steps:[s for i,s in enumerate(steps) if bath_number(s,i) not in changed]
    if unchanged(aa)!=unchanged(bb):raise ValueError('Bath revision affects reactions outside the selected future baths')


def _program(value):
    from film_foundry.core.film_process.operators import FilmProcessProgram,FilmProcessStep,FilmProcessAction
    from film_foundry.core.film_process.formation_history import BathFormationResponse
    steps=[]
    for row in value['steps']:
        row=deepcopy(row);row['action']=FilmProcessAction(row['action'])
        for key in ('layer_selectivity','dye_coupling_ratio','reaction_window','chemical_contact','chemical_placement'):
            if row.get(key) is not None:row[key]=tuple(row[key])
        if row.get('formation_response') is not None:row['formation_response']=BathFormationResponse(**row['formation_response'])
        steps.append(FilmProcessStep(**row))
    return FilmProcessProgram(**{**value,'steps':tuple(steps),'compatible_interpreters':tuple(value['compatible_interpreters'])})


def rebind_wet_state(old,candidate,old_config,new_config,old_program,new_program,cursor,changed,shape,*,inhibitor=False):
    """Keep actual inventories while replacing only the future declaration."""
    from film_foundry.core.film_process.surface import SurfaceProcessSession
    from film_foundry.core.film_process.inhibitor import InhibitorProcessSession
    from film_foundry.model.config import DarkroomConfig
    cls=InhibitorProcessSession if inhibitor else SurfaceProcessSession
    complete_history=not inhibitor and old['model']==candidate['model']=='film_surface_boundary_v2'
    make=lambda c,p:cls(DarkroomConfig.from_dict(c),shape,program=_program(p),
        **({'track_wet_history':complete_history} if not inhibitor else {}))
    a=make(old_config,old_program);b=make(new_config,new_program)
    a.bind(_program(old_program),old,cursor)
    before=canonical_wet_definition(old['definition']);after=canonical_wet_definition(candidate['definition'])
    rows_a=before.pop('baths');rows_b=after.pop('baths')
    if before!=after or len(rows_a)!=len(rows_b):raise ValueError('Bath revision changes the wet material or event definition')
    if any(x!=y for i,(x,y) in enumerate(zip(rows_a,rows_b),1) if i not in changed):
        raise ValueError('Bath revision changes unselected bath conditions')
    unentered=not any(step.get('time_history') is None for step in old_program['steps'][:cursor])
    if not unentered and not complete_history and old['active']!=candidate['active']:
        raise ValueError('This revision needs wet history absent from this checkpoint; start a new process')
    b.bind(_program(new_program))
    prefix=lambda session:[_json(e) for e,position in zip(session.timeline,session.schedule) if position<2*cursor]
    if prefix(a)!=prefix(b):raise ValueError('Bath revision changes completed wet history')
    if unentered:
        # No material has entered a bath: the initial temperature/first-wet
        # reference belong to the newly selected first bath, not elapsed time.
        fresh=dict(candidate,next_step=cursor)
        b.bind(_program(new_program),fresh,cursor)
        return b.snapshot()
    if old['active'] and not inhibitor and candidate['active'] and set(old['fields'])!=set(candidate['fields']):
        raise ValueError('Bath revision changes the saved surface inventory layout')
    rebound=dict(old,definition=candidate['definition'])
    b.bind(_program(new_program),rebound,cursor)
    return b.snapshot()
