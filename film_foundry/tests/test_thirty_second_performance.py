import numpy as np
from film_foundry.core.execution_topology import execution_topology


def test_batched_spatial_interpolation_preserves_reference_and_partitioning():
    from film_foundry.core.spatial_fields import GlobalFieldGrid, GlobalRateMultiplierField
    rng = np.random.default_rng(19)
    for full, work in (((193,257),(37,51)), ((117,53),(389,179)), ((193,71),(1,1))):
        values = rng.uniform(.2,1.8,work).astype(np.float32)
        field = GlobalRateMultiplierField(values,GlobalFieldGrid(full,work))
        with execution_topology('reference'):expected = field.slice_rows(0,full[0])
        np.testing.assert_array_equal(field.slice_rows(0,full[0]),expected)
        actual = np.concatenate([field.slice_rows(a,min(a+17,full[0])) for a in range(0,full[0],17)])
        np.testing.assert_array_equal(actual,expected)
        assert field.slice_rows(0,0).shape==(0,full[1])
        assert not values.flags.writeable


def test_border_only_drawing_keeps_interior_quantization_and_clipped_text():
    from film_foundry.core.provenance import apply_scanner_raw_border_watermark
    rng = np.random.default_rng(29)
    for shape,border in (((13,17,3),0),((13,17,3),9),((19,173,3),11),((257,503,3),22),((257,503,3),300)):
        values = rng.uniform(-.1,1.1,shape).astype(np.float32)
        values.setflags(write=False)
        with execution_topology('reference'):
            expected = apply_scanner_raw_border_watermark(values,{'id':'ggJY19'},border_width=border)
        actual = apply_scanner_raw_border_watermark(values,{'id':'ggJY19'},border_width=border)
        np.testing.assert_array_equal(actual,expected)
        assert not np.shares_memory(actual,values)


def test_parallel_silver_grain_preserves_coordinate_identity_and_local_response(monkeypatch):
    from film_foundry.core import material_parallel
    from film_foundry.core.silver_grain import SilverGrainPlan, apply_metallic_silver_grain
    monkeypatch.setattr(material_parallel,'PARALLEL_MIN_PIXELS',1)
    rng=np.random.default_rng(71);shape=(71,103)
    silver=rng.uniform(.0,.8,shape).astype(np.float32)
    source=rng.uniform(.2,1.2,(*shape,3)).astype(np.float32)
    local=(rng.uniform(.05,.15,shape).astype(np.float32),
           rng.uniform(.001,.02,shape).astype(np.float32),
           rng.uniform(.2,.8,shape).astype(np.float32))
    for parameters in (None,local):
        plan=SilverGrainPlan(shape,17,.1,.008,.3,tile_rows=11,local_parameters=parameters)
        before=source.copy();after=source.copy()
        apply_metallic_silver_grain(before,silver,plan,workers=1)
        apply_metallic_silver_grain(after,silver,plan,workers=4)
        np.testing.assert_array_equal(after,before)
