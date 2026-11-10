from copy import deepcopy
import threading
import zlib
import numpy as np
import pytest
from film_foundry.core import engine, material_parallel, parallel_deflate, scan_runtime
from film_foundry.model.config import DarkroomConfig


def test_adaptive_deflate_preserves_payload_and_avoids_poor_fit(monkeypatch):
    monkeypatch.delenv('FILM_FOUNDRY_ADAPTIVE_DEFLATE', raising=False)
    random = np.random.default_rng(5).uniform(.3,.9,262144).astype(np.float32).tobytes()
    constant = bytes(len(random))
    assert parallel_deflate._strategy(random,1) == zlib.Z_HUFFMAN_ONLY
    assert parallel_deflate._strategy(constant,1) == zlib.Z_DEFAULT_STRATEGY
    for value in (random, constant, random+constant):
        encoded=parallel_deflate._block(value,1)+zlib.compressobj(1,zlib.DEFLATED,-15).flush()
        assert zlib.decompress(encoded,-15)==value
    monkeypatch.setenv('FILM_FOUNDRY_ADAPTIVE_DEFLATE','0')
    assert parallel_deflate._strategy(random,1)==zlib.Z_DEFAULT_STRATEGY


@pytest.mark.parametrize('positive', [False, True])
def test_parallel_scan_preserves_global_calibration_and_readonly_medium(monkeypatch, positive):
    monkeypatch.setenv('FILM_FOUNDRY_COMPUTE','cpu')
    monkeypatch.setenv('FILM_FOUNDRY_MATERIAL_THREADS','2')
    monkeypatch.setattr(material_parallel,'PARALLEL_MIN_PIXELS',1)
    config=DarkroomConfig()
    medium=engine.develop_negative(np.linspace(.01,.99,31*43*3,dtype=np.float32).reshape(31,43,3),config)
    config=deepcopy(medium.metadata['runtime_config'])
    config.scanner.scan_normalize=True; config.scanner.scan_normalize_mode='rgb'
    config.scanner.include_clear_base_border=True
    before=medium.optical_density_rgb.copy(); medium.optical_density_rgb.setflags(write=False)
    run=scan_runtime._scan_positive_output_tiled if positive else scan_runtime._scan_negative_output_tiled
    config.processing.native_thread_limit=1
    expected=run(medium,config,5)
    threads=set(); original=scan_runtime.capture_optical_density
    def capture(*args,**kwargs):
        threads.add(threading.current_thread().ident)
        return original(*args,**kwargs)
    monkeypatch.setattr(scan_runtime,'capture_optical_density',capture)
    config.processing.native_thread_limit=2
    actual=run(medium,config,5)
    assert any(value!=threading.current_thread().ident for value in threads)
    np.testing.assert_array_equal(actual,expected)
    np.testing.assert_array_equal(medium.optical_density_rgb,before)
    threads.clear(); config.processing.memory_budget_mb=1024
    np.testing.assert_array_equal(run(medium,config,5),expected)
    assert threads=={threading.current_thread().ident}


@pytest.mark.parametrize('color', [False, True])
def test_development_scratch_preserves_all_pools_history_and_reports(monkeypatch, color):
    from film_foundry.core.execution_topology import execution_topology
    from film_foundry.core.film_process.operators import _develop_with_history, CompatibilityProfile
    from film_foundry.tests.test_gpu_development_conversion import fixture
    monkeypatch.setenv('FILM_FOUNDRY_COMPUTE','cpu')
    original,step,local=fixture((31,27,3))
    original.coupler[::3]=0.
    results=[];reports=[]
    for topology in ('reference','optimized'):
        state=original.copy()
        with execution_topology(topology):
            reports.append([_develop_with_history(state,step,CompatibilityProfile(),color,local) for _ in range(2)])
        results.append(state)
    for name in ('halide','developability','metallic_silver','coupler','dye'):
        np.testing.assert_array_equal(getattr(results[0],name),getattr(results[1],name))
    for key in results[0].formation_history:
        np.testing.assert_array_equal(results[0].formation_history[key],results[1].formation_history[key])
    assert results[0].formation_created==results[1].formation_created
    assert reports[0]==reports[1]


def test_deduplicated_medium_validation_still_rejects_invalid_stages(monkeypatch):
    monkeypatch.setenv('FILM_FOUNDRY_COMPUTE','cpu')
    from film_foundry.core.runtime_validate import _validate_developed_medium_state
    medium=engine.develop_negative(np.full((5,7,3),.4,np.float32),DarkroomConfig())
    _validate_developed_medium_state(medium)
    for name in ('linear_input','after_mtf','after_halation','density_cmy','density_grain','optical_density_rgb'):
        invalid=deepcopy(medium)
        value=getattr(invalid,name).copy(); value[0,0,0]=np.nan;setattr(invalid,name,value)
        with pytest.raises(ValueError,match='finite'):_validate_developed_medium_state(invalid)


@pytest.mark.parametrize('plates', [False, True])
def test_file_export_keeps_history_only_for_stage_consumers(tmp_path, monkeypatch, plates):
    from PIL import Image
    from film_foundry.core.negative_io import load_developed_negative_npz
    monkeypatch.setenv('FILM_FOUNDRY_COMPUTE','cpu')
    source=tmp_path/'source.png';Image.fromarray(np.full((11,17,3),110,np.uint8)).save(source)
    config=DarkroomConfig();config.output.export_plate_set=plates
    config.output.save_scanner_raw=False;config.output.export_transparent_plate=False
    captured=[];original=engine._save_full_developed_medium_materials
    def save(input_path, output_path, medium, runtime, *args, **kwargs):
        history=[getattr(medium,key) for key in ('linear_input','after_mtf','after_halation')]
        assert runtime.processing.history_storage_policy!='discard'
        if plates:assert all(a.shape==(11,17,3) and a.dtype==np.float32 for a in history)
        else:assert all(a.shape==(0,0,3) for a in history)
        captured.append(medium.optical_density_rgb.copy())
        return original(input_path,output_path,medium,runtime,*args,**kwargs)
    monkeypatch.setattr(engine,'_save_full_developed_medium_materials',save)
    engine.process_file(source,tmp_path/'render.png',config)
    saved=load_developed_negative_npz(next((tmp_path/'negatives').glob('*.npz')))
    np.testing.assert_array_equal(saved.optical_density_rgb,captured[0])
    assert config.processing.history_storage_policy=='full'
