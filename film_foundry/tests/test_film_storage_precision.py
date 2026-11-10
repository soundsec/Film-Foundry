from pathlib import Path
from dataclasses import replace
import numpy as np
import pytest
from film_foundry.model.config import DarkroomConfig
from film_foundry.app.workflow import film_preset_path
from film_foundry.app.diagnostic_targets import diagnostic_target
from film_foundry.core.engine import develop_negative,scan_medium_output
from film_foundry.core.negative_io import load_developed_medium_for_scan,load_developed_negative_npz
from film_foundry.core.medium_export import _save_full_developed_medium_materials


@pytest.mark.parametrize('name',('clear_modern_negative','color_reversal_transparency','monochrome_fine_negative'))
def test_film_auto_storage_preserves_authoritative_scan_and_full_optout(tmp_path,name):
    config=DarkroomConfig.from_json(film_preset_path(name))
    config.output.save_scanner_raw=False;config.output.export_transparent_plate=False
    medium=develop_negative(diagnostic_target('color24',long_edge=96),config,rng=np.random.default_rng(13))
    before=scan_medium_output(medium,config).output_srgb
    for policy in ('auto','full'):
        config.output.material_storage_precision=policy;target=tmp_path/(policy+'.npz')
        _save_full_developed_medium_materials(tmp_path/'source.png',tmp_path/'result.png',medium,config,13,developed_path=target)
        with np.load(target) as data:
            assert data['density_cmy'].dtype==(np.float16 if policy=='auto' else np.float32)
            np.testing.assert_array_equal(data['optical_density_rgb'],medium.optical_density_rgb)
            if policy=='full':np.testing.assert_array_equal(data['density_cmy'],medium.density_cmy)
        for restored in (load_developed_negative_npz(target),load_developed_medium_for_scan(target)):
            np.testing.assert_array_equal(scan_medium_output(restored,config).output_srgb,before)


def test_legacy_and_out_of_range_cold_fields_stay_full_and_binary_masks_exact():
    from film_foundry.core.storage_precision import pack_film_fields
    source={'density_cmy':np.full((3,4,3),.3,np.float32),'density_grain':np.full((3,4,3),70000,np.float32),
            'spectral_emulsion_coverage':np.array([[0,1],[1,0]],np.float32)}
    packed,_=pack_film_fields(source)
    assert packed['density_cmy'] is source['density_cmy']
    source['optical_density_rgb']=np.ones((3,4,3),np.float32)
    packed,_=pack_film_fields(source)
    assert packed['density_grain'] is source['density_grain']
    assert packed['spectral_emulsion_coverage'].dtype==np.uint8
    np.testing.assert_array_equal(packed['spectral_emulsion_coverage'],source['spectral_emulsion_coverage'])
