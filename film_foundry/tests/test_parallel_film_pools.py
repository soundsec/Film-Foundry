from copy import deepcopy
from threading import Event, Lock
import numpy as np
import pytest


def test_ordered_workers_bound_results_and_drain_failure(monkeypatch):
    from film_foundry.core.material_parallel import ordered_results
    monkeypatch.setenv('FILM_FOUNDRY_MATERIAL_THREADS','2')
    second=Event();finished=[];guard=Lock()
    def work(index):
        if index==0:assert second.wait(2.)
        if index==1:second.set()
        with guard:finished.append(index)
        return index
    with ordered_results(range(7),work,workers=2) as (workers,results):
        assert workers==2
        assert list(results)==list(range(7))
    assert finished.index(1)<finished.index(0)
    finished.clear();second.clear()
    with pytest.raises(RuntimeError,match='consumer'):
        with ordered_results(range(7),work,workers=2) as (_,results):
            assert next(results)==0
            raise RuntimeError('consumer failed')
    count=len(finished)
    with ordered_results((0,),lambda value:value,workers=2) as (workers,results):
        assert workers==2 and list(results)==[0]
    assert len(finished)==count
    def fail(value):raise ValueError('producer failed')
    with pytest.raises(ValueError,match='producer'):
        with ordered_results(range(3),fail,workers=2) as (_,results):list(results)


@pytest.mark.parametrize('program',('color_negative','color_reversal','bw_negative','bw_reversal'))
def test_parallel_pools_keep_arrays_moments_and_ordered_audit(monkeypatch,program):
    from film_foundry.core.engine import _develop_reduced_material_tiled
    from film_foundry.core.process_operations import BathContactRate
    from film_foundry.core.film_process.local_grain import LocalGrainCollector
    from film_foundry.tests.test_material_stage_consistency import config
    from film_foundry.tests.test_edge_wetting import event
    from film_foundry.contracts.emulsion_integrity import validate_emulsion_integrity
    monkeypatch.setenv('FILM_FOUNDRY_COMPUTE','cpu')
    c=config(mono=program.startswith('bw_'),positive=program.endswith('reversal'))
    c.chemistry.program_key=program;c.processing.native_thread_limit=2
    c.film.process_response={'model':'creative_process_response_v4'}
    c.film.emulsion_integrity=validate_emulsion_integrity({'model':'wet_emulsion_integrity_v2'})
    c.chemistry.process_events={'model':'film_process_events_v15','events':[
        event(pause_s=30.),
        {'kind':'emulsion_wear','after_bath':1,'pressure':.6,'duration_s':15.,'radius_mm':4.,'edge_width_mm':.4,'boundary_variation':.5},
        {'kind':'emulsion_shear','after_bath':1,'pressure':.7,'duration_s':20.,'travel_mm':.4,'band_width_mm':6.}]}
    shape=(1024,1025);line=np.linspace(.01,.95,shape[1]*3,dtype=np.float32).reshape(1,shape[1],3)
    image=np.broadcast_to(line,(*shape,3)).copy();image.setflags(write=False)
    outputs=[];collectors=[]
    for workers in (1,2):
        monkeypatch.setenv('FILM_FOUNDRY_MATERIAL_THREADS',str(workers))
        collector=LocalGrainCollector(shape,('silver','dye'))
        rate=BathContactRate(c,shape)
        result=_develop_reduced_material_tiled(image,c,program,256,local_development_rate=rate,grain_response_output=collector)
        assert result[3]['execution']['material_pool_workers']==workers
        outputs.append(result);collectors.append(collector)
    for left,right in zip(outputs[0][:3],outputs[1][:3]):np.testing.assert_array_equal(left,right)
    a,b=(deepcopy(out[3]) for out in outputs)
    a['execution'].pop('material_pool_workers');b['execution'].pop('material_pool_workers')
    assert a==b
    for product in ('silver','dye'):
        for left,right in zip(collectors[0].fields[product],collectors[1].fields[product]):np.testing.assert_array_equal(left,right)
        np.testing.assert_array_equal(collectors[0].masses[product],collectors[1].masses[product])
