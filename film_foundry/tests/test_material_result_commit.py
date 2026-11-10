"""Result publication/rollback checks, without material benchmarks or hashes."""
from dataclasses import replace
import json
from pathlib import Path
import numpy as np
from PIL import Image
import pytest
from film_foundry.app import workspace_jobs as jobs,material_result_store as store,material_image_io as image_io
from film_foundry.app.material_lab_session import MaterialLabSession
from film_foundry.app.material_route import run_material_route_file
from film_foundry.app.control import WorkflowControl,WorkflowCancelled
from film_foundry.core import atomic_io,medium_export
from film_foundry.core.material_family import ViewingConditions
from film_foundry.core.photographic_paper import FAMILY as PAPER
from film_foundry.model.config import DarkroomConfig

IMAGE=np.random.default_rng(2).random((12,18,3),dtype=np.float32)
VIEW=ViewingConditions()
PAPER_STEPS=[{'operation':op} for op in ('develop','fix','wash')]


def source(tmp_path,name='source.png'):
    path=tmp_path/name;Image.fromarray((IMAGE*255).astype('uint8')).save(path,format='PNG');return path


def formed(tmp_path):
    session=MaterialLabSession('peel');session.open_image(source(tmp_path),0)
    for op in ('expose','spread','advance','peel'):session.process(op,{'seconds':90} if op=='advance' else {},seed=3)
    return session


def all_files(root):return {p.relative_to(root):p.read_bytes() for p in root.rglob('*') if p.is_file()}


def test_initial_multibody_result_is_hidden_until_complete_and_retryable(tmp_path,monkeypatch):
    session=formed(tmp_path);root=tmp_path/'results';target=root/'bundle'
    monkeypatch.setattr(jobs,'uuid4',lambda:'bundle');export=session.export_png
    state=session.state;group=session.group
    def fail_second(path,view,index):
        assert not target.exists()
        export(path,view,index)
        if index==1:raise OSError('second body failed')
    monkeypatch.setattr(session,'export_png',fail_second)
    with pytest.raises(OSError,match='second body'):jobs.archive_material_result(session,root,VIEW)
    assert not list(root.iterdir()) and getattr(session,'_result_archive_cache',None) is None
    monkeypatch.setattr(session,'export_png',export)
    record=jobs.archive_material_result(session,root,VIEW)
    assert session.state is state and session.group is group
    assert json.loads((target/'result.json').read_text())==record
    assert all(Path(p).is_file() and '.ff-stage-' not in p for p in record['artifacts'].values())
    from film_foundry.app import material_families as api
    assert len(api.load(record['medium']).bodies)==2
    before=all_files(target)
    with pytest.raises(FileExistsError):store.save_result(session,target,VIEW)
    assert all_files(target)==before


def test_new_result_commit_failure_and_cancellation_remove_only_staging(tmp_path,monkeypatch):
    session=formed(tmp_path);target=tmp_path/'bundle'
    sentinel=tmp_path/'keep';sentinel.mkdir();(sentinel/'notes').write_text('keep')
    rename=atomic_io.os.rename
    def fail(source,destination):
        if Path(source).name.startswith('.ff-stage-') and Path(destination)==target:raise OSError('publish failed')
        return rename(source,destination)
    monkeypatch.setattr(atomic_io.os,'rename',fail)
    with pytest.raises(OSError,match='publish'):store.save_result(session,target,VIEW)
    assert not target.exists() and not list(tmp_path.glob('.ff-stage-*'))
    monkeypatch.setattr(atomic_io.os,'rename',rename)
    cancelled=[False];export=session.export_png
    def cancel_after_export(*args):export(*args);cancelled[0]=True
    monkeypatch.setattr(session,'export_png',cancel_after_export)
    with pytest.raises(WorkflowCancelled):store.save_result(session,target,VIEW,control=WorkflowControl(cancel_requested=lambda:cancelled[0]))
    assert not target.exists() and not list(tmp_path.glob('.ff-stage-*'))
    assert (sentinel/'notes').read_text()=='keep'


def test_refresh_keeps_old_images_visible_then_rolls_back_failed_publication(tmp_path,monkeypatch):
    session=formed(tmp_path);record=jobs.archive_material_result(session,tmp_path/'results',VIEW)
    root=Path(record['medium']).parent;before=all_files(root);export=session.export_png
    def render(*args):
        for name,data in before.items():assert (root/name).read_bytes()==data
        export(*args)
    monkeypatch.setattr(session,'export_png',render)
    original=store.os.replace
    def fail_second(source,destination):
        if Path(source).parent.name.startswith('.ff-observe-') and Path(destination).name=='donor_residue.png':
            raise OSError('refresh publish failed')
        return original(source,destination)
    monkeypatch.setattr(store.os,'replace',fail_second)
    with pytest.raises(OSError,match='refresh publish'):jobs.archive_material_result(session,root.parent,replace(VIEW,exposure_ev=1))
    assert all_files(root)==before and session._result_archive_cache['view']['exposure_ev']==0
    monkeypatch.setattr(store.os,'replace',original)
    jobs.archive_material_result(session,root.parent,replace(VIEW,exposure_ev=1))
    assert session._result_archive_cache['view']['exposure_ev']==1
    for name,data in before.items():
        if str(name).endswith('.npz'):assert (root/name).read_bytes()==data


