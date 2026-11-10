"""Durable per-image plans for independent materials, using their own sessions."""
from copy import deepcopy
from dataclasses import asdict
import inspect
from pathlib import Path
from uuid import uuid4

from film_foundry.app.material_lab_session import MaterialLabSession,FAMILIES
from film_foundry.app.material_recipes import bind_recipe
from film_foundry.contracts.material_recipe import MODEL as RECIPE_MODEL
from film_foundry.core.atomic_io import atomic_write_json,strict_json_load
from film_foundry.core.material_family import ViewingConditions
from film_foundry.app.control import WorkflowCancelled

MODEL='material_batch_v1'


def create_batch(kind,paths,output,material,steps,view,long_edge=0,seed=0,*,on_run=None,material_version=None,scan_adjustments=None,storage_precision="auto"):
    if kind not in FAMILIES:raise ValueError('Unknown material family')
    if type(long_edge) is not int or long_edge<0:raise ValueError('Invalid working size')
    if not paths or not steps:raise ValueError('Select batch sources and operations')
    module=FAMILIES[kind][0]
    first=getattr(module,'prepare',module.expose) if steps[0][0]=='prepare' else module.expose
    version=inspect.signature(first).parameters['schema_version'].default if material_version is None else material_version
    _,recipe=bind_recipe({'model':RECIPE_MODEL,'family':module.FAMILY,'material_version':version,
        'material':deepcopy(material),'seed':seed,'steps':[{'operation':op,'parameters':deepcopy(params)} for op,params in steps]})
    from film_foundry.app.material_scan import normalize_adjustments
    adjustments=normalize_adjustments(scan_adjustments)
    from film_foundry.core.storage_precision import precision_policy
    storage_precision=precision_policy(storage_precision)
    root=Path(output).resolve()/str(uuid4());root.mkdir(parents=True)
    settings={'kind':kind,'material':recipe['material'],'steps':steps,'view':asdict(view),'long_edge':long_edge,'seed':seed,'recipe':recipe,'scan_adjustments':adjustments,'storage_precision':storage_precision}
    atomic_write_json(root/'settings.json',settings)
    plan={'model':MODEL,'run_id':str(uuid4()),'settings':settings,'status':'pending','items':[
        {'source':str(Path(path).resolve()),'directory':f'{index+1:04d}','seed':(seed+index)%2**32,'status':'pending'}
        for index,path in enumerate(paths)]}
    manifest=root/'material_batch.json';atomic_write_json(manifest,plan)
    if on_run:on_run(root)
    return manifest


def load_plan(path):
    path=Path(path).resolve();plan=strict_json_load(path)
    if not isinstance(plan,dict) or plan.get('model')!=MODEL:raise ValueError('Not a material batch record')
    settings=plan['settings'];kind,recipe=bind_recipe(settings['recipe'])
    if kind!=settings['kind']:raise ValueError('Batch family does not match its recipe')
    ViewingConditions(**settings['view'])
    from film_foundry.app.material_scan import normalize_adjustments
    normalize_adjustments(settings.get('scan_adjustments'))
    from film_foundry.core.storage_precision import precision_policy
    precision_policy(settings.get('storage_precision','full'))
    if type(settings['long_edge']) is not int or settings['long_edge']<0:raise ValueError('Invalid batch working size')
    if not isinstance(plan.get('items'),list) or not plan['items']:raise ValueError('Invalid material batch items')
    for index,item in enumerate(plan['items']):
        if (item.get('directory')!=f'{index+1:04d}' or not isinstance(item.get('source'),str)
                or type(item.get('seed')) is not int or not 0<=item['seed']<2**32):raise ValueError('Invalid material batch item')
        target=path.parent/item['directory']
        if target.resolve()!=target:raise ValueError('Batch result links are not supported')
    return path,plan


def run_batch(path,*,on_result=None,control=None):
    from film_foundry.app.material_result_store import save_result,result_record
    manifest,plan=load_plan(path);settings=plan['settings'];root=manifest.parent
    view=ViewingConditions(**settings['view']);recipe=settings['recipe']
    report={'manifest':str(manifest),'completed':0,'reused':0,'failures':[],'notification_errors':[],'cancelled':False}
    plan['status']='running';atomic_write_json(manifest,plan)
    for index,item in enumerate(plan['items']):
        record=None;target=root/item['directory']
        try:
            if control:
                control.check_cancel();control.report('material-batch','developing',index/len(plan['items']))
            if target.exists():
                record=result_record(target)
                # Presence checks concern declared artifacts only. No pixel reread/hash.
                if record is None or record.get('family')!=settings['kind'] or record.get('batch_item')!={'run_id':plan['run_id'],'index':index} or not all(Path(p).is_file() for p in record['artifacts'].values()):
                    record=None
                    raise ValueError('Existing result is incomplete or belongs to another run; keep it and choose another output')
                report['reused']+=1
            else:
                session=MaterialLabSession(settings['kind']);session.open_image(item['source'],settings['long_edge'])
                from film_foundry.app.material_scan import normalize_adjustments
                session.scan_adjustments=normalize_adjustments(settings.get('scan_adjustments'))
                session.storage_precision=settings.get('storage_precision','full')
                current=deepcopy(recipe);current['seed']=item['seed'];session.replay_recipe(current,control=control)
                record=save_result(session,target,view,settings_path=root/'settings.json',control=control,
                    batch_item={'run_id':plan['run_id'],'index':index})
                report['completed']+=1
            item.update(status='completed',result='result.json');item.pop('error',None)
        except WorkflowCancelled:
            if item.get('status')!='completed':item['status']='pending'
            report['cancelled']=True
        except Exception as exc:
            item.update(status='failed',error=str(exc));report['failures'].append({'source':item['source'],'error':str(exc)})
        atomic_write_json(manifest,plan)
        if record is not None and on_result:
            try:on_result(record)
            except Exception as exc:report['notification_errors'].append(str(exc))
        if report['cancelled']:break
    plan['status']='cancelled' if report['cancelled'] else 'completed_with_errors' if report['failures'] else 'completed'
    plan['summary']=report;atomic_write_json(manifest,plan)
    return report
