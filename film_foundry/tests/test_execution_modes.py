import json
import zipfile

import numpy as np
from PIL import Image
import film_foundry.core.engine as engine_module

from film_foundry.core.density_grain import (
    FULL_DENSITY_GRAIN_MODEL,
    REDUCED_DENSITY_GRAIN_MODEL,
)
from film_foundry.core.engine import develop_negative, process_file
from film_foundry.core.execution import (
    processing_long_edge,
    resolve_execution_axes,
    resolve_execution_mode,
    uses_reduced_implementation,
)
from film_foundry.core.implementation_plan import (
    BUILTIN_REDUCED_IMPLEMENTATION_PROFILE,
    FULL_IMPLEMENTATION_PROFILE,
    resolve_implementation_plan,
)
from film_foundry.core.halation import (
    FULL_HALATION_SOURCE_MODEL,
    REDUCED_HALATION_SOURCE_MODEL,
)
from film_foundry.core.mtf import (
    FULL_EMULSION_MTF_MODEL,
    REDUCED_EMULSION_MTF_MODEL,
)
from film_foundry.core.negative_io import load_developed_medium_for_scan
from film_foundry.model.config import DarkroomConfig


def test_scaling_reduction_and_quality_resolve_as_orthogonal_axes():
    config = DarkroomConfig()
    config.output.render_long_edge = 4200
    config.output.preview_long_edge = 1400

    config.processing.execution_mode = "quality"
    assert resolve_execution_mode(config) == "quality"
    assert processing_long_edge(config) == 4200
    assert uses_reduced_implementation(config) is False
    assert resolve_execution_axes(config).quality_mode == "standard"

    config.processing.execution_mode = "scaled_fast"
    assert resolve_execution_mode(config) == "scaled_fast"
    assert processing_long_edge(config) == 1400
    assert uses_reduced_implementation(config) is False

    config.processing.execution_mode = "reduced_fast"
    assert resolve_execution_mode(config) == "reduced_fast"
    assert processing_long_edge(config) == 4200
    assert uses_reduced_implementation(config) is True

    config.processing.execution_mode = "scaled_fast"
    config.fast_mode = True
    config.processing.quality_mode = "high"
    axes = resolve_execution_axes(config)
    assert axes.scaling_enabled is True
    assert axes.lower_order_enabled is True
    assert axes.quality_mode == "high"
    assert axes.label == "scaled_reduced_fast"
    assert resolve_execution_mode(config) == "scaled_reduced_fast"
    assert processing_long_edge(config) == 1400


def test_all_scaling_reduction_and_quality_combinations_remain_representable():
    for scaling_enabled in (False, True):
        for lower_order_enabled in (False, True):
            for quality_mode in ("draft", "standard", "high"):
                config = DarkroomConfig()
                config.processing.execution_mode = (
                    "scaled_fast" if scaling_enabled else "quality"
                )
                config.fast_mode = lower_order_enabled
                config.processing.quality_mode = quality_mode

                axes = resolve_execution_axes(config)
                plan = resolve_implementation_plan(config)

                assert axes.scaling_enabled is scaling_enabled
                assert axes.lower_order_enabled is lower_order_enabled
                assert axes.quality_mode == quality_mode
                assert plan.scaling_enabled is scaling_enabled
                assert plan.lower_order_enabled is lower_order_enabled
                assert plan.emulsion_mtf_model == (
                    REDUCED_EMULSION_MTF_MODEL
                    if lower_order_enabled
                    else FULL_EMULSION_MTF_MODEL
                )
                assert plan.quality_mode == quality_mode
                assert plan.emulsion_grain_model == (
                    REDUCED_DENSITY_GRAIN_MODEL
                    if lower_order_enabled
                    else FULL_DENSITY_GRAIN_MODEL
                )
                assert plan.halation_source_model == (
                    REDUCED_HALATION_SOURCE_MODEL
                    if lower_order_enabled
                    else FULL_HALATION_SOURCE_MODEL
                )
def test_legacy_fast_config_migrates_to_reduced_fast():
    config = DarkroomConfig.from_dict({"fast_mode": True})
    assert config.processing.execution_mode == "reduced_fast"
    assert resolve_execution_mode(config) == "reduced_fast"

    combined = DarkroomConfig.from_dict(
        {
            "fast_mode": True,
            "processing": {
                "execution_mode": "scaled_fast",
                "quality_mode": "high",
            },
        }
    )
    axes = resolve_execution_axes(combined)
    assert axes.scaling_enabled is True
    assert axes.lower_order_enabled is True
    assert axes.quality_mode == "high"


