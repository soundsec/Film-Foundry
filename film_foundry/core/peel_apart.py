"""Creative reduced peel-apart v1, not a measured commercial instant-film model.

Transfer is a finite-pool process, not an overlay on a finished photograph.
Time is explicit process time; evaluation needs no sleep or animation loop.
"""
from film_foundry.core.transfer_inventory import ReagentMaterial
from film_foundry.core import transfer_inventory as inventory
from dataclasses import dataclass, asdict, replace, field
import json
import math
import numpy as np
from film_foundry.core.material_family import MediumBody, ProductGroup, ViewingConditions, frozen_fields, frame_observation
from film_foundry.core import material_defects as defects
from film_foundry.core.material_metadata import ProcessAnnotations
from film_foundry.core.material_family import cached_body_validation
from film_foundry.core import transfer_environment as segmented, material_structure as ms, scaled_structure as scaled, peel_separation as separation, layer_ownership as layers

FAMILY = "peel_apart_transfer_v1"
BASE_FIELDS={"exposure_response","immobile","remaining","received","coverage","contact","arrival_seconds"}
CHEMISTRY_FIELDS={"reagent","escaped_reagent","stain_density","channel_activity","transfer_yield","paper_texture","backing_texture","developed_donor"}


def state_fields(version):return BASE_FIELDS | (CHEMISTRY_FIELDS if version>=2 else set()) | (segmented.FIELDS if version>=3 else set()) | (inventory.FIELDS if version>=5 else set())


@dataclass(frozen=True)
class PeelMaterial(ReagentMaterial):
    transfer: str = "dye"  # dye or silver_compound: three pools versus one pool
    half_exposure: float = .18
    reaction_seconds: float = 60.
    maximum_density: float = 2.
    paper_reflectance: float = .92
    batch_variation: float = 0.
    batch_seed: int = 0
    donor_mode: str = "residue"
    border_style: str = "equal"
    light_sensitivity: float = 1.
    temperature_sensitivity: float = 1.
    inhibitor_sensitivity: float = 1.
    initial_contamination: float = 0.
    interlayer_adhesion: float = field(default=.25,kw_only=True)
    receiver_cohesion: float = field(default=.75,kw_only=True)
    donor_cohesion: float = field(default=.8,kw_only=True)
    separation_variation: float = field(default=.4,kw_only=True)

    def __post_init__(self):
        super().__post_init__()
        if self.transfer not in ("dye", "silver_compound"):raise ValueError("Unknown transfer mechanism")
        if any(not math.isfinite(x) or x <= 0 for x in (self.half_exposure,self.reaction_seconds,self.maximum_density)):
            raise ValueError("Positive finite material parameters required")
        if not 0 < self.paper_reflectance <= 1:raise ValueError("Invalid paper reflectance")
        defects.fraction(self.batch_variation,"Batch variation")
        if not isinstance(self.batch_seed,int) or self.batch_seed<0:raise ValueError("Nonnegative batch identity required")
        if self.donor_mode not in ("residue","silver_negative"):raise ValueError("Unknown donor material")
        if self.donor_mode=="silver_negative" and self.transfer!="silver_compound":raise ValueError("The positive/negative material requires B&W silver transfer")
        if self.border_style not in ("none","equal","pod"):raise ValueError("Unknown sheet border")
        for key in ('light_sensitivity','temperature_sensitivity','inhibitor_sensitivity'):
            if not math.isfinite(getattr(self,key)) or not 0<=getattr(self,key)<=2:raise ValueError('Invalid material sensitivity')
        defects.fraction(self.initial_contamination,'Initial contamination')
        for key in separation.MATERIAL_KEYS:defects.fraction(getattr(self,key),key)


