"""Causal operations on bath access and a separate final surface state.

No requested-effect strengths are read or written here. The v1 response is a
creative, unit-labelled reduction, not stock-calibrated chemistry or mechanics.
"""
from dataclasses import replace
from copy import copy
import math
import cv2
import numpy as np
from film_foundry.contracts.process_events import (validate_process_events,validate_process_response,
    SURFACE_FIELDS,SURFACE_MODEL,SURFACE_FIELDS_V2,SURFACE_MODEL_V2,EVENT_MODEL_V7,EVENT_MODEL_V8,EVENT_MODEL_V9,EVENT_MODEL_V10,EVENT_MODEL_V11,EVENT_MODEL_V12,EVENT_MODEL_V13,EVENT_MODEL_V14,EVENT_MODEL_V15,
    SURFACE_MODEL_V3,SURFACE_MODEL_V4,SURFACE_FIELDS_V4,SURFACE_MODEL_V5,SURFACE_FIELDS_V5,SURFACE_SCHEMAS)
from film_foundry.contracts.carbon_backing import validate_carbon_backing
from film_foundry.core import carbon_backing
from film_foundry.contracts.emulsion_integrity import validate_emulsion_integrity,OWNED_POOLS
from film_foundry.core.development import (build_effective_development,
    REACTION_RATE_PER_MINUTE, REFERENCE_DEVELOPMENT_MINUTES)
from film_foundry.core.material_geometry import FRAME_LONG_EDGES_MM
from film_foundry.core.film_process.recipe import program_from_develop_recipe
from film_foundry.contracts.process_events import CONTACT_KINDS
from film_foundry.core.compute_precision import contact_precision

def events_for(config):
    value=validate_process_events(config.chemistry.process_events)
    return [] if value is None else value['events']

def frame_extent(config,shape):
    # Same nominal formats used by the grain/spatial configuration. Explicit
    # recorded geometry wins; coordinates are physical millimetres on the frame.
    longest=config.chemistry.frame_long_edge_mm or FRAME_LONG_EDGES_MM.get(config.chemistry.frame_size,36.)
    scale=float(longest)/max(shape)
    return shape[1]*scale,shape[0]*scale

CONTROL_GRID_LONG_EDGE = 512

def control_grid(shape):
    scale=min(1.,CONTROL_GRID_LONG_EDGE/max(shape[:2]))
    return tuple(max(1,round(v*scale)) for v in shape[:2])

def wash_fraction(response,hardening,event):
    exchange=(event['duration_s']/response['wash_exchange_s'])*(1-.6*hardening)*2**((event['temperature_c']-20)/20)
    if np.ndim(exchange):return -np.expm1(-np.minimum(exchange,event['water_changes']))
    return -math.expm1(-min(exchange,event['water_changes']))

def hardened_fraction(response,hardening,event):
    return 1-(1-hardening)*math.exp(-response['hardening_rate_l_g_s']*event['concentration_g_l']*event['duration_s'])

def contact_duration(shape,extent,start,stop,duration,event,*,precision='float64'):
    """Shared physical access for the primary reaction and gelatin hardening."""
    if event.get('kind')=='rotary_contact':
        from film_foundry.core.rotary_contact import contact_duration as rotary_duration
        return rotary_duration(shape,start,stop,duration,event,extent=extent,precision=precision)
    h,w=shape;physical_w,physical_h=extent
    x=(np.arange(w,dtype=np.float32)+.5)/w;y=(np.arange(start,stop,dtype=np.float32)+.5)/h
    angle=math.radians(event['angle_degrees']);cx,cy=math.cos(angle),math.sin(angle)
    along=(cx*(x[None,:]-.5)+cy*(y[:,None]-.5))/max(abs(cx)+abs(cy),1e-8)+.5
    fraction=event['immersion_fraction'];wet=np.clip(duration-event['fill_s']*along/max(fraction,1e-8),0,duration)
    wet=np.where(along<=fraction,wet,0.)
    if event['bubble_radius_mm']>0 and event['bubble_release_s']>0:
        distance=((x[None,:]-event['center_x'])*physical_w)**2+((y[:,None]-event['center_y'])*physical_h)**2
        wet=np.where(distance<event['bubble_radius_mm']**2,np.maximum(wet-min(duration,event['bubble_release_s']),0.),wet)
    return wet

