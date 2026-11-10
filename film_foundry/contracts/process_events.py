"""Versioned physical operation inputs and creative material response units."""
from copy import deepcopy
import math
from film_foundry.contracts.carbon_backing import FIELDS as CARBON_BACKING_FIELDS
from film_foundry.contracts.emulsion_integrity import FIELDS as EMULSION_FIELDS

EVENT_MODEL='film_process_events_v1'
EVENT_MODEL_V2='film_process_events_v2'
EVENT_MODEL_V3='film_process_events_v3'
EVENT_MODEL_V4='film_process_events_v4'
EVENT_MODEL_V5='film_process_events_v5'
EVENT_MODEL_V6='film_process_events_v6'
EVENT_MODEL_V7='film_process_events_v7'
EVENT_MODEL_V8='film_process_events_v8'
EVENT_MODEL_V9='film_process_events_v9'
EVENT_MODEL_V10='film_process_events_v10'
EVENT_MODEL_V11='film_process_events_v11'
EVENT_MODEL_V12='film_process_events_v12'
EVENT_MODEL_V13='film_process_events_v13'
EVENT_MODEL_V14='film_process_events_v14'
EVENT_MODEL_V15='film_process_events_v15'
WETTING_CHOICES={'wetting_model':('periodic_arc_v1','edge_front_v1'),'contact_edge':('bottom','top')}
EDGE_WETTING_FIELDS={'meniscus_width_mm':(.7,.01,20.),'meniscus_roughness_mm':(.8,0.,20.),
    'residue_concentration_mg_l':(0.,0.,100000.),'retained_ml_m2':(20.,0.,1000.),
    'evaporation_time_s':(120.,.01,100000.)}
MENISCUS_FIELDS={'liquid_level_gradient':(0.,-1.,1.),'meniscus_retention_s':(0.,0.,60.)}
BOUNDARY_FIELDS={'edge_width_mm':(0.,0.,100.),'boundary_variation':(0.,0.,1.)}
CONTACT_KINDS=('bath_contact','rotary_contact')
ROTARY_INSTABILITY_FIELDS={'rotation_instability':(0.,0.,1.),'rotation_seed':(0,0,2147483647)}
RESPONSE_MODEL='creative_process_response_v1'
RESPONSE_MODEL_V2='creative_process_response_v2'
RESPONSE_MODEL_V3='creative_process_response_v3'
RESPONSE_MODEL_V4='creative_process_response_v4'
SURFACE_MODEL='film_process_surface_v1'
SURFACE_FIELDS=('soluble_residue_mg_m2','bound_residue_mg_m2','surface_silver_mg_m2','emulsion_damage','emulsion_texture')
SURFACE_MODEL_V2='film_process_surface_v2'
SURFACE_FIELDS_V2=(*SURFACE_FIELDS,'surface_halide_mg_m2')
SURFACE_MODEL_V3='film_process_surface_v3'
SURFACE_FIELDS_V3=(*SURFACE_FIELDS_V2,*CARBON_BACKING_FIELDS)
SURFACE_MODEL_V4='film_process_surface_v4'
SURFACE_FIELDS_V4=(*SURFACE_FIELDS_V2,*EMULSION_FIELDS)
SURFACE_MODEL_V5='film_process_surface_v5'
SURFACE_FIELDS_V5=(*SURFACE_FIELDS_V4,'emulsion_shift_x_mm')
SURFACE_SCHEMAS={SURFACE_MODEL_V5:SURFACE_FIELDS_V5,SURFACE_MODEL_V4:SURFACE_FIELDS_V4,SURFACE_MODEL:SURFACE_FIELDS,SURFACE_MODEL_V2:SURFACE_FIELDS_V2,SURFACE_MODEL_V3:SURFACE_FIELDS_V3}
# Values are illustrative response parameters, not measurements of a stock.
RESPONSE_DEFAULTS={'model':RESPONSE_MODEL,'wash_exchange_s':40.,'binding_rate_s':.004,
    'initial_hardening':.4,'hardening_rate_l_g_s':.004,'thermal_expansion_per_c':.002,
    'relaxation_s':30.,'yield_strain':.025,'damage_strain':.04,'hydration_s':30.,
    'silver_reduction_l_mmol_s':.008,'silver_density_per_mg_m2':.02,
    'residue_density_rgb_per_mg_m2':(.002,.006,.012),'structure_density':.10}
