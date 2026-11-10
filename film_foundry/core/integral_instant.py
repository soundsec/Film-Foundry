"""Integral instant: sealed layers, finite reagent and delayed neutralization.

Shares reduced transfer math with peel-apart; never exposes a separable donor.
Timing and optical clearing are creative parameters, not a manufacturer's fit.
"""
from film_foundry.core.transfer_inventory import ReagentMaterial
from film_foundry.core import transfer_inventory as inventory
from dataclasses import dataclass, asdict, replace, field
import json
import math
import numpy as np
from film_foundry.core import peel_apart as transfer
from film_foundry.core.material_family import MediumBody, ProductGroup, ViewingConditions, frozen_fields, frame_observation
from film_foundry.core import material_defects as defects
from film_foundry.core.material_metadata import ProcessAnnotations
from film_foundry.core.material_family import cached_body_validation

FAMILY="integral_instant_v1"


@dataclass(frozen=True)
class InstantMaterial(ReagentMaterial):
    neutralizer_capacity: float=field(default=1.25,kw_only=True)
    transfer: str="dye"
    half_exposure: float=.18
    reaction_seconds: float=60.
    maximum_density: float=2.
    paper_reflectance: float=.92
    stabilization_seconds: float=300.
    batch_variation: float=0.
    batch_seed: int=0
    border_style: str="pod"
    light_sensitivity: float=1.
    temperature_sensitivity: float=1.
    inhibitor_sensitivity: float=1.
    initial_contamination: float=0.

    def transfer_material(self):
        return transfer.PeelMaterial(**{k:v for k,v in asdict(self).items() if k!="stabilization_seconds"})

    def __post_init__(self):
        self.transfer_material()
        if not math.isfinite(self.stabilization_seconds) or self.stabilization_seconds<=0:
            raise ValueError("Invalid stabilization time")


@dataclass(frozen=True)
class InstantState(ProcessAnnotations):
    group_id: str
    seed: int
    material: InstantMaterial
    stage: str
    elapsed_seconds: float
    fields: object
    process_json: str="{}"
    schema_version: int=4

    def __post_init__(self):
        if self.schema_version not in (1,2,3,4) or self.stage not in ("exposed","ejected","reacting","stable","finished"):
            raise ValueError("Unsupported integral state")
        if self.schema_version<4 and self.elapsed_seconds>self.material.stabilization_seconds:raise ValueError("Integral time exceeds stabilization")
        fields=frozen_fields(self.fields)
        if set(fields)!=transfer.state_fields(_transfer_version(self.schema_version))|{"opacifier","stabilization"}:raise ValueError("Missing integral layers")
        for key in ("opacifier","stabilization"):
            if fields[key].shape!=fields["coverage"].shape or (fields[key]<0).any() or (fields[key]>1).any():
                raise ValueError("Invalid integral optical/stabilizing layer")
        transfer.PeelState(self.group_id,self.seed,self.material.transfer_material(),
            "exposed" if self.stage in ("exposed","ejected") else "contact",self.elapsed_seconds,
            {k:fields[k] for k in transfer.state_fields(_transfer_version(self.schema_version))},self.process_json,schema_version=_transfer_version(self.schema_version))
        if self.schema_version>=4:
            progress,opacifier=inventory.optical_progress(fields,self.material)
            if self.stage in ('exposed','ejected'):progress=np.zeros_like(progress);opacifier=np.ones_like(opacifier)
            if not np.allclose(fields['stabilization'],progress,atol=1e-6) or not np.allclose(fields['opacifier'],opacifier,atol=1e-6):raise ValueError('Neutralization optical state mismatch')
            if self.stage=='stable' and np.any(fields['alkali_remaining']>1e-6):raise ValueError('Stable sheet retains active alkali')
        else:
            expected=self.elapsed_seconds/self.material.stabilization_seconds
            if not np.allclose(fields['stabilization'],expected,atol=1e-6):raise ValueError('Stabilization time mismatch')
            if (self.stage=='stable')!=(expected==1) or self.stage=='finished':raise ValueError('Invalid integral completion state')
        object.__setattr__(self,"fields",fields)