def test_preview_long_edge_override_combines_with_reduced_fast():
    config = DarkroomConfig()
    config.fast_mode = True
    config.processing.execution_mode = "reduced_fast"
    config.output.render_long_edge = None
    config.output.preview_long_edge = 1600

    assert resolve_execution_mode(config) == "reduced_fast"
    assert (
        resolve_execution_mode(config, scaled_override=True)
        == "scaled_reduced_fast"
    )
    assert processing_long_edge(config, scaled_override=True) == 1600


def test_implementation_plan_only_allows_lower_order_models_in_reduced_fast():
    config = DarkroomConfig()
    resolved = {}
    for mode in ("quality", "scaled_fast", "reduced_fast"):
        config.processing.execution_mode = mode
        config.fast_mode = False
        resolved[mode] = resolve_implementation_plan(config)

    assert resolved["quality"].profile_id == FULL_IMPLEMENTATION_PROFILE
    assert resolved["scaled_fast"].profile_id == FULL_IMPLEMENTATION_PROFILE
    assert resolved["quality"].lower_order_enabled is False
    assert resolved["scaled_fast"].lower_order_enabled is False
    assert resolved["quality"].emulsion_grain_model == FULL_DENSITY_GRAIN_MODEL
    assert resolved["scaled_fast"].emulsion_grain_model == FULL_DENSITY_GRAIN_MODEL
    assert resolved["quality"].emulsion_mtf_model == FULL_EMULSION_MTF_MODEL
    assert resolved["scaled_fast"].emulsion_mtf_model == FULL_EMULSION_MTF_MODEL
    assert resolved["quality"].halation_source_model == FULL_HALATION_SOURCE_MODEL
    assert (
        resolved["scaled_fast"].halation_source_model
        == FULL_HALATION_SOURCE_MODEL
    )
    assert (
        resolved["reduced_fast"].profile_id
        == BUILTIN_REDUCED_IMPLEMENTATION_PROFILE
    )
    assert resolved["reduced_fast"].lower_order_enabled is True
    assert (
        resolved["reduced_fast"].emulsion_grain_model
        == REDUCED_DENSITY_GRAIN_MODEL
    )
    assert (
        resolved["reduced_fast"].emulsion_mtf_model
        == REDUCED_EMULSION_MTF_MODEL
    )
    assert (
        resolved["reduced_fast"].halation_source_model
        == REDUCED_HALATION_SOURCE_MODEL
    )

    config.processing.execution_mode = "scaled_fast"
    config.fast_mode = True
    combined = resolve_implementation_plan(config)
    assert combined.execution_mode == "scaled_reduced_fast"
    assert combined.scaling_enabled is True
    assert combined.lower_order_enabled is True
    assert combined.emulsion_grain_model == REDUCED_DENSITY_GRAIN_MODEL
    assert combined.emulsion_mtf_model == REDUCED_EMULSION_MTF_MODEL
    assert combined.halation_source_model == REDUCED_HALATION_SOURCE_MODEL


def test_scaled_override_preserves_lower_order_family_on_reusable_fast_config():
    config = DarkroomConfig()
    config.processing.execution_mode = "reduced_fast"
    config.fast_mode = True

    reduced = resolve_implementation_plan(config)
    scaled = resolve_implementation_plan(config, scaled_override=True)

    assert reduced.lower_order_enabled is True
    assert scaled.execution_mode == "scaled_reduced_fast"
    assert scaled.scaling_enabled is True
    assert scaled.profile_id == BUILTIN_REDUCED_IMPLEMENTATION_PROFILE
    assert scaled.lower_order_enabled is True
    assert scaled.emulsion_grain_model == REDUCED_DENSITY_GRAIN_MODEL
    assert scaled.emulsion_mtf_model == REDUCED_EMULSION_MTF_MODEL
    assert scaled.halation_source_model == REDUCED_HALATION_SOURCE_MODEL


