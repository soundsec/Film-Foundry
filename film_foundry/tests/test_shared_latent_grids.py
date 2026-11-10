import numpy as np
import pytest


@pytest.mark.parametrize('mono',[False,True])
def test_two_grids_share_exposure_keep_integrals_and_transfer_ownership(monkeypatch,mono):
    from film_foundry.core.material_latent_grid import MaterialLatentSource
    from film_foundry.tests.test_material_stage_consistency import config
    from film_foundry.core.spatial_fields import LayerExposureAdditionField
    c=config(mono=mono);shape=(173,251)
    image=np.random.default_rng(53).uniform(0.,1.5,(*shape,3)).astype(np.float32)
    image.setflags(write=False)
    addition=LayerExposureAdditionField(np.full(shape,.013,np.float32),(1.,) if mono else (1.,.7,.3))
    monkeypatch.setattr(MaterialLatentSource,'ROW_PIXELS',251*11)
    from film_foundry.core import material_parallel
    monkeypatch.setattr(material_parallel,'PARALLEL_MIN_PIXELS',1)
    monkeypatch.setenv('FILM_FOUNDRY_MATERIAL_THREADS','4')
    snapshots=[];counts=[]
    for shared,workers in ((False,1),(True,1),(True,4)):
        source=MaterialLatentSource(c.film,image_linear=image,latent_layer_exposure_addition=addition,exposure_duration_seconds=.2)
        count=[];original=source._rows
        def tracked(a,b):count.append((a,b));return original(a,b)
        source._rows=tracked
        if shared:assert source.prepare_working_states((71,47),workers=workers)
        one=source.working_state(71)[0]
        two=source.take_working_state(47)[0]
        snapshots.append((one,two));counts.append(sum(b-a for a,b in count))
        assert not np.shares_memory(one.halide,two.halide)
    assert counts[1]<counts[0]*.65
    assert counts[2]==counts[1]
    for actual in snapshots[1:]:
        for a,b in zip(snapshots[0],actual):
            for name in MaterialLatentSource.POOLS:
                left,right=getattr(a,name),getattr(b,name)
                if left is None:assert right is None
                else:np.testing.assert_array_equal(left,right)
    borrowed=snapshots[1][0].halide.copy();snapshots[1][1].halide.fill(0.)
    np.testing.assert_array_equal(borrowed,snapshots[1][0].halide)
    from film_foundry.core.execution_topology import execution_topology
    with execution_topology('reference'):assert not source.prepare_working_states((71,47))
    assert not source.prepare_working_states((71,71))
    assert not source.prepare_working_states((999,71))


def test_cpu_coordinate_noise_keeps_wrap_tiles_and_reference(monkeypatch):
    from film_foundry.core.antibanding import _coordinate_noise_region
    from film_foundry.core.execution_topology import execution_topology
    monkeypatch.setenv('FILM_FOUNDRY_COMPUTE','cpu')
    for h,w in ((7,3),(271,1025)):
        with execution_topology('reference'):before=_coordinate_noise_region(0,h,w,h)
        np.testing.assert_array_equal(before,_coordinate_noise_region(0,h,w,h))
        np.testing.assert_array_equal(before,np.concatenate([_coordinate_noise_region(a,b,w,h) for a,b in ((0,3),(3,h))]))
