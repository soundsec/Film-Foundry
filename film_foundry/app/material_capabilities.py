"""Headless capability queries over the same operations used by workspaces/routes."""
from dataclasses import asdict
from film_foundry.core.material_structure import from_record
import inspect
import json
from film_foundry.app import material_families as api
from film_foundry.app.material_operations import parameter_spec,operation_functions
from film_foundry.contracts.material_capabilities import MODEL,FAMILIES,operation_semantics
from film_foundry.contracts.material_recipe import KEY,STATUS_KEY


def _products(name,material,version):
    if name=='peel_apart_transfer_v1':
        result=[{'role':'receiver','observation':'reflection','usable_negative':False,'after':'peel'},
                {'role':'donor_residue','observation':'reflection','usable_negative':False,'after':'peel'}]
        if version>=2 and material.donor_mode=='silver_negative':
            result.append({'role':'silver_negative','observation':'transmission','usable_negative':True,
                           'after':'clear_negative','replaces':'donor_residue'})
        return result
    role,observation,negative={
        'integral_instant_v1':('integral_print','reflection',False),
        'daguerreotype_surface_v1':('silver_plate','directional_reflection',False),
        'photographic_paper_v1':('paper_print','reflection',False),
        'wet_collodion_v1':('wet_negative' if getattr(material,'support',None)=='glass_negative' else 'wet_positive',
                            'transmission' if getattr(material,'support',None)=='glass_negative' else 'reflection',
                            getattr(material,'support',None)=='glass_negative'),
        'cyanotype_v1':('cyanotype_print','reflection',False),
        'platinum_palladium_v1':('noble_print','reflection',False),
    }[name]
    return [{'role':role,'observation':observation,'usable_negative':negative,'after':'final_stage'}]