def test_only_reduced_fast_selects_lower_order_component_models():
    image = np.linspace(0.05, 0.95, 32 * 48 * 3, dtype=np.float32).reshape(
        32,
        48,
        3,
    )
    resolved_models = {}

    cases = (
        ("quality", False, "quality", FULL_DENSITY_GRAIN_MODEL),
        ("scaled_fast", False, "scaled_fast", FULL_DENSITY_GRAIN_MODEL),
        ("reduced_fast", True, "reduced_fast", REDUCED_DENSITY_GRAIN_MODEL),
        (
            "scaled_fast",
            True,
            "scaled_reduced_fast",
            REDUCED_DENSITY_GRAIN_MODEL,
        ),
    )
    for mode, fast_mode, expected_label, expected_model in cases:
        config = DarkroomConfig()
        config.processing.execution_mode = mode
        config.fast_mode = fast_mode
        config.output.preview_long_edge = 24
        config.enable_grain = True
        config.enable_halation = False
        config.enable_mtf = False
        medium = develop_negative(
            image,
            config,
            rng=np.random.default_rng(20260811),
        )
        contract = medium.metadata["film_process_model"]["emulsion_grain_formation"]
        halation_contract = medium.metadata["film_process_model"][
            "halation_formation"
        ]
        plan_contract = medium.metadata["film_process_model"][
            "execution_implementation"
        ]
        resolved_models[expected_label] = contract["model"]

        assert medium.optical_density_rgb.shape == image.shape
        assert medium.density_grain.shape == image.shape
        assert contract["execution_mode"] == expected_label
        assert contract["preserves_requested_output_shape"] is True
        assert contract["scanner_owned"] is False
        assert plan_contract["execution_mode"] == expected_label
        assert plan_contract["scaling_enabled"] is ("scaled" in expected_label)
        assert plan_contract["lower_order_enabled"] is fast_mode
        assert plan_contract["component_models"]["emulsion_grain"] == expected_model
        assert plan_contract["component_models"]["emulsion_mtf"] == (
            REDUCED_EMULSION_MTF_MODEL
            if fast_mode
            else FULL_EMULSION_MTF_MODEL
        )
        assert plan_contract["component_models"]["emulsion_grain"] == contract[
            "model"
        ]
        assert plan_contract["speed_estimate"] is None
        assert plan_contract["component_models"]["halation_source"] == (
            REDUCED_HALATION_SOURCE_MODEL
            if fast_mode
            else FULL_HALATION_SOURCE_MODEL
        )
        assert halation_contract["source_model"] == plan_contract[
            "component_models"
        ]["halation_source"]
        assert halation_contract["execution_mode"] == expected_label

    assert resolved_models == {
        "quality": FULL_DENSITY_GRAIN_MODEL,
        "scaled_fast": FULL_DENSITY_GRAIN_MODEL,
        "reduced_fast": REDUCED_DENSITY_GRAIN_MODEL,
        "scaled_reduced_fast": REDUCED_DENSITY_GRAIN_MODEL,
    }


def test_engine_routes_halation_source_model_from_the_same_plan(monkeypatch):
    image = np.full((48, 72, 3), 0.04, dtype=np.float32)
    image[22:25, 34:37] = 1.5
    original_apply_halation = engine_module.apply_halation
    observed_models = []

    def record_model(*args, source_model, **kwargs):
        observed_models.append(source_model)
        return original_apply_halation(
            *args,
            source_model=source_model,
            **kwargs,
        )

    monkeypatch.setattr(engine_module, "apply_halation", record_model)
    for lower_order_enabled in (False, True):
        config = DarkroomConfig()
        config.fast_mode = lower_order_enabled
        config.processing.execution_mode = "quality"
        config.enable_grain = False
        config.enable_mtf = False
        config.enable_halation = True
        config.film.halation_strength = 0.2
        config.film.halation_threshold = 0.45
        config.film.halation_peak_threshold = 0.001
        config.film.halation_area_suppression = 0.0
        develop_negative(image, config, rng=np.random.default_rng(17))

    assert observed_models == [
        FULL_HALATION_SOURCE_MODEL,
        REDUCED_HALATION_SOURCE_MODEL,
    ]