RESPONSE_RANGES={'wash_exchange_s':(.01,10000.),'binding_rate_s':(0.,1.),'initial_hardening':(0.,1.),
    'hardening_rate_l_g_s':(0.,1.),'thermal_expansion_per_c':(0.,.1),'relaxation_s':(.01,10000.),
    'yield_strain':(0.,1.),'damage_strain':(.0001,1.),'hydration_s':(.01,10000.),
    'silver_reduction_l_mmol_s':(0.,1.),'silver_density_per_mg_m2':(0.,10.),'structure_density':(0.,2.)}
RESPONSE_V2_DEFAULTS={'inhibitor_half_response_mmol_m2':.2,'fixer_contact_rate_l_mmol_s':.001}
RESPONSE_V2_RANGES={'inhibitor_half_response_mmol_m2':(.00001,100.),'fixer_contact_rate_l_mmol_s':(0.,1.)}
RESPONSE_V3_DEFAULTS={'dye_attack_l_mmol_s':.002}
RESPONSE_V3_RANGES={'dye_attack_l_mmol_s':(0.,1.)}
RESPONSE_V4_DEFAULTS={'surface_silver_scattering_rgb':(.003,.018,.006),
    'surface_silver_roughness':.35,'surface_silver_directionality':.8}
RESPONSE_V4_RANGES={'surface_silver_roughness':(.02,1.),'surface_silver_directionality':(0.,1.)}
PLACEMENT={'center_x':(.5,0.,1.),'center_y':(.5,0.,1.),'radius_mm':(0.,0.,1000.)}
EVENT_FIELDS={
    'rotary_contact':{'bath':(1,1,32),'immersion_fraction':(.5,0.,1.),'rotation_rpm':(30.,0.,120.),
        'pause_at_fraction':(.5,0.,1.),'pause_s':(0.,0.,7200.),**ROTARY_INSTABILITY_FIELDS,**MENISCUS_FIELDS,**EDGE_WETTING_FIELDS},
    'emulsion_shear':{'pressure':(.4,0.,1.),'duration_s':(10.,0.,7200.),'temperature_c':(20.,0.,60.),'transfer_s':(10.,0.,3600.),'travel_mm':(1.,-100.,100.),'center_y':(.5,0.,1.),'band_width_mm':(8.,0.,1000.)},
    'emulsion_wear':{'pressure':(.4,0.,1.),'duration_s':(10.,0.,7200.),'temperature_c':(20.,0.,60.),'transfer_s':(10.,0.,3600.),**PLACEMENT},
    'backing_condition':{'alkalinity_g_l':(1.,0.,100.),'duration_s':(30.,0.,7200.),'temperature_c':(20.,0.,60.),'transfer_s':(10.,0.,3600.),**PLACEMENT},
    'backing_wash':{'duration_s':(90.,0.,7200.),'temperature_c':(20.,0.,60.),'transfer_s':(10.,0.,3600.),
        'water_changes':(5.,0.,100.),'agitation':(.5,0.,1.),'back_rubbing':(.5,0.,1.),'front_rubbing':(0.,0.,1.),'emulsion_contact':(.05,0.,1.),**PLACEMENT},
    'dye_bath':{'concentration_mmol_l':(1.,0.,1000.),'duration_s':(120.,0.,7200.),'temperature_c':(20.,0.,60.),'transfer_s':(10.,0.,3600.),**PLACEMENT},
    'fixer_contact':{'before_bath':(1,1,32),'concentration_mmol_l':(10.,0.,10000.),'duration_s':(30.,0.,7200.),**PLACEMENT},
    'developer_inhibitor':{'before_bath':(1,1,32),'concentration_mmol_l':(10.,0.,10000.),'retained_ml_m2':(20.,0.,1000.),**PLACEMENT},
    'bath_contact':{'bath':(1,1,32),'fill_s':(0.,0.,7200.),'immersion_fraction':(1.,0.,1.),
        'angle_degrees':(90.,-360.,360.),'bubble_radius_mm':(0.,0.,1000.),'bubble_release_s':(0.,0.,7200.),'center_x':(.5,0.,1.),'center_y':(.5,0.,1.)},
    'contaminate':{'concentration_mg_l':(1000.,0.,100000.),'retained_ml_m2':(20.,0.,1000.),'contact_s':(0.,0.,7200.),**PLACEMENT},
    'wash':{'duration_s':(300.,0.,7200.),'water_changes':(5.,0.,100.),'temperature_c':(20.,0.,60.),'transfer_s':(10.,0.,3600.)},
    'temperature':{'temperature_c':(20.,0.,60.),'transfer_s':(10.,0.,3600.),'hold_s':(30.,0.,7200.)},
    'harden':{'concentration_g_l':(1.,0.,100.),'duration_s':(60.,0.,7200.)},
    'silver_bath':{'silver_mg_l':(100.,0.,10000.),'solution_ml_m2':(100.,0.,1000.),'reducer_mmol_l':(1.,0.,100.),
        'duration_s':(120.,0.,7200.),'temperature_c':(20.,0.,60.),'transfer_s':(10.,0.,3600.),**PLACEMENT},
}
DISTRIBUTION_FIELDS={'distribution_variation':(0.,0.,1.),'distribution_scale_mm':(2.,.05,1000.),'distribution_seed':(0,0,2147483647)}
for _kind in ('contaminate','silver_bath','developer_inhibitor','backing_condition','backing_wash','emulsion_wear'):
    EVENT_FIELDS[_kind].update(DISTRIBUTION_FIELDS)
    EVENT_FIELDS[_kind].update(BOUNDARY_FIELDS)