_TRANSFER_FIELDS=("exposure_response","immobile","remaining","received","coverage","contact","arrival_seconds")


def _transfer_version(version):return 5 if version==4 else version


def _transfer_state(state,stage):
    return transfer.PeelState(state.group_id,state.seed,state.material.transfer_material(),stage,state.elapsed_seconds,
        {k:state.fields[k] for k in transfer.state_fields(_transfer_version(state.schema_version))},state.process_json,schema_version=_transfer_version(state.schema_version))


def expose(image,material=InstantMaterial(),*,group_id,seed=0,schema_version=4):
    base=transfer.expose(image,material.transfer_material(),group_id=group_id,seed=seed,schema_version=_transfer_version(schema_version))
    return InstantState(group_id,seed,material,"exposed",0.,dict(base.fields)|{
        "opacifier":np.ones_like(base.fields["coverage"]),"stabilization":np.zeros_like(base.fields["coverage"])},base.process_json,schema_version=schema_version)


def eject(state):
    if state.stage!="exposed":raise ValueError("Only an exposed sheet can be ejected")
    return replace(state,stage="ejected")


def light_leak(state,**parameters):
    if state.stage not in ("exposed","ejected"):
        raise ValueError("Pack opening is supported before reagent spreading only")
    base=_transfer_state(state,"exposed")
    base=transfer.light_leak(base,**parameters)
    return replace(state,fields=dict(state.fields)|dict(base.fields),process_json=base.process_json)


def spread(state,*,light_protection=1.,ambient_light=1.,solar_warming=0.,**parameters):
    if state.stage!="ejected":raise ValueError("Eject the sheet before reagent spreading")
    light_protection=defects.fraction(light_protection,"Development light protection")
    if not math.isfinite(ambient_light) or not 0<=ambient_light<=10:raise ValueError("Ambient light must be within [0,10]")
    if not math.isfinite(solar_warming) or not 0<=solar_warming<=30:raise ValueError("Additional warming must be within [0,30] degrees")
    if state.schema_version==1 and solar_warming:raise ValueError("Additional warming requires a newly exposed v2 sheet")
    if state.schema_version==1 and light_protection<1 and ambient_light>0:raise ValueError("Shielding response requires a newly exposed v2 sheet")
    base=_transfer_state(state,"exposed")
    initial_temperature=parameters.get("temperature_c",20.)
    if not math.isfinite(initial_temperature) or not -10<=initial_temperature<=50:raise ValueError("Temperature outside model range")
    parameters["temperature_c"]=min(50.,initial_temperature+solar_warming)
    base=transfer.spread(base,**parameters)
    settings=json.loads(base.process_json)
    settings.update(light_protection=light_protection,ambient_light=ambient_light,solar_warming=solar_warming,initial_temperature_c=initial_temperature)
    snapshot=json.dumps(settings,sort_keys=True,allow_nan=False)
    if state.schema_version==2 and light_protection<1 and ambient_light>0:
        y,x=defects.coordinates(base.fields["coverage"].shape)
        direction=settings["direction"]
        along=y if direction in ("down","up") else x
        if direction in ("up","left"):along=1-along
        early=.65*np.exp(-along/.14)+.65*(1-base.fields["coverage"])
        mixing=max(settings["spread_seconds"],.1)/2
        cold=2**((20-settings["temperature_c"])/20)
        dose=(1-light_protection)*min(ambient_light,1)*early*mixing*cold*2
        # Stronger-than-reference illumination also reaches the broader sheet.
        # It washes out the image; warmth comes from the separate chemical temperature.
        broad_dose=(1-light_protection)*max(ambient_light-1,0)*.12*mixing
        channels=np.array([.12,.25,1.],np.float32) if base.fields["remaining"].shape[-1]==3 else np.ones(1,np.float32)
        fields=dict(base.fields)
        affected=fields["remaining"]*(-np.expm1(-(dose[...,None]*channels+broad_dose)))
        fields["immobile"]=np.minimum(fields["immobile"]+affected,1)
        fields["exposure_response"]=fields["immobile"];fields["remaining"]=1-fields["immobile"]
        snapshot=defects.record_event(snapshot,"early_light_exposure",light_protection=light_protection,ambient_light=ambient_light)
        base=replace(base,fields=fields,process_json=snapshot)
    if state.schema_version>=4:
        progress,opacifier=inventory.optical_progress(base.fields,state.material)
        return replace(state,stage="reacting",fields=dict(base.fields)|{"opacifier":opacifier,"stabilization":progress},process_json=snapshot)
    return replace(state,stage="reacting",fields=dict(base.fields)|{
        "opacifier":state.fields["opacifier"],"stabilization":state.fields["stabilization"]},process_json=snapshot)