@dataclass(frozen=True)
class PeelState(ProcessAnnotations):
    group_id: str
    seed: int
    material: PeelMaterial
    stage: str
    elapsed_seconds: float
    fields: object
    process_json: str = "{}"
    schema_version: int = 5

    def __post_init__(self):
        if self.schema_version not in (1,2,3,4,5) or self.stage not in ("exposed","contact","separated","cleared"):
            raise ValueError("Unsupported peel state version/stage")
        if not self.group_id or not isinstance(self.seed,int) or self.seed<0:raise ValueError("Process identity and nonnegative seed required")
        if not math.isfinite(self.elapsed_seconds) or self.elapsed_seconds < 0:raise ValueError("Invalid process time")
        fields=frozen_fields(self.fields)
        required=state_fields(self.schema_version)
        if self.schema_version>=4 and self.stage in ("separated","cleared"):required|=separation.FIELDS
        if set(fields)!=required:raise ValueError("Incomplete peel state")
        shape=fields["received"].shape
        channels=3 if self.material.transfer=="dye" else 1
        if len(shape)!=3 or shape[2]!=channels:raise ValueError("Incorrect transfer channel count")
        for key in ("exposure_response","immobile","remaining","received"):
            if fields[key].shape!=shape:raise ValueError("Incorrect pool geometry")
        for key in ("coverage","contact","arrival_seconds"):
            if fields[key].shape!=shape[:2]:raise ValueError("Incorrect process field geometry")
        if any((v<0).any() for v in fields.values()):raise ValueError("Negative material amounts")
        if any((fields[k]>1).any() for k in BASE_FIELDS-{"arrival_seconds"}):raise ValueError("Invalid material fraction")
        if self.schema_version>=2:
            validate_chemistry(fields,shape)
            if (fields["developed_donor"]>fields["immobile"]+1e-6).any():raise ValueError("Donor silver exceeds its exposed pool")
        if self.stage=="cleared" and (self.schema_version<2 or self.material.donor_mode!="silver_negative"):
            raise ValueError("Only a declared positive/negative material can be cleared")
        if self.schema_version>=3:segmented.validate(fields,shape)
        if self.schema_version>=5:inventory.validate(fields,shape,self.material)
        if self.schema_version>=4 and self.stage in ("separated","cleared"):separation.validate(fields,shape[:2])
        if not np.allclose(fields["immobile"]+fields["remaining"]+fields["received"]+fields.get('transfer_loss',0),1,atol=2e-6,rtol=0):
            raise ValueError("Transfer pool is not conserved")
        if not np.array_equal(fields["immobile"],fields["exposure_response"]):raise ValueError("Exposure pool mismatch")
        object.__setattr__(self,"fields",fields)
        process=json.loads(self.process_json)
        for key in ("reagent_decay","contact_defects","reagent_leak","spread_unevenness"):
            defects.fraction(process.get(key,0.),key)
        if self.stage=="exposed":
            if self.elapsed_seconds!=0 or any(np.any(fields[k]) for k in ("received","coverage","contact","arrival_seconds")):
                raise ValueError("Unprocessed state contains process activity")
        else:
            temperature=process.get("temperature_c")
            if not isinstance(temperature,(float,int)) or not math.isfinite(temperature) or not -10<=temperature<=50:
                raise ValueError("Missing or invalid process temperature")


def validate_chemistry(fields,shape):
    for key in CHEMISTRY_FIELDS:
        expected=shape if key in ("channel_activity","transfer_yield","developed_donor") else shape[:2]+(3,) if key=="stain_density" else shape[:2]
        if fields[key].shape!=expected:raise ValueError("Invalid chemistry field geometry")
    if any((fields[k]>1).any() for k in ("transfer_yield","paper_texture","backing_texture")):raise ValueError("Invalid chemistry fraction")
    if (fields["channel_activity"]>4).any():raise ValueError("Invalid chemical activity")


