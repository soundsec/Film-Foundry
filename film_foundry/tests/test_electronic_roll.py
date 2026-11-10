from dataclasses import asdict
import numpy as np
from PIL import Image
import pytest
from film_foundry.app.api import WorkflowRequest,run_request
from film_foundry.app.electronic_roll import import_roll,RollSession
from film_foundry.core.latent_io import load_exposed_latent
from film_foundry.model.config import DarkroomConfig
from film_foundry.app.workspace_jobs import process_photos
from film_foundry.app.desktop_catalog import DesktopCatalog


@pytest.fixture
def roll(tmp_path):
    cfg=DarkroomConfig(enable_grain=False,enable_mtf=False,enable_halation=False)
    cfg.seed_strategy='fixed';cfg.random_seed=31
    cfg.output.save_scanner_raw=False;cfg.output.export_transparent_plate=False
    image=tmp_path/'source.png';Image.fromarray(np.full((6,8,3),128,np.uint8)).save(image)
    latent=tmp_path/'frame.darkroom_latent.npz'
    run_request(WorkflowRequest('expose',image,latent,cfg,frame_identity={'roll_id':'test-roll','frame_index':2}))
    session=import_roll(latent,tmp_path/'rolls',paths=[latent],capacity=8,confirmed_complete=True)
    return session,cfg,latent,image


def test_roll_roundtrip_blank_processing_and_readonly_scanning(roll,tmp_path):
    session,cfg,latent,image=roll;stamp=latent.stat().st_mtime_ns
    session.export(tmp_path/'private.ffroll')
    restored=import_roll(tmp_path/'private.ffroll',tmp_path/'restored')
    assert restored.data['roll']==session.data['roll']
    restored.develop(cfg,tmp_path/'output')
    assert restored.data['phase']=='developed' and len(restored.data['frames'])==8
    blank=load_exposed_latent(restored.path.parent/'blank-1.darkroom_latent.npz')[0]
    assert np.count_nonzero(blank.developability)==0 and np.any(blank.halide)
    with pytest.raises(ValueError,match='already developed'):restored.develop(cfg,tmp_path/'other')
    medium=restored.data['frames']['1']['medium']
    from pathlib import Path
    before=Path(medium).stat().st_mtime_ns
    restored.scan(cfg)
    assert all(r.get('cover') for r in restored.data['frames'].values())
    assert Path(medium).stat().st_mtime_ns==before and latent.stat().st_mtime_ns==stamp
    destination=tmp_path/'chosen-scans'
    restored.scan(cfg,output=destination)
    assert all(Path(r['cover']).is_relative_to(destination) and Path(r['cover']).is_file() for r in restored.data['frames'].values())
    assert Path(medium).stat().st_mtime_ns==before


def test_missing_not_blank_and_retry_freezes_bath(roll,tmp_path,monkeypatch):
    session,cfg,latent,image=roll
    import film_foundry.app.electronic_roll as module
    original=module.run_request;calls=[]
    def fail(request):
        calls.append(request)
        if len(calls)==2:raise OSError('interrupted')
        return original(request)
    monkeypatch.setattr(module,'run_request',fail)
    with pytest.raises(OSError):session.develop(cfg,tmp_path/'output')
    assert list(session.data['frames'])==['1']
    frozen=session.data['config'];first=session.data['frames']['1']['medium']
    cfg.chemistry.program_key='color_reversal'
    monkeypatch.setattr(module,'run_request',original)
    resumed=RollSession(session.path);resumed.develop(cfg,tmp_path/'ignored')
    assert resumed.data['config']==frozen and resumed.data['frames']['1']['medium']==first
    (session.path.parent/'frame.darkroom_latent.npz').unlink()
    with pytest.raises(ValueError,match='does not exist'):import_roll(session.path.parent/'roll.json',tmp_path/'broken')


def test_photo_batch_stacks_group_all_runs(roll,tmp_path):
    session,cfg,latent,image=roll;catalog=DesktopCatalog(tmp_path/'catalog.json')
    for _ in range(2):
        assert process_photos([image],tmp_path/'photos',cfg,on_result=catalog.register_result)==[]
    result=DesktopCatalog(catalog.path).results
    assert len(result)==1 and len(result[0]['runs'])==2
    assert all(any(k.startswith('scan:') for k in r['artifacts']) for r in result[0]['runs'])


def test_selected_batch_resume_keeps_same_named_sources_distinct(roll,tmp_path):
    from film_foundry.app.control import CancelFlag,WorkflowControl,WorkflowCancelled
    from film_foundry.app.replay_batch import load_resume_plan,run_resume_batch
    from pathlib import Path
    session,cfg,latent,image=roll
    other=tmp_path/'other'/image.name;other.parent.mkdir();Image.open(image).save(other)
    flag=CancelFlag();records=[]
    def completed(record):records.append(record);flag.cancel()
    with pytest.raises(WorkflowCancelled):
        process_photos([image,other],tmp_path/'resume',cfg,control=WorkflowControl(cancel_requested=flag.requested),on_result=completed)
    manifest=next((tmp_path/'resume').glob('*/film_foundry_run.json'))
    plan=load_resume_plan(manifest)
    assert len({i.output_path for i in plan.items})==2
    first=Path(records[0]['medium']);stamp=first.stat().st_mtime_ns
    result=run_resume_batch(plan,on_result=records.append)
    assert result.completed==1 and result.reused==1 and not result.failures
    assert first.stat().st_mtime_ns==stamp and len(records)==2
