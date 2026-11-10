"""Persisted spatial preparation must replace replay without changing formation."""
from copy import deepcopy
import numpy as np
import pytest
from film_foundry.tests.test_film_inhibitor_checkpoint import inhibitor_config
from film_foundry.tests.test_film_process_work import source,scene,same_medium,config
from film_foundry.core.engine import expose_image_to_latent,develop_negative_from_latent
from film_foundry.core.latent_io import load_exposed_latent
from film_foundry.core.negative_io import load_developed_negative_npz
from film_foundry.core.film_process.checkpoint_io import load_checkpoint,save_checkpoint
from film_foundry.app.film_process_work import start_work,continue_work,describe_work


def prepared_config():
    c=inhibitor_config()
    c.process_control_mode='expected'
    c.chemistry.development_adjacency_strength=.4
    c.film.dir_material_response_matrix=((0.,.16,.09),(.12,0.,.18),(.16,.11,0.))
    c.chemistry.dir_process_effectiveness=.75
    c.chemistry.uneven_development=.35
    c.chemistry.process_variation=.2
    c.film.silver_grain_strength=.12
    return c


@pytest.mark.parametrize('spectral',[False,True])
def test_resume_reuses_fields_without_provisional_work_and_preserves_rng(tmp_path,monkeypatch,spectral):
    from film_foundry.core import engine,process_operations
    from film_foundry.app import film_process_work
    c=prepared_config()
    c.chemistry.incident_identity_seed=7 if spectral else None
    if spectral:
        from film_foundry.tests.test_spectral_grain import configuration
        c.film.spectral_material_profile=configuration().film.spectral_material_profile
    path=source(tmp_path,c);latent,_=load_exposed_latent(path);expected_rng=np.random.default_rng(19)
    expected=develop_negative_from_latent(latent.copy(),c,rng=expected_rng)
    cp=tmp_path/'zero.ffprocess.npz';start_work(path,cp,c,stop_after=0)
    metadata,_,_=load_checkpoint(cp);record=metadata['spatial_preparation']
    assert metadata['kind']=='film_process_work_v5'
    assert all(record['fields'][name] is not None for name in ('uneven','adjacency','dir'))
    assert (record['rng_before']==record['rng_after']) is spectral
    def forbidden(*args,**kwargs):raise AssertionError('Resumed a completed spatial preparation')
    for name in ('uneven_development_rate_field','build_development_adjacency_field','build_dir_material_response_field','MaterialLatentSource'):
        monkeypatch.setattr(engine,name,forbidden)
    monkeypatch.setattr(process_operations.BathContactRate,'at_shape',forbidden)
    generators=[];make_rng=film_process_work._rng
    def tracked(value):
        rng=make_rng(value);generators.append(rng);return rng
    monkeypatch.setattr(film_process_work,'_rng',tracked)
    path.unlink()
    moved=tmp_path/'moved.ffprocess.npz';cp.rename(moved)
    mid=tmp_path/'mid.ffprocess.npz';continue_work(moved,mid,stop_after=4)
    again=load_checkpoint(mid)[0]['spatial_preparation']
    for name,array in record['arrays'].items():np.testing.assert_array_equal(array,again['arrays'][name])
    final=continue_work(mid,tmp_path/'finished.npz')
    same_medium(expected,load_developed_negative_npz(final.output_path))
    assert generators[-1].bit_generator.state==expected_rng.bit_generator.state


def test_empty_preparation_and_mismatched_dependencies(tmp_path):
    c=config('bw_reversal');c.chemistry.uneven_development=0.
    path=source(tmp_path,c);empty=tmp_path/'empty.ffprocess.npz';start_work(path,empty,c)
    meta,_,_=load_checkpoint(empty)
    assert meta['spatial_preparation']['arrays']=={}
    latent,_=load_exposed_latent(path)
    expected=develop_negative_from_latent(latent.copy(),c,rng=np.random.default_rng(19))
    result=continue_work(empty,tmp_path/'empty-finish.npz');same_medium(expected,load_developed_negative_npz(result.output_path))
    c=prepared_config();path=source(tmp_path,c);cp=tmp_path/'prepared.ffprocess.npz';start_work(path,cp,c)
    meta,latent,state=load_checkpoint(cp)
    for kind in ('config','rng','shape','version'):
        bad=deepcopy(meta);record=bad['spatial_preparation']
        if kind=='config':bad['config']['chemistry']['development_adjacency_strength']=.2
        elif kind=='rng':record['rng_before']['state']['state']+=1
        elif kind=='shape':record['arrays']['uneven']=record['arrays']['uneven'][:-1]
        else:record['model']='unsupported_preparation'
        out=tmp_path/f'{kind}.ffprocess.npz';save_checkpoint(out,bad,latent,state)
        with pytest.raises(ValueError,match='[Ss]patial|prepared'):
            continue_work(out,tmp_path/f'{kind}.npz')