def expose(image_linear, material=PeelMaterial(), *, group_id, seed=0,schema_version=5):
    image=np.asarray(image_linear,dtype=np.float32)
    if image.ndim!=3 or image.shape[2]!=3 or not np.isfinite(image).all() or (image<0).any():
        raise ValueError("Exposure requires nonnegative finite HxWx3 linear RGB")
    if schema_version not in (2,3,4,5):raise ValueError('Only v2/v3/v4/v5 material formation is supported')
    inhibitor=segmented.contamination_map(image.shape[:2],seed,material.initial_contamination) if schema_version>=3 else None
    if schema_version>=3 and material.initial_contamination:image=image*10**(-inhibitor[...,None]*segmented.RESIDUE_RGB)
    if material.transfer=="silver_compound":image=(image @ np.array([.2126,.7152,.0722],np.float32))[...,None]
    channels=image.shape[-1]
    rng=np.random.default_rng(np.random.SeedSequence([material.batch_seed,401]))
    variation=material.batch_variation
    sensitivity=np.exp2(variation*rng.uniform(-.65,.65,channels)).astype(np.float32)
    contrast=(1+variation*rng.uniform(-.25,.35,channels)).astype(np.float32)
    activity=np.exp2(variation*rng.uniform(-.8,.8,channels)).astype(np.float32)
    if variation:
        power=np.power(image,contrast)
        response=power/(power+np.power(material.half_exposure*sensitivity,contrast))
    else:response=image/(image+material.half_exposure)
    h,w=image.shape[:2]
    fields={
        "exposure_response":response,"immobile":response,"remaining":1-response,"received":np.zeros_like(response),
        "coverage":np.zeros((h,w),np.float32),"contact":np.zeros((h,w),np.float32),"arrival_seconds":np.zeros((h,w),np.float32),
        "reagent":np.zeros((h,w),np.float32),"escaped_reagent":np.zeros((h,w),np.float32),"stain_density":np.zeros((h,w,3),np.float32),
        "channel_activity":np.broadcast_to(activity,image.shape).copy(),"transfer_yield":np.ones_like(image),"developed_donor":np.zeros_like(image),
        "paper_texture":np.full((h,w),material.paper_reflectance,np.float32),
        "backing_texture":(.4*ms.noise((h,w),seed,material,415,material.process_scale_mm)+.6*ms.noise((h,w),seed,material,425,material.texture_scale_mm) if ms.enabled(material) else .4*defects.clouds((h,w),seed,415,cells=9)+.6*defects.clouds((h,w),seed,416,cells=97))}
    snapshot=json.dumps(dict(batch=dict(identity=material.batch_seed,variation=variation,sensitivity=sensitivity.tolist(),contrast=contrast.tolist(),activity=activity.tolist())),sort_keys=True)
    if schema_version>=5:fields.update(inventory.empty(image.shape))
    if schema_version>=3:
        fields.update(transfer_loss=np.zeros_like(response),inhibitor=inhibitor)
        fields['stain_density']=inhibitor[...,None]*segmented.RESIDUE_RGB
    return PeelState(group_id,seed,material,"exposed",0.,fields,snapshot,schema_version=schema_version)


def light_leak(state, *, strength=0., edge="left"):
    """Accidental light before processing consumes available image-forming material."""
    if state.stage!="exposed":raise ValueError("Accidental opening is supported before reagent spreading only")
    strength=defects.fraction(strength,"Light exposure")
    if strength==0:return state
    dose=defects.edge_field(state.fields["coverage"].shape,state.seed,edge)*strength*5
    if state.schema_version>=3:
        dose=dose*state.material.light_sensitivity*10**(-state.fields['inhibitor']*.65)
    fields=dict(state.fields)
    affected=fields["remaining"]*(-np.expm1(-dose))[...,None]
    fields["immobile"]=np.minimum(fields["immobile"]+affected,1)
    fields["exposure_response"]=fields["immobile"]
    fields["remaining"]=1-fields["immobile"]
    return replace(state,fields=fields,process_json=defects.record_event(state.process_json,"light_leak",strength=strength,edge=edge))


