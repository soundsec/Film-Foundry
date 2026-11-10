"""Material saving/reuse closure; no new material appearance benchmarks."""
from copy import deepcopy
from dataclasses import asdict,replace
import json
from pathlib import Path
import shutil

import numpy as np
import pytest
from PIL import Image

from film_foundry.app import material_result_store as results,material_workspace as workspace
from film_foundry.app.material_lab_session import MaterialLabSession,FAMILIES
from film_foundry.app.workspace_jobs import archive_material_result,process_material_photos
from film_foundry.app.material_batch_run import run_batch
from film_foundry.core.material_family import ViewingConditions
from film_foundry.tests.test_material_recipes import formed,image
from film_foundry.tests.test_unified_workspace import panel,wait


def source(path):
    Image.fromarray(np.uint8(image()*255)).save(path);return path


@pytest.mark.parametrize('kind',list(FAMILIES))
def test_seven_results_move_reopen_all_products_reuse_and_recipe(kind,tmp_path,monkeypatch):
    session=formed(kind);session.source_path=str(tmp_path/'gone.png');session.source_name='gone.png'
    view=ViewingConditions(exposure_ev=.2)
    record=archive_material_result(session,tmp_path/'before',view)
    old=Path(record['medium']).parent;root=tmp_path/'moved';shutil.move(old,root)
    restored,rebound,restored_view=results.load_result(root)
    if kind=='peel':
        from film_foundry.app.desktop_catalog import DesktopCatalog
        catalog=DesktopCatalog(tmp_path/'catalog.json');catalog.register_result(record);catalog.register_result(rebound)
        assert len(catalog.results[0]['runs'])==1 and catalog.results[0]['runs'][0]['medium']==rebound['medium']
    assert restored.group.group_id==session.group.group_id
    assert [(b.body_id,b.role,b.usable_negative) for b in restored.group.bodies]==[(b.body_id,b.role,b.usable_negative) for b in session.group.bodies]
    assert restored.state.stage==session.state.stage
    for key in session.state.fields:np.testing.assert_array_equal(restored.state.fields[key],session.state.fields[key])
    np.testing.assert_array_equal(restored.image,session.image)
    for index in range(len(session.group.bodies)):
        np.testing.assert_array_equal(restored.preview(restored_view,index),session.preview(view,index))
    saved={p.name:p.read_bytes() for p in root.glob('*.npz')}
    def forbid(*args,**kwargs):raise AssertionError('Unchanged formation must not be saved again')
    monkeypatch.setattr(restored,'save_products',forbid)
    same=archive_material_result(restored,root.parent,restored_view)
    assert same['medium']==rebound['medium']
    refreshed=archive_material_result(restored,root.parent,replace(restored_view,exposure_ev=.8))
    assert refreshed['medium']==same['medium']
    for name,data in saved.items():assert (root/name).read_bytes()==data
    assert results.load_result(root)[2].exposure_ev==.8
    recipe=restored.recipe();replay=MaterialLabSession(kind);replay.image=restored.image
    replay.replay_recipe(recipe)
    for key in session.state.fields:np.testing.assert_array_equal(replay.state.fields[key],session.state.fields[key])


def test_peel_member_reopens_group_continues_and_keeps_original_bundle(panel,tmp_path):
    session=MaterialLabSession('peel');session.image=image()
    for operation in ('expose','spread','advance','peel'):
        session.process(operation,{'seconds':90} if operation=='advance' else {},
            {'transfer':'silver_compound','donor_mode':'silver_negative'},seed=19)
    record=archive_material_result(session,tmp_path/'archive',ViewingConditions(exposure_ev=.4))
    role=session.group.bodies[1].role;path=record['artifacts'][role]
    panel._open_result_artifact(path,'peel');page=panel.workspace_host.currentWidget()
    wait(lambda:not page.busy and page.session.group is not None)
    assert len(page.session.group.bodies)==2 and page.body_choice.currentIndex()==1
    assert page.conditions().exposure_ev==.4 and page.session.state is not None
    old={p.name:p.read_bytes() for p in Path(path).parent.glob('*.npz')}
    page.session.process('clear_negative',{'strength':.3})
    new=archive_material_result(page.session,tmp_path/'archive',page.conditions())
    assert new['medium']!=record['medium']
    for name,data in old.items():assert (Path(path).parent/name).read_bytes()==data


def test_legacy_result_group_and_state_still_restore(tmp_path):
    session=formed('peel');record=archive_material_result(session,tmp_path/'results',ViewingConditions(exposure_ev=.6))
    manifest=Path(record['medium']).parent/'result.json';legacy=deepcopy(record)
    for key in ('model','bundle_files','bodies','input','view','group_id'):legacy.pop(key,None)
    for key in ('recipe','working_exposure','result'):legacy['artifacts'].pop(key,None)
    manifest.write_text(json.dumps(legacy),encoding='utf-8')
    restored,_,view=results.load_result(manifest)
    assert restored.image is None and restored.state is not None and len(restored.group.bodies)==2
    assert view.exposure_ev==.6


