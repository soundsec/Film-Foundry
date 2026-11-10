"""Equivalent geometry and rotary integration reuse at full resolution."""
import numpy as np
from film_foundry.core.execution_topology import execution_topology
from film_foundry.core.emulsion_shear import shift_rows
from film_foundry.core.row_transport import RowShiftPlan
from film_foundry.core.rotary_contact import _rotate,contact_duration
from film_foundry.tests.test_rotary_instability import event


def test_shared_shift_plan_matches_original_fields_and_row_partitions():
    rng=np.random.default_rng(71);shape=(31,107)
    pixels=np.zeros(31);pixels[9:19]=[-140.,-107.,-21.4,-1.,-.23,.18,1.,3.7,107.,140.]
    plan=RowShiftPlan(shape,pixels)
    for channels in ((),(1,),(3,)):
        field=rng.uniform(0,3,(*shape,*channels)).astype(np.float32)
        expected=shift_rows(field,pixels);actual=plan(field)
        np.testing.assert_array_equal(actual,expected)
        split=np.concatenate([RowShiftPlan((b-a,shape[1]),pixels[a:b])(field[a:b]) for a,b in ((0,12),(12,31))])
        np.testing.assert_array_equal(actual,split)
        np.testing.assert_array_equal(actual[:9],field[:9])


def test_rotary_fast_cycles_preserve_dose_history_and_exact_contact():
    rng=np.random.default_rng(5);phase=rng.random((13,29));phase[0,:4]=[0.,.2,.5,np.nextafter(1.,0.)]
    activity=rng.random(phase.shape)
    for fraction,rpm,seconds in ((.4,12.,10.),(.4,12.,98.5),(.2,17.,130.),(.5,120.,.9),(.9,.01,12500.)):
        with execution_topology('reference'):expected=_rotate(phase,activity,seconds,fraction,rpm)
        actual=_rotate(phase,activity,seconds,fraction,rpm)
        for a,b in zip(actual,expected):np.testing.assert_array_equal(a,b)
    for e in (event(rotation_instability=.75,rotation_seed=7,pause_s=90.,pause_at_fraction=.35),
              event(rotation_rpm=0.,rotation_instability=.8),event(rotation_instability=0.)):
        for window in (None,(.125,.25),(.75,1.)):
            with execution_topology('reference'):expected=contact_duration((83,131),0,83,480.,e,window)
            np.testing.assert_array_equal(contact_duration((83,131),0,83,480.,e,window),expected)


def test_tiled_audit_counts_untouched_area_and_normalized_mask_once():
    from copy import deepcopy
    from film_foundry.tests.test_emulsion_shear import config,SHEAR
    from film_foundry.core.engine import develop_negative
    c=config({**SHEAR,'before_bath':1,'band_width_mm':1.})
    c.enable_grain=False;c.enable_halation=False;c.enable_mtf=False
    c.chemistry.process_variation=0.
    c.processing.material_tile_rows=0
    image=np.full((31,107,3),.35,np.float32)
    whole=develop_negative(image,c,rng=np.random.default_rng(7))
    tiled=deepcopy(c);tiled.processing.material_tile_rows=4;tiled.processing.material_tile_threshold_megapixels=.0001
    split=develop_negative(image,tiled,rng=np.random.default_rng(7))
    np.testing.assert_array_equal(whole.optical_density_rgb,split.optical_density_rgb)
    a=whole.metadata['film_process_model']['pool_totals'];b=split.metadata['film_process_model']['pool_totals']
    assert a['masking_coupler_remaining']==b['masking_coupler_remaining']
    np.testing.assert_allclose(a['emulsion_retained_area'],b['emulsion_retained_area'],atol=1e-8,rtol=0.)
