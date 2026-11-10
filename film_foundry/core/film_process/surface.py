"""Resumable film surface inventory; the ordinary pipeline uses the same events.

No observation texture is drawn until final assembly at the historical RNG point.
"""
import math
from copy import deepcopy
import numpy as np
from film_foundry.core.process_operations import (
    events_for, source_bath_rows, BathContactRate, control_grid, frame_extent,
    operation_timeline, _footprint, wash_fraction, hardened_fraction,
    solution_hardening, finish_process_surface_texture)
from film_foundry.contracts.process_events import (
    validate_process_response, SURFACE_FIELDS, SURFACE_FIELDS_V2,
    SURFACE_MODEL, SURFACE_MODEL_V2, SURFACE_MODEL_V3, SURFACE_MODEL_V4, SURFACE_MODEL_V5,
    EVENT_MODEL_V7, EVENT_MODEL_V8, EVENT_MODEL_V9, EVENT_MODEL_V10, EVENT_MODEL_V11, EVENT_MODEL_V12, EVENT_MODEL_V13,EVENT_MODEL_V14,EVENT_MODEL_V15)
from film_foundry.contracts.emulsion_integrity import validate_emulsion_integrity, OWNED_POOLS
from film_foundry.contracts.carbon_backing import validate_carbon_backing
from film_foundry.core import carbon_backing
from film_foundry.core.emulsion_integrity import wear, update_deposit_ownership
from film_foundry.core.film_process.bindings import canonical_wet_definition
from film_foundry.contracts.process_events import CONTACT_KINDS