def solution_hardening(config,shape,response,hardening,event):
    from .implementation_plan import resolve_implementation_plan
    if resolve_implementation_plan(config).basic_formation_only:
        return hardening
    contact=next((e for e in events_for(config) if e['kind'] in CONTACT_KINDS and e['bath']==event['bath']),None)
    if contact is None:return hardened_fraction(response,hardening,event)
    grid=control_grid(shape)
    from .implementation_plan import resolve_implementation_plan
    contact_edge=resolve_implementation_plan(config).bath_contact_long_edge
    if contact_edge is None:
        wet=contact_duration(grid,frame_extent(config,shape),0,grid[0],event['duration_s'],contact,precision=contact_precision(config.processing))
    else:
        from .contact_sampling import ContactTimeFieldCache
        field=ContactTimeFieldCache(shape,frame_extent(config,shape),contact_edge,precision=contact_precision(config.processing))
        wet=field.sample(event['bath']-1,contact,event['duration_s'],None,grid,0,grid[0])
    return np.asarray(1-(1-hardening)*np.exp(-response['hardening_rate_l_g_s']*event['concentration_g_l']*wet),np.float32)

def sample_control_rows(field,shape,start,stop):
    h,w=shape[:2];gh,gw=field.shape
    xx=np.broadcast_to(((np.arange(w,dtype=np.float32)+.5)*gw/w-.5)[None,:],(stop-start,w))
    yy=np.broadcast_to(((np.arange(start,stop,dtype=np.float32)+.5)*gh/h-.5)[:,None],(stop-start,w))
    return cv2.remap(field,xx,yy,cv2.INTER_LINEAR,borderMode=cv2.BORDER_REPLICATE)

def source_bath_rows(config,program):
    recipe=config.chemistry
    rows=recipe.process_steps
    if rows is None:
        if recipe.bath_overrides:
            from film_foundry.core.film_process.recipe import _base_program_from_develop_recipe
            from film_foundry.core.media import is_monochrome_material
            program=_base_program_from_develop_recipe(recipe,mode='bw_negative' if is_monochrome_material(config) else config.mode,material_process=config.film.medium_process)
        rows=[{'action':s.action.value} for s in program.steps if s.chemical_contact is None and s.process_event_index is None and s.time_history is None] if program else ()
    if recipe.bath_overrides:
        from film_foundry.contracts.bath_overrides import merge_bath_overrides
        return merge_bath_overrides(rows,recipe.bath_overrides)
    return rows

def operation_timeline(config,program,events,*,include_reactions=False):
    rows=source_bath_rows(config,program)
    timeline=[];first_wet=True
    for index,bath in enumerate(rows,1):
        timeline.extend(e for e in events if e.get('before_bath')==index)
        if bath['action'] not in ('destroy_dye','activate_remaining_halide'):
            timeline.append({'kind':'bath_temperature','bath':index,'conditions':bath,'first_wet':first_wet})
            first_wet=False
            solution=bath.get('solution')
            if solution is not None:
                from film_foundry.contracts.bath_solution import validate_solution
                solution=validate_solution(solution,bath['action'])
            if solution and solution['hardener_g_l']>0:
                timeline.append({'kind':'solution_hardening','bath':index,
                    'concentration_g_l':solution['hardener_g_l']*solution['activity_remaining']*bath.get('concentration',config.chemistry.concentration),
                    'duration_s':float(bath.get('time_min',config.chemistry.time_min))*60})
        if include_reactions and program:
            for position,step in enumerate(program.steps):
                source=position if step.bath_index is None else step.bath_index
                if step.process_event_index is None and source==index-1 and step.action.value in ('bleach_silver','fix_halide','remove_silver'):
                    timeline.append({'kind':'surface_reaction','bath':index,'step':step})
        timeline.extend(e for e in events if e.get('after_bath',0)==index)
    timeline.extend(e for e in events if not e.get('after_bath',0) and not e.get('before_bath',0))
    return timeline