def _number(value,low,high,name):
    if isinstance(value,bool) or not isinstance(value,(int,float)) or not math.isfinite(value) or not low<=value<=high:
        raise ValueError(f'{name} must be finite and between {low} and {high}')
    return float(value)

def validate_process_response(raw):
    value=deepcopy(RESPONSE_DEFAULTS)
    if isinstance(raw,dict) and raw.get('model') in (RESPONSE_MODEL_V2,RESPONSE_MODEL_V3,RESPONSE_MODEL_V4):value.update(RESPONSE_V2_DEFAULTS)
    if isinstance(raw,dict) and raw.get('model') in (RESPONSE_MODEL_V3,RESPONSE_MODEL_V4):value.update(RESPONSE_V3_DEFAULTS)
    if isinstance(raw,dict) and raw.get('model')==RESPONSE_MODEL_V4:value.update(RESPONSE_V4_DEFAULTS)
    if raw is not None:
        if not isinstance(raw,dict) or set(raw)-set(value):raise ValueError('Unknown process material response fields')
        value.update(raw)
    if value['model'] not in (RESPONSE_MODEL,RESPONSE_MODEL_V2,RESPONSE_MODEL_V3,RESPONSE_MODEL_V4):raise ValueError('Unsupported process response model')
    for name,(low,high) in RESPONSE_RANGES.items():value[name]=_number(value[name],low,high,name)
    if value['model'] in (RESPONSE_MODEL_V2,RESPONSE_MODEL_V3,RESPONSE_MODEL_V4):
        for name,(low,high) in RESPONSE_V2_RANGES.items():value[name]=_number(value[name],low,high,name)
    if value['model'] in (RESPONSE_MODEL_V3,RESPONSE_MODEL_V4):
        for name,(low,high) in RESPONSE_V3_RANGES.items():value[name]=_number(value[name],low,high,name)
    rgb=value['residue_density_rgb_per_mg_m2']
    if not isinstance(rgb,(tuple,list)) or len(rgb)!=3:raise ValueError('Residue response needs three RGB coefficients')
    value['residue_density_rgb_per_mg_m2']=tuple(_number(v,0.,10.,'residue response') for v in rgb)
    if value['model']==RESPONSE_MODEL_V4:
        for name,(low,high) in RESPONSE_V4_RANGES.items():value[name]=_number(value[name],low,high,name)
        rgb=value['surface_silver_scattering_rgb']
        if not isinstance(rgb,(tuple,list)) or len(rgb)!=3:raise ValueError('Surface scattering needs three RGB coefficients')
        value['surface_silver_scattering_rgb']=tuple(_number(v,0.,10.,'surface scattering') for v in rgb)
    return value