class SurfaceProcessSession:
    def __init__(self,config,shape,*,program=None,coverage_transitions=None,shear_transitions=None,track_wet_history=False):
        from film_foundry.core.implementation_plan import resolve_implementation_plan
        basic=resolve_implementation_plan(config).basic_formation_only
        if basic:
            from film_foundry.core.basic_formation import basic_formation_config
            config,_=basic_formation_config(config)
        self.config=config;self.shape=shape;self.program=program
        self.track_wet_history=bool(track_wet_history)
        self.coverage_transitions=coverage_transitions;self.shear_transitions=shear_transitions
        self.fields=None;self.cursor=0;self.light_done=False;self.timeline=[]
        self.next_step=0;self.schedule=[]
        self.all_events=events_for(config)
        self.event_indices={id(event):i for i,event in enumerate(self.all_events)}
        self.integrity=validate_emulsion_integrity(config.film.emulsion_integrity)
        self.has_shear=any(e['kind']=='emulsion_shear' for e in self.all_events)
        self.has_integrity=self.has_shear or any(e['kind']=='emulsion_wear' for e in self.all_events)
        if self.has_shear and (self.integrity or {}).get('model')!='wet_emulsion_integrity_v2':raise ValueError('Emulsion shear requires declared v2 shear material response')
        if self.has_integrity and self.integrity is None:raise ValueError('Emulsion wear requires a declared emulsion integrity material')
        self.backing=validate_carbon_backing(config.film.carbon_backing)
        self.has_backing=self.backing is not None and self.backing['initial_mg_m2']>0
        if basic:
            # Standard clearing is assumed; no transported residue/backing fields.
            self.backing=None;self.has_backing=False
        self.lifecycle=(config.chemistry.process_events or {}).get('model') in (EVENT_MODEL_V7,EVENT_MODEL_V8,EVENT_MODEL_V9,EVENT_MODEL_V10,EVENT_MODEL_V11,EVENT_MODEL_V12,EVENT_MODEL_V13,EVENT_MODEL_V14,EVENT_MODEL_V15) and any(e['kind']=='silver_bath' for e in events_for(config))
        self.events=[e for e in self.all_events if e['kind'] not in (*CONTACT_KINDS,'developer_inhibitor') and (self.lifecycle or e['kind']!='fixer_contact')]
        # Deposited at withdrawal, before any user handling/wash at that bath
        # boundary. The declaration stays in the original contact operation.
        self.events=[{'kind':'meniscus_residue','after_bath':e['bath'],'contact':e}
            for e in self.all_events if e.get('wetting_model')=='edge_front_v1'
            and e.get('residue_concentration_mg_l',0.)>0 and e.get('retained_ml_m2',0.)>0]+self.events
        scheduled=any(e.get('after_bath',0) or e.get('before_bath',0) for e in self.events)
        program=program or (BathContactRate(config,shape,prepare_surface=False).program if scheduled or config.chemistry.bath_overrides or self.has_backing or self.has_integrity else None)
        rows=source_bath_rows(config,program)
        baths=[row for row in rows if row['action'] not in ('destroy_dye','activate_remaining_halide')]
        temperatures=[row.get('temperature_c',config.chemistry.temperature_c) for row in baths]
        has_hardener=any(row.get('solution') and row['solution'].get('hardener_g_l',0)>0 for row in rows)
        needs_surface=bool(self.events or len(set(temperatures))>1 or has_hardener or self.has_backing)
        if basic:needs_surface=False
        # A temperature-matched dye-only bath changes the existing dye pool. Do
        # not allocate/remap five empty surface fields for a purely pool operation.
        if not self.has_backing and self.events and all(e['kind']=='dye_bath' and e['temperature_c']==(temperatures[-1] if temperatures else config.chemistry.temperature_c) for e in self.events) and len(set(temperatures))<=1 and not has_hardener:
            needs_surface=False
        if not needs_surface and not self.track_wet_history:return
        self.response=validate_process_response(config.film.process_response)
        self.grid=control_grid(shape);self.extent=frame_extent(config,shape[:2])
        self.lifecycle=self.has_integrity or self.has_backing or self.lifecycle and any(e['kind']=='silver_bath' for e in self.events)
        if needs_surface:self._allocate_fields()
        self.pretreated=False
        self.hardening=self.response['initial_hardening'];self.temperature=float(temperatures[0] if temperatures else config.chemistry.temperature_c)
        self.wet_seconds=float(baths[0].get('time_min',config.chemistry.time_min) if baths else config.chemistry.time_min)*60
        if not needs_surface and not baths:self.wet_seconds=0.
        first_wet_index=next((i for i,row in enumerate(rows,1) if row['action'] not in ('destroy_dye','activate_remaining_halide')),0)
        if any(e['kind'] in ('backing_condition','backing_wash','emulsion_wear','emulsion_shear') and 0<e.get('before_bath',0)<=first_wet_index for e in self.events):
            # The old path starts after the first bath. A new prebath starts before
            # it: do not count that bath twice, or hydrate dry film retroactively.
            self.wet_seconds=0.
        self.ledger={'residue_added_mg_m2':0.,'residue_washed_mg_m2':0.,'silver_supplied_mg_m2':0.,'silver_discharged_mg_m2':0.}
        self.surface_contact=None
        if self.lifecycle:self.ledger.update(surface_silver_bleached_mg_m2=0.,surface_silver_removed_mg_m2=0.,surface_halide_fixed_mg_m2=0.)
        self.trace=[]
        self.program=program
        self.timeline=operation_timeline(config,program,self.events,include_reactions=self.lifecycle)
        # Existing reduction credits the first wet bath once before the surface
        # timeline. Keep that reference explicit so a fresh checkpoint is dry.
        self.initial_wet_credit=self.wet_seconds;self.wet_seconds=0.

    def _allocate_fields(self):
        """A dormant history has explicitly zero surface inventories."""
        self.fields={key:np.zeros(self.grid,np.float32) for key in (SURFACE_FIELDS_V2 if self.lifecycle else SURFACE_FIELDS)}
        if self.has_backing:carbon_backing.initialize(self.fields,self.backing)
        if self.has_integrity:
            self.fields['emulsion_coverage']=np.ones(self.grid,np.float32)
            if self.has_shear:self.fields['emulsion_shift_x_mm']=np.zeros(self.grid,np.float32)
            self.fields.update({key:np.zeros(self.grid,np.float32) for key in OWNED_POOLS.values()})
        self.soluble=self.fields['soluble_residue_mg_m2'];self.bound=self.fields['bound_residue_mg_m2'];self.silver=self.fields['surface_silver_mg_m2']

    def definition(self):
        return canonical_wet_definition(dict(shape=list(self.shape[:2]),
            response=validate_process_response(self.config.film.process_response), integrity=self.integrity,
            backing=self.backing, events=self.all_events,
            baths=source_bath_rows(self.config,self.program),
            temperature_c=self.config.chemistry.temperature_c,
            time_min=self.config.chemistry.time_min,
            bath_transfer_s=self.config.chemistry.bath_transfer_s,
            extent=frame_extent(self.config,self.shape[:2]),
            post_process_light=self.config.chemistry.post_process_light,
            time_response=self.config.film.time_response))

    def bind(self,program,resume=None,next_step=0):
        """Align surface events to before/after positions of the film operators.

        An injected wear/shear computes its transition immediately before the
        matching primary operator. Later surface handling waits until that
        primary operator has completed. Nothing from the next bath is advanced
        merely because the preceding reaction reached a save boundary.
        """
        from film_foundry.core.film_process.event_schedule import event_schedule
        self.schedule=event_schedule(program,self.all_events,self.timeline)
        self.total_steps=len(program.steps)
        self.next_step=next_step
        if resume is not None:self.restore(resume)
        expected=sum(position<2*next_step for position in self.schedule)
        if self.cursor!=expected:
            raise ValueError('Surface and primary reaction cursors differ')
        if (self.fields is not None or self.track_wet_history) and self.light_done!=(next_step==self.total_steps):
            raise ValueError('Surface terminal-light state differs from its reaction cursor')

    def before_step(self,index):
        self.advance_through(2*index)

    def after_step(self,index):
        self.advance_through(2*index+1)
        self.next_step=index+1
        if self.next_step==self.total_steps:self.apply_terminal_light()

    def advance_through(self,position):
        while self.cursor<len(self.schedule) and self.schedule[self.cursor]<=position:self.advance()

    def snapshot(self):
        value=dict(model='film_surface_boundary_v2' if self.track_wet_history else 'film_surface_boundary_v1',definition=self.definition(),
            next_step=self.next_step,active=self.fields is not None)
        if self.fields is not None:
            value['fields']=self.fields
        if self.fields is not None or self.track_wet_history:
            value.update(hardening=self.hardening,temperature=self.temperature,
                wet_seconds=self.wet_seconds,initial_wet_credit=self.initial_wet_credit,
                pretreated=self.pretreated,ledger=deepcopy(self.ledger),trace=deepcopy(self.trace),
                cursor=self.cursor,light_done=self.light_done)
        return value

    def restore(self,value):
        model='film_surface_boundary_v2' if self.track_wet_history else 'film_surface_boundary_v1'
        if (value.get('model')!=model or canonical_wet_definition(value.get('definition'))!=self.definition()
            or value.get('next_step')!=self.next_step or type(value.get('active')) is not bool
            or (not self.track_wet_history and value['active']!=(self.fields is not None))):
            raise ValueError('Surface checkpoint differs from the frozen material or process')
        if self.fields is None and not self.track_wet_history:return
        from film_foundry.core.process_operations import validate_surface_fields
        if value['active']:
            # Future conditions may no longer require a surface allocation, but
            # already formed fields still belong to the material and must stay.
            if self.fields is None:self._allocate_fields()
            incoming=value['fields']
            if set(incoming)!=set(self.fields):raise ValueError('Incomplete wet surface inventory')
            checked=validate_surface_fields(incoming)
            if any(a.shape!=self.grid for a in checked.values()):raise ValueError('Surface checkpoint grid differs')
            self.fields={key:array.copy() for key,array in checked.items()}
        elif 'fields' in value or self.has_backing or self.has_integrity or self.lifecycle:
            raise ValueError('Dormant history cannot omit existing surface inventories')
        hard=np.asarray(value['hardening'])
        if (hard.shape not in ((),self.grid) or not np.isfinite(hard).all()
            or hard.min()<0 or hard.max()>1):raise ValueError('Invalid wet surface hardening')
        self.hardening=hard.copy() if hard.ndim else float(hard)
        for name in ('temperature','wet_seconds','initial_wet_credit'):
            number=value[name]
            if not isinstance(number,(int,float)) or not math.isfinite(number) or (name!='temperature' and number<0):
                raise ValueError('Invalid wet surface clock or temperature')
            setattr(self,name,number)
        if type(value['cursor']) is not int or not 0<=value['cursor']<=len(self.timeline):raise ValueError('Invalid surface cursor')
        if type(value['light_done']) is not bool or type(value['pretreated']) is not bool:raise ValueError('Invalid surface stage')
        if not isinstance(value['ledger'],dict) or any(not isinstance(v,(int,float)) or not math.isfinite(v) or v<0 for v in value['ledger'].values()):
            raise ValueError('Invalid surface material ledger')
        if not isinstance(value['trace'],list) or len(value['trace'])>value['cursor']+1:raise ValueError('Invalid surface trace')
        for name in ('cursor','light_done','pretreated','ledger','trace'):setattr(self,name,deepcopy(value[name]))
        if self.fields is not None:
            self.soluble=self.fields['soluble_residue_mg_m2'];self.bound=self.fields['bound_residue_mg_m2'];self.silver=self.fields['surface_silver_mg_m2']

    def thermal(self,target,transfer,hold):
        tau=self.response['relaxation_s'];ramp=1. if transfer==0 else -math.expm1(-transfer/tau)*tau/transfer
        strain=abs(target-self.temperature)*self.response['thermal_expansion_per_c']*ramp*(1-.7*self.hardening)
        hydrated=-math.expm1(-self.wet_seconds/self.response['hydration_s'])
        if np.ndim(strain):
            excess=np.maximum(0.,strain-self.response['yield_strain']*(1+self.hardening))
            increment=-np.expm1(-(excess/self.response['damage_strain'])**2*hydrated)
        else:
            excess=max(0.,strain-self.response['yield_strain']*(1+self.hardening))
            increment=-math.expm1(-(excess/self.response['damage_strain'])**2*hydrated)
        if self.fields is None and np.any(increment):self._allocate_fields()
        if self.fields is not None:
            damage=self.fields['emulsion_damage'];damage[:]=1-(1-damage)*(1-increment)
        self.temperature=target;self.wet_seconds+=transfer+hold
        return {'peak_mismatch_strain':float(np.max(strain)),'damage_increment':float(np.mean(increment))}

    def apply_event(self,event):
        config=self.config;shape=self.shape
        kind=event['kind'];report={'operation':dict(event)}
        before_deposits={key:self.fields[key].copy() for key in OWNED_POOLS if key in self.fields} if self.has_integrity and kind not in ('emulsion_wear','emulsion_shear') else {}
        if kind=='surface_reaction':
            step=event['step'];action=step.action.value
            halide=self.fields['surface_halide_mg_m2']
            pool=halide if action=='fix_halide' else self.silver
            if not np.any(pool):return
            if self.surface_contact is None:self.surface_contact=BathContactRate(config,self.grid,prepare_surface=False,prepare_inhibitor=False,
                frame_extent_mm=self.extent)
            contact=self.surface_contact.contact_for_step(event['bath']-1,step)
            # External surface silver has no image-layer selectivity. The bath
            # conversion and access are shared; its separate mass stays bounded.
            rate=np.clip(step.strength*(1. if contact is None else contact),0.,1.)
            removed=pool*rate;pool-=removed
            amount=float(np.mean(removed,dtype=np.float64))
            key={'bleach_silver':'surface_silver_bleached_mg_m2','fix_halide':'surface_halide_fixed_mg_m2','remove_silver':'surface_silver_removed_mg_m2'}[action]
            self.ledger[key]+=amount
            if action=='bleach_silver':halide+=removed
            report={'operation':{'kind':kind,'bath':event['bath'],'action':action},'reacted_silver_mg_m2':amount}
        elif kind=='fixer_contact':
            if not self.lifecycle or not np.any(self.fields['surface_halide_mg_m2']):return
            rate=_footprint(self.grid,self.extent,event)*(-math.expm1(-self.response.get('fixer_contact_rate_l_mmol_s',.001)*event['concentration_mmol_l']*event['duration_s']))
            removed=self.fields['surface_halide_mg_m2']*rate;self.fields['surface_halide_mg_m2']-=removed
            self.ledger['surface_halide_fixed_mg_m2']+=float(np.mean(removed,dtype=np.float64))
        elif kind=='bath_temperature':
            if event['first_wet'] and not self.pretreated:return
            bath=event['conditions'];index=event['bath']
            report={'operation':{'kind':kind,'bath':index,'action':bath['action']}}
            report.update(self.thermal(float(bath.get('temperature_c',config.chemistry.temperature_c)),
                float(bath.get('bath_transfer_s',config.chemistry.bath_transfer_s)),float(bath.get('time_min',config.chemistry.time_min))*60))
        elif kind=='contaminate':
            added=_footprint(self.grid,self.extent,event)*(event['concentration_mg_l']*event['retained_ml_m2']/1000.)
            self.soluble+=added;self.ledger['residue_added_mg_m2']+=float(np.mean(added,dtype=np.float64))
            bound_now=self.soluble*(-math.expm1(-self.response['binding_rate_s']*event['contact_s']))
            self.soluble-=bound_now;self.bound+=bound_now
            self.wet_seconds+=event['contact_s']
        elif kind=='meniscus_residue':
            from film_foundry.core.edge_wetting import residue_deposit
            contact=event['contact']
            bath=source_bath_rows(config,self.program)[event['after_bath']-1]
            duration=float(bath.get('time_min',config.chemistry.time_min))*60.
            added,details=residue_deposit(self.grid,self.extent,duration,contact)
            # Binding and later washing use the material's existing response.
            bound_fraction=-math.expm1(-self.response['binding_rate_s']*contact['pause_s'])
            self.bound+=added*bound_fraction;self.soluble+=added*(1.-bound_fraction)
            self.ledger['residue_added_mg_m2']+=float(np.mean(added,dtype=np.float64))
            report.update(details)
        elif kind in ('backing_condition','backing_wash'):
            report.update(self.thermal(event['temperature_c'],event['transfer_s'],event['duration_s']))
            self.pretreated=True
            contact=np.clip(_footprint(self.grid,self.extent,event),0.,1.)
            if kind=='backing_wash':
                removed=self.soluble*wash_fraction(self.response,self.hardening,event)*contact
                self.soluble-=removed;self.ledger['residue_washed_mg_m2']+=float(np.mean(removed,dtype=np.float64))
            if self.has_backing:
                carbon_backing.process(self.fields,self.backing,event,contact,
                    front_owned=self.fields.get('emulsion_carbon_mg_m2'),coverage=self.fields.get('emulsion_coverage'))
                report.update(carbon_backing.balance(self.fields,self.backing))
            else:report['carbon_processing']='no_carbon_backing'
        elif kind=='emulsion_shear':
            from film_foundry.core.emulsion_shear import displacement_mm,transport_surface
            self.pretreated=True
            report.update(self.thermal(event['temperature_c'],event['transfer_s'],event['duration_s']))
            shift=displacement_mm(self.fields,self.integrity,event,self.hardening,self.wet_seconds,self.response['hydration_s'],self.extent)
            self.hardening,transport=transport_surface(self.fields,shift,self.extent,self.hardening)
            report.update(transport)
            if self.shear_transitions is not None:self.shear_transitions[self.event_indices[id(event)]]=shift
            for pool,amount in transport['outside_deposits_mg_m2'].items():
                self.ledger['outside_'+pool]=self.ledger.get('outside_'+pool,0.)+amount
            self.ledger['emulsion_outside_area_fraction']=self.ledger.get('emulsion_outside_area_fraction',0.)+transport['outside_area_fraction']
        elif kind=='emulsion_wear':
            self.pretreated=True
            report.update(self.thermal(event['temperature_c'],event['transfer_s'],event['duration_s']))
            coverage_before=self.fields['emulsion_coverage'].copy() if self.has_shear else None
            handling=wear(self.fields,self.integrity,event,_footprint(self.grid,self.extent,event),self.hardening,self.wet_seconds,self.response['hydration_s'])
            report.update(handling)
            if self.coverage_transitions is not None:
                current=self.fields['emulsion_coverage']
                self.coverage_transitions[self.event_indices[id(event)]]=np.divide(current,coverage_before,out=np.ones_like(current),where=coverage_before>0) if self.has_shear else current.copy()
            for pool,amount in handling['carried_deposits_mg_m2'].items():
                key={'bound_residue_mg_m2':'residue_mechanical_removed_mg_m2','surface_silver_mg_m2':'surface_silver_removed_mg_m2','surface_halide_mg_m2':'surface_halide_mechanical_removed_mg_m2'}.get(pool)
                if key:self.ledger[key]=self.ledger.get(key,0.)+amount
        elif kind=='wash':
            report.update(self.thermal(event['temperature_c'],event['transfer_s'],event['duration_s']))
            # Finite changes of fresh water and diffusion through wet gelatin
            # jointly limit exchange. Zero time or zero fresh water removes none.
            removed=self.soluble*wash_fraction(self.response,self.hardening,event);self.soluble-=removed
            self.ledger['residue_washed_mg_m2']+=float(np.mean(removed,dtype=np.float64))
            if self.has_backing:carbon_backing.ordinary_wash(self.fields,wash_fraction(self.response,self.hardening,event))
        elif kind=='temperature':report.update(self.thermal(event['temperature_c'],event['transfer_s'],event['hold_s']))
        elif kind=='dye_bath':report.update(self.thermal(event['temperature_c'],event['transfer_s'],event['duration_s']))
        elif kind=='harden':
            self.hardening=hardened_fraction(self.response,self.hardening,event)
            self.wet_seconds+=event['duration_s']
        elif kind=='solution_hardening':
            self.hardening=solution_hardening(config,shape,self.response,self.hardening,event)
        elif kind=='silver_bath':
            report.update(self.thermal(event['temperature_c'],event['transfer_s'],event['duration_s']))
            footprint=_footprint(self.grid,self.extent,event)
            available=event['silver_mg_l']*event['solution_ml_m2']/1000.
            # One declared reducing equivalent per Ag+; mmol Ag -> mg Ag.
            reducing_capacity=event['reducer_mmol_l']*event['solution_ml_m2']/1000.*107.8682
            reaction=-math.expm1(-self.response['silver_reduction_l_mmol_s']*event['reducer_mmol_l']*event['duration_s']*2**((self.temperature-20)/20))
            supplied_field=footprint*available
            deposited=np.minimum(supplied_field,footprint*reducing_capacity)*reaction;self.silver+=deposited
            supplied=float(np.mean(supplied_field,dtype=np.float64));amount=float(np.mean(deposited,dtype=np.float64))
            self.ledger['silver_supplied_mg_m2']+=supplied;self.ledger['silver_discharged_mg_m2']+=supplied-amount
            report.update({'silver_deposited_mg_m2':amount,'reducing_capacity_mg_m2':reducing_capacity})
        if before_deposits:update_deposit_ownership(self.fields,before_deposits,carbon_handled=kind=='backing_wash')
        report['hardening_fraction']=float(np.mean(self.hardening));self.trace.append(report)

    def advance(self):
        if self.cursor>=len(self.timeline):return
        self.wet_seconds+=self.initial_wet_credit;self.initial_wet_credit=0.
        self.apply_event(self.timeline[self.cursor])
        self.cursor+=1

    def apply_terminal_light(self):
        if self.light_done or self.fields is None and not self.track_wet_history:return
        # The same explicit terminal light stage follows every bath/handling event
        # in both the primary pools and this separate surface inventory. It never
        # runs in an observer, and cannot bring fixed or mechanically removed salt back.
        from film_foundry.core.film_process.time_history import light_surface_halide
        surface_light=light_surface_halide(self.fields,self.config.chemistry.post_process_light,self.config.film.time_response) if self.fields is not None else None
        if surface_light is not None:
            self.ledger['surface_halide_printed_mg_m2']=surface_light['converted_mg_m2']
            self.trace.append({'operation':{'kind':'surface_residual_light'},**surface_light})
        self.light_done=True

    def finish(self,rng=None,*,generate_texture=True):
        if self.fields is None and not self.track_wet_history:return None,None
        while self.cursor<len(self.timeline):self.advance()
        self.wet_seconds+=self.initial_wet_credit;self.initial_wet_credit=0.
        self.apply_terminal_light()
        if self.fields is None:return None,None
        config=self.config
        if generate_texture:finish_process_surface_texture(config,self.fields,rng)
        self.ledger.update({'residue_remaining_mg_m2':float(np.mean(self.soluble+self.bound,dtype=np.float64)),
            'silver_deposited_mg_m2':float(np.mean(self.silver,dtype=np.float64))})
        if self.lifecycle:self.ledger['surface_halide_remaining_mg_m2']=float(np.mean(self.fields['surface_halide_mg_m2'],dtype=np.float64))
        if self.has_backing:self.ledger.update(carbon_backing.balance(self.fields,self.backing))
        audit={'model':SURFACE_MODEL_V5 if self.has_shear else SURFACE_MODEL_V4 if self.has_integrity else SURFACE_MODEL_V3 if self.has_backing else SURFACE_MODEL_V2 if self.lifecycle else SURFACE_MODEL,'grid_shape':list(self.grid),'physical_extent_mm':list(self.extent),'response':self.response,'events':self.trace,
            'balance':self.ledger,'final_temperature_c':self.temperature,'wet_seconds':self.wet_seconds,'final_hardening_fraction':float(np.mean(self.hardening)),
            'hardening_fraction_range':[float(np.min(self.hardening)),float(np.max(self.hardening))],
            'observation':'density_proxy_not_dichroic_reflection','implicit_final_stage':'dry_remaining_solute'}
        if (config.chemistry.process_events or {}).get('model') in (EVENT_MODEL_V10,EVENT_MODEL_V11,EVENT_MODEL_V12,EVENT_MODEL_V13,EVENT_MODEL_V14,EVENT_MODEL_V15):audit['primary_emulsion_model']='ordered_pool_removal_v1'
        if self.has_shear:audit['deformation']={'model':'horizontal_emulsion_shear_v1','units':'millimetres','axis':'frame_x','domain':'emulsion_owned','surface_sampling':'control_grid_cell_overlap','primary_sampling':'full_width_cell_overlap','outside_policy':'discard_with_inventory'}
        if self.has_backing:audit['carbon_backing']=self.backing
        if self.has_integrity:
            audit['emulsion_integrity']=self.integrity
            self.ledger['emulsion_retained_area_fraction']=float(np.mean(self.fields['emulsion_coverage'],dtype=np.float64))
            self.ledger['emulsion_removed_area_fraction']=max(0.,1-self.ledger['emulsion_retained_area_fraction']-self.ledger.get('emulsion_outside_area_fraction',0.))
        from film_foundry.contracts.process_events import RESPONSE_MODEL_V4
        if self.response['model']==RESPONSE_MODEL_V4:
            from film_foundry.core.film_surface_optics import MODEL
            audit['observation']=MODEL
        return self.fields,audit