def compile_inhibitor_fields(config,shape,program):
    """One frame-local bounded plan, shared by every material tile.

    An explicit development inhibitor is distinct from coloured contamination.
    The v3 half-response load is 0.2 mmol/m², a declared reduced model constant.
    Water exchanges remove the mobile load; already formed material is untouched.
    """
    from film_foundry.core.film_process.inhibitor import InhibitorProcessSession
    return InhibitorProcessSession(config,shape,program,collect_rates=True).finish()

def compile_fixing_fields(config,shape):
    events=[event for event in events_for(config) if event['kind']=='fixer_contact']
    if not events:return {},None
    response=validate_process_response(config.film.process_response)
    coefficient=response.get('fixer_contact_rate_l_mmol_s',.001)
    grid=control_grid(shape);extent=frame_extent(config,shape);rates={}
    for event in events:
        rate=_footprint(grid,extent,event)*(-math.expm1(-coefficient*event['concentration_mmol_l']*event['duration_s']))
        index=event['before_bath']-1
        if index in rates:rate=1-(1-rates[index])*(1-rate)
        rates[index]=np.asarray(rate,np.float32)
    for rate in rates.values():rate.setflags(write=False)
    return rates,{'model':'premature_fixing_v1','target':'halide_and_bleached_halide','domain':'wet_emulsion',
        'material_rate_l_mmol_s':coefficient,'grid_shape':list(grid),'cached_fields':len(rates),
        'cached_bytes':sum(rate.nbytes for rate in rates.values()),'scanner_owned':False}