def test_old_v3_still_rebuilds_its_own_preparation(tmp_path,monkeypatch):
    from film_foundry.app.film_process_work import _run
    from film_foundry.model.config import DarkroomConfig
    from film_foundry.core import engine
    c=prepared_config();path=source(tmp_path,c);cp=tmp_path/'v4.ffprocess.npz';start_work(path,cp,c,stop_after=4)
    meta,latent,_=load_checkpoint(cp);oldmeta=deepcopy(meta);oldmeta['scope']='frozen_program_wet_inventory_v3'
    old=tmp_path/'v3.ffprocess.npz';_run(path,old,latent,DarkroomConfig.from_dict(meta['config']),oldmeta,None,4,None)
    assert load_checkpoint(old)[0]['kind']=='film_process_work_v3'
    calls=[];original=engine.build_dir_material_response_field
    def tracked(*a,**kw):calls.append(1);return original(*a,**kw)
    monkeypatch.setattr(engine,'build_dir_material_response_field',tracked)
    previous=continue_work(old,tmp_path/'previous.npz');assert calls
    calls.clear();new=continue_work(cp,tmp_path/'new.npz');assert not calls
    same_medium(load_developed_negative_npz(previous.output_path),load_developed_negative_npz(new.output_path))


def test_reduced_grid_mapping_survives_codec_without_upsampling():
    from film_foundry.core.spatial_fields import GlobalFieldGrid,GlobalRateMultiplierField,GlobalLayerRateMultiplierField
    from film_foundry.core.film_process.preparation import SpatialPreparation,pack_preparation,unpack_preparation
    grid=GlobalFieldGrid((29,43),(7,11));values=np.linspace(.5,1.5,77,dtype=np.float32).reshape(7,11)
    scalar=GlobalRateMultiplierField(values,grid)
    layers=GlobalLayerRateMultiplierField(tuple(GlobalRateMultiplierField(values*(1+i*.1),grid) for i in range(3)))
    rng=np.random.default_rng(19);p=SpatialPreparation();c=config()
    assert p.lookup(c,(29,43),'color_negative',rng) is None
    rng.random(7);p.capture(rng,None,scalar,{'label':'adj'},layers,{'label':'dir'})
    arrays={};packed=pack_preparation(p.snapshot(),arrays);restored=unpack_preparation(packed,arrays)
    q=SpatialPreparation(restored);fresh=np.random.default_rng(19);fields=q.lookup(c,(29,43),'color_negative',fresh)
    assert fields[1].grid==grid and fields[3].grid==grid
    np.testing.assert_array_equal(fields[1].slice_rows(4,12),scalar.slice_rows(4,12))
    np.testing.assert_array_equal(fields[3].slice_rows(4,12),layers.slice_rows(4,12))
    assert fresh.bit_generator.state==rng.bit_generator.state


def test_cli_reference_rebuild_keeps_same_results_and_binding(tmp_path,monkeypatch,capsys):
    from film_foundry.app.cli import main
    from film_foundry.core import engine
    c=prepared_config();path=source(tmp_path,c);cp=tmp_path/'work.ffprocess.npz';start_work(path,cp,c,stop_after=4)
    cached=continue_work(cp,tmp_path/'cached.npz')
    calls=[];original=engine.build_development_adjacency_field
    def tracked(*a,**kw):calls.append(1);return original(*a,**kw)
    monkeypatch.setattr(engine,'build_development_adjacency_field',tracked)
    rebuilt=tmp_path/'rebuilt.npz'
    assert main(['film-process','continue',str(cp),str(rebuilt),'--rebuild-preparation'])==0
    assert len(calls)==1
    same_medium(load_developed_negative_npz(cached.output_path),load_developed_negative_npz(rebuilt))
    meta,latent,state=load_checkpoint(cp);meta['config']['chemistry']['development_adjacency_strength']=.1
    changed=tmp_path/'changed.ffprocess.npz';save_checkpoint(changed,meta,latent,state)
    with pytest.raises(ValueError,match='Spatial preparation differs'):
        continue_work(changed,tmp_path/'changed.npz',reuse_preparation=False)
