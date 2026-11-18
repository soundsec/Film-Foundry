"""Small, read-only film brackets over one frozen source and recipe.

The application owns iteration and comparison identity. The existing engine
owns validation and image formation; no pixels or preview recipes are saved.
"""
from copy import deepcopy
from dataclasses import asdict, dataclass
from pathlib import Path
import math

import numpy as np

from film_foundry.app.preview_budget import Priority, preview_budget
from film_foundry.model.config import DarkroomConfig, SCAN_LOOK_FIELDS
from film_foundry.app.control import WorkflowCancelled
from film_foundry.app.preview_session import PreviewSession
from film_foundry.core.file_cache import file_revision
from film_foundry.contracts.process_steps import CONDITION_RANGES, validate_process_conditions


@dataclass(frozen=True)
class StripAxis:
    path: tuple
    stage: str
    current: float
    integer: bool = False
    bath_action: str = ''


_CAPTURE_LOOK = {'exposure_ev','halation_multiplier','halation_sensitivity',
                 'emulsion_mtf_strength','digital_artifact_suppression','halation_edge_compensation'}
_STRUCTURAL = {'version','bath','before_bath','after_bath','source_bath','width','height','count'}


def _stage(path):
    bank,key=path[:2]
    if bank in ('film','film_border','exposure'):return 'film'
    if bank=='look':return 'scan' if key in SCAN_LOOK_FIELDS else 'film' if key in _CAPTURE_LOOK else 'develop'
    if bank=='scanner':return 'scan'
    return 'film' if key.startswith(('light_leak_','exposure_occlusion_')) or key in ('frame_size','frame_long_edge_mm') else 'develop'


def bath_rows(config):
    from film_foundry.core.film_process.recipe import program_from_develop_recipe
    from film_foundry.core.process_operations import source_bath_rows
    from film_foundry.core.media import is_monochrome_material
    program=program_from_develop_recipe(config.chemistry,
        mode='bw_negative' if is_monochrome_material(config) else config.mode,
        material_process=config.film.medium_process)
    return source_bath_rows(config,program)


def available_axes(config,mode):
    active={'full':{'film','develop','scan'},'expose':{'film'},'develop':{'film','develop'},
            'develop_latent':{'develop'},'scan':{'scan'}}[mode]
    values=asdict(config);result=[]
    from film_foundry.core.process_controls import RAW_CONDITIONS,RAW_CHEMISTRY
    principle=config.process_control_mode=='principle'
    intent=config.chemistry.effect_intent
    def walk(value,path):
        if isinstance(value,dict):
            for key,child in value.items():
                if 'seed' in key or key in _STRUCTURAL:continue
                walk(child,(*path,key))
        elif isinstance(value,(list,tuple)):
            for i,child in enumerate(value):walk(child,(*path,i))
        elif type(value) in (int,float) and math.isfinite(value):
            stage=_stage(path)
            if stage in active:result.append(StripAxis(path,stage,value,type(value) is int))
    for bank in ('look','exposure','chemistry','scanner','film','film_border'):
        for key,value in values[bank].items():
            if 'seed' in key or key in ('process_steps','bath_overrides'):continue
            if bank=='chemistry':
                if intent and intent.get('enabled',True) and key in intent.get('amounts',{}):continue
                if principle and key not in {*RAW_CONDITIONS,*RAW_CHEMISTRY,'latent_wait','post_process_light','process_events','frame_long_edge_mm'} and not key.startswith(('light_leak_','exposure_occlusion_')):continue
            if bank=='look' and principle and key not in {'exposure_ev',*SCAN_LOOK_FIELDS}:continue
            if bank=='scanner' and key in ('negative_backlight_ev','light_table_ev') and config.scanner.transmission_light_ev is not None:continue
            if bank=='scanner' and key in ('negative_backlight_temperature_k','light_table_temperature_k') and config.scanner.transmission_light_temperature_k is not None:continue
            walk(value,(bank,key))
    if 'develop' in active:
        from film_foundry.contracts.bath_overrides import CONDITIONS
        from film_foundry.contracts.bath_solution import ACTIONS
        for index,row in enumerate(bath_rows(config)):
            if row['action'] not in ACTIONS:continue
            for key in CONDITIONS:
                value=row.get(key,getattr(config.chemistry,key))
                if value is None:
                    if key!='agitation_time_s':continue
                    value=row.get('time_min',config.chemistry.time_min)*60
                result.append(StripAxis(('bath',index,key),'develop',float(value),bath_action=row['action']))
    return result


def _set_path(data,path,value):
    """Copy only the requested branch, including immutable tuple components."""
    if not path:return value
    key,*tail=path
    result=list(data) if isinstance(data,(list,tuple)) else dict(data)
    result[key]=_set_path(data[key],tail,value)
    return tuple(result) if isinstance(data,tuple) else result