def test_engine_routes_emulsion_mtf_model_from_the_same_plan(monkeypatch):
    image = np.linspace(0.04, 0.96, 36 * 54 * 3, dtype=np.float32).reshape(
        36,
        54,
        3,
    )
    original_apply_mtf = engine_module.apply_emulsion_mtf
    observed_models = []

    def record_model(*args, model, **kwargs):
        observed_models.append(model)
        return original_apply_mtf(*args, model=model, **kwargs)

    monkeypatch.setattr(engine_module, "apply_emulsion_mtf", record_model)
    for lower_order_enabled in (False, True):
        config = DarkroomConfig()
        config.fast_mode = lower_order_enabled
        config.processing.execution_mode = "quality"
        config.enable_grain = False
        config.enable_halation = False
        config.enable_mtf = True
        develop_negative(image, config, rng=np.random.default_rng(29))

    assert observed_models == [
        FULL_EMULSION_MTF_MODEL,
        REDUCED_EMULSION_MTF_MODEL,
    ]


def test_scaled_fast_file_mode_resizes_without_preview_argument(tmp_path):
    source = tmp_path / "large-enough.jpg"
    output = tmp_path / "result.png"
    pixels = np.linspace(10, 240, 80 * 40 * 3, dtype=np.uint8).reshape(40, 80, 3)
    Image.fromarray(pixels, mode="RGB").save(source, quality=95)

    config = DarkroomConfig()
    config.processing.execution_mode = "scaled_fast"
    config.output.preview_long_edge = 20
    config.output.save_scanner_raw = False
    config.output.export_transparent_plate = False
    config.save_sidecar = True
    process_file(source, output, config)

    assert Image.open(output).size == (20, 10)
    sidecar = json.loads(
        output.with_suffix(output.suffix + ".json").read_text(encoding="utf-8")
    )
    assert sidecar["preview"] is True
    assert sidecar["config"]["processing"]["execution_mode"] == "scaled_fast"


def test_scaled_and_reduced_file_mode_combines_both_acceleration_axes(tmp_path):
    source = tmp_path / "combined.jpg"
    output = tmp_path / "combined-result.png"
    pixels = np.linspace(10, 240, 80 * 40 * 3, dtype=np.uint8).reshape(40, 80, 3)
    Image.fromarray(pixels, mode="RGB").save(source, quality=95)

    config = DarkroomConfig()
    config.processing.execution_mode = "scaled_fast"
    config.fast_mode = True
    config.processing.quality_mode = "draft"
    config.output.preview_long_edge = 20
    config.output.save_scanner_raw = False
    config.output.export_transparent_plate = False
    config.save_sidecar = True
    process_file(source, output, config)

    assert Image.open(output).size == (20, 10)
    sidecar = json.loads(
        output.with_suffix(output.suffix + ".json").read_text(encoding="utf-8")
    )
    assert sidecar["preview"] is True
    assert sidecar["config"]["processing"]["execution_mode"] == "scaled_fast"
    assert sidecar["config"]["processing"]["quality_mode"] == "draft"
    assert sidecar["config"]["fast_mode"] is True

    medium = load_developed_medium_for_scan(
        tmp_path / "negatives" / "combined.darkroom_negative.npz"
    )
    contract = medium.metadata["film_process_model"]["execution_implementation"]
    assert contract["execution_mode"] == "scaled_reduced_fast"
    assert contract["scaling_enabled"] is True
    assert contract["quality_mode"] == "draft"
    assert contract["lower_order_enabled"] is True
    assert (
        contract["component_models"]["emulsion_grain"]
        == REDUCED_DENSITY_GRAIN_MODEL
    )
    assert (
        contract["component_models"]["emulsion_mtf"]
        == REDUCED_EMULSION_MTF_MODEL
    )
    assert (
        contract["component_models"]["halation_source"]
        == REDUCED_HALATION_SOURCE_MODEL
    )


def test_medium_store_mode_changes_only_archive_compression(tmp_path):
    source = tmp_path / "source.png"
    output = tmp_path / "result.png"
    Image.fromarray(np.full((8, 10, 3), 140, dtype=np.uint8), mode="RGB").save(source)
    config = DarkroomConfig()
    config.output.medium_npz_compression = "store"
    config.output.material_storage_precision = "full"
    config.output.save_scanner_raw = False
    config.output.export_transparent_plate = False
    process_file(source, output, config)

    medium = tmp_path / "negatives" / "source.darkroom_negative.npz"
    with zipfile.ZipFile(medium) as archive:
        assert archive.infolist()
        assert {entry.compress_type for entry in archive.infolist()} == {
            zipfile.ZIP_STORED
        }
    with np.load(medium) as payload:
        assert payload["density_cmy"].dtype == np.float32
        assert payload["density_grain"].dtype == np.float32
