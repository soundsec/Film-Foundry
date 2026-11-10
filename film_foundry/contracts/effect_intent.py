"""Versioned desired process outcomes; never consumed by pixel operators."""
import math
MODEL='process_effect_intent_v1'
MODEL_V2='process_effect_intent_v2'
MODEL_V3='process_effect_intent_v3'
EFFECTS=('chemical_stain','silver_plating','reticulation_strength','uneven_development')
EFFECTS_V2=(*EFFECTS,'dye_damage_strength')
EFFECTS_V3=(*EFFECTS_V2,'silver_retention')

def effects_for_model(model):
    return {MODEL:EFFECTS, MODEL_V2:EFFECTS_V2, MODEL_V3:EFFECTS_V3}[model]

def validate_effect_intent(value):
    if value is None:return None
    if not isinstance(value,dict) or set(value)-{'model','enabled','amounts'} or value.get('model') not in (MODEL,MODEL_V2,MODEL_V3):
        raise ValueError('Unsupported process effect intent')
    if not isinstance(value.get('enabled',True),bool):raise ValueError('Effect enabled must be boolean')
    amounts=value.get('amounts',{})
    effects=effects_for_model(value['model'])
    if not isinstance(amounts,dict) or set(amounts)-set(effects):raise ValueError('Unknown process effect')
    clean={}
    for key in effects:
        number=amounts.get(key,0.)
        if isinstance(number,bool) or not isinstance(number,(int,float)) or not math.isfinite(number) or not 0<=number<=1:
            raise ValueError('Effect amount must be finite between 0 and 1')
        clean[key]=float(number)
    return {'model':value['model'],'enabled':value.get('enabled',True),'amounts':clean}