def describe_family(name,*,state=None,material=None,group=None,exposure_available=False):
    """Return detached JSON data; does not form, observe or scan any image.

    `available` is stage/input availability, not a promise that arbitrary
    parameters pass the family's own validation. Old schemas are not upgraded.
    """
    module,_,material_type,_=api.binding(name)
    if state is not None and api.state_family(state)!=name:raise ValueError('State belongs to another material family')
    if state is not None and material is not None:raise ValueError('A state already owns its frozen material')
    provenance={}
    if group is not None:
        from film_foundry.core.material_family import ProductGroup
        if state is not None or material is not None:raise ValueError('Products already own their material definition')
        if (not isinstance(group,ProductGroup) or not group.bodies or
            any(body.family!=name for body in group.bodies) or len({b.schema_version for b in group.bodies})!=1):
            raise ValueError('Products must belong to one family and version')
        provenance=json.loads(group.provenance_json)
        material=provenance['material']
    material=state.material if state is not None else from_record(material_type,material) if group is not None else material_type(**(material or {}))
    declared=FAMILIES[name]
    version=state.schema_version if state is not None else group.bodies[0].schema_version if group is not None else declared['current_version']
    reagent_version={'peel_apart_transfer_v1':5,'integral_instant_v1':4}.get(name)
    available=module.available_operations(state) if group is None and (state is not None or exposure_available) else ()
    available=tuple(op for op in available if op!='expose' or exposure_available)
    names=dict.fromkeys((*getattr(module,'EXPOSURE_STEPS',('expose',)),*operation_functions(module)))
    operations=[]
    for operation in names:
        parameters={}
        for key,spec in parameter_spec(module,operation).items():
            item={'required':spec.default is inspect.Parameter.empty}
            if not item['required']:item['default']=spec.default
            if key in ('seconds','spread_seconds'):item['unit']='s'
            elif key in ('temperature_c','solar_warming'):item['unit']='degC'
            elif key=='angle_degrees':item['unit']='degrees'
            parameters[key]=item
        operations.append({'operation':operation,'available':operation in available,
            'parameters':parameters,**operation_semantics(name,operation)})
        if name in ('peel_apart_transfer_v1','integral_instant_v1'):
            if operation in ('environment','contaminate','clean'):operations[-1]['minimum_version']=3
            if operation=='advance' and version>=3:operations[-1]['time_model']='incremental_competing_rates'
            if operation=='spread':parameters['reagent_load']['minimum_version']=reagent_version
            if operation=='finish':operations[-1]['minimum_version']=4
            if version>=reagent_version and operation in ('advance','stabilize'):
                operations[-1]['targets']+=['developer_inventory','alkali_inventory','neutralizer_inventory']
                operations[-1]['time_model']='finite_inventory_incremental' if operation=='advance' else 'remaining_acid_release_may_not_stabilize'
        if name=='daguerreotype_surface_v1' and version>=3 and operation in ('develop','fix','gild'):
            operations[-1]['time_model']='incremental_seconds'
            if operation=='fix':operations[-1]['targets']=['surface_halide','removed_surface_halide']
        if name=='daguerreotype_surface_v1' and operation in ('fix','gild'):
            for key in ('seconds','activity','temperature_c','coverage'):parameters[key]['minimum_version']=3
        if name=='daguerreotype_surface_v1':
            if operation in ('load_gilding','rinse_gilding','finish'):operations[-1]['minimum_version']=4
            if version>=4 and operation=='gild':
                operations[-1].update(targets=['gold_inventory','deposited_gold'],time_model='finite_inventory_incremental')
            if version>=4 and operation in ('surface','weather'):
                operations[-1].update(targets=['metal_surface','image_particles','removed_particles','gold','removed_gold'],domain='cumulative_surface_attrition')
        if name=='wet_collodion_v1' and version>=3 and operation in ('sensitize','develop','fix','wash'):
            operations[-1]['targets'].append('external_silver_inventory')
        if name=='wet_collodion_v1':
            if operation in ('load_enhancer','intensify'):operations[-1]['minimum_version']=4
            added={'sensitize':('coverage','temperature_c'),'develop':('coverage',),
                   'fix':('coverage','temperature_c'),'wash':('coverage','temperature_c'),'dry':('humidity','temperature_c')}
            for key in added.get(operation,()):parameters[key]['minimum_version']=4
            if version>=4 and operation in ('sensitize','develop','fix','wash','intensify','wait','dry'):
                operations[-1]['targets']+=['wetness','collodion_access']
            if version>=4 and operation in ('fix','wash'):operations[-1]['targets'].append('enhancement_silver_inventory')
        if name in ('photographic_paper_v1','cyanotype_v1','platinum_palladium_v1'):
            support_version=2 if name=='photographic_paper_v1' else 3
            if operation=='dry':operations[-1]['minimum_version']=support_version
            if operation in ('wash','clear'):
                for key in ('region','position_x','position_y','radius','coverage','temperature_c'):parameters[key]['minimum_version']=support_version
            if version>=support_version:
                if operation in ('develop','fix','bleach_fix','wash','clear','dry','alkali_contact') and 'paper_water' not in operations[-1]['targets']:operations[-1]['targets'].append('paper_water')
                if name=='cyanotype_v1' and operation=='dry':operations[-1]['targets']+=['reduced_pigment','blue_pigment']
    process=json.loads(state.process_json) if state is not None else provenance.get('process',{})
    recipe={'complete':True} if KEY in process else process.get(STATUS_KEY,{'complete':False,'reason':'no_record'})
    result={'model':MODEL,'family':name,'material_version':version,'current_version':declared['current_version'],
        'supported_versions':list(range(1,declared['current_version']+1)),
        'stage':state.stage if state is not None else ('finished_products' if group is not None else None),
        'material':asdict(material),'operations':operations,'final_stages':list(module.FINAL_STAGES),
        'exposure_steps':list(getattr(module,'EXPOSURE_STEPS',('expose',))),
        'products':_products(name,material,version),'recipe':recipe,
        'bath_solution_supported':False,
        'limitations':[*(declared.get(f'v{version}_limits',declared.get('v2_limits',declared['limits']) if version==1 else declared['limits'])),
                       *(declared['legacy_limits'] if version==1 and declared['current_version']>1 else [])]}
    if name in ('peel_apart_transfer_v1','integral_instant_v1') and version>=3:
        from film_foundry.app.transfer_intent import WARM_SHIFT_COUPLINGS
        result['effect_intents']=[{'id':'warm_shift','operation':'environment',
            'target':'normalized_thermal_imbalance','couplings':list(WARM_SHIFT_COUPLINGS),'display_rgb_linear':False}]
    if reagent_version is not None:
        result['reagent_inventory']={'active':version>=reagent_version,'minimum_version':reagent_version,
            'units':'normalized_equivalents_per_layer','developer_consumed_by':['transfer_conversion','donor_development'],
            'alkali_consumed_by':['acid_neutralization'],'spatial_transport':False}
        if version>=reagent_version:
            from film_foundry.app.transfer_intent import REAGENT_SHORTAGE_COUPLINGS
            result['effect_intents'].append({'id':'reagent_shortage','operation':'spread',
                'target':'linear_supplied_load_shortfall','couplings':list(REAGENT_SHORTAGE_COUPLINGS),'display_rgb_linear':False})
        elif name=='integral_instant_v1':result['final_stages']=['stable']
    if name=='peel_apart_transfer_v1':
        for operation in operations:
            op=operation['operation']
            if op=='peel':
                for spec in operation['parameters'].values():spec['minimum_version']=4
                if version>=4:operation['targets']=['receiver_layer','donor_layer','layer_ownership']
            elif op in ('clean','handling'):
                for key in ('target','abrasion','marks','angle_degrees'):
                    if key in operation['parameters'] and (op=='clean' or key=='target'):
                        operation['parameters'][key].update(minimum_version=4,requires_stage=['separated','cleared'])
                if version>=4 and (state is None or state.stage in ('separated','cleared')):
                    operation['targets']=['selected_sheet_residue','selected_sheet_layers'] if op=='clean' else ['selected_sheet_residue']
        result['separation']={'model':'fractional_layer_ownership_v1' if version>=4 else None,
            'targets':['receiver','donor'] if version>=4 else [],'cumulative_negative_cleanup':version>=4,
            'removed_image_accounted':version>=4,'geometry_displacement':False}
    result['structure']={'model':material.structure_model,'material_long_edge_mm':material.material_long_edge_mm,
        'texture_scale_mm':material.texture_scale_mm,'process_scale_mm':material.process_scale_mm,
        'millimetres_active':version>=2 and material.structure_model=='millimetres_v1',
        'frozen_material_fields':True,'full_geometry_deformation':False}
    if name=='wet_collodion_v1' and version>=4:
        from film_foundry.app.wet_plate_intent import COUPLINGS
        result['effect_intents']=[{'id':'enhancement_silver_supply','operation':'load_enhancer',
            'target':'linear_supplied_silver_per_coating','couplings':list(COUPLINGS),'display_rgb_linear':False}]
    if name=='daguerreotype_surface_v1':
        if version<4:result['final_stages']=['fixed','gilded']
        else:
            from film_foundry.app.plate_gold_intent import COUPLINGS
            result['effect_intents']=[{'id':'gold_supply','operation':'load_gilding',
                'target':'linear_supplied_gold_fraction','couplings':list(COUPLINGS),'display_rgb_linear':False}]
    # Detach constants and ensure a usable contract for non-Python frontends.
    return json.loads(json.dumps(result,allow_nan=False))