def advance(state,*,seconds):
    if state.stage!="reacting":raise ValueError("Only reacting integral sheets can advance")
    if not math.isfinite(seconds) or seconds<0:raise ValueError("Invalid time increment")
    if state.schema_version>=4:
        base=transfer.advance(_transfer_state(state,"contact"),seconds=seconds)
        progress,opacifier=inventory.optical_progress(base.fields,state.material)
        stable=not np.any(base.fields["alkali_remaining"]>1e-6)
        return replace(state,stage="stable" if stable else "reacting",elapsed_seconds=base.elapsed_seconds,fields=dict(base.fields)|{"opacifier":opacifier,"stabilization":progress},process_json=base.process_json)
    total=min(state.elapsed_seconds+seconds,state.material.stabilization_seconds)
    # Legacy versions retain their declared-time cutoff.
    base=_transfer_state(state,"contact")
    base=transfer.advance(base,seconds=total-state.elapsed_seconds)
    progress=total/state.material.stabilization_seconds
    fields=dict(base.fields)|{"opacifier":np.full_like(state.fields["coverage"],(1-progress)**2),
                              "stabilization":np.full_like(state.fields["coverage"],progress)}
    return replace(state,stage="stable" if progress==1 else "reacting",elapsed_seconds=total,fields=fields,process_json=base.process_json)


def stabilize(state):
    if state.schema_version>=4:
        if state.stage!='reacting':raise ValueError('Only reacting sheets can stabilize')
        temperature=json.loads(state.process_json)['temperature_c']
        rate=2**((temperature-20)*state.material.temperature_sensitivity/10)*state.fields['contact']
        needed=np.maximum(state.material.neutralization_delay_seconds+state.material.neutralization_seconds-state.fields['neutralization_clock'],0)
        duration=np.divide(needed.astype(np.float64),rate,out=np.zeros(needed.shape,np.float64),where=rate>0)
        duration+=np.maximum(state.fields['arrival_seconds']-state.elapsed_seconds,0)
        return advance(state,seconds=min(float(duration.max(initial=0.)),31536000.))
    return advance(state,seconds=state.material.stabilization_seconds-state.elapsed_seconds)


def finish(state):
    if state.schema_version<4 or state.stage!='reacting':raise ValueError('Only a reacting finite-reagent sheet can be captured early')
    return replace(state,stage='finished',process_json=defects.record_event(state.process_json,'finish',reason='explicit_snapshot_with_residual_chemistry'))


def _body(state):
    h,w=state.fields["coverage"].shape
    initial = dict(zip(inventory.INITIAL_FIELDS, inventory.capacities(state.fields,state.material))) if state.schema_version>=4 else {}
    # All layers remain in the one sealed medium, including the retained donor material.
    return MediumBody(FAMILY,state.schema_version,state.group_id,state.group_id+":integral","integral_print",f"integral_reflection_v{state.schema_version}",
        dict(state.fields)|initial|{"paper":state.fields["paper_texture"] if state.schema_version>=2 else np.full((h,w),state.material.paper_reflectance,np.float32),
                            "density_per_unit":np.full((h,w),state.material.maximum_density,np.float32)},
                      presentation_json=json.dumps({"frame":state.material.border_style,**({'formation_stage':state.stage} if state.schema_version>=4 else {})}) if state.schema_version>=2 else "{}")


