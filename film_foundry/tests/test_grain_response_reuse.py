"""Same noise and output under reusable interpolation/filter preparation."""
import numpy as np
import pytest
from film_foundry.core.execution_topology import execution_topology
from film_foundry.core.film_process.local_grain import LocalGrainResponse


@pytest.mark.parametrize('model', ['multiscale_layer_density_v1','reduced_effective_scale_cloud_v2'])
def test_cached_dye_radius_and_weights_preserve_exact_output_and_rng(model):
    from film_foundry.core.density_grain import apply_density_grain
    from film_foundry.tests.test_local_formation_grain import local_config
    c=local_config();y,x=np.mgrid[:95,:137]
    density=np.broadcast_to((.05+.8*x/136)[...,None],(95,137,3)).copy().astype(np.float32)
    radius=np.stack((.7+x/137,.6+y/95,1.2+x*y/(95*137)),axis=-1).astype(np.float32)
    response=LocalGrainResponse('dye',np.full_like(density,.8),radius,1.2*radius)
    outputs=[];states=[]
    for topology in ('reference','optimized'):
        rng=np.random.default_rng(41)
        with execution_topology(topology):
            outputs.append(apply_density_grain(density,c.film,c.chemistry,rng=rng,model=model,
                                               local_response=response,work_long_edge=100))
        states.append(rng.bit_generator.state)
    np.testing.assert_array_equal(*outputs);assert states[0]==states[1]
    np.testing.assert_array_equal(response.grain_radius_factor,radius)


def test_silver_reuses_coordinate_realizations_and_preserves_halos(monkeypatch):
    import film_foundry.core.silver_grain as silver
    h,w=95,137;y,x=np.mgrid[:h,:w]
    gain=np.full((h,w),.1,np.float32);radius=(.001+.02*x/(w-1)).astype(np.float32)
    clump=(.2+.6*y/(h-1)).astype(np.float32)
    plan=silver.SilverGrainPlan((h,w),41,.1,.01,.2,tile_rows=17,local_parameters=(gain,radius,clump))
    counts=[0];original=silver._coordinate_unit_noise
    def counted(*args,**kwargs):counts[0]+=1;return original(*args,**kwargs)
    monkeypatch.setattr(silver,'_coordinate_unit_noise',counted)
    outputs=[];calls=[]
    for topology in ('reference','optimized'):
        counts[0]=0;out=np.full((h,w,3),.6,np.float32)
        with execution_topology(topology):silver.apply_metallic_silver_grain(out,out[...,0].copy(),plan)
        outputs.append(out);calls.append(counts[0])
    np.testing.assert_array_equal(*outputs)
    assert calls[0]==36 and calls[1]==12
    tiled=np.full((h,w,3),.6,np.float32)
    for start in range(0,h,11):
        silver.apply_metallic_silver_grain(tiled[start:start+11],np.full((min(11,h-start),w),.6,np.float32),plan,row_offset=start)
    np.testing.assert_array_equal(tiled,outputs[0])