def test_batch_failure_continues_retry_reuses_complete_and_cancel_keeps_pending(tmp_path,monkeypatch):
    paths=[source(tmp_path/f'{i}.png') for i in range(3)]
    steps=[(s['operation'],s['parameters']) for s in formed('paper').recipe()['steps']]
    save=results.save_result;failed=[False];records=[]
    def fail_one(session,target,*args,**kwargs):
        if Path(target).name=='0002' and not failed[0]:failed[0]=True;raise OSError('write failure')
        return save(session,target,*args,**kwargs)
    monkeypatch.setattr(results,'save_result',fail_one)
    report=process_material_photos('paper',paths,tmp_path/'runs',{},steps,ViewingConditions(),seed=30,on_result=records.append)
    assert report['completed']==2 and len(report['failures'])==1 and len(records)==2
    manifest=Path(report['manifest']);first=manifest.parent/'0001'/'state.ffmaterial.npz';before=first.read_bytes()
    from film_foundry.app.control import WorkflowControl
    cancelled=run_batch(manifest,control=WorkflowControl(cancel_requested=lambda:True))
    assert cancelled['cancelled'] and len(json.loads(manifest.read_text())['items'])==3
    resumed=run_batch(manifest)
    assert resumed['reused']==2 and resumed['completed']==1 and not resumed['failures']
    assert first.read_bytes()==before
    # A late catalogue error is separately reported and cannot invalidate a saved item.
    def fail_index(record):raise OSError('catalogue unavailable')
    indexed=run_batch(manifest,on_result=fail_index)
    assert indexed['reused']==3 and len(indexed['notification_errors'])==3 and not indexed['failures']
    session,record,_=results.load_result(manifest.parent/'0002')
    assert session.state.seed==31 and record['batch_item']['index']==1


def test_workspace_carries_batch_and_pending_optics_after_sources_disappear(tmp_path):
    first=source(tmp_path/'first.png');second=source(tmp_path/'second.png')
    optical=tmp_path/'optical.ffmaterial.npz';formed('peel').save_products(optical)
    session=MaterialLabSession('paper');session.open_image(first,0)
    from film_foundry.app.optical_cascade import source_choices
    from film_foundry.contracts.optical_cascade import OpticalRelay
    choices=source_choices(optical)
    draft={'pending_optical':{'path':str(optical),'choices':choices,'body':None,'relay':OpticalRelay().as_dict()}}
    package=tmp_path/'work.ffworkspace'
    workspace.save_material_workspace(package,[workspace.snapshot_document(session,draft,batch_paths=[str(first),str(second)])])
    expected=[first.read_bytes(),second.read_bytes(),optical.read_bytes()]
    first.unlink();second.unlink();optical.unlink()
    moved=tmp_path/'moved.ffworkspace';shutil.move(package,moved)
    loaded=workspace.load_material_workspace(moved).documents[0]
    assert [Path(p).read_bytes() for p in loaded.batch_paths]==expected[:2]
    assert Path(loaded.draft['pending_optical']['path']).read_bytes()==expected[2]
    workspace.save_material_workspace(moved,[loaded])
    assert len(workspace.load_material_workspace(moved).documents[0].batch_paths)==2


def test_material_batch_cli_and_real_resume_button(panel,tmp_path,monkeypatch,capsys):
    from film_foundry.app.cli import main
    from film_foundry.ui.material_batch_io import resume
    from film_foundry.ui.sheet_lab_window import SheetBatchWindow
    recipe=tmp_path/'recipe.json';formed('paper').save_recipe(recipe);photo=source(tmp_path/'photo.png')
    assert main(['material-batch','run','--recipe',str(recipe),'--sources',str(photo),'--output',str(tmp_path/'run')])==0
    report=json.loads(capsys.readouterr().out);manifest=report['manifest']
    assert report['completed']==1
    page=panel._mount_material_workspace(SheetBatchWindow('paper',[str(photo)],load_source=False),'batch-test')
    assert resume(page,manifest);wait(lambda:not page.busy)
    assert page.last_error is None and '复用 1' in page.status.text()
    assert page.resume_batch_button.isEnabled()


def test_two_open_pages_refresh_current_view_and_protect_group_members(tmp_path):
    record=archive_material_result(formed('paper'),tmp_path/'results',ViewingConditions())
    root=Path(record['medium']).parent
    first,_,view=results.load_result(root);second,_,_=results.load_result(root)
    archive_material_result(first,root.parent,replace(view,exposure_ev=1.2))
    shared=archive_material_result(second,root.parent,replace(view,exposure_ev=1.2))
    assert shared['view']['exposure_ev']==1.2
    archive_material_result(second,root.parent,view)
    assert results.load_result(root)[2].exposure_ev==0.
    with pytest.raises(ValueError,match='separate filename'):second.save(record['medium'],0)
    with pytest.raises(ValueError,match='separate filename'):second.export_image(record['cover'])
    with pytest.raises(ValueError,match='separate filename'):second.save_recipe(record['artifacts']['recipe'])


def test_old_workspace_keeps_external_batch_paths_without_claiming_portability(tmp_path):
    session=MaterialLabSession('paper');session.image=image();path=tmp_path/'legacy.ffworkspace'
    workspace.save_material_workspace(path,[workspace.snapshot_document(session,{})])
    manifest=path/'workspace.json';value=json.loads(manifest.read_text())
    value['model']=workspace.MODEL;value['documents'][0].pop('sources')
    value['documents'][0]['batch_paths']=[str(tmp_path/'missing-legacy.png')]
    manifest.write_text(json.dumps(value),encoding='utf-8')
    restored=workspace.load_material_workspace(path).documents[0]
    assert restored.batch_paths==(str(tmp_path/'missing-legacy.png'),)