def products(state):
    if state.stage not in ("stable","finished"):
        raise ValueError("Advance neutralization, or explicitly finish to keep the current reacting print" if state.schema_version>=4 else "Stabilize before committing the final integral print")
    body=_body(state)
    extra = dict(completion=state.stage,reagent_budget=inventory.budget(state.fields,state.material)) if state.schema_version>=4 else {}
    return ProductGroup(state.group_id,(body,),json.dumps(dict(family=FAMILY,material=asdict(state.material),**extra,
        process=json.loads(state.process_json),seed=state.seed,process_seconds=state.elapsed_seconds,
        group_id=state.group_id,members=[body.body_id]),sort_keys=True))


@cached_body_validation
def validate_body(body):
    if body.family!=FAMILY or body.schema_version not in (1,2,3,4) or body.role!="integral_print" or body.observation_model!=f"integral_reflection_v{body.schema_version}" or body.usable_negative:
        raise ValueError("Unsupported integral print")
    initial=set(inventory.INITIAL_FIELDS) if body.schema_version>=4 else set()
    if set(body.fields)!=transfer.state_fields(_transfer_version(body.schema_version))|initial|{"opacifier","stabilization","paper","density_per_unit"}:
        raise ValueError("Incomplete sealed layers")
    shape=body.fields["received"].shape
    if body.schema_version>=4:
        inventory.validate(body.fields,shape)
        initial_alkali=body.fields['alkali_initial']
        residual=np.divide(body.fields['alkali_remaining'],initial_alkali,out=np.zeros_like(initial_alkali),where=initial_alkali>0)
        stage=json.loads(body.presentation_json).get('formation_stage')
        if stage not in ('exposed','ejected','reacting','stable','finished'):raise ValueError('Missing sealed formation stage')
        if stage in ('exposed','ejected'):
            if np.any(initial_alkali):raise ValueError('Unprocessed sheet contains reagent')
            residual=np.ones_like(residual)
        if stage=='stable' and np.any(body.fields['alkali_remaining']>1e-6):raise ValueError('Stable print retains active alkali')
        if not np.allclose(body.fields['stabilization'],1-residual,atol=1e-6) or not np.allclose(body.fields['opacifier'],residual**2,atol=1e-6):
            raise ValueError('Invalid neutralized optical layer')
    if body.schema_version>=2:
        transfer.validate_chemistry(body.fields,shape)
        if any((body.fields[k]<0).any() for k in transfer.CHEMISTRY_FIELDS):raise ValueError("Negative chemistry field")
        if (body.fields["developed_donor"]>body.fields["immobile"]+1e-6).any():raise ValueError("Invalid sealed donor silver")
    for key in _TRANSFER_FIELDS:
        value=body.fields[key]
        expected=shape if key in ("exposure_response","immobile","remaining","received") else shape[:2]
        if value.shape!=expected or (value<0).any() or (key!="arrival_seconds" and (value>1).any()):
            raise ValueError("Invalid sealed transfer field")
    if not np.array_equal(body.fields["immobile"],body.fields["exposure_response"]):raise ValueError("Exposure pool mismatch")
    if body.schema_version>=3:transfer.segmented.validate(body.fields,shape)
    proxy=_receiver_proxy(body)
    transfer.validate_body(proxy)
    for key in ("opacifier","stabilization"):
        value=body.fields[key]
        if value.ndim!=2 or (value<0).any() or (value>1).any():raise ValueError("Invalid integral layer")
    if not np.allclose(body.fields["immobile"]+body.fields["remaining"]+body.fields["received"]+body.fields.get("transfer_loss",0),1,atol=2e-6,rtol=0):
        raise ValueError("Integral transfer pool mismatch")


def _receiver_proxy(body):
    extra={"stain_density":body.fields["stain_density"]} if body.schema_version>=2 else {}
    return MediumBody(transfer.FAMILY,_transfer_version(body.schema_version),body.group_id,body.body_id,"receiver",f"diffuse_paper_v{_transfer_version(body.schema_version)}",
        {"transferred":body.fields["received"],"paper":body.fields["paper"],"density_per_unit":body.fields["density_per_unit"],**extra})