def spread(state, *, direction="down", coverage=1., contact=1., spread_seconds=2.,
           temperature_c=20., roller_streaks=0., edge_loss=0., reagent_decay=0., contact_defects=0., reagent_leak=0., spread_unevenness=0.,reagent_load=1.):
    if state.stage!="exposed":raise ValueError("Spread requires an exposed, uncontacted pair")
    if direction not in ("down","up","left","right"):raise ValueError("Invalid spread direction")
    if not math.isfinite(spread_seconds) or spread_seconds<0:raise ValueError("Invalid spread time")
    if not math.isfinite(temperature_c) or not -10<=temperature_c<=50:raise ValueError("Temperature outside model range")
    if not 0<=roller_streaks<=1 or not 0<=edge_loss<=1:raise ValueError("Invalid spread defects")
    reagent_decay=defects.fraction(reagent_decay,"Reagent decay")
    contact_defects=defects.fraction(contact_defects,"Contact defects")
    reagent_leak=defects.fraction(reagent_leak,"Reagent leakage")
    spread_unevenness=defects.fraction(spread_unevenness,"Uneven spreading")
    if state.schema_version==1 and (reagent_leak or spread_unevenness):raise ValueError("New chemistry requires a newly exposed v2 sheet")
    if not math.isfinite(reagent_load) or not 0<=reagent_load<=4:raise ValueError("Reagent load must be in [0,4]")
    if state.schema_version<5 and reagent_load!=1.:raise ValueError("Finite reagent load requires a new material")
    h,w=state.fields["coverage"].shape
    def field(value):
        array=np.broadcast_to(np.asarray(value,dtype=np.float32),(h,w)).copy()
        if not np.isfinite(array).all() or (array<0).any() or (array>1).any():raise ValueError("Coverage/contact must lie in [0,1]")
        return array
    covered=field(coverage);touch=field(contact)
    y,x=np.mgrid[0:h,0:w].astype(np.float32);x=(x+.5)/w;y=(y+.5)/h
    along=y if direction in ("up","down") else x
    across=x if direction in ("up","down") else y
    if direction in ("up","left"):along=1-along
    physical=state.schema_version>=2 and ms.enabled(state.material)
    if physical:
        if roller_streaks or reagent_leak or spread_unevenness:
            reagent,escaped,residue,roller=scaled.transfer_flow((h,w),state.seed,state.material,direction,reagent_leak,spread_unevenness)
        else:
            reagent=np.ones((h,w),np.float32);escaped=np.zeros((h,w),np.float32);residue=escaped;roller=escaped
        covered*=1-roller_streaks*roller
        unit=state.material.material_long_edge_mm/max(h,w)
        distance=np.minimum.reduce([x*w*unit,(1-x)*w*unit,y*h*unit,(1-y)*h*unit])
        covered*=1-edge_loss*np.exp(-distance/(state.material.process_scale_mm/3))
    else:
        phase=np.random.default_rng(state.seed).uniform(0,2*np.pi)
        covered*=1-roller_streaks*(.5+.5*np.cos(across*18*np.pi+phase))
        covered*=1-edge_loss*np.exp(-np.minimum.reduce([x,1-x,y,1-y])*30)
    inactive_fraction=0.
    if reagent_decay:
        ageing=ms.noise((h,w),state.seed,state.material,201,state.material.process_scale_mm*2) if physical else defects.clouds((h,w),state.seed,201)
        # Locally exhausted reagent remains inactive even after prolonged waiting.
        if state.schema_version>=5:inactive_fraction=np.clip(reagent_decay*(.4+1.8*ageing),0,1)
        else:covered*=np.clip(1-reagent_decay*(.4+1.8*ageing),0,1)
    if contact_defects:
        cloud=ms.noise((h,w),state.seed,state.material,211,state.material.process_scale_mm) if physical else defects.clouds((h,w),state.seed,202,cells=11)
        voids=np.clip((cloud-.35)*2.5,0,1)
        touch*=np.clip(1-contact_defects*2*voids,0,1)
    fields=dict(state.fields);fields.update(coverage=covered,contact=touch,arrival_seconds=along*spread_seconds)
    if state.schema_version>=2:
        if not physical:reagent,escaped,residue=defects.reagent_flow((h,w),state.seed,direction,reagent_leak,spread_unevenness)
        if state.schema_version>=5:reagent=reagent*reagent_load;escaped=escaped*reagent_load;residue=residue*reagent_load
        fields["reagent"]=reagent;fields["escaped_reagent"]=escaped
        fields["coverage"]=covered*np.clip(reagent,0,1)
        # Residue density is distinct from missing image dye, and persists in the medium.
        fields["stain_density"]=residue[...,None]*np.array([.25,.65,1.1],np.float32)
        sheet=ms.noise((h,w),state.seed,state.material,441,state.material.process_scale_mm) if physical else defects.clouds((h,w),state.seed,414,cells=17)
        strength=max(reagent_leak,spread_unevenness,state.material.batch_variation)
        fields["paper_texture"]=np.full((h,w),state.material.paper_reflectance,np.float32)*(1-strength*.035*sheet)
        activity=state.fields["channel_activity"]*(1+strength*.12*(sheet[...,None]-.5))
        yield_fraction=np.ones(state.fields["received"].shape[-1],np.float32)
        warm=np.clip((temperature_c-28)/12,0,1) if state.schema_version==2 else 0.
        cold=np.clip((13-temperature_c)/13,0,1) if state.schema_version==2 else 0.
        if yield_fraction.size==3:
            activity=activity*np.exp(warm*np.array([-.18,.08,-.10],np.float32)+cold*np.array([.04,.0,-.30],np.float32))
            yield_fraction-=warm*np.array([.12,0,.06],np.float32)+cold*np.array([0,.035,.18],np.float32)
            fields["stain_density"]=fields["stain_density"]+warm*np.array([0,.055,.035],np.float32)+cold*np.array([.025,.005,0],np.float32)
        else:yield_fraction*=1-.2*warm-.1*cold
        fields["channel_activity"]=np.asarray(activity,np.float32)
        fields["transfer_yield"]=np.broadcast_to(yield_fraction,state.fields["received"].shape).copy()
        if state.schema_version>=3:
            fields['stain_density']=fields['stain_density']+state.fields['stain_density']
            if reagent_decay:
                rng=np.random.default_rng(np.random.SeedSequence([state.seed,203]))
                fields['channel_activity']=fields['channel_activity']*(1-reagent_decay*rng.uniform(.25,.85,state.fields['received'].shape[-1])).astype(np.float32)
    if state.schema_version>=5:inventory.initialize(fields,state.material,inactive_fraction)
    snapshot=json.dumps(json.loads(state.process_json)|dict(direction=direction,spread_seconds=spread_seconds,temperature_c=temperature_c,
                             roller_streaks=roller_streaks,edge_loss=edge_loss,reagent_decay=reagent_decay,contact_defects=contact_defects,
                             reagent_leak=reagent_leak,spread_unevenness=spread_unevenness),sort_keys=True,allow_nan=False)
    if state.schema_version>=5:snapshot=json.dumps({**json.loads(snapshot),"reagent_load":reagent_load})
    return replace(state,stage="contact",fields=fields,process_json=snapshot)


