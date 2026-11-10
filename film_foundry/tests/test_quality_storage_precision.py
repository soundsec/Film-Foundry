"""Quality-linked auxiliary precision; authoritative material observation stays exact."""
from dataclasses import replace
import json
import numpy as np
import pytest
from film_foundry.core.storage_precision import pack_film_fields,round_float_fraction,film_precision_profile,film_precision_record


def test_fraction_rounding_matches_independent_numeric_oracle_and_retains_source():
    values=np.random.default_rng(9).uniform(-4,4,(79,113)).astype(np.float32)
    original=values.copy()
    for bits in (7,10,12,15,23):
        step=2.**-bits
        ties=np.array([0.,-0.,1.+step/2,1.+3*step/2,-1.-step/2,-1.-3*step/2],np.float32)
        for source in (values[:,::2],ties):
            mantissa,exponent=np.frexp(source.astype(np.float64))
            expected=np.ldexp(np.rint(np.ldexp(mantissa,bits+1)),exponent-bits-1).astype(np.float32)
            actual=round_float_fraction(source,bits)
            np.testing.assert_array_equal(actual,expected)
            np.testing.assert_array_equal(round_float_fraction(actual,bits),actual)
    np.testing.assert_array_equal(values,original)
    for values in (np.array([np.nan,1],np.float32),np.array([np.inf,1],np.float32),np.array([65504],np.float32)):
        assert round_float_fraction(values,7,'float16') is values


def test_quality_profiles_have_distinct_budgets_and_full_storage_overrides():
    density=np.random.default_rng(17).uniform(.01,3,(48,64,3)).astype(np.float32)
    fields={'density_cmy':density,'density_grain':density.copy(),'optical_density_rgb':density.copy(),
            'spectral_component_amounts':density.copy(),'process_surface_emulsion_texture':density[...,0]/3}
    errors=[]
    for quality,bits,dtype in [('draft',7,np.float16),('standard',10,np.float16),('high',15,np.float32)]:
        packed,changed=pack_film_fields(fields,quality=quality)
        assert packed['density_cmy'].dtype==dtype
        assert film_precision_profile(quality)['cold_fraction_bits']==bits
        assert packed['optical_density_rgb'] is fields['optical_density_rgb']
        assert packed['spectral_component_amounts'] is fields['spectral_component_amounts']
        errors.append(np.max(np.abs(packed['density_cmy'].astype(np.float32)-density)))
        assert 'density_cmy' in changed
        full,changes=pack_film_fields(fields,'full',quality=quality)
        assert not changes and all(full[key] is value for key,value in fields.items())
    assert 0<errors[2]<errors[1]<errors[0]
    previous=film_precision_record({'density_cmy':'float16_fraction7'},'draft','auto')
    restored=film_precision_record({},'high','full',previous)
    assert restored['source_quantized'] and restored['cold_fraction_bits']==23
    assert film_precision_profile('FULL')['cold_fraction_bits']==15


@pytest.mark.parametrize('quality',('draft','standard','high'))
def test_quality_is_frozen_from_formation_and_scan_is_exact_after_export(tmp_path,quality):
    from film_foundry.app.diagnostic_targets import diagnostic_target
    from film_foundry.model.config import DarkroomConfig
    from film_foundry.core.engine import develop_negative,scan_medium_output
    from film_foundry.core.medium_export import _save_full_developed_medium_materials
    from film_foundry.core.negative_io import load_developed_negative_npz
    config=DarkroomConfig();config.processing.quality_mode=quality
    config.output.save_scanner_raw=False;config.output.export_transparent_plate=False
    medium=develop_negative(diagnostic_target('grayscale',long_edge=96),config,rng=np.random.default_rng(8))
    source=medium.density_cmy.copy();before=scan_medium_output(medium,config).output_srgb
    # A later scan request does not retrospectively change formation quality.
    request=replace(config,processing=replace(config.processing,quality_mode='high'))
    path=tmp_path/'auto.npz'
    _save_full_developed_medium_materials(tmp_path/'source.png',tmp_path/'out.png',medium,request,8,developed_path=path)
    with np.load(path) as archive:
        record=json.loads(str(archive['developed_medium_metadata'].item()))['storage_precision']
        assert record['quality']==quality and record['cold_fraction_bits']==film_precision_profile(quality)['cold_fraction_bits']
    loaded=load_developed_negative_npz(path)
    np.testing.assert_array_equal(scan_medium_output(loaded,config).output_srgb,before)
    np.testing.assert_array_equal(medium.density_cmy,source)


def test_shared_ui_description_distinguishes_high_and_full():
    from film_foundry.ui.precision_labels import film_storage_label
    assert '15' in film_storage_label('high','auto')
    assert '15' not in film_storage_label('high','full')