def validate_process_events(raw):
    if raw is None:return None
    if not isinstance(raw,dict) or set(raw)!={'model','events'} or raw['model'] not in (EVENT_MODEL,EVENT_MODEL_V2,EVENT_MODEL_V3,EVENT_MODEL_V4,EVENT_MODEL_V5,EVENT_MODEL_V6,EVENT_MODEL_V7,EVENT_MODEL_V8,EVENT_MODEL_V9,EVENT_MODEL_V10,EVENT_MODEL_V11,EVENT_MODEL_V12,EVENT_MODEL_V13,EVENT_MODEL_V14,EVENT_MODEL_V15):raise ValueError('Unsupported process events model')
    rows=raw['events']
    if not isinstance(rows,(list,tuple)) or len(rows)>64:raise ValueError('Process events require at most 64 operations')
    result=[];surface_started=False;contacts=set()
    for row in rows:
        if not isinstance(row,dict) or row.get('kind') not in EVENT_FIELDS:raise ValueError('Unknown process operation')
        kind=row['kind'];fields=EVENT_FIELDS[kind]
        if raw['model'] not in (EVENT_MODEL_V14,EVENT_MODEL_V15):
            fields={key:val for key,val in fields.items() if key not in MENISCUS_FIELDS and key not in BOUNDARY_FIELDS}
        if raw['model']!=EVENT_MODEL_V15 and kind=='rotary_contact':
            fields={key:val for key,val in fields.items() if key not in EDGE_WETTING_FIELDS}
        choices=WETTING_CHOICES if kind=='rotary_contact' and raw['model']==EVENT_MODEL_V15 else {}
        if kind=='rotary_contact':
            if raw['model'] not in (EVENT_MODEL_V12,EVENT_MODEL_V13,EVENT_MODEL_V14,EVENT_MODEL_V15):raise ValueError('Rotary contact requires process events v12 or later')
            if raw['model']==EVENT_MODEL_V12:fields={key:val for key,val in fields.items() if key not in ROTARY_INSTABILITY_FIELDS}
        if kind=='dye_bath' and raw['model'] not in (EVENT_MODEL_V7,EVENT_MODEL_V8,EVENT_MODEL_V9,EVENT_MODEL_V10,EVENT_MODEL_V11,EVENT_MODEL_V12,EVENT_MODEL_V13,EVENT_MODEL_V14,EVENT_MODEL_V15):fields={key:val for key,val in fields.items() if key not in PLACEMENT}
        if raw['model'] not in (EVENT_MODEL_V5,EVENT_MODEL_V6,EVENT_MODEL_V7,EVENT_MODEL_V8,EVENT_MODEL_V9,EVENT_MODEL_V10,EVENT_MODEL_V11,EVENT_MODEL_V12,EVENT_MODEL_V13,EVENT_MODEL_V14,EVENT_MODEL_V15):fields={key:val for key,val in fields.items() if key not in DISTRIBUTION_FIELDS}
        if kind=='developer_inhibitor' and raw['model'] not in (EVENT_MODEL_V3,EVENT_MODEL_V4,EVENT_MODEL_V5,EVENT_MODEL_V6,EVENT_MODEL_V7,EVENT_MODEL_V8,EVENT_MODEL_V9,EVENT_MODEL_V10,EVENT_MODEL_V11,EVENT_MODEL_V12,EVENT_MODEL_V13,EVENT_MODEL_V14,EVENT_MODEL_V15):raise ValueError('Developer inhibitor requires process events v3 or later')
        if kind=='fixer_contact' and raw['model'] not in (EVENT_MODEL_V4,EVENT_MODEL_V5,EVENT_MODEL_V6,EVENT_MODEL_V7,EVENT_MODEL_V8,EVENT_MODEL_V9,EVENT_MODEL_V10,EVENT_MODEL_V11,EVENT_MODEL_V12,EVENT_MODEL_V13,EVENT_MODEL_V14,EVENT_MODEL_V15):raise ValueError('Fixer contact requires process events v4 or later')
        if kind=='dye_bath' and raw['model'] not in (EVENT_MODEL_V6,EVENT_MODEL_V7,EVENT_MODEL_V8,EVENT_MODEL_V9,EVENT_MODEL_V10,EVENT_MODEL_V11,EVENT_MODEL_V12,EVENT_MODEL_V13,EVENT_MODEL_V14,EVENT_MODEL_V15):raise ValueError('Dye bath requires process events v6 or later')
        if kind=='emulsion_shear' and raw['model'] not in (EVENT_MODEL_V11,EVENT_MODEL_V12,EVENT_MODEL_V13,EVENT_MODEL_V14,EVENT_MODEL_V15):raise ValueError('Emulsion shear requires process events v11 or later')
        if kind=='emulsion_wear':
            if raw['model'] not in (EVENT_MODEL_V9,EVENT_MODEL_V10,EVENT_MODEL_V11,EVENT_MODEL_V12,EVENT_MODEL_V13,EVENT_MODEL_V14,EVENT_MODEL_V15):raise ValueError('Emulsion wear requires process events v9 or later')
            if raw['model']==EVENT_MODEL_V9 and ('before_bath' in row or row.get('after_bath',0)!=0):raise ValueError('Emulsion wear is only supported after all chemical baths')
        backing=kind in ('backing_condition','backing_wash')
        if backing and raw['model'] not in (EVENT_MODEL_V8,EVENT_MODEL_V9,EVENT_MODEL_V10,EVENT_MODEL_V11,EVENT_MODEL_V12,EVENT_MODEL_V13,EVENT_MODEL_V14,EVENT_MODEL_V15):raise ValueError('Carbon backing operations require process events v8')
        if (backing or kind in ('emulsion_wear','emulsion_shear')) and 'before_bath' in row and 'after_bath' in row:raise ValueError('Choose before_bath or after_bath, not both')
        before=(backing and 'after_bath' not in row) or (kind in ('emulsion_wear','emulsion_shear') and 'before_bath' in row)
        scheduled=raw['model']!=EVENT_MODEL and kind not in (*CONTACT_KINDS,'developer_inhibitor','fixer_contact') and (kind!='dye_bath' or raw['model'] in (EVENT_MODEL_V7,EVENT_MODEL_V8,EVENT_MODEL_V9,EVENT_MODEL_V10,EVENT_MODEL_V11,EVENT_MODEL_V12,EVENT_MODEL_V13,EVENT_MODEL_V14,EVENT_MODEL_V15))
        if set(row)-{'kind',*fields,*choices}-({'before_bath'} if before else {'after_bath'} if scheduled else set()):raise ValueError('Unknown process operation parameters')
        clean={'kind':kind}
        for key,options in choices.items():
            value=row.get(key,options[0])
            if value not in options:raise ValueError(f'Unsupported {key}')
            clean[key]=value
        if before:
            index=_number(row.get('before_bath',1),1,32,'before_bath')
            if index!=int(index):raise ValueError('before_bath must be an integer')
            clean['before_bath']=int(index)
        elif scheduled:
            after=_number(row.get('after_bath',0),0,32,'after_bath')
            if after!=int(after):raise ValueError('after_bath must be an integer; 0 means after the whole program')
            clean['after_bath']=int(after)
        for key,(default,low,high) in fields.items():clean[key]=_number(row.get(key,default),low,high,key)
        if 'distribution_seed' in clean:
            if clean['distribution_seed']!=int(clean['distribution_seed']):raise ValueError('Distribution seed must be an integer')
            clean['distribution_seed']=int(clean['distribution_seed'])
        if 'rotation_seed' in clean:
            if clean['rotation_seed']!=int(clean['rotation_seed']):raise ValueError('Rotation seed must be an integer')
            clean['rotation_seed']=int(clean['rotation_seed'])
        if kind in ('developer_inhibitor','fixer_contact'):
            if clean['before_bath']!=int(clean['before_bath']):raise ValueError('before_bath must be an integer')
            clean['before_bath']=int(clean['before_bath'])
        if kind in CONTACT_KINDS:
            if surface_started:raise ValueError('Bath contact must precede post-process handling')
            if clean['bath']!=int(clean['bath']) or clean['bath'] in contacts:raise ValueError('One contact declaration per integer bath index')
            clean['bath']=int(clean['bath']);contacts.add(clean['bath'])
        else:surface_started=True
        result.append(clean)
    return {'model':raw['model'],'events':result}