class BathContactRate:
    """Lazy global-coordinate contact duration, composed with existing rates."""
    def __init__(self,config,shape,base=None,*,start=0,stop=None,prepare_surface=True,incremental_surface=False,
                 incremental_inhibitor=False,prepare_inhibitor=True,track_wet_history=False,frame_extent_mm=None):
        self.config=config;self.full_shape=tuple(shape[:2]);self.base=base;self.start=start;self.stop=stop or shape[0]
        from .implementation_plan import resolve_implementation_plan
        implementation_plan=resolve_implementation_plan(config)
        if implementation_plan.basic_formation_only:
            from .basic_formation import basic_formation_config
            config,_=basic_formation_config(config)
            self.config=config
        self.entries={e['bath']-1:e for e in events_for(config) if e['kind'] in CONTACT_KINDS}
        from film_foundry.core.media import is_monochrome_material
        self.program=program_from_develop_recipe(config.chemistry,mode='bw_negative' if is_monochrome_material(config) else config.mode,material_process=config.film.medium_process,coupled_intervals=implementation_plan.coupled_intervals)
        self.validate_program(self.program)
        self.current_contact=None
        self.current_fog_contact=None
        self._wet_key=None;self._wet=None
        self._base_rate_cache=None
        self._bath_contexts={}
        from .implementation_plan import resolve_implementation_plan
        contact_edge=resolve_implementation_plan(config).bath_contact_long_edge
        self._contact_fields=None
        if contact_edge is not None:
            from .contact_sampling import ContactTimeFieldCache
            self._contact_fields=ContactTimeFieldCache(self.full_shape,
                frame_extent(config,self.full_shape) if frame_extent_mm is None else frame_extent_mm,contact_edge,precision=contact_precision(config.processing))
        self.inhibitor_session=None;self._provisional_inhibitor_plan=None
        self.inhibitor_fields={};self.inhibitor_audit=None
        if incremental_inhibitor:
            from film_foundry.core.film_process.inhibitor import InhibitorProcessSession
            self.inhibitor_session=InhibitorProcessSession(config,self.full_shape,self.program)
        elif prepare_inhibitor:
            self.inhibitor_fields,self.inhibitor_audit=compile_inhibitor_fields(config,self.full_shape,self.program)
        self.fixing_fields,self.fixing_audit=compile_fixing_fields(config,self.full_shape)
        self.ordered=(config.chemistry.process_events or {}).get('model') in (EVENT_MODEL_V10,EVENT_MODEL_V11,EVENT_MODEL_V12,EVENT_MODEL_V13,EVENT_MODEL_V14,EVENT_MODEL_V15)
        self.events=events_for(config)
        self.has_shear=any(e['kind']=='emulsion_shear' for e in self.events)
        self.coverage_transitions={};self.shear_transitions={};self.prepared_surface=None
        self.surface_session=None
        self._provisional_surface_plan=None
        self.coverage_output=np.ones(self.full_shape,np.float32) if self.has_shear and prepare_surface else None
        if incremental_surface:
            from film_foundry.core.film_process.surface import SurfaceProcessSession
            self.surface_session=SurfaceProcessSession(config,self.full_shape,program=self.program,
                coverage_transitions=self.coverage_transitions,shear_transitions=self.shear_transitions,track_wet_history=track_wet_history)
        elif self.ordered and prepare_surface:
            self.prepared_surface=form_process_surface(config,self.full_shape,None,program=self.program,
                coverage_transitions=self.coverage_transitions,shear_transitions=self.shear_transitions,generate_texture=False)

    def at_shape(self,shape):
        """Reuse the physical timeline for a private provisional working grid."""
        field=copy(self);field.full_shape=tuple(shape[:2]);field.start=0;field.stop=shape[0]
        if self.inhibitor_session is not None:
            if self._provisional_inhibitor_plan is None:
                self._provisional_inhibitor_plan=compile_inhibitor_fields(self.config,self.full_shape,self.program)
            field.inhibitor_session=None
            field.inhibitor_fields,field.inhibitor_audit=self._provisional_inhibitor_plan
        if self.surface_session is not None:
            # Provisional adjacency/DIR may inspect the whole frozen program.
            # It owns a separate plan and must never advance the live inventory.
            if self._provisional_surface_plan is None:
                coverage={};shear={}
                prepared=form_process_surface(self.config,self.full_shape,None,program=self.program,
                    coverage_transitions=coverage,shear_transitions=shear,generate_texture=False)
                self._provisional_surface_plan=(prepared,coverage,shear)
            field.surface_session=None
            field.prepared_surface,field.coverage_transitions,field.shear_transitions=self._provisional_surface_plan
        field.coverage_output=None
        field.base=None;field.current_contact=None;field.current_fog_contact=None;field._wet_key=None;field._wet=None
        return field

    def finish_surface(self):
        if self.surface_session is not None:
            self.prepared_surface=self.surface_session.finish(generate_texture=False)
        if self.inhibitor_session is not None:
            self.inhibitor_audit=self.inhibitor_session.audit()
        return self.prepared_surface

    def coverage_for_step(self,step):
        field=self.coverage_transitions[step.process_event_index]
        return sample_control_rows(field,self.full_shape,self.start,self.stop)

    def shear_for_step(self,step):
        profile=self.shear_transitions[step.process_event_index]
        h,w=self.full_shape
        positions=(np.arange(self.start,self.stop,dtype=np.float64)+.5)*len(profile)/h-.5
        mm=np.interp(positions,np.arange(len(profile)),profile)
        return mm*w/frame_extent(self.config,self.full_shape)[0]

    def capture_coverage(self,coverage):
        if self.coverage_output is not None:
            self.coverage_output[self.start:self.stop]=1. if coverage is None else coverage

    def fixing_before_step(self,index):
        if self.ordered:return None
        field=self.fixing_fields.get(index)
        return None if field is None else sample_control_rows(field,self.full_shape,self.start,self.stop)

    def validate_program(self,program):
        allowed={'develop_silver','develop_color','bleach_silver','fix_halide','remove_silver'}
        rows=source_bath_rows(self.config,program)
        for index in self.entries:
            if index>=len(rows) or rows[index]['action'] not in allowed:
                raise ValueError('Contact operation must select a development, bleach, fixing or silver-removal bath')
            if self.entries[index]['kind']=='rotary_contact':
                from film_foundry.core.rotary_contact import validate_duration
                validate_duration(self.entries[index],rows[index].get('time_min',self.config.chemistry.time_min)*60.)
        for event in events_for(self.config):
            if max(event.get('after_bath',0),event.get('before_bath',0))>len(rows):
                raise ValueError('Operation refers to a nonexistent bath')
            after=event.get('after_bath',0)
            if self.config.chemistry.process_events['model'] not in (EVENT_MODEL_V7,EVENT_MODEL_V8,EVENT_MODEL_V9,EVENT_MODEL_V10,EVENT_MODEL_V11,EVENT_MODEL_V12,EVENT_MODEL_V13,EVENT_MODEL_V14,EVENT_MODEL_V15) and event['kind']=='silver_bath' and after and any(
                row['action'] in ('bleach_silver','remove_silver') for row in rows[after:]
            ):
                raise ValueError('Surface silver deposition must follow the last silver-removal bath')

    def slice_rows(self,start,stop):
        base=self.base
        if base is not None:base=base.slice_rows(start,stop) if callable(getattr(base,'slice_rows',None)) else base[start:stop]
        tile=copy(self);tile.base=base;tile.start=self.start+start;tile.stop=self.start+stop;tile.current_contact=None;tile.current_fog_contact=None;tile._wet_key=None;tile._wet=None
        tile._bath_contexts=dict(self._bath_contexts);tile._base_rate_cache=None
        return tile

    def contact_for_step(self,index,step):
        self._base_rate_cache=None
        self.current_contact=None
        self.current_fog_contact=None
        if self.ordered and step.process_event_index is not None and step.action.value=='fix_halide':
            event=self.events[step.process_event_index]
            response=validate_process_response(self.config.film.process_response)
            field=_footprint(control_grid(self.full_shape),frame_extent(self.config,self.full_shape),event)
            field*=(-math.expm1(-response.get('fixer_contact_rate_l_mmol_s',.001)*event['concentration_mmol_l']*event['duration_s']))
            return sample_control_rows(field,self.full_shape,self.start,self.stop)
        if step.chemical_placement is not None:
            x,y,radius=step.chemical_placement
            footprint=_footprint(control_grid(self.full_shape),frame_extent(self.config,self.full_shape),{'center_x':x,'center_y':y,'radius_mm':radius})
            return sample_control_rows(footprint,self.full_shape,self.start,self.stop)
        if step.action.value in ('destroy_dye','activate_remaining_halide','remove_emulsion','shear_emulsion','age_latent','light_residual_halide'):return None
        index=index if step.bath_index is None else step.bath_index
        event=self.entries.get(index);self.current_contact=None
        inhibitor=(self.inhibitor_session.rate_for_bath(index) if self.inhibitor_session is not None else self.inhibitor_fields.get(index)) if step.action.value in ('develop_silver','develop_color') else None
        sampled_inhibitor=sample_control_rows(inhibitor,self.full_shape,self.start,self.stop) if inhibitor is not None else None
        if event is None:
            self.current_contact=sampled_inhibitor
            self.current_fog_contact=self.current_contact
            return self.current_contact
        context=self._bath_contexts.get(index)
        if context is None:
            recipe=self.config.chemistry;solution=None
            if recipe.process_steps is not None or recipe.bath_overrides:
                from film_foundry.contracts.process_steps import CONDITION_RANGES,CHEMISTRY_FIELDS
                row=source_bath_rows(self.config,self.program)[index]
                recipe=replace(recipe,**{key:row[key] for key in (*CONDITION_RANGES,*CHEMISTRY_FIELDS) if key in row})
                solution=row.get('solution')
            context=(recipe,build_effective_development(recipe),float(recipe.time_min)*60,solution)
            self._bath_contexts[index]=context
        recipe,state,duration,solution=context
        rotary=event['kind']=='rotary_contact'
        cache_contact=not rotary and step.reaction_window is not None and (self.stop-self.start)*self.full_shape[1]<=CONTROL_GRID_LONG_EDGE**2
        if cache_contact and self._wet_key==index:
            wet=self._wet
        else:
            if self._contact_fields is not None:
                wet=self._contact_fields.sample(index,event,duration,step.reaction_window,
                    self.full_shape,self.start,self.stop)
            elif rotary:
                from film_foundry.core.rotary_contact import contact_duration as rotary_duration
                wet=rotary_duration(self.full_shape,self.start,self.stop,duration,event,step.reaction_window,
                    extent=frame_extent(self.config,self.full_shape),precision=contact_precision(self.config.processing))
            else:wet=contact_duration(self.full_shape,frame_extent(self.config,self.full_shape),self.start,self.stop,duration,event)
            self._wet=wet if cache_contact else None
            self._wet_key=index if cache_contact else None
        if solution is not None:
            from film_foundry.core.bath_kinetics import solution_rate_per_second
            from film_foundry.contracts.bath_solution import reaction_capability
            k=solution_rate_per_second(reaction_capability(solution,step.action.value),recipe)
            if step.reaction_window is not None:
                start,end=step.reaction_window
                # The shared effective contact interval ends at bath exit.
                # Clip it against this reaction's own part of the bath clock.
                if not rotary:wet=np.clip(wet-duration*(1-end),0,duration*(end-start))
                duration*=end-start
            full=-math.expm1(-k*duration);local=-np.expm1(-k*wet)
        else:
            k=REACTION_RATE_PER_MINUTE*state.activity/60.
            reference=1-math.exp(-REACTION_RATE_PER_MINUTE*REFERENCE_DEVELOPMENT_MINUTES)
            full=min(-math.expm1(-k*duration)/reference,1.)
            local=np.minimum(-np.expm1(-k*wet)/reference,1.)
        self.current_contact=np.asarray(local/max(full,1e-12),dtype=np.float32)
        if step.formation_response is not None:
            self.current_fog_contact=np.asarray(wet/max(duration,1e-12),dtype=np.float32)
            if sampled_inhibitor is not None:self.current_fog_contact*=sampled_inhibitor
        if sampled_inhibitor is not None:self.current_contact*=sampled_inhibitor
        return self.current_contact

    def fog_rate_for_step(self,label,action):
        """Chemical fog integrates wet seconds, not image-conversion fraction."""
        base=self._base_rate_for_step(label,action)
        contact=self.current_fog_contact
        if base is None:return contact
        if contact is None:return base
        return base*(contact[...,None] if np.ndim(base)==3 else contact)

    def rate_for_step(self,label,action):
        base=self._base_rate_for_step(label,action)
        contact=self.current_contact
        if base is None:return contact
        if contact is None:return base
        return base*(contact[...,None] if np.ndim(base)==3 else contact)

    def _base_rate_for_step(self,label,action):
        # Reuse only the built-in frozen spatial composition, within one
        # actual step and tile. Contact/fog fractions remain independent.
        # Unknown/stateful providers keep their original call order.
        from .spatial_fields import StepDevelopmentRateField,GlobalRateMultiplierField,GlobalLayerRateMultiplierField
        from .execution_topology import reference_execution_enabled
        base=self.base
        cacheable=type(base) is StepDevelopmentRateField and not reference_execution_enabled()
        if cacheable:
            fields=(base.common_rate,base.first_step_rate,*(entry[2] for entry in base.step_rate_fields))
            cacheable=all(field is None or type(field) in (np.ndarray,GlobalRateMultiplierField,GlobalLayerRateMultiplierField) for field in fields)
        key=(id(base),label,action)
        if cacheable and self._base_rate_cache is not None and self._base_rate_cache[0]==key:
            return self._base_rate_cache[1]
        if base is not None:
            base=base.rate_for_step(label,action) if callable(getattr(base,'rate_for_step',None)) else base
        if cacheable:self._base_rate_cache=(key,base)
        return base