def advance(state, *, seconds):
    if state.stage!="contact":raise ValueError("Transfer requires contact; peeling terminates transfer")
    if state.schema_version>=5:return inventory.advance(state,seconds)
    if state.schema_version>=3:return segmented.advance(state,seconds)
    if not math.isfinite(seconds) or seconds<0:raise ValueError("Time increment must be nonnegative")
    total=state.elapsed_seconds+seconds
    if not math.isfinite(total):raise ValueError("Process time overflow")
    settings=json.loads(state.process_json)
    # Creative Q10 rate, deliberately not a manufacturer's development schedule.
    rate=2**((settings["temperature_c"]-20)/10)/state.material.reaction_seconds
    fields=dict(state.fields)
    dose=np.maximum(total-fields["arrival_seconds"],0)*fields["coverage"]*fields["contact"]*rate
    decay=settings.get("reagent_decay",0.)
    if state.schema_version==2:
        activity=fields["channel_activity"]
        if decay:
            rng=np.random.default_rng(np.random.SeedSequence([state.seed,203]))
            activity=activity*(1-decay*rng.uniform(.25,.85,state.fields["received"].shape[-1])).astype(np.float32)
        progress=-np.expm1(-dose[...,None]*activity)
        progress*=fields["transfer_yield"]
    elif decay:
        # Creative channel-dependent activity, not a fitted expired-film stock.
        rng=np.random.default_rng(np.random.SeedSequence([state.seed,203]))
        activity=1-decay*rng.uniform(.25,.85,state.fields["received"].shape[-1])
        progress=-np.expm1(-dose[...,None]*activity.astype(np.float32))
    else:progress=(-np.expm1(-dose))[...,None]
    available=1-fields["immobile"]
    fields["received"]=available*progress
    fields["remaining"]=available-fields["received"]
    if state.schema_version==2:fields["developed_donor"]=fields["immobile"]*progress
    return replace(state,elapsed_seconds=total,fields=fields)


