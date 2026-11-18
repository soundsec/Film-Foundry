"""Numerical and ownership boundaries for shared seven-family optimizations."""
from dataclasses import replace
import gc
import numpy as np
import pytest
from film_foundry.core.execution_topology import execution_topology
from film_foundry.core.material_arrays import freeze_array,array_bounds
from film_foundry.core.material_family import frozen_fields
from film_foundry.tests.test_material_recipes import formed


def test_range_reuse_never_trusts_mutable_or_reinterpreted_storage():
    mutable=np.array([0.,1.,2.],np.float32)
    view=mutable.view();view.setflags(write=False)
    assert array_bounds(view)==(0.,2.)
    mutable[1]=-4.;assert array_bounds(view)==(-4.,2.)
    fields=frozen_fields({'a':mutable.reshape(1,3),'b':mutable.reshape(1,3)})
    mutable[:]=50
    assert array_bounds(fields['a'])==(-4.,2.)
    with pytest.raises(ValueError):fields['a'].setflags(write=True)
    detached=freeze_array(np.array([0.,1.,2.],np.float32));assert array_bounds(detached)==(0.,2.)
    detached.dtype=np.int32
    assert array_bounds(detached)==(0.,float(2**30))
    with pytest.raises(ValueError):frozen_fields({'bad':np.array([[float('nan')]],np.float32)})
    # Scalar metadata must not keep a multi-megapixel image alive.
    import weakref
    probe=freeze_array(np.ones((2,3),np.float32));ref=weakref.ref(probe);array_bounds(probe)
    del probe;gc.collect();assert ref() is None


@pytest.mark.parametrize('kind',['peel','integral','plate','paper','wet','cyanotype','platinum'])
def test_reference_and_reused_material_states_and_observations_match(kind):
    with execution_topology('reference'):before=formed(kind)
    with execution_topology('optimized'):after=formed(kind)
    assert before.state.stage==after.state.stage and before.recipe()==after.recipe()
    for key,value in before.state.fields.items():
        np.testing.assert_array_equal(value,after.state.fields[key],err_msg=key)
    for index in range(len(before.group.bodies)):
        with execution_topology('reference'):expected=before.preview(body_index=index)
        np.testing.assert_array_equal(expected,after.preview(body_index=index))
    # State construction must still check new/corrupted pools, even after a
    # previous version of these fields has filled the scalar range cache.
    fields=dict(after.state.fields);key=next(iter(fields))
    fields[key]=np.full_like(fields[key],np.nan)
    with pytest.raises(ValueError):replace(after.state,fields=fields)


def test_sparse_illuminated_transfer_keeps_dense_fp64_result():
    from film_foundry.core.transfer_inventory import _illuminated_transfer
    rng=np.random.default_rng(47);shape=(17,19,3)
    remaining=rng.uniform(0.,1.,shape);substrate=remaining+rng.uniform(.1,1.,shape)
    developer=rng.uniform(.2,1.,shape);consumed=np.minimum(substrate,developer)*.6
    rate=rng.uniform(.01,.1,shape);light=rng.uniform(.001,.03,shape)
    b=rng.uniform(.3,1.,(17,19,1))
    for sparse in (False,True):
        if sparse:consumed[:13]=0.;remaining[-2:]=0.
        for acid in (np.zeros_like(b),np.full_like(b,.002)):
            with execution_topology('reference'):expected=_illuminated_transfer(remaining,substrate,developer,consumed,rate,light,b,acid)
            np.testing.assert_array_equal(_illuminated_transfer(remaining,substrate,developer,consumed,rate,light,b,acid),expected)