def _footprint(shape,extent,event):
    h,w=shape
    if event.get('radius_mm',0.)<=0:footprint=np.ones(shape,np.float32)
    elif event.get('edge_width_mm',0.) or event.get('boundary_variation',0.):
        from .contact_geometry import contact_footprint
        footprint=contact_footprint(shape,extent,event)
    else:
        x=((np.arange(w)+.5)/w-event['center_x'])*extent[0]
        y=((np.arange(h)+.5)/h-event['center_y'])*extent[1]
        d=np.sqrt(x[None,:]**2+y[:,None]**2);edge=max(extent[0]/w,extent[1]/h)
        footprint=np.clip((event['radius_mm']-d)/edge+.5,0,1).astype(np.float32)
    variation=event.get('distribution_variation',0.)
    if variation and np.any(footprint):
        from film_foundry.core.accidents import _stain_deposit_map
        sigma=max(.5,event['distribution_scale_mm']*w/extent[0])
        pattern=_stain_deposit_map(shape,np.random.default_rng(event['distribution_seed']),sigma)
        target=float(np.mean(footprint,dtype=np.float64))
        footprint*=1-variation+2*variation*pattern
        footprint*=target/max(float(np.mean(footprint,dtype=np.float64)),1e-12)
    return footprint

def form_process_surface(config,shape,rng,*,program=None,coverage_transitions=None,shear_transitions=None,generate_texture=True):
    from film_foundry.core.film_process.surface import SurfaceProcessSession
    return SurfaceProcessSession(config,shape,program=program,
        coverage_transitions=coverage_transitions,shear_transitions=shear_transitions).finish(
            rng,generate_texture=generate_texture)