def peel(state,*,angle_degrees=150.,speed_mm_s=25.):
    if state.stage!="contact":raise ValueError("Only a contacted pair can be peeled")
    if state.schema_version>=4:return separation.separate(state,angle_degrees,speed_mm_s)
    if angle_degrees!=150. or speed_mm_s!=25.:raise ValueError('Mechanical peeling requires a v4 pair')
    return replace(state,stage="separated")


def clear_negative(state,*,strength=1.,marks=0.,angle_degrees=0.):
    if state.schema_version>=4:return separation.clear_negative(state,strength=strength,marks=marks,angle_degrees=angle_degrees)
    if state.stage!="separated" or state.schema_version<2 or state.material.donor_mode!="silver_negative":
        raise ValueError("Only a separated positive/negative B&W donor can be cleared")
    # Parent state keeps formation pools as provenance. The finished negative excludes
    # the removable developer/residual layer; it is not an inverted receiver image.
    defects.fraction(strength,'strength');defects.fraction(marks,'marks')
    if not math.isfinite(angle_degrees):raise ValueError('Invalid brush direction')
    return replace(state,stage="cleared",process_json=defects.record_event(state.process_json,"clear_negative",strength=strength,marks=marks,angle_degrees=angle_degrees))


def handling(state,*,target="receiver",**parameters):
    if state.schema_version<2 or state.stage not in ('separated','cleared'):
        raise ValueError('Handle the separated sheets after processing')
    fields=dict(state.fields)
    key='stain_density'
    if state.schema_version>=4:
        separation.require_target(state,target);key=target+'_residue'
    elif target!='receiver':raise ValueError('Independent handling requires a v4 pair')
    fields[key]=fields[key]+defects.handling_density(fields['coverage'].shape,state.seed,**parameters)
    if state.schema_version>=4:parameters={'target':target,**parameters}
    return replace(state,fields=fields,process_json=defects.record_event(state.process_json,'handling',**parameters))


def _separated_bodies(state):
    f=state.fields;m=state.material;shape=f['coverage'].shape
    received,donor_amount=separation.image_amounts(f)
    common={'paper':f['paper_texture'],'density_per_unit':np.full(shape,m.maximum_density,np.float32)}
    receiver=MediumBody(FAMILY,state.schema_version,state.group_id,state.group_id+':receiver','receiver',f'diffuse_paper_v{state.schema_version}',
        {**common,'transferred':received,'stain_density':f['receiver_residue']},presentation_json=json.dumps({'frame':m.border_style}))
    donor=MediumBody(FAMILY,state.schema_version,state.group_id,state.group_id+':donor','donor_residue',f'diffuse_residue_v{state.schema_version}',
        {**common,'immobile':donor_amount,'remaining':np.zeros_like(donor_amount),'stain_density':f['donor_residue']})
    if state.stage=='cleared':
        density=layers.retained(f['developed_donor'],f['donor_native'])*m.maximum_density
        density+=layers.retained(f['received'],f['receiver_on_donor'])*m.maximum_density
        density+=layers.retained(.35+.8*f['remaining'],f['donor_gel']*f['donor_native'])
        donor=MediumBody(FAMILY,state.schema_version,state.group_id,state.group_id+':donor','silver_negative',f'donor_transmission_v{state.schema_version}',
            {'density':density,'base_transmission':np.full(shape,.92,np.float32),'stain_density':f['donor_residue']},usable_negative=True)
    return receiver,donor


