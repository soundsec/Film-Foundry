from pathlib import Path
import pytest
from PIL import Image

from film_foundry.app.workspace_jobs import process_photos
from film_foundry.model.config import DarkroomConfig


@pytest.mark.parametrize('command',['expose','develop','scan'])
def test_selected_photos_keep_endpoint_and_numbered_resume_destinations(tmp_path,command):
    from film_foundry.app.replay_batch import load_resume_plan,run_resume_batch
    sources=[]
    for i in range(2):
        directory=tmp_path/f'source-{i}';directory.mkdir()
        path=directory/'same.png';Image.new('RGB',(32,24),(80+i*40,100,130)).save(path);sources.append(path)
    cfg=DarkroomConfig(enable_mtf=False,enable_halation=False,enable_grain=False)
    cfg.film_border.enabled=True;cfg.film_border.frame_number=4
    runs=[];records=[]
    assert not process_photos(sources,tmp_path/'result',cfg,command=command,on_run=runs.append,on_result=records.append)
    assert len(records)==2 and all(Path(r['medium']).is_file() for r in records)
    assert records[0]['medium']!=records[1]['medium']
    manifest=runs[0]/'film_foundry_run.json'
    plan=load_resume_plan(manifest)
    assert plan.command==command and all(item.output_path.exists() for item in plan.items)
    # Force one missing item, then resume into its recorded numbered directory.
    # Only this isolated test output is removed; no source image is touched.
    plan.items[1].output_path.unlink()
    run_resume_batch(plan)
    assert plan.items[1].output_path.exists()
    if command=='expose':
        from film_foundry.core.latent_io import load_exposed_latent
        for index,item in enumerate(plan.items):
            latent,_=load_exposed_latent(item.output_path)
            assert latent.film_border_snapshot['frame_number']==4+index