def variant_config(base,axis,value):
    if not math.isfinite(value):raise ValueError('Trial value must be finite')
    if axis.integer and value!=round(value):raise ValueError('This parameter requires whole numbers')
    value=int(value) if axis.integer else float(value)
    data=asdict(base)
    if axis.path[0]=='bath':
        from film_foundry.contracts.bath_overrides import validate_bath_overrides
        from film_foundry.contracts.bath_solution import MODEL,validate_solution
        _,index,key=axis.path;row=bath_rows(base)[index]
        if base.chemistry.process_steps is not None and not any(e['bath']==index+1 for e in base.chemistry.bath_overrides or ()):
            steps=list(deepcopy(base.chemistry.process_steps));steps[index][key]=value;data['chemistry']['process_steps']=steps
        else:
            overrides={entry['bath']:deepcopy(entry) for entry in base.chemistry.bath_overrides or ()}
            entry=overrides.get(index+1,{'bath':index+1,'action':row['action'],
                'solution':deepcopy(row.get('solution')) or validate_solution({'model':MODEL,'action':row['action']},row['action'])})
            entry[key]=value;overrides[index+1]=entry
            data['chemistry']['bath_overrides']=validate_bath_overrides(tuple(overrides[i] for i in sorted(overrides)))
        conditions={name:getattr(base.chemistry,name,None) for name in CONDITION_RANGES}
        conditions.update(row);conditions[key]=value;validate_process_conditions(conditions)
    else:
        data=_set_path(data,axis.path,value)
        # Desired-effect controls have a compatibility mirror in the desktop.
        if axis.path[:3]==('chemistry','effect_intent','amounts'):
            data['chemistry'][axis.path[-1]]=value
    return DarkroomConfig.from_dict(data)


def trial_values(start, stop, count, spacing='linear', custom=''):
    if spacing == 'list':
        values = [float(item.strip()) for item in custom.replace('，', ',').split(',')]
    else:
        if type(count) is not int or not 2 <= count <= 9:
            raise ValueError('Choose 2–9 trials')
        if spacing == 'geometric' and (start <= 0 or stop <= 0):
            raise ValueError('Geometric endpoints must be positive')
        method = np.geomspace if spacing == 'geometric' else np.linspace
        values = method(start, stop, count).tolist()
    if not 2 <= len(values) <= 9 or not all(map(math.isfinite, values)) or len(set(values)) != len(values):
        raise ValueError('Choose 2–9 distinct finite values')
    return values


def build_variants(base,mode,axis,start,stop,count,*,values=None):
    if not 2<=count<=9 or type(count) is not int:raise ValueError('Choose 2–9 trials')
    known={entry.path:entry for entry in available_axes(base,mode)}
    if axis.path not in known:raise ValueError('Parameter is frozen or unavailable in this task')
    axis=known[axis.path]
    if values is None:
        values=trial_values(start, stop, count)
    from film_foundry.core.runtime_validate import _validate_develop_runtime,_validate_scan_runtime
    variants=[]
    for value in values:
        config=variant_config(base,axis,value)
        if mode!='scan':_validate_develop_runtime(config)
        if mode in ('full','scan'):_validate_scan_runtime(config)
        variants.append((value,config))
    return variants