def products(state):
    if state.stage not in ("separated","cleared"):raise ValueError("Peel before saving independent final products")
    material=state.material
    if state.schema_version>=4:receiver,donor=_separated_bodies(state)
    else:
        common={"paper":np.full(state.fields["coverage"].shape,material.paper_reflectance,np.float32),
                "density_per_unit":np.full(state.fields["coverage"].shape,material.maximum_density,np.float32)}
        if state.schema_version>=2:common.update(paper=state.fields["paper_texture"],stain_density=state.fields["stain_density"])
        receiver=MediumBody(FAMILY,state.schema_version,state.group_id,state.group_id+":receiver","receiver",f"diffuse_paper_v{state.schema_version}",
            {**common,"transferred":state.fields["received"]},presentation_json=json.dumps({"frame":material.border_style}) if state.schema_version>=2 else "{}")
        donor=MediumBody(FAMILY,state.schema_version,state.group_id,state.group_id+":donor","donor_residue",f"diffuse_residue_v{state.schema_version}",
            {**common,"immobile":state.fields["immobile"],"remaining":state.fields["remaining"]+state.fields.get("transfer_loss",0)})
        if state.stage=="cleared" and state.schema_version<4:
            event=next((e for e in reversed(json.loads(state.process_json).get('events',[])) if e.get('operation')=='clear_negative'),{})
            strength=event.get('strength',1.);marks=event.get('marks',0.)
            brush=defects.brush_field(state.fields['coverage'].shape,event.get('angle_degrees',0.)) if marks else 0.
            removed=strength*(1-.65*marks*brush)
            residue=(.35+.8*state.fields['remaining'])*(1-np.asarray(removed)[...,None])
            density=state.fields['developed_donor']*material.maximum_density*(1-.6*strength*marks*np.asarray(brush)[...,None])+residue
            donor=MediumBody(FAMILY,state.schema_version,state.group_id,state.group_id+":donor","silver_negative",f"donor_transmission_v{state.schema_version}",
                {"density":density,
                 "base_transmission":np.full(state.fields["coverage"].shape,.92,np.float32)},usable_negative=True)
    provenance=json.dumps(dict(family=FAMILY,version=state.schema_version,material=asdict(material),process=json.loads(state.process_json),
        seed=state.seed,process_seconds=state.elapsed_seconds,donor_negative_available=donor.usable_negative,
        group_id=state.group_id,members=[receiver.body_id,donor.body_id]),sort_keys=True)
    if state.schema_version>=5:provenance=json.dumps({**json.loads(provenance),"reagent_budget":inventory.budget(state.fields,material)})
    return ProductGroup(state.group_id,(receiver,donor),provenance)


def preview(state, view=ViewingConditions()):
    """Read the current receiver, without separating or advancing process time."""
    if state.schema_version>=4 and state.stage in ("separated","cleared"):return observe(_separated_bodies(state)[0],view)
    extra={"stain_density":state.fields["stain_density"]} if state.schema_version>=2 else {}
    body=MediumBody(FAMILY,state.schema_version,state.group_id,state.group_id+":preview","receiver_preview",f"diffuse_paper_v{state.schema_version}",
        {"paper":np.full(state.fields["coverage"].shape,state.material.paper_reflectance,np.float32),
         "density_per_unit":np.full(state.fields["coverage"].shape,state.material.maximum_density,np.float32),
         "transferred":state.fields["received"],**extra},presentation_json=json.dumps({"frame":state.material.border_style}) if state.schema_version>=2 else "{}")
    if state.schema_version>=2:body=replace(body,fields=dict(body.fields)|{"paper":state.fields["paper_texture"]})
    return observe(body,view)


@cached_body_validation
def validate_body(body):
    if body.family!=FAMILY or body.schema_version not in (1,2,3,4,5):
        raise ValueError("Unsupported medium observation")
    if body.role=="silver_negative":
        if body.schema_version not in (2,3,4,5) or body.observation_model!=f"donor_transmission_v{body.schema_version}" or not body.usable_negative:
            raise ValueError("Invalid negative declaration")
        expected={"density","base_transmission"}|({'stain_density'} if body.schema_version>=4 else set())
        if set(body.fields)!=expected:raise ValueError("Incomplete negative")
        density=body.fields["density"];base=body.fields["base_transmission"]
        if density.shape!=base.shape+(1,) or (density<0).any() or (base<=0).any() or (base>1).any():raise ValueError("Invalid negative fields")
        if body.schema_version>=4 and (body.fields["stain_density"].shape!=base.shape+(3,) or np.any(body.fields["stain_density"]<0)):raise ValueError("Invalid donor residue")
        return
    receiver=body.role in ("receiver","receiver_preview")
    if not receiver and body.role!="donor_residue":raise ValueError("Invalid peel body role")
    expected={"paper","density_per_unit"}|({"transferred"} if receiver else {"immobile","remaining"})
    if body.schema_version>=2:expected.add("stain_density")
    if set(body.fields)!=expected:raise ValueError("Invalid peel body fields")
    if body.observation_model!=(f"diffuse_paper_v{body.schema_version}" if receiver else f"diffuse_residue_v{body.schema_version}"):
        raise ValueError("Role and observation model disagree")
    amount=body.fields["transferred"] if receiver else body.fields["immobile"]+body.fields["remaining"]
    paper=body.fields["paper"];scale=body.fields["density_per_unit"]
    if amount.ndim!=3 or amount.shape[2] not in (1,3) or paper.ndim!=2 or scale.ndim!=2:
        raise ValueError("Invalid reflection field geometry")
    if any((v<0).any() for v in body.fields.values()) or (amount>1.000002).any() or (paper<=0).any() or (paper>1).any() or (scale<=0).any():
        raise ValueError("Invalid reflection amounts")
    if body.usable_negative:raise ValueError("This material does not declare a recoverable negative")
    if body.schema_version>=2 and body.fields["stain_density"].shape!=paper.shape+(3,):raise ValueError("Invalid residue geometry")


