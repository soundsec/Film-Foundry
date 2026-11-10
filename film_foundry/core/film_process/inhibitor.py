"""The existing mobile-inhibitor reduction as a resumable carried inventory.

The v1 mobile liquid is not an emulsion-owned solid: wear/shear do not transport
it or its independent wash-access hardening history. No new kinetics here.
"""
from copy import deepcopy
import math
import numpy as np
from film_foundry.contracts.process_events import validate_process_response,CONTACT_KINDS
from film_foundry.core.process_operations import (
    events_for,control_grid,frame_extent,source_bath_rows,operation_timeline,
    _footprint,wash_fraction,hardened_fraction,solution_hardening)
from film_foundry.core.film_process.event_schedule import event_schedule
from film_foundry.core.film_process.bindings import canonical_wet_definition

MODEL='film_inhibitor_boundary_v1'


class InhibitorProcessSession:
    def __init__(self,config,shape,program,*,collect_rates=False):
        self.config=config;self.shape=tuple(shape[:2]);self.program=program
        self.all_events=events_for(config)
        self.active=any(e['kind']=='developer_inhibitor' for e in self.all_events)
        self.grid=control_grid(shape);self.extent=frame_extent(config,self.shape)
        self.response=validate_process_response(config.film.process_response)
        self.cursor=0;self.next_step=0;self.schedule=[]
        self.timeline=operation_timeline(config,program,[e for e in self.all_events if e['kind'] not in CONTACT_KINDS]) if self.active else []
        self.load=np.zeros(self.grid,np.float32) if self.active else None
        self.hardening=self.response['initial_hardening']
        self.added=0.;self.washed=0.;self.history=[]
        self.rate=None;self.current_bath=None;self.current_developing=False
        self.rate_count=0;self.rates={} if collect_rates else None

    def definition(self):
        return canonical_wet_definition(dict(shape=list(self.shape),response=self.response,
            events=self.all_events,baths=source_bath_rows(self.config,self.program),
            extent=self.extent,temperature_c=self.config.chemistry.temperature_c,
            time_min=self.config.chemistry.time_min))

    def bind(self,program,resume=None,next_step=0):
        self.schedule=event_schedule(program,self.all_events,self.timeline)
        self.next_step=next_step;self.total_steps=len(program.steps)
        if resume is not None:self.restore(resume)
        if self.cursor!=sum(position<2*next_step for position in self.schedule):
            raise ValueError('Inhibitor and primary reaction cursors differ')

    def before_step(self,index):self.advance_through(2*index)

    def after_step(self,index):
        self.advance_through(2*index+1);self.next_step=index+1

    def advance_through(self,position):
        while self.cursor<len(self.schedule) and self.schedule[self.cursor]<=position:self.advance()

    def advance(self):
        self.apply_event(self.timeline[self.cursor]);self.cursor+=1

    def apply_event(self,event):
        kind=event['kind']
        if kind=='developer_inhibitor':
            incoming=_footprint(self.grid,self.extent,event)*(event['concentration_mmol_l']*event['retained_ml_m2']/1000.)
            self.load+=incoming;self.added+=float(np.mean(incoming,dtype=np.float64));self.rate=None
        elif kind in ('wash','backing_wash'):
            fraction=wash_fraction(self.response,self.hardening,event)
            if kind=='backing_wash':fraction=fraction*np.clip(_footprint(self.grid,self.extent,event),0.,1.)
            removed=self.load*fraction;self.load-=removed
            self.washed+=float(np.mean(removed,dtype=np.float64));self.rate=None
        elif kind=='harden':self.hardening=hardened_fraction(self.response,self.hardening,event)
        elif kind=='solution_hardening':self.hardening=solution_hardening(self.config,self.shape,self.response,self.hardening,event)
        elif kind=='bath_temperature':
            self.current_bath=event['bath']-1
            self.current_developing=event['conditions']['action'] in ('develop_silver','develop_color')
            if self.current_developing and np.any(self.load):
                if self.rate is None:
                    self.rate=np.asarray(1/(1+self.load/self.response.get('inhibitor_half_response_mmol_m2',.2)),np.float32)
                    self.rate.setflags(write=False);self.rate_count+=1
                if self.rates is not None:self.rates[self.current_bath]=self.rate
                self.history.append({'bath':event['bath'],'mean_activity_fraction':float(np.mean(self.rate)),
                    'inhibitor_mmol_m2':float(np.mean(self.load,dtype=np.float64))})

    def rate_for_bath(self,index):
        return self.rate if self.current_bath==index and self.current_developing else None

    def finish(self):
        while self.cursor<len(self.timeline):self.advance()
        return self.rates or {},self.audit()

    def audit(self):
        if not self.active:return None
        # Preserve the existing complete-plan accounting. The incremental path
        # retains only the current rate; these two legacy metrics describe the
        # distinct fields that the complete compiled plan would retain.
        return {'model':'mobile_development_inhibitor_v1','grid_shape':list(self.grid),
            'half_response_mmol_m2':self.response.get('inhibitor_half_response_mmol_m2',.2),
            'added_mmol_m2':self.added,'washed_mmol_m2':self.washed,
            'remaining_mmol_m2':float(np.mean(self.load,dtype=np.float64)),
            'baths':deepcopy(self.history),'cached_fields':self.rate_count,
            'cached_bytes':self.rate_count*self.load.nbytes,
            'scope':'frame_local_shared_by_tiles','scanner_owned':False}

    def snapshot(self):
        value=dict(model=MODEL,definition=self.definition(),active=self.active,next_step=self.next_step)
        if self.active:
            value.update(load=self.load,hardening=self.hardening,rate=self.rate,cursor=self.cursor,
                added=self.added,washed=self.washed,history=deepcopy(self.history),
                current_bath=self.current_bath,current_developing=self.current_developing,rate_count=self.rate_count)
        return value

    def restore(self,value):
        if (value.get('model')!=MODEL or canonical_wet_definition(value.get('definition'))!=self.definition()
            or value.get('active')!=self.active or value.get('next_step')!=self.next_step):
            raise ValueError('Inhibitor checkpoint differs from its frozen conditions')
        if not self.active:return
        def array(name,low,high,scalar=False):
            a=np.asarray(value[name])
            if (a.shape not in (((),self.grid) if scalar else (self.grid,)) or
                not np.isfinite(a).all() or a.min()<low or a.max()>high):
                raise ValueError('Invalid inhibitor '+name)
            return a.copy() if a.ndim else float(a)
        self.load=array('load',0.,float('inf'))
        self.hardening=array('hardening',0.,1.,scalar=True)
        self.rate=None if value['rate'] is None else array('rate',0.,1.)
        if self.rate is not None:self.rate.setflags(write=False)
        for name in ('added','washed'):
            number=value[name]
            if not isinstance(number,(int,float)) or not math.isfinite(number) or number<0:raise ValueError('Invalid inhibitor ledger')
            setattr(self,name,number)
        if not math.isclose(self.added,self.washed+float(np.mean(self.load,dtype=np.float64)),rel_tol=2e-5,abs_tol=1e-6):
            raise ValueError('Inhibitor inventory balance differs')
        if type(value['cursor']) is not int or not 0<=value['cursor']<=len(self.timeline):raise ValueError('Invalid inhibitor cursor')
        if not isinstance(value['history'],list) or len(value['history'])>value['cursor']:raise ValueError('Invalid inhibitor bath history')
        if type(value['rate_count']) is not int or not 0<=value['rate_count']<=len(value['history']):raise ValueError('Invalid inhibitor rate count')
        if type(value['current_developing']) is not bool:raise ValueError('Invalid inhibitor bath stage')
        bath=value['current_bath']
        if bath is not None and (type(bath) is not int or not 0<=bath<len(source_bath_rows(self.config,self.program))):raise ValueError('Invalid inhibitor bath')
        for name in ('cursor','history','rate_count','current_bath','current_developing'):setattr(self,name,deepcopy(value[name]))
