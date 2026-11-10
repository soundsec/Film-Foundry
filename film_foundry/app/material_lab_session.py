"""UI-independent session and I/O coordination for the experimental material windows."""
from dataclasses import asdict,replace
import json
from pathlib import Path
from copy import deepcopy
from uuid import uuid4
import numpy as np
from film_foundry.app import material_families as api
from film_foundry.core import peel_apart, integral_instant, silver_plate
from film_foundry.core import photographic_paper,wet_plate,cyanotype,platinum_palladium
from film_foundry.core.material_family import ProductGroup, ViewingConditions
from film_foundry.core.color import linear_to_srgb
from film_foundry.app.material_image_io import load_exposure_image,save_observation
from film_foundry.contracts.material_recipe import KEY as RECIPE_KEY,validate_recipe
from film_foundry.app.material_operations import resolve_parameters
from film_foundry.app.material_process_record import record_transition

FAMILIES={"peel":(peel_apart,peel_apart.PeelMaterial),
          "integral":(integral_instant,integral_instant.InstantMaterial),
          "plate":(silver_plate,silver_plate.PlateMaterial)}
SHEET_FAMILIES={'paper':(photographic_paper,photographic_paper.PaperMaterial),
    'wet':(wet_plate,wet_plate.WetPlateMaterial),'cyanotype':(cyanotype,cyanotype.CyanotypeMaterial),
    'platinum':(platinum_palladium,platinum_palladium.PlatinumMaterial)}
FAMILIES.update(SHEET_FAMILIES)