def test_batch_announces_only_committed_items_and_preserves_success_before_failure(tmp_path,monkeypatch):
    photo=source(tmp_path);root=tmp_path/'batch';records=[];export=MaterialLabSession.export_png
    count=[0]
    def fail_second(self,*args):
        count[0]+=1;export(self,*args)
        if count[0]==2:raise OSError('batch observation failed')
    monkeypatch.setattr(MaterialLabSession,'export_png',fail_second)
    def accept(record):
        assert all(Path(p).is_file() for p in record['artifacts'].values())
        assert (Path(record['medium']).parent/'result.json').is_file()
        records.append(record)
    steps=[(op,{}) for op in ('expose','develop','fix','wash')]
    report=jobs.process_material_photos('paper',[photo,photo],root,{},steps,VIEW,on_result=accept)
    assert len(report['failures'])==1 and 'batch observation' in report['failures'][0]['error']
    run=next(root.iterdir());assert len(records)==1 and (run/'0001').is_dir() and not (run/'0002').exists()
    assert not list(run.glob('.ff-stage-*')) and (run/'settings.json').is_file()


@pytest.mark.parametrize('suffix,depth',[('.png',8),('.tif',16)])
def test_route_family_companion_image_and_metadata_roll_back_together(tmp_path,monkeypatch,suffix,depth):
    photo=source(tmp_path);target=tmp_path/('result'+suffix);medium=tmp_path/'result.formed.ffmaterial.npz'
    sidecar=target.with_suffix(suffix+'.json')
    old={target:b'old image',sidecar:b'old metadata',medium:b'old medium'}
    for path,data in old.items():path.write_bytes(data)
    export=image_io.save_observation
    def fail(*args,**kw):export(*args,**kw);raise OSError('final observation failed')
    monkeypatch.setattr(image_io,'save_observation',fail)
    cfg=DarkroomConfig();cfg.output.bit_depth=depth
    with pytest.raises(OSError,match='final observation'):
        run_material_route_file(photo,target,start='image',receiver=PAPER,stop='image',operations=PAPER_STEPS,config=cfg)
    for path,data in old.items():assert path.read_bytes()==data
    assert not list(tmp_path.glob('.ff-backup-*'))
    monkeypatch.setattr(image_io,'save_observation',export)
    result=run_material_route_file(photo,target,start='image',receiver=PAPER,stop='image',operations=PAPER_STEPS,config=cfg)
    assert Path(result['medium_path'])==medium
    assert json.loads(sidecar.read_text())['FilmFoundryObservation']['medium_name']==medium.name


def test_route_film_includes_optional_companions_and_directories_in_rollback(tmp_path,monkeypatch):
    from film_foundry.tests.test_optical_cascade import config
    photo=source(tmp_path);cfg=config();target=tmp_path/'film.png'
    medium=tmp_path/'film.formed.darkroom_negative.npz'
    for path in medium_export.developed_medium_output_paths(medium):
        if path.name.endswith(('_layer_pack','_transparent_plate','_plate_set')):
            path.mkdir();(path/'old-part').write_bytes(b'old component')
        else:path.write_bytes(b'old '+path.name.encode())
    target.write_bytes(b'old image');target.with_suffix('.png.json').write_bytes(b'old metadata')
    before=all_files(tmp_path)
    def fail(*a,**k):raise OSError('film scan export failed')
    monkeypatch.setattr(image_io,'save_observation',fail)
    with pytest.raises(OSError,match='film scan'):
        run_material_route_file(photo,target,start='image',receiver='film',stop='image',config=cfg)
    assert all_files(tmp_path)==before


def test_route_source_is_protected_and_observe_only_does_not_rewrite_medium(tmp_path,monkeypatch):
    session=formed(tmp_path);medium=tmp_path/'result.formed.ffmaterial.npz';session.save_products(medium);before=medium.read_bytes()
    with pytest.raises(ValueError,match='source'):
        run_material_route_file(medium,tmp_path/'result.png',start='medium',receiver=PAPER,stop='image',operations=PAPER_STEPS)
    assert medium.read_bytes()==before
    # The 8-bit metadata path is part of source protection as well.
    protected=tmp_path/'protected.png.json';protected.write_bytes(b'original')
    with pytest.raises(ValueError,match='source'):
        image_io.save_observation(IMAGE,tmp_path/'protected.png',{},protected_paths=(protected,))
    assert protected.read_bytes()==b'original'
    cancelled=[False]
    def progress(event):
        if event.stage=='completed':cancelled[0]=True
    result=run_material_route_file(medium,tmp_path/'view.png',start='medium',stop='image',result_body_role='receiver',
        control=WorkflowControl(cancel_requested=lambda:cancelled[0],on_progress=progress))
    assert Path(result['medium_path'])==medium and medium.read_bytes()==before
    assert (tmp_path/'view.png').is_file() and (tmp_path/'view.png.json').is_file()