def finish_process_surface_texture(config,fields,rng):
    """Generate the stochastic observation texture at its historical RNG point."""
    if fields is not None and np.any(fields['emulsion_damage']):
        from film_foundry.core.accidents import _reticulation_texture_map
        from film_foundry.core.incident_identity import incident_rng
        fields['emulsion_texture']=_reticulation_texture_map(fields['emulsion_damage'].shape,incident_rng(config.chemistry,rng,'reticulation'))
        if 'emulsion_shift_x_mm' in fields:
            from film_foundry.core.emulsion_shear import shift_rows
            w=fields['emulsion_texture'].shape[1]
            fields['emulsion_texture']=shift_rows(fields['emulsion_texture'],fields['emulsion_shift_x_mm'][:,0]*w/frame_extent(config,fields['emulsion_texture'].shape)[0])

def prepare_surface_observer(fields):
    # Preserve interpolation order and per-field values; pack only independent
    # scalar reads so OpenCV performs two remaps rather than five per row tile.
    channels=[fields[key] for key in ('soluble_residue_mg_m2','bound_residue_mg_m2','surface_silver_mg_m2','emulsion_damage')]
    if 'backing_bound_mg_m2' in fields:channels.append(carbon_backing.optical_mass(fields))
    return np.stack(channels,axis=-1)


