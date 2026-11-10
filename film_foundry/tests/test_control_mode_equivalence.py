"""Both presentations must execute identical explicit material operations.

This does not assert that historical effect sliders already compile to those
operations: that translation is still absent for several incident controls.
"""
from copy import deepcopy
from dataclasses import asdict
import numpy as np
import pytest

from film_foundry.model.config import DarkroomConfig
from film_foundry.core.process_controls import condition_driven_config
from film_foundry.core.engine import develop_negative
from film_foundry.core.scan_runtime import scan_medium_output
from film_foundry.contracts.process_events import EVENT_MODEL_V4
from film_foundry.contracts.bath_solution import MODEL_V2,validate_solution


@pytest.mark.parametrize('case',('baseline','incidents','coupled'))
def test_explicit_process_has_identical_material_and_observation_in_both_modes(case):
    cfg=DarkroomConfig(random_seed=419,seed_strategy='fixed')
    cfg.chemistry.incident_identity_seed=19
    if case=='incidents':
        cfg.chemistry.process_steps=({'action':'develop_color'},{'action':'bleach_silver'},{'action':'fix_halide'})
        cfg.chemistry.process_events={'model':EVENT_MODEL_V4,'events':[
            {'kind':'bath_contact','bath':1,'fill_s':300.},
            {'kind':'contaminate','concentration_mg_l':1600.,'radius_mm':8.},
            {'kind':'wash','duration_s':35.,'water_changes':1.},
            {'kind':'temperature','temperature_c':60.,'transfer_s':0.}]}
    elif case=='coupled':
        cfg.chemistry.process_steps=({'action':'develop_color'}, {'action':'bleach_silver','solution':validate_solution(
            {'model':MODEL_V2,'action':'bleach_silver','secondary':{'action':'fix_halide'}},'bleach_silver')})
    principle=condition_driven_config(cfg);expected=deepcopy(principle);expected.process_control_mode='expected'
    before=asdict(expected)
    image=np.linspace(.03,.95,24*36*3,dtype=np.float32).reshape(24,36,3)
    a=develop_negative(image,expected);b=develop_negative(image,principle)
    assert asdict(expected)==before
    np.testing.assert_array_equal(a.optical_density_rgb,b.optical_density_rgb)
    frozen=a.optical_density_rgb.copy()
    np.testing.assert_array_equal(scan_medium_output(a,expected).output_srgb,scan_medium_output(b,principle).output_srgb)
    np.testing.assert_array_equal(a.optical_density_rgb,frozen)