class MaterialLabSession:
    def __init__(self,kind):
        if kind not in FAMILIES:raise ValueError("Unknown material window")
        self.kind=kind;self.module,self.material_type=FAMILIES[kind]
        self.image=None;self.state=None;self.group=None;self.source_name="";self.source_path=''
        self.optical_source=None;self.optical_body_id=None;self.optical_relay=None
        self.optical_reference=None
        self.source_info={}
        self.scan_adjustments={}
        self.storage_precision='auto'

    @property
    def operations(self):
        if self.state is None and (self.image is None or self.group is not None):return ()
        return tuple(op for op in self.module.available_operations(self.state) if op!='expose' or self.image is not None)

    def open_image(self,path,long_edge=1600,preserve_preparation=False,*,input_icc_policy='apply'):
        image,source_info=load_exposure_image(path,long_edge,input_icc_policy=input_icc_policy)
        if preserve_preparation:
            if self.kind not in ('plate','wet') or self.state is None or self.state.stage not in ("blank","polished","coated","sensitized"):
                raise ValueError("Only an unexposed plate can receive an exposure image")
            if image.shape[:2]!=self.state.fields["latent"].shape:raise ValueError("曝光图尺寸须与已准备银版一致；可调整工作尺寸后重试")
        else:self.state=None;self.group=None
        self.image=image;self.source_name=Path(path).name;self.source_path=str(Path(path).resolve())
        self.source_info=source_info
        self.optical_source=None;self.optical_body_id=None;self.optical_relay=None
        self.optical_reference=None

    def open_medium(self,path,*,body_id=None,relay=None,long_edge=1600,preserve_preparation=False):
        from film_foundry.app.optical_cascade import load_source,project_medium
        from film_foundry.contracts.optical_cascade import OpticalRelay
        from film_foundry.core.preview import resize_to_long_edge
        if long_edge<0:raise ValueError('Invalid working size')
        relay=OpticalRelay.from_value(relay);source=load_source(path,reflection=relay.mode=='reflection')
        image=project_medium(source,relay,body_id=body_id);source_size=list(image.shape[1::-1])
        image=resize_to_long_edge(image,long_edge or None)
        if preserve_preparation:
            if self.kind not in ('plate','wet') or self.state is None or self.state.stage not in ('blank','polished','coated','sensitized'):
                raise ValueError('Only an unexposed plate can receive an optical source')
            if image.shape[:2]!=self.state.fields['latent'].shape:raise ValueError('光学来源尺寸须与已准备版材一致')
        else:self.state=None;self.group=None
        self.image=image
        self.optical_source=source;self.optical_body_id=body_id;self.optical_relay=relay
        self.optical_reference=None
        self.source_name=Path(path).name;self.source_path=str(Path(path).resolve())
        self.source_info={'model':'material_source_input_v1','kind':'optical','name':self.source_name,
            'source_size':source_size,'working_size':list(image.shape[1::-1]),'encoding':'linear_rgb','formation_long_edge':long_edge}

    def load(self,path):
        value=api.load(path)
        if isinstance(value,ProductGroup):
            if any(b.family!=self.module.FAMILY for b in value.bodies):raise ValueError("此存档属于其他材料窗口")
            state=None;group=value
        else:
            if api.state_family(value)!=self.module.FAMILY:raise ValueError("此存档属于其他材料窗口")
            state=value;group=self._final_group(state)
        self.state=state;self.group=group;self.image=None;self.source_name=Path(path).name;self.source_path=str(Path(path).resolve())
        self.optical_source=None;self.optical_body_id=None;self.optical_relay=None
        self.optical_reference=None
        process=json.loads(state.process_json) if state else json.loads(group.provenance_json).get('process',{})
        self.source_info=deepcopy(process.get('source_input',{}))

    def _final_group(self,state):
        return self.module.products(state) if state.stage in self.module.FINAL_STAGES else None

    def process(self,operation,parameters=None,material=None,seed=0,*,material_version=None):
        if operation not in self.operations:raise ValueError("当前阶段不可执行此操作")
        parameters=resolve_parameters(self.module,operation,dict(parameters or {}))
        # Detach caller-owned values and reject non-portable arrays before work.
        parameters=json.loads(json.dumps(parameters,allow_nan=False))
        if self.state is None:
            material=self.material_type(**(material or {}))
            if operation=="prepare":
                version={'schema_version':material_version} if material_version is not None else {}
                candidate=self.module.prepare(self.image.shape[:2],material,group_id=str(uuid4()),seed=seed,**version)
            else:
                version={'schema_version':material_version} if material_version is not None else {}
                candidate=self.module.expose(self.image,material,group_id=str(uuid4()),seed=seed,**version)
        elif self.kind in ('plate','wet') and operation=="expose":candidate=self.module.expose_surface(self.state,image=self.image)
        else:candidate=api.family(self.module.FAMILY).process(self.state,operation,**parameters)
        annotations={}
        if operation=='expose' and (self.optical_source is not None or self.optical_reference is not None):
            from film_foundry.app.optical_cascade import _record,record_reference
            from film_foundry.contracts.optical_cascade import CASCADE_KEY
            relay=replace(self.optical_relay,output_shape=tuple(self.image.shape[:2]))
            target={'kind':'family','family':self.module.FAMILY,'group_id':candidate.group_id,'seed':candidate.seed}
            trace=(record_reference(self.optical_reference,relay,target,self.image.shape) if self.optical_reference is not None else
                _record(self.optical_source,relay,target,self.image.shape,body_id=self.optical_body_id,source_name=self.source_name))
            annotations[CASCADE_KEY]=trace
        if operation in ('prepare','expose') and self.source_info:
            annotations['source_input']=self.source_info
        candidate=record_transition(self.module,self.state,candidate,operation,parameters)
        if annotations:candidate=candidate.with_annotations(**annotations)
        group=self._final_group(candidate)
        self.state=candidate;self.group=group

    def capabilities(self):
        from film_foundry.app.material_capabilities import describe_family
        return describe_family(self.module.FAMILY,state=self.state,exposure_available=self.image is not None,
                               group=self.group if self.state is None else None)

    def recipe(self):
        process=json.loads(self.state.process_json) if self.state is not None else json.loads(self.group.provenance_json).get('process',{}) if self.group is not None else {}
        if RECIPE_KEY not in process:raise ValueError('此材料没有完整操作配方；旧工艺记录不能自动还原为可重放配方')
        return validate_recipe(process[RECIPE_KEY])

    def save_recipe(self,path):
        from film_foundry.app.material_recipes import save_recipe
        self._protect_bundle_member(path)
        if self.source_path and Path(path).resolve()==Path(self.source_path).resolve():raise ValueError('Recipe must not overwrite its source')
        save_recipe(path,self.recipe())

    def replay_recipe(self,value,*,control=None):
        from film_foundry.app.material_recipes import bind_recipe
        kind,recipe=bind_recipe(value)
        if kind!=self.kind:raise ValueError('配方属于其他材料工作区')
        if self.image is None:raise ValueError('先打开曝光图像或光学来源，再应用配方')
        temporary=MaterialLabSession(kind)
        temporary.image=self.image;temporary.source_name=self.source_name;temporary.source_path=self.source_path
        temporary.source_info=deepcopy(self.source_info);temporary.optical_source=self.optical_source
        temporary.optical_body_id=self.optical_body_id;temporary.optical_relay=self.optical_relay
        temporary.optical_reference=deepcopy(self.optical_reference)
        for index,step in enumerate(recipe['steps']):
            if control:
                control.check_cancel();control.report('material-replay','developing',index/len(recipe['steps']))
            try:temporary.process(step['operation'],step['parameters'],recipe['material'],recipe['seed'],material_version=recipe['material_version'])
            except (TypeError,OverflowError) as exc:raise ValueError(f'Invalid {step["operation"]} parameters: {exc}') from exc
        if control:control.report('material-replay','developing',1.)
        # Publish only the fully replayed result. Failure/cancellation leaves the
        # current image, state and result group intact.
        self.state=temporary.state;self.group=temporary.group

    def preview(self,view=ViewingConditions(),body_index=0):
        if self.group is not None:
            if not 0<=body_index<len(self.group.bodies):raise ValueError("Invalid product selection")
            image=api.observe(self.group.bodies[body_index],view)
        elif self.state is not None:image=self.module.preview(self.state,view)
        elif self.image is not None:image=self.image
        else:return None
        # Observers return owned buffers; an unexposed source is kept intact.
        image=np.asarray(image,dtype=np.float32)
        if self.state is None and self.group is None:image=image.copy()
        from film_foundry.app.material_scan import render_material_scan
        from film_foundry.core.material_family import observation_border
        border=(0,0,0,0)
        if self.group is not None:
            body=self.group.bodies[body_index]
            if self.kind!='plate':border=observation_border(body,next(iter(body.fields.values())).shape,view)
        return render_material_scan(image,self.scan_adjustments,border=border)

    def run_standard(self,material=None,seed=0,overrides=None):
        """A fresh sheet follows its family's declared reference sequence."""
        if self.kind not in SHEET_FAMILIES or self.state is not None or self.group is not None:
            raise ValueError('Standard processing requires a fresh sheet source')
        definition=self.material_type(**(material or {}))
        for operation,parameters in self.module.default_operations(definition):
            self.process(operation,{**parameters,**(overrides or {}).get(operation,{})},material,seed)

    def save(self,path,body_index=None,*,precision=None):
        precision=self.storage_precision if precision is None else precision
        self._protect_bundle_member(path)
        if body_index is not None:
            if self.group is None:raise ValueError("尚未形成最终介质")
            api.save_body(path,self.group,self.group.bodies[body_index].body_id,precision=precision)
        elif self.state is not None:api.save(path,self.state,precision=precision)
        elif self.group is not None:api.save(path,self.group,precision=precision)
        else:raise ValueError("尚无材料状态")

    def save_products(self,path,*,precision=None):
        precision=self.storage_precision if precision is None else precision
        self._protect_bundle_member(path)
        if self.group is None:raise ValueError("尚未形成最终介质")
        api.save(path,self.group,precision=precision)

    def export_png(self,path,view=ViewingConditions(),body_index=0):
        if Path(path).suffix.lower()!='.png':raise ValueError('PNG output requires a .png path')
        return self.export_image(path,view,body_index)

    def export_image(self,path,view=ViewingConditions(),body_index=0,*,bit_depth=8,quality=100):
        self._protect_bundle_member(path)
        if self.state is None and self.group is None:raise ValueError("先形成材料再导出观察结果")
        image=self.preview(view,body_index)
        fields=self.state.fields if self.state is not None else self.group.bodies[body_index].fields
        metadata={"family":self.module.FAMILY,"view":asdict(view),'scan_adjustments':deepcopy(self.scan_adjustments),
                  'observation_pipeline':'shared_positive_observation_v1','interpretation':'direct_observation',
                  'medium_size':list(next(iter(fields.values())).shape[1::-1]),'source_input':deepcopy(self.source_info),
                  'resolution':'formed_medium',
                  "stage":self.state.stage if self.state else "saved_product","experimental":True}
        from film_foundry.app.material_processing import processing_info
        metadata['processing']=processing_info(self,body_index)
        if self.group:
            metadata.update(group_id=self.group.group_id,body_id=self.group.bodies[body_index].body_id)
        else:metadata.update(group_id=self.state.group_id,process_seconds=self.state.elapsed_seconds)
        return save_observation(image,path,metadata,bit_depth=bit_depth,quality=quality,protected_paths=(self.source_path,))

    def _protect_bundle_member(self,path):
        cache=getattr(self,'_result_archive_cache',None)
        if cache and Path(path).resolve() in {Path(p).resolve() for p in cache['record']['artifacts'].values()}:
            raise ValueError('结果组内文件由成组保存维护；单独导出请另选文件名 / Choose a separate filename for individual exports from a result bundle')