def surface_density_rows(fields,audit,shape,start,stop,*,prepared=None):
    """Observe frozen material fields, resampling only their v1 control grid."""
    h,w=shape[:2];gh,gw=fields['emulsion_damage'].shape
    xx=np.broadcast_to(((np.arange(w,dtype=np.float32)+.5)*gw/w-.5)[None,:],(stop-start,w))
    yy=np.broadcast_to(((np.arange(start,stop,dtype=np.float32)+.5)*gh/h-.5)[:,None],(stop-start,w))
    packed=prepare_surface_observer(fields) if prepared is None else prepared
    sampled=cv2.remap(packed,xx,yy,cv2.INTER_LINEAR,borderMode=cv2.BORDER_REPLICATE)
    texture=cv2.remap(fields['emulsion_texture'],xx,yy,cv2.INTER_LINEAR,borderMode=cv2.BORDER_REPLICATE)
    response=audit['response']
    residue=sampled[...,0]+sampled[...,1]
    density=residue[...,None]*np.asarray(response['residue_density_rgb_per_mg_m2'],np.float32)
    if 'surface_silver_scattering_rgb' in response:
        from film_foundry.core.film_surface_optics import extinction_coefficients
        density+=sampled[...,2,None]*extinction_coefficients(response)
    else:density+=sampled[...,2,None]*response['silver_density_per_mg_m2']
    if 'surface_halide_mg_m2' in fields and np.any(fields['surface_halide_mg_m2']):
        halide=cv2.remap(fields['surface_halide_mg_m2'],xx,yy,cv2.INTER_LINEAR,borderMode=cv2.BORDER_REPLICATE)
        density+=halide[...,None]*(response['silver_density_per_mg_m2']*.12)
    structure=sampled[...,3]*texture
    if 'emulsion_coverage' in fields:
        structure*=cv2.remap(fields['emulsion_coverage'],xx,yy,cv2.INTER_LINEAR,borderMode=cv2.BORDER_REPLICATE)
    density+=structure[...,None]*response['structure_density']
    if 'backing_bound_mg_m2' in fields:
        density+=sampled[...,4,None]*audit['carbon_backing']['density_per_mg_m2']
    return density