def observe(body,view=ViewingConditions()):
    validate_body(body)
    proxy=_receiver_proxy(body)
    # Internal layers retain image geometry; frame the completed observation once.
    image=transfer.observe(proxy,replace(view,include_border=False))
    if body.schema_version>=2:
        # Paste-starved sealed regions expose the backing rather than bare receiver paper.
        uncoated=np.clip(1-body.fields["coverage"]/.12,0,1)[...,None]
        backing=np.array([.14,.085,.045],np.float32)*np.asarray(view.illuminant_rgb)*(2**view.exposure_ev)
        backing=backing*(.55+.9*body.fields["backing_texture"][...,None])
        image=image*(1-uncoated)+backing*uncoated
    opacity=body.fields["opacifier"][...,None]
    veil=np.asarray(view.illuminant_rgb,dtype=np.float32)*.12*(2**view.exposure_ev)
    return frame_observation(body,(image*(1-opacity)+veil*opacity).astype(np.float32),view)


def preview(state,view=ViewingConditions()):return observe(_body(state),view)


class IntegralInstantFamily:
    expose=staticmethod(expose)
    finish=staticmethod(products)
    observe=staticmethod(observe)
    @staticmethod
    def process(state,operation,**parameters):
        if operation not in OPERATION_FUNCTIONS:raise ValueError("Unsupported integral operation; sheet is sealed")
        return OPERATION_FUNCTIONS[operation](state,**parameters)


def handling(state,**parameters):
    if state.schema_version<2 or state.stage not in ('stable','finished'):raise ValueError('Handle the print after stabilization')
    fields=dict(state.fields)
    fields['stain_density']=fields['stain_density']+defects.handling_density(fields['coverage'].shape,state.seed,**parameters)
    return replace(state,fields=fields,process_json=defects.record_event(state.process_json,'handling',**parameters))


def environment(state,*,temperature_c=20.,light_protection=1.,ambient_light=1.,solar_warming=0.):
    if state.stage!='reacting':raise ValueError('Only reacting sheets accept new environment conditions')
    base=transfer.environment(_transfer_state(state,'contact'),temperature_c=temperature_c,
        light_protection=light_protection,ambient_light=ambient_light,solar_warming=solar_warming)
    return replace(state,process_json=base.process_json)


def contaminate(state,*,amount=0.,position_x=.5,position_y=.5,radius=.2):
    if state.stage!='ejected':raise ValueError('Internal contamination is accessible only before sealing/spreading')
    base=transfer.contaminate(_transfer_state(state,'exposed'),amount=amount,position_x=position_x,position_y=position_y,radius=radius)
    return replace(state,fields=dict(state.fields)|dict(base.fields),process_json=base.process_json)


def clean(state,*,strength=1.):
    if state.stage!='ejected':raise ValueError('Internal residue cannot be washed out of a sealed print')
    base=transfer.clean(_transfer_state(state,'exposed'),strength=strength)
    return replace(state,fields=dict(state.fields)|dict(base.fields),process_json=base.process_json)


OPERATION_FUNCTIONS={'finish':finish,'environment':environment,'contaminate':contaminate,'clean':clean,'eject':eject,'spread':spread,'advance':advance,'stabilize':stabilize,'light_leak':light_leak,'handling':handling}
FINAL_STAGES=('stable','finished')
EXPOSURE_STEPS=('expose',)


def available_operations(state):
    operations={None:('expose',),'exposed':('eject','light_leak'),'ejected':('spread','light_leak'),
                'reacting':('advance','stabilize'),'stable':('handling',),'finished':('handling',)}[state.stage if state else None]
    if state is not None and state.schema_version>=3:
        operations+=({'ejected':('contaminate','clean'),'reacting':('environment',)}.get(state.stage,()))
    if state is not None and state.schema_version>=4 and state.stage=='reacting':operations+=('finish',)
    return tuple(op for op in operations if op!='handling' or state.schema_version>=2)