def observe(body, view=ViewingConditions()):
    validate_body(body)
    light=np.asarray(view.illuminant_rgb,dtype=np.float32)
    if light.shape!=(3,) or not np.isfinite(light).all() or (light<0).any():raise ValueError("Invalid illuminant")
    if not math.isfinite(view.exposure_ev) or not -20<=view.exposure_ev<=20:raise ValueError("Invalid viewing exposure")
    if body.role=="silver_negative":
        density=body.fields["density"]
        if body.schema_version>=4:density=density+body.fields["stain_density"]
        return (body.fields["base_transmission"][...,None]*10**(-density)*light*2**view.exposure_ev).astype(np.float32)
    amount=body.fields["transferred"] if "transferred" in body.fields else body.fields["immobile"]+body.fields["remaining"]
    density=amount*body.fields["density_per_unit"][...,None]
    if density.shape[-1]==1:density=np.repeat(density,3,axis=2)
    if body.schema_version>=2:density=density+body.fields["stain_density"]
    image=(body.fields["paper"][...,None]*np.power(10.,-density)*light*(2**view.exposure_ev)).astype(np.float32)
    return frame_observation(body,image,view)


class PeelApartFamily:
    expose=staticmethod(expose)
    finish=staticmethod(products)
    observe=staticmethod(observe)
    @staticmethod
    def process(state, operation, **parameters):
        if operation not in OPERATION_FUNCTIONS:raise ValueError("Unsupported peel operation")
        return OPERATION_FUNCTIONS[operation](state,**parameters)


environment=segmented.environment
contaminate=segmented.contaminate
def clean(state,*,strength=1.,target='receiver',abrasion=0.,marks=0.,angle_degrees=0.):
    if state.schema_version>=4 and state.stage in ('separated','cleared'):
        return separation.clean(state,target=target,strength=strength,abrasion=abrasion,marks=marks,angle_degrees=angle_degrees)
    if target!='receiver' or abrasion or marks or angle_degrees:raise ValueError('Independent sheet cleaning requires a separated v4 pair')
    return segmented.clean(state,strength=strength)

OPERATION_FUNCTIONS={'environment':environment,'contaminate':contaminate,'clean':clean,'spread':spread,'advance':advance,'peel':peel,'light_leak':light_leak,'clear_negative':clear_negative,'handling':handling}
FINAL_STAGES=('separated','cleared')
EXPOSURE_STEPS=('expose',)


def available_operations(state):
    operations={None:('expose',),'exposed':('spread','light_leak'),'contact':('advance','peel'),
                'separated':('clear_negative','handling'),'cleared':('handling',)}[state.stage if state else None]
    if state is not None and state.schema_version>=3:
        operations+=({'exposed':('contaminate','clean'),'contact':('environment',),'separated':('clean',),'cleared':('clean',)}.get(state.stage,()))
    if state is not None and state.schema_version>=4 and state.stage=='cleared':operations+=('clear_negative',)
    return tuple(op for op in operations if (op!='handling' or state.schema_version>=2) and
        (op!='clear_negative' or (state.schema_version>=2 and state.material.donor_mode=='silver_negative')))
