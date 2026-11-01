"""Seven-basis branch algebra, HDR, frozen provenance and independent editor."""
import copy
import itertools
import os
from dataclasses import replace

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
import numpy as np
import pytest

from film_foundry.contracts.spectral_exposure import make_smits_exposure_profile, SpectralExposureProfile
from film_foundry.core.spectral_exposure import map_layer_exposure, compile_spectral_exposure, exposure_sensitivity_matrix
from film_foundry.core.engine import expose_image_to_latent, develop_negative_from_latent
from film_foundry.core.latent_io import save_exposed_latent, load_exposed_latent, latent_identity_from_film
from film_foundry.model.config import DarkroomConfig


def test_all_six_orders_ties_negative_and_hdr_match_explicit_spectrum():
    profile = make_smits_exposure_profile()
    film = DarkroomConfig().film
    film.spectral_exposure_profile = profile
    cases = list(itertools.permutations((.2, .5, .9))) + [(0,0,0), (1,1,1), (2,2,2),
             (1,1,.2), (.2,1,1), (1,.2,1), (-2,.3,4), (4,2,1)]
    rgb = np.asarray(cases, dtype=np.float32).reshape(2, 7, 3)
    before = rgb.copy()
    expected = []
    basis = np.asarray(profile.rgb_basis)
    wave = np.asarray(profile.wavelengths_nm)
    for pixel in rgb.reshape(-1, 3):
        pixel = np.maximum(pixel, 0).astype(np.float64)
        order = np.argsort(pixel)
        lo, mid, hi = pixel[order]
        spectrum = lo * basis[0] + (mid-lo)*basis[1+order[0]] + (hi-mid)*basis[4+order[2]]
        rows = []
        for response, gain in zip(profile.layer_sensitivity, profile.layer_gain):
            integrand = spectrum * response
            rows.append(np.sum((integrand[:-1]+integrand[1:])*.5*np.diff(wave))*gain)
        expected.append(rows)
    np.testing.assert_allclose(map_layer_exposure(rgb, film), np.asarray(expected).reshape(rgb.shape), rtol=3e-7, atol=1e-7)
    np.testing.assert_array_equal(rgb, before)
    np.testing.assert_allclose(map_layer_exposure(rgb*4, film), map_layer_exposure(rgb, film)*4, rtol=0, atol=0)
    with pytest.raises(ValueError, match="piecewise"):
        exposure_sensitivity_matrix(film)
    assert np.asarray(compile_spectral_exposure(profile)).shape == (3,7)


def test_smits_frozen_pipeline_spatial_sources_and_share_roundtrip(tmp_path):
    from film_foundry.app.workflow import config_from_share_code, encode_recipe_share
    config = DarkroomConfig(enable_grain=False, enable_mtf=False)
    config.film.spectral_exposure_profile = make_smits_exposure_profile()
    config.film.emulsion_highlight_spread_strength = .2
    config.film.halation_return_model = "layer_selective"
    config.chemistry.development_adjacency_strength = .1
    before = copy.deepcopy(config)
    rgb = np.linspace(.01, 1., 18*24*3, dtype=np.float32).reshape(18,24,3)
    latent = expose_image_to_latent(rgb, config)
    path = tmp_path / "smits.darkroom_latent.npz"
    save_exposed_latent(path, latent, identity=latent_identity_from_film(config.film))
    loaded, _ = load_exposed_latent(path)
    assert loaded.spectral_exposure_profile == config.film.spectral_exposure_profile
    changed = copy.deepcopy(config)
    changed.film.spectral_exposure_profile = replace(config.film.spectral_exposure_profile,
        profile_id="later_unrelated_exposure", layer_gain=(2., 3., 4.))
    a = develop_negative_from_latent(loaded.copy(), config, rng=np.random.default_rng(19))
    b = develop_negative_from_latent(loaded.copy(), changed, rng=np.random.default_rng(19))
    np.testing.assert_array_equal(a.optical_density_rgb, b.optical_density_rgb)
    assert b.metadata['film_process_model']['spectral_exposure_profile'] == config.film.spectral_exposure_profile.as_dict()
    assert config.film.spectral_exposure_profile == before.film.spectral_exposure_profile
    assert config_from_share_code(encode_recipe_share(config)).film.spectral_exposure_profile == config.film.spectral_exposure_profile


def test_single_layer_and_model_basis_validation():
    config = DarkroomConfig()
    profile = make_smits_exposure_profile(1)
    config.film.spectral_exposure_profile = profile
    mapped = map_layer_exposure(np.ones((2,3,3), np.float32), config.film)
    np.testing.assert_allclose(mapped, 1, atol=1e-7)
    with pytest.raises(ValueError, match="7 spectra"):
        replace(profile, rgb_basis=profile.rgb_basis[:3])
    config.mode = "bw_negative"
    config.film.color_process = "bw"
    latent = expose_image_to_latent(np.full((4,6,3),.5,np.float32),config)
    assert latent.developability.shape == (4,6,1)
    assert latent.spectral_exposure_profile == profile


def test_independent_spectral_editor_keeps_cancelled_draft_and_validates_selected_series():
    from film_foundry.ui.qt import _application, QtWidgets
    from film_foundry.ui.spectral_editor import SpectralProfileDialog
    _application()
    source = make_smits_exposure_profile()
    dialog = SpectralProfileDialog(source)
    try:
        dialog.series.setCurrentIndex(7) # First sensitivity, after seven input bases.
        dialog.table.item(10,1).setText("-1")
        dialog.series.setCurrentIndex(1)
        dialog.apply()
        assert dialog.result() != QtWidgets.QDialog.DialogCode.Accepted
        assert dialog.error.text()
        assert dialog.profile is source
        dialog.series.setCurrentIndex(7)
        dialog.table.item(10,1).setText("0.123")
        dialog.apply()
        assert dialog.result() == QtWidgets.QDialog.DialogCode.Accepted
        assert dialog.profile.layer_sensitivity[0][10] == .123
        assert source.layer_sensitivity[0][10] != .123
    finally:
        dialog.close()
    material = SpectralProfileDialog(None, material=True)
    try:
        material.series.setCurrentIndex(6) # Clear support, independent of exposure.
        material.table.item(2,1).setText("0.42")
        material.apply()
        assert material.profile.component_absorbance[6][2] == .42
        assert len(material.profile.component_absorbance) == 9
    finally:
        material.close()


def test_material_editor_applies_spectral_dialog_to_its_working_copy(monkeypatch):
    from film_foundry.tools.run_film_material_editor import FilmMaterialEditor
    from film_foundry.ui.qt import _application, QtCore
    from film_foundry.ui.spectral_editor import SpectralProfileDialog
    _application()
    config = DarkroomConfig()
    editor = FilmMaterialEditor(config=config)
    original_exec = SpectralProfileDialog.exec
    def edit_and_accept(dialog):
        def apply():
            dialog.profile_id.setText("edited_smits_material")
            dialog.apply()
        QtCore.QTimer.singleShot(0, apply)
        return original_exec(dialog)
    monkeypatch.setattr(SpectralProfileDialog, "exec", edit_and_accept)
    try:
        editor._edit_spectral_profile()
        updated = editor._config_from_controls()
        assert updated.film.spectral_exposure_profile.profile_id == "edited_smits_material"
        assert updated.film.spectral_exposure_profile.model == make_smits_exposure_profile().model
        assert config.film.spectral_exposure_profile is None
        assert updated.chemistry == config.chemistry
    finally:
        editor.root.destroy()