class FilmStripSession:
    """Serial engine adapter; owned by the worker, independent of Qt."""
    def __init__(self,path,mode,config,*,long_edge=640,path_seed_offset=None):
        from film_foundry.app.workflow import rng_and_resolved_seed_for_develop
        self.path=Path(path).resolve();self.mode=mode;self.config=deepcopy(config)
        self.revision=self._source_revision();self.long_edge=int(long_edge)
        if not 16<=self.long_edge<=900:raise ValueError('Trial preview edge must be 16–900')
        _,self.seed=rng_and_resolved_seed_for_develop(self.path,config,path_seed_offset=path_seed_offset)
        self.draft = False
        self.frame_settings = (1,0.)
        self.preview=PreviewSession();self.source=None;self.latent=None

    def check_source(self):
        if self._source_revision()!=self.revision:raise ValueError('Source changed; reopen the test strip')

    def _source_revision(self):
        dependencies=[self.path.with_suffix(self.path.suffix+'.json')]
        if self.mode=='scan' and self.path.suffix.lower()=='.npz':
            from film_foundry.app.workflow import transmission_raw_path_for_medium
            raw=transmission_raw_path_for_medium(self.path)
            dependencies.extend((raw,raw.with_suffix(raw.suffix+'.json')))
        return (file_revision(self.path),*(file_revision(p) if p.is_file() else None for p in dependencies))

    def render(self,config):
        from film_foundry.app.workflow import gray_to_rgb,scan_from_file
        from film_foundry.core.engine import expose_image_to_latent,expose_linear_to_latent,develop_negative,develop_negative_from_latent
        from film_foundry.core.preview import optical_density_visual_preview,negative_visual_preview,resize_to_long_edge
        self.check_source();config=deepcopy(config)
        if self.mode in ('full','expose','develop'):
            from film_foundry.app.workspace_jobs import apply_frame_settings
            apply_frame_settings(config,*self.frame_settings)
        config.seed_strategy='fixed';config.random_seed=self.seed
        if self.draft:
            config.fast_mode = True
            config.processing.execution_mode = 'reduced_fast'
            config.processing.quality_mode = 'draft'
            config.output.render_long_edge = config.output.preview_long_edge = self.long_edge
        # Comparison identity is private; adoption never copies these values.
        for name in ('incident_identity_seed','light_leak_identity_seed','exposure_occlusion_identity_seed'):
            setattr(config.chemistry,name,getattr(self.config.chemistry,name))
        config.debug_output=False;config.comparison_grid=False;config.save_sidecar=False
        if self.mode=='full':
            _,result,_=self.preview.render(self.path,config,long_edge=self.long_edge)
        elif self.mode=='scan':
            scanned,_,_=scan_from_file(self.path,config,retain_stages=False,preview_long_edge=self.long_edge)
            result=scanned.output_srgb
        elif self.mode=='develop_latent':
            if self.latent is None:
                from film_foundry.core.latent_io import load_exposed_latent
                from film_foundry.app.preview_inputs import preview_latent
                latent,_=load_exposed_latent(self.path);self.latent=preview_latent(latent,self.long_edge)
            medium=develop_negative_from_latent(deepcopy(self.latent),config,rng=np.random.default_rng(self.seed))
            result=optical_density_visual_preview(medium.optical_density_rgb)
        else:
            if self.source is None:
                from film_foundry.core.source_image import load_formation_image
                image,color=load_formation_image(self.path,input_icc_policy=config.input_icc_policy)
                self.source=resize_to_long_edge(image,self.long_edge);self.encoding=color['encoding']
                self.source.setflags(write=False)
            if self.mode=='expose':
                expose=expose_linear_to_latent if self.encoding=='linear_rgb' else expose_image_to_latent
                latent=expose(self.source,config,rng=np.random.default_rng(self.seed))
                result=gray_to_rgb(np.mean(latent.developability,axis=-1))
            elif self.mode=='develop':
                medium=develop_negative(self.source,config,input_encoding=self.encoding,rng=np.random.default_rng(self.seed))
                result=optical_density_visual_preview(medium.optical_density_rgb) if medium.optical_density_rgb is not None else negative_visual_preview(medium.density_grain,config.film)
            else:raise ValueError('Unsupported film test-strip task')
        self.check_source()
        result=np.asarray(result)
        if not np.isfinite(result).all():raise ValueError('Non-finite trial output')
        return result

    def formal_frame(self, config, *, scaled_override=False):
        from film_foundry.core.image_orientation import rotate_output
        from film_foundry.app.workflow import (gray_to_rgb, load_formal_photo, processing_long_edge,
            prepare_develop_runtime_config, scan_from_file)
        from film_foundry.core.engine import (render_file_pixels, develop_negative, develop_negative_from_latent,
            expose_linear_to_latent, expose_image_to_latent)
        from film_foundry.core.preview import optical_density_visual_preview, negative_visual_preview
        from film_foundry.app.workspace_jobs import apply_frame_settings
        self.check_source()
        settings=deepcopy(config)
        if self.mode in ('full','expose','develop'):
            apply_frame_settings(settings,*self.frame_settings)
        for name in ('incident_identity_seed','light_leak_identity_seed','exposure_occlusion_identity_seed'):
            setattr(settings.chemistry,name,getattr(self.config.chemistry,name))
        if self.mode=='full':
            pixels=render_file_pixels(self.path,settings,scaled_override=scaled_override,resolved_seed=self.seed)
        elif self.mode=='scan':
            pixels=scan_from_file(self.path,settings)[0].output_srgb
        else:
            runtime,axes=prepare_develop_runtime_config(settings,scaled_override=scaled_override)
            rng=np.random.default_rng(self.seed)
            if self.mode=='develop_latent':
                from film_foundry.core.latent_io import load_exposed_latent
                latent,_=load_exposed_latent(self.path)
                medium=develop_negative_from_latent(latent,runtime,rng=rng)
            elif self.mode in ('develop','expose'):
                edge=processing_long_edge(settings,scaled_override=scaled_override)
                image,info=load_formal_photo(self.path,runtime,axes,edge)
                if self.mode=='expose':
                    expose=expose_linear_to_latent if info['encoding']=='linear_rgb' else expose_image_to_latent
                    latent=expose(image,runtime,rng=rng)
                    pixels=gray_to_rgb(np.mean(latent.developability,axis=-1))
                else:
                    medium=develop_negative(image,runtime,rng=rng,input_encoding=info['encoding'])
            else:raise ValueError('Unsupported film endpoint')
            if self.mode!='expose':
                pixels=(optical_density_visual_preview(medium.optical_density_rgb) if medium.optical_density_rgb is not None
                        else negative_visual_preview(medium.density_grain,runtime.film))
        pixels=rotate_output(pixels,settings.output.rotation_quarters)
        self.check_source()
        return pixels

    def run(self,variants,cancel,on_result):
        from film_foundry.app.control import WorkflowControl
        control = WorkflowControl(cancel_requested=cancel.requested)
        try:
            for index,(value,config) in enumerate(variants):
                if cancel.requested():raise WorkflowCancelled()
                pixels=preview_budget.run(Priority.STRIP, self.render, config, check=control.check_cancel)
                if cancel.requested():raise WorkflowCancelled()
                on_result(index,value,pixels)
        finally:
            self.preview.clear();self.source=None;self.latent=None