def validate_surface_fields(fields):
    if fields is None:return None
    if not isinstance(fields,dict) or set(fields) not in tuple(set(names) for names in (*SURFACE_SCHEMAS.values(),(*SURFACE_FIELDS_V4,*carbon_backing.FIELDS),(*SURFACE_FIELDS_V5,*carbon_backing.FIELDS))):raise ValueError('Invalid process surface fields')
    result={};shape=None
    for key,value in fields.items():
        a=np.asarray(value,dtype=np.float32)
        if a.ndim!=2 or min(a.shape)<1 or max(a.shape)>512 or shape is not None and a.shape!=shape:raise ValueError('Invalid process surface grid')
        shape=a.shape;low,high=(-6400.,6400.) if key=='emulsion_shift_x_mm' else (-1.,1.) if key=='emulsion_texture' else (0.,1.) if key in ('emulsion_damage','backing_softening','emulsion_coverage') else (0.,float('inf'))
        if not np.isfinite(a).all() or np.min(a)<low or np.max(a)>high:raise ValueError('Invalid process surface values')
        a.setflags(write=False);result[key]=a
    if 'emulsion_coverage' in result:
        for key,owned_key in OWNED_POOLS.items():
            if np.any(result[owned_key]>result.get(key,0.)+1e-5):raise ValueError('Emulsion-owned deposit exceeds its total pool')
    return result

def surface_field_names(audit):
    names=SURFACE_SCHEMAS[audit['model']]
    if audit['model'] in (SURFACE_MODEL_V4,SURFACE_MODEL_V5) and audit.get('carbon_backing') is not None:names=(*names,*carbon_backing.FIELDS)
    return names

def surface_archive_header(data,metadata):
    """Validate capability without decompressing fields for scan-only loading."""
    audit=metadata.get('film_process_model',{}).get('process_surface')
    present=any(key.startswith('process_surface_') for key in data.files)
    if audit is None and not present:return None
    if not isinstance(audit,dict) or audit.get('model') not in SURFACE_SCHEMAS:raise ValueError('Unsupported process surface model')
    keys=[f'process_surface_{key}' for key in surface_field_names(audit)]
    if not all(key in data.files for key in keys):raise ValueError('Incomplete process surface archive')
    if set(key for key in data.files if key.startswith('process_surface_'))!=set(keys):raise ValueError('Unexpected process surface archive fields')
    validate_process_response(audit.get('response'))
    if audit['model']==SURFACE_MODEL_V3 or audit.get('carbon_backing') is not None:
        backing=validate_carbon_backing(audit.get('carbon_backing'))
        if backing is None or backing['initial_mg_m2']<=0:raise ValueError('Missing carbon backing material')
    if audit['model'] in (SURFACE_MODEL_V4,SURFACE_MODEL_V5) and validate_emulsion_integrity(audit.get('emulsion_integrity')) is None:raise ValueError('Missing emulsion integrity material')
    if audit['model']==SURFACE_MODEL_V5:
        if audit.get('deformation',{}).get('model')!='horizontal_emulsion_shear_v1' or audit['emulsion_integrity']['model']!='wet_emulsion_integrity_v2':
            raise ValueError('Missing horizontal emulsion shear declaration')
    return audit

def load_surface_fields(data,metadata):
    audit=surface_archive_header(data,metadata)
    if audit is None:return None
    fields=validate_surface_fields({key:data[f'process_surface_{key}'] for key in surface_field_names(audit)})
    if list(fields['emulsion_damage'].shape)!=audit.get('grid_shape'):raise ValueError('Process surface grid does not match metadata')
    if audit['model']==SURFACE_MODEL_V3 or audit.get('carbon_backing') is not None:
        total=sum(float(np.mean(fields[key],dtype=np.float64)) for key in carbon_backing.FIELDS[:4])
        initial=audit['carbon_backing']['initial_mg_m2']
        if not math.isclose(total,initial,rel_tol=2e-5,abs_tol=1e-5):raise ValueError('Carbon backing mass balance mismatch')
    return fields
