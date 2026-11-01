import numpy as np

from film_foundry.core.engine import develop_negative, scan_medium_direct, scan_medium_output, scan_negative
from film_foundry.core.scanner import scanner_light_exposure_record, transmission_illuminant_rgb
from film_foundry.model.config import DarkroomConfig, ScannerConfig


def test_exposure_record_and_illuminant_share_explicit_and_legacy_controls():
    scanner = ScannerConfig()
    scanner.scanner_light_color = (1., 1., 1.)
    scanner.negative_backlight_ev = -.5
    scanner.light_table_ev = .5
    scanner.transmission_light_temperature_k = 6500.
    scanner.transmission_light_ev = 0.
    neutral = transmission_illuminant_rgb(scanner)
    for ev in (None, -1., 1.):
        scanner.transmission_light_ev = ev
        for invert, interpreter in ((True, 'negative_scan'), (False, 'transmission_scan'), (True, 'positive_transparency_scan')):
            scanner.invert_transmission = invert
            scanner.interpreter_key = interpreter
            expected = ev if ev is not None else (-.5 if invert and interpreter != 'positive_transparency_scan' else .5)
            record = scanner_light_exposure_record(scanner)
            assert record['stage'] == 'transmission_capture'
            assert record['lamp_ev'] == record['effective_ev'] == record['calibration_ev'] == expected
            np.testing.assert_allclose(transmission_illuminant_rgb(scanner), neutral * 2**expected, rtol=1e-6)


def test_scanning_entries_record_the_actual_runtime_exposure_without_writing_medium():
    cfg = DarkroomConfig(enable_mtf=False, enable_halation=False, enable_grain=False)
    cfg.scanner.transmission_light_ev = -.5
    medium = develop_negative(np.linspace(.02, .9, 45, dtype=np.float32).reshape(3, 5, 3), cfg)
    original = medium.optical_density_rgb.copy()
    outputs = [scan_negative(medium, cfg)]
    for mode in ('auto', 'manual', 'negative', 'positive', 'direct'):
        outputs += [scan_medium_direct(medium, cfg, mode), scan_medium_output(medium, cfg, mode)]
    for result in outputs:
        assert result.metadata['scanner_light_exposure'] == scanner_light_exposure_record(result.metadata['runtime_config'].scanner)
        assert result.metadata['scanner_light_exposure']['lamp_ev'] == -.5
    np.testing.assert_array_equal(original, medium.optical_density_rgb)
