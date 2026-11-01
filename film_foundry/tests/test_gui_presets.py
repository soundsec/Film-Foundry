import numpy as np
import json
from dataclasses import asdict
from pathlib import Path
from types import SimpleNamespace

from film_foundry.tools import run_darkroom_gui as gui
from film_foundry.tools import run_develop_process_editor as process_editor
from film_foundry.tools import run_film_material_editor as material_editor
from film_foundry.tools import run_scanner_render_editor as scanner_editor
from film_foundry.core.engine import develop_negative, process_array, scan_negative
from film_foundry.model.config import DEVELOP_LOOK_FIELDS, DarkroomConfig, merge_config_presets
from film_foundry.ui.i18n import current_language, set_language, tr
import run_film_foundry_launcher as launcher


def test_main_gui_seed_strategy_labels_round_trip_in_both_languages():
    previous = current_language()
    try:
        for language in ("zh_CN", "en_US"):
            set_language(language)
            labels = gui.seed_strategy_labels()
            assert set(labels) == {"random", "fixed", "path"}
            for key, label in labels.items():
                assert gui.seed_strategy_key_from_label(label) == key
                assert gui.seed_strategy_label(key) == label
    finally:
        set_language(previous)


def test_main_brand_is_media_wide_while_silver_halide_editors_stay_specific():
    previous = current_language()
    try:
        set_language("zh_CN")
        assert "成像介质" in tr("app.title")
        assert "成像介质" in tr("launcher.brand")
        assert "银盐胶片工坊" not in tr("launcher.brand")
        assert "银盐" in tr("launcher.tool.film.name")
        assert "银盐" in tr("launcher.tool.develop.name")
    finally:
        set_language(previous)


def test_gui_default_preset_lists_hide_internal_presets_except_explicit_clear_base_process():
    film_names = gui.film_preset_names()
    develop_names = gui.develop_preset_names()
    scanner_names = gui.scanner_preset_names()

    assert "diagnostic_develop_sensitive" not in film_names
    assert "accident_kelp_light_leak" not in develop_names
    assert "diagnostic_flat_scan" not in scanner_names
    assert "color_reversal_transparency" in film_names
    assert "standard_color_reversal" in develop_names
    assert "standard_bw_reversal" in develop_names
    assert "experimental_color_negative_clear_reversal" in develop_names
    assert "compensating_bw" not in develop_names
    assert "fine_grain_bw" not in develop_names
    assert "high_acutance_bw" not in develop_names
    assert "monobath_clean" not in develop_names
    assert "monobath_exhausted_silvering" not in develop_names
    assert "positive_transparency_scan" in scanner_names
    assert "positive_transparency_bright" in scanner_names
    assert "positive_transparency_warm" in scanner_names
    assert "color_restored_scan" in scanner_names
    assert "color_reversal_soft" in film_names
    assert "color_reversal_vivid" in film_names
    assert "monochrome_reversal_transparency" in film_names

    for name in film_names + scanner_names:
        assert not gui.is_internal_preset_name(name)
    for name in develop_names:
        assert (
            not gui.is_internal_preset_name(name)
            or name == "experimental_color_negative_clear_reversal"
        )

    assert "high_acutance_bw" in process_editor.develop_preset_names()


def test_material_editor_save_json_uses_utf8_without_bom(tmp_path):
    path = tmp_path / "preset.json"
    material_editor.save_json(path, {"film": {"name": "中文材料"}})

    data = path.read_bytes()
    assert not data.startswith(b"\xef\xbb\xbf")
    assert "中文材料" in data.decode("utf-8")


def test_process_editor_persists_both_reversal_development_stages():
    assert "first_development_completion" in process_editor.DEVELOP_RECIPE_SCALAR_FIELDS
    assert "second_development_completion" in process_editor.DEVELOP_RECIPE_SCALAR_FIELDS


def test_process_editor_keeps_execution_mode_aligned_with_program_topology():
    assert process_editor.process_mode_for_program("color_reversal", "normal_negative") == "reversal"
    assert process_editor.process_mode_for_program("bw_reversal", "monobath") == "reversal"
    assert process_editor.process_mode_for_program("color_negative", "reversal") == "normal_negative"
    assert process_editor.process_mode_for_program("color_negative", "monobath") == "monobath"


def test_shared_film_material_editor_preserves_negative_and_reversal_classes():
    negative = DarkroomConfig()
    slide = DarkroomConfig()
    slide.film.medium_process = "slide"
    slide.film.image_polarity = "positive"
    slide.film.color_process = "color"

    assert material_editor.is_supported_silver_halide_material(negative)
    assert material_editor.is_supported_silver_halide_material(slide)
    assert material_editor.material_class_for_config(negative) == "color_negative"
    assert material_editor.material_class_for_config(slide) == "color_reversal"
    assert material_editor.material_identity_for_class("bw_reversal") == {
        "medium_process": "reversal",
        "image_polarity": "positive",
        "color_process": "monochrome",
        "mode": "bw_negative",
    }
    assert "experimental_slide_transparency" in material_editor.film_preset_names()


def test_material_editor_previews_native_negative_and_reversal_curve_directions():
    exposure = np.repeat(np.asarray([[0.01], [2.0]], dtype=np.float32), 3, axis=1)
    negative = DarkroomConfig()
    slide = DarkroomConfig()
    slide.film.medium_process = "slide"
    slide.film.image_polarity = "positive"

    negative_density = material_editor.material_preview_density(exposure, negative).mean(axis=1)
    positive_density = material_editor.material_preview_density(exposure, slide).mean(axis=1)

    assert negative_density[1] > negative_density[0]
    assert positive_density[1] < positive_density[0]


def test_main_gui_recommends_matching_standard_process_for_material_identity():
    color_negative = DarkroomConfig()
    color_slide = DarkroomConfig()
    color_slide.film.medium_process = "slide"
    color_slide.film.image_polarity = "positive"
    bw_slide = DarkroomConfig()
    bw_slide.film.medium_process = "reversal"
    bw_slide.film.image_polarity = "positive"
    bw_slide.film.color_process = "monochrome"

    assert gui.recommended_standard_develop_preset(color_negative) == "standard_color_negative"
    assert gui.recommended_standard_develop_preset(color_slide) == "standard_color_reversal"
    assert gui.recommended_standard_develop_preset(bw_slide) == "standard_bw_reversal"


def test_all_builtin_film_materials_complete_their_recommended_pool_program():
    image = np.linspace(0.02, 0.95, 6 * 8 * 3, dtype=np.float32).reshape(6, 8, 3)
    film_dir = Path(gui.FILM_PRESET_DIR)
    develop_dir = Path(gui.DEVELOP_PRESET_DIR)

    for path in sorted(film_dir.glob("*.json")):
        film_config = DarkroomConfig.from_json(path)
        develop_name = gui.recommended_standard_develop_preset(film_config)
        develop_config = DarkroomConfig.from_json(develop_dir / f"{develop_name}.json")
        config = merge_config_presets(film_config, develop_config=develop_config)
        config.enable_grain = False
        config.enable_halation = False

        medium = develop_negative(image, config)
        expected_polarity = "positive" if "reversal" in develop_name else "negative"
        assert medium.image_polarity == expected_polarity, path.name
        assert np.all(np.isfinite(medium.density_grain)), path.name
        assert medium.metadata["film_process_model"]["process_trace"], path.name


def test_builtin_film_presets_pin_the_halation_exponential_tail():
    film_dir = Path(gui.FILM_PRESET_DIR)

    for path in sorted(film_dir.glob("*.json")):
        payload = json.loads(path.read_text(encoding="utf-8"))
        film = payload["film"]
        assert "halation_exponential_radius" in film, path.name
        assert float(film["halation_exponential_radius"]) > 0.0, path.name


def test_color_reversal_presets_hold_a_neutral_axis_in_the_final_medium():
    film_dir = Path(gui.FILM_PRESET_DIR)
    develop_config = DarkroomConfig.from_json(
        Path(gui.DEVELOP_PRESET_DIR) / "standard_color_reversal.json"
    )
    exposure = np.linspace(0.03, 0.97, 33, dtype=np.float32)
    neutral_ramp = np.repeat(exposure[None, :, None], 3, axis=-1)

    for name in (
        "color_reversal_soft",
        "color_reversal_transparency",
        "color_reversal_vivid",
    ):
        film_config = DarkroomConfig.from_json(film_dir / f"{name}.json")
        config = merge_config_presets(film_config, develop_config=develop_config)
        config.enable_grain = False
        config.enable_halation = False
        config.enable_mtf = False

        medium = develop_negative(neutral_ramp, config)
        optical_density = medium.optical_density_rgb
        channel_span = np.ptp(optical_density, axis=-1)

        assert float(channel_span.max()) <= 0.032, name


def test_standard_color_process_presets_separate_dir_material_from_process_effectiveness():
    negative = DarkroomConfig.from_json(
        Path(gui.DEVELOP_PRESET_DIR) / "standard_color_negative.json"
    )
    reversal = DarkroomConfig.from_json(
        Path(gui.DEVELOP_PRESET_DIR) / "standard_color_reversal.json"
    )

    assert negative.chemistry.dir_process_effectiveness == 1.0
    assert reversal.chemistry.dir_process_effectiveness == 0.0
    assert "dir_process_effectiveness" not in process_editor.DEVELOP_RECIPE_SCALAR_FIELDS
    assert "dir_diffusion_radius" in material_editor.DIR_MATERIAL_SCALAR_FIELDS
    assert "dir_material_response_matrix" in material_editor.DIR_MATERIAL_MATRIX_FIELDS


def test_negative_reversal_presets_distinguish_retained_mask_from_experimental_clear_base():
    film_config = DarkroomConfig.from_json(
        Path(gui.FILM_PRESET_DIR) / "clear_modern_negative.json"
    )
    scanner_config = DarkroomConfig.from_json(
        Path(gui.SCANNER_PRESET_DIR) / "positive_transparency_scan.json"
    )
    saturated_patches = np.asarray(
        [
            [1.00, 0.05, 0.05],
            [0.05, 1.00, 0.05],
            [0.05, 0.05, 1.00],
            [1.00, 1.00, 0.05],
            [0.05, 1.00, 1.00],
            [1.00, 0.05, 1.00],
            [0.50, 0.50, 0.50],
            [1.00, 1.00, 1.00],
        ],
        dtype=np.float32,
    )[None, ...]

    results = {}
    for process_name in (
        "standard_color_reversal",
        "experimental_color_negative_clear_reversal",
    ):
        develop_config = DarkroomConfig.from_json(
            Path(gui.DEVELOP_PRESET_DIR) / f"{process_name}.json"
        )
        config = merge_config_presets(
            film_config,
            scanner_config=scanner_config,
            develop_config=develop_config,
        )
        config.enable_grain = False
        config.enable_halation = False
        config.enable_mtf = False
        config.scanner.scan_normalize = False

        medium = develop_negative(saturated_patches, config)
        optical_before = medium.optical_density_rgb.copy()
        scanned = scan_negative(medium, config)

        assert np.array_equal(medium.optical_density_rgb, optical_before)
        assert np.all(scanned.output_srgb > 0.0)
        assert np.all(scanned.output_srgb < 1.0)
        results[process_name] = (medium, scanned)

    retained_medium, retained_scan = results["standard_color_reversal"]
    clear_medium, clear_scan = results["experimental_color_negative_clear_reversal"]

    retained_base_span = np.ptp(retained_medium.clear_base_optical_density_rgb)
    clear_base_span = np.ptp(clear_medium.clear_base_optical_density_rgb)
    assert retained_medium.base_type == "orange_mask"
    assert clear_medium.base_type == "clear_base"
    assert clear_base_span < retained_base_span * 0.10

    # The blue test patch remains blue-dominant after the explicit mask/dye
    # bleach. The standard process deliberately retains the orange mask rather
    # than asking the scanner to hide it.
    blue_patch = clear_scan.output_srgb[0, 2]
    assert blue_patch[2] > blue_patch[0]
    assert blue_patch[2] > blue_patch[1]
    assert np.ptp(clear_scan.output_srgb[0, 2]) > np.ptp(retained_scan.output_srgb[0, 2])


def test_main_gui_material_catalog_separates_native_polarity_and_marks_color_type():
    negative_records = gui.film_preset_catalog("negative")
    positive_records = gui.film_preset_catalog("positive")

    assert negative_records
    assert positive_records
    assert all(record["polarity"] == "negative" for record in negative_records)
    assert all(record["polarity"] == "positive" for record in positive_records)
    assert any(record["key"] == "color_reversal_transparency" for record in positive_records)
    assert all(record["label"].startswith((gui.material_color_label("color"), gui.material_color_label("monochrome"))) for record in negative_records + positive_records)


def test_main_gui_material_display_preserves_unicode_name(tmp_path):
    config = DarkroomConfig()
    config.film.name = "测试中文彩色负片"
    path = tmp_path / "stable_material_key.json"
    path.write_text(json.dumps(asdict(config), ensure_ascii=False), encoding="utf-8")

    record = gui.film_preset_record(str(path))

    assert record is not None
    assert record["name"] == "测试中文彩色负片"
    assert "测试中文彩色负片" in record["label"]
    assert record["key"] == str(path)


def test_layer_selective_halation_material_config_round_trips_and_editor_exposes_modes():
    config = DarkroomConfig()
    config.film.halation_return_model = "layer_selective"
    config.film.halation_layer_return_weights = (1.0, 0.33, 0.07)
    config.film.halation_spread_scale_weights = (0.2, 0.6, 0.2)

    restored = DarkroomConfig.from_dict(asdict(config))

    assert restored.film.halation_return_model == "layer_selective"
    assert restored.film.halation_layer_return_weights == (1.0, 0.33, 0.07)
    assert restored.film.halation_spread_scale_weights == (0.2, 0.6, 0.2)
    assert set(material_editor.HALATION_RETURN_MODE_LABELS.values()) == {
        "compatibility_rgb",
        "layer_selective",
    }


def test_emulsion_highlight_spread_material_config_round_trips_and_editor_exposes_fields():
    config = DarkroomConfig()
    config.film.emulsion_highlight_spread_strength = 0.24
    config.film.emulsion_highlight_spread_radius = 0.0018
    config.film.emulsion_highlight_spread_threshold = 0.68
    config.film.emulsion_highlight_spread_softness = 0.11
    config.film.emulsion_highlight_spread_layer_radius_scales = (0.8, 1.0, 1.2)
    config.film.emulsion_highlight_spread_layer_strengths = (0.9, 1.0, 1.1)

    restored = DarkroomConfig.from_dict(asdict(config))
    editor = material_editor.FilmMaterialEditor()
    try:
        editor._set_controls_from_config(restored)
        edited = editor._config_from_controls()
        assert "emulsion_highlight_spread_strength" in editor.scalar_vars
        assert "emulsion_highlight_spread_layer_strengths" in editor.vector_vars
        assert edited.film.emulsion_highlight_spread_strength == 0.24
        assert edited.film.emulsion_highlight_spread_radius == 0.0018
        assert edited.film.emulsion_highlight_spread_layer_radius_scales == (0.8, 1.0, 1.2)
        assert edited.film.emulsion_highlight_spread_layer_strengths == (0.9, 1.0, 1.1)
    finally:
        editor.root.destroy()


def test_dir_material_response_round_trips_in_the_shared_film_editor():
    config = DarkroomConfig()
    config.film.dir_diffusion_radius = 0.0042
    config.film.dir_material_response_matrix = (
        (0.22, 0.04, 0.01),
        (0.04, 0.21, 0.04),
        (0.01, 0.04, 0.20),
    )

    restored = DarkroomConfig.from_dict(asdict(config))
    editor = material_editor.FilmMaterialEditor()
    try:
        editor._set_controls_from_config(restored)
        edited = editor._config_from_controls()
        assert "dir_diffusion_radius" in editor.scalar_vars
        assert "dir_material_response_matrix" in editor.matrix_vars
        assert edited.film.dir_diffusion_radius == 0.0042
        assert edited.film.dir_material_response_matrix == (
            (0.22, 0.04, 0.01),
            (0.04, 0.21, 0.04),
            (0.01, 0.04, 0.20),
        )
        editor.material_class.set("bw_negative")
        editor._apply_material_class()
        assert "disabled" in editor.field_widgets["dir_diffusion_radius"].state()
        assert "disabled" in editor.matrix_inputs["dir_material_response_matrix"][0].state()
    finally:
        editor.root.destroy()


def test_light_piping_material_config_round_trips_and_editor_exposes_declared_edges():
    config = DarkroomConfig()
    config.film.light_piping_strength = 0.2
    config.film.light_piping_depth = 0.08
    config.film.light_piping_edge_mode = "long_edges"
    config.film.light_piping_layer_weights = (1.0, 0.4, 0.12)

    restored = DarkroomConfig.from_dict(asdict(config))

    assert restored.film.light_piping_strength == 0.2
    assert restored.film.light_piping_depth == 0.08
    assert restored.film.light_piping_edge_mode == "long_edges"
    assert restored.film.light_piping_layer_weights == (1.0, 0.4, 0.12)
    assert set(material_editor.LIGHT_PIPING_EDGE_LABELS.values()) == {
        "none",
        "top",
        "right",
        "bottom",
        "left",
        "long_edges",
        "short_edges",
        "all_edges",
    }


def test_research_extreme_exposure_tail_round_trips_and_is_localized_as_default_off():
    config = DarkroomConfig()
    config.film.extreme_exposure_reversal_strength = 0.35
    config.film.extreme_exposure_reversal_start_loge = (0.52, 0.58, 0.64)
    config.film.extreme_exposure_reversal_width = 0.21

    restored = DarkroomConfig.from_dict(asdict(config))

    assert restored.film.extreme_exposure_reversal_strength == 0.35
    assert restored.film.extreme_exposure_reversal_start_loge == (0.52, 0.58, 0.64)
    assert restored.film.extreme_exposure_reversal_width == 0.21
    assert material_editor.MATERIAL_EDITOR_EN[
        "研究性极端曝光潜影尾部（默认关闭）"
    ] == "Research Extreme-Exposure Latent Tail (Off by Default)"


def test_development_adjacency_round_trips_and_process_editor_exposes_reduced_controls():
    config = DarkroomConfig()
    config.chemistry.development_adjacency_strength = 0.4
    config.chemistry.development_adjacency_radius = 0.0035
    config.processing.adjacency_work_long_edge = 1400

    restored = DarkroomConfig.from_dict(asdict(config))

    assert restored.chemistry.development_adjacency_strength == 0.4
    assert restored.chemistry.development_adjacency_radius == 0.0035
    assert restored.processing.adjacency_work_long_edge == 1400
    assert "development_adjacency_strength" in process_editor.DEVELOP_RECIPE_SCALAR_FIELDS
    assert "development_adjacency_radius" in process_editor.DEVELOP_RECIPE_SCALAR_FIELDS
    assert process_editor.DEVELOP_EDITOR_EN[
        "显影邻接强度（降阶）"
    ] == "Development Adjacency Strength (Reduced)"


def test_reticulation_round_trips_and_is_exposed_as_explicit_process_damage():
    config = DarkroomConfig()
    config.chemistry.reticulation_strength = 0.42

    restored = DarkroomConfig.from_dict(asdict(config))

    assert restored.chemistry.reticulation_strength == 0.42
    assert "reticulation_strength" in process_editor.DEVELOP_RECIPE_SCALAR_FIELDS
    assert process_editor.DEVELOP_EDITOR_EN[
        "乳剂网裂 / 温度冲击损伤"
    ] == "Reticulation / Temperature-Shock Damage"
    assert tr("label.reticulation_strength") in {
        "乳剂网裂 / 温度冲击损伤",
        "Reticulation / temperature-shock damage",
    }


def test_dye_damage_round_trips_and_is_exposed_as_explicit_material_loss():
    config = DarkroomConfig()
    config.chemistry.dye_damage_strength = 0.37

    restored = DarkroomConfig.from_dict(asdict(config))

    assert restored.chemistry.dye_damage_strength == 0.37
    assert "dye_damage_strength" in process_editor.DEVELOP_RECIPE_SCALAR_FIELDS
    assert process_editor.DEVELOP_EDITOR_EN[
        "染料损伤（显式事故）"
    ] == "Dye Damage (Explicit Accident)"
    assert tr("label.dye_damage_strength") in {
        "染料损伤（显式事故）",
        "Dye damage (explicit accident)",
    }


def test_exposure_occlusion_round_trips_and_is_exposed_before_development():
    config = DarkroomConfig()
    config.chemistry.exposure_occlusion_dust = 0.31
    config.chemistry.exposure_occlusion_hair = 0.22
    config.chemistry.exposure_occlusion_insect = 0.18
    config.chemistry.exposure_occlusion_debris = 0.27

    restored = DarkroomConfig.from_dict(asdict(config))
    assert restored.chemistry.exposure_occlusion_dust == 0.31
    assert restored.chemistry.exposure_occlusion_hair == 0.22
    assert "exposure_occlusion_dust" in process_editor.DEVELOP_RECIPE_SCALAR_FIELDS
    assert process_editor.DEVELOP_EDITOR_EN["灰尘（冲洗前遮挡）"] == "Dust (Pre-Develop Occlusion)"
    assert tr("label.exposure_occlusion_dust") in {"灰尘（冲洗前）", "Dust (pre-develop)"}

    editor = process_editor.DevelopProcessEditor()
    try:
        editor._set_controls_from_config(restored)
        edited = editor._config_from_controls()
        assert edited.chemistry.exposure_occlusion_dust == 0.31
        assert edited.chemistry.exposure_occlusion_insect == 0.18
        restored.chemistry.exposure_occlusion_identity_seed = 23
        editor._set_controls_from_config(restored)
        edited = editor._config_from_controls()
        assert edited.chemistry.exposure_occlusion_identity_seed == 23
    finally:
        editor.root.destroy()


def test_edge_imprint_session_fields_round_trip_and_are_not_develop_recipe_scalars():
    config = DarkroomConfig()
    config.film_border.imprint_style = "laser_dot"
    config.film_border.imprint_side = "top"
    config.film_border.imprint_text = "A12"
    config.film_border.imprint_time = "2026-08-27 02:14"
    restored = DarkroomConfig.from_dict(asdict(config))
    assert restored.film_border.imprint_style == "laser_dot"
    assert restored.film_border.imprint_text == "A12"
    assert "imprint_text" not in process_editor.DEVELOP_RECIPE_SCALAR_FIELDS
    assert tr("label.imprint_style") in {"边字光源", "Edge-imprint lamp"}
    previous = current_language()
    try:
        for language in ("zh_CN", "en_US"):
            set_language(language)
            labels = gui.imprint_style_labels()
            assert set(labels) == {"none", "laser_dot", "optical_amber", "lab_edge"}
            for key, label in labels.items():
                assert gui.imprint_style_key_from_label(label) == key
            assert gui.imprint_side_key_from_label(gui.imprint_side_label("top")) == "top"
    finally:
        set_language(previous)


def test_light_leak_templates_round_trip_and_use_localized_display_labels():
    config = DarkroomConfig()
    config.chemistry.light_leak_template = "cone"
    config.chemistry.light_leak_strength = 0.45
    config.chemistry.light_leak_secondary_template = "half_frame"
    config.chemistry.light_leak_mix = "add"
    config.chemistry.light_leak_placement = "manual"
    config.chemistry.light_leak_entry_side = "top"
    config.chemistry.light_leak_center_x = 0.42
    config.chemistry.light_leak_rotation_degrees = 12.5
    config.chemistry.light_leak_identity_seed = 19
    restored = DarkroomConfig.from_dict(asdict(config))

    assert restored.chemistry.light_leak_template == "cone"
    assert restored.chemistry.light_leak_strength == 0.45
    assert restored.chemistry.light_leak_secondary_template == "half_frame"
    assert restored.chemistry.light_leak_mix == "add"
    assert restored.chemistry.light_leak_placement == "manual"
    assert restored.chemistry.light_leak_entry_side == "top"
    assert restored.chemistry.light_leak_identity_seed == 19

    previous = current_language()
    try:
        for language in ("zh_CN", "en_US"):
            set_language(language)
            main_labels = gui.light_leak_template_labels()
            editor_labels = process_editor.light_leak_template_labels()
            assert set(main_labels) == {"auto", "local_edge", "perimeter", "half_frame", "cone"}
            assert editor_labels == main_labels
            assert set(process_editor.light_leak_secondary_template_labels()) == {
                "none",
                "local_edge",
                "perimeter",
                "half_frame",
                "cone",
            }
            for key, label in process_editor.light_leak_mix_labels().items():
                assert process_editor.light_leak_mix_key_from_label(label) == key
    finally:
        set_language(previous)

    editor = process_editor.DevelopProcessEditor()
    try:
        editor._set_controls_from_config(restored)
        edited = editor._config_from_controls()
        assert editor.light_leak_template_display.get() == process_editor.light_leak_template_label("cone")
        assert editor.light_leak_secondary_template_display.get() == process_editor.light_leak_secondary_template_label("half_frame")
        assert edited.chemistry.light_leak_template == "cone"
        assert edited.chemistry.light_leak_strength == 0.45
        assert edited.chemistry.light_leak_secondary_template == "half_frame"
        assert edited.chemistry.light_leak_mix == "add"
        assert edited.chemistry.light_leak_placement == "manual"
        assert edited.chemistry.light_leak_entry_side == "top"
        assert edited.chemistry.light_leak_center_x == 0.42
        assert edited.chemistry.light_leak_rotation_degrees == 12.5
        assert edited.chemistry.light_leak_identity_seed == 19
    finally:
        editor.root.destroy()


def test_main_gui_develop_only_uses_final_medium_polarity_for_npz_suffix(tmp_path):
    input_path = Path("frame.png")
    negative = SimpleNamespace(image_polarity="negative")
    positive = SimpleNamespace(image_polarity="positive")

    negative_path = gui.developed_path_for(input_path, tmp_path, negative)
    positive_path = gui.developed_path_for(input_path, tmp_path, positive)

    assert negative_path.name == "frame.darkroom_negative.npz"
    assert positive_path.name == "frame.darkroom_positive.npz"
    assert gui.output_path_for(negative_path, tmp_path, "png").name == "frame_negative_darkroom.png"
    assert gui.output_path_for(positive_path, tmp_path, "png").name == "frame_positive_darkroom.png"
    assert gui.output_path_for(negative_path, tmp_path, "png") != gui.output_path_for(positive_path, tmp_path, "png")
    negative_path.touch()
    positive_path.touch()
    negative_path.with_suffix(".scanner_raw.tiff").touch()
    positive_path.with_suffix(".light_table_raw.tiff").touch()
    assert gui.iter_developed_medium_files(tmp_path) == [negative_path, positive_path]


def test_main_gui_reserves_unique_timestamped_full_and_develop_run_directories(tmp_path):
    full_root, full_id = gui.create_run_directory(
        tmp_path,
        "full",
        timestamp="2026-07-15_12-34-56",
    )
    second_root, second_id = gui.create_run_directory(
        tmp_path,
        "full",
        timestamp="2026-07-15_12-34-56",
    )
    develop_root, develop_id = gui.create_run_directory(
        tmp_path,
        "develop",
        timestamp="2026-07-15_12-34-56",
    )

    assert full_root.name == full_id == "2026-07-15_12-34-56_full"
    assert second_root.name == second_id == "2026-07-15_12-34-56_full_02"
    assert develop_root.name == develop_id == "2026-07-15_12-34-56_develop"
    assert full_root.is_dir() and second_root.is_dir() and develop_root.is_dir()


def test_scan_only_defaults_above_negative_folder_and_uses_generation_suffix(tmp_path):
    negative_root = tmp_path / "A" / "negative"
    negative_root.mkdir(parents=True)
    negative_path = negative_root / "frame.darkroom_negative.npz"

    scan_root = gui.default_scan_output_root(negative_root)
    run_id = gui.unique_scan_run_id(
        scan_root,
        timestamp="2026-07-15_12-34-56",
    )
    output_path = gui.output_path_for(
        negative_path,
        scan_root,
        "png",
        run_suffix=run_id,
    )

    assert scan_root == tmp_path / "A"
    assert gui.default_scan_output_root(negative_path) == tmp_path / "A"
    assert run_id == "2026-07-15_12-34-56_scan"
    assert output_path.parent == tmp_path / "A"
    assert output_path.name == "frame_negative_darkroom_2026-07-15_12-34-56_scan.png"

    (scan_root / f"film_foundry_{run_id}.json").touch()
    assert gui.unique_scan_run_id(
        scan_root,
        timestamp="2026-07-15_12-34-56",
    ) == "2026-07-15_12-34-56_scan_02"


def test_run_settings_snapshot_records_actual_generation_paths_without_mutating_source(tmp_path):
    snapshot = {
        "paths": {
            "input": "input",
            "output": "old-output",
            "developed_medium": "old-negative",
            "negative": "old-negative",
        }
    }
    run_root = tmp_path / "run"
    developed_root = run_root / "negative"

    payload = gui.DarkroomPanel._run_settings_payload(
        snapshot,
        run_id="run-id",
        run_root=run_root,
        output_root=run_root,
        developed_root=developed_root,
    )

    assert payload["run"] == {
        "id": "run-id",
        "directory": str(run_root),
        "actual_output": str(run_root),
        "developed_medium": str(developed_root),
        "status": "running",
    }
    assert payload["paths"]["output"] == "old-output"
    assert payload["paths"]["developed_medium"] == str(developed_root)
    assert snapshot["paths"]["output"] == "old-output"


def test_batch_item_and_final_summary_are_lightweight_and_explicit():
    completed = gui.batch_item_record(Path("input-a.jpg"), Path("output-a.jpg"), 1.23456789)
    failure = gui.batch_item_record(
        Path("input-b.jpg"),
        Path("output-b.jpg"),
        0.25,
        ValueError("bad input"),
    )
    payload = {"run": {"status": "running"}}

    gui.finalize_batch_run(
        payload,
        operation="full",
        started_at="2026-08-02T12:00:00",
        elapsed_seconds=1.5,
        items=[completed, failure],
    )

    assert completed == {
        "input": "input-a.jpg",
        "output": "output-a.jpg",
        "status": "completed",
        "elapsed_seconds": 1.234568,
    }
    assert failure["status"] == "failed"
    assert failure["error"] == {"type": "ValueError", "message": "bad input"}
    assert payload["run"]["status"] == "completed_with_errors"
    assert payload["batch"]["completed"] == 1
    assert payload["batch"]["failed"] == 1
    assert payload["batch"]["recording_strategy"] == "memory_then_single_atomic_write"


def test_batch_record_write_failure_does_not_raise(monkeypatch, tmp_path):
    monkeypatch.setattr(gui, "save_session", lambda *_args, **_kwargs: (_ for _ in ()).throw(OSError("disk busy")))
    payload = {"run": {"status": "running"}}

    error = gui.save_batch_run_record(
        tmp_path / "run.json",
        payload,
        operation="scan",
        started_at="2026-08-02T12:00:00",
        started_counter=gui.perf_counter(),
        items=[],
    )

    assert isinstance(error, OSError)
    assert str(error) == "disk busy"
    assert payload["run"]["status"] == "completed"


class _ImmediateRoot:
    @staticmethod
    def after(_delay, callback):
        callback()


def _batch_test_panel():
    panel = gui.DarkroomPanel.__new__(gui.DarkroomPanel)
    panel.root = _ImmediateRoot()
    panel.process_button = None
    panel._processing = True
    panel._cancel_flag = None
    panel.negative_path = SimpleNamespace(set=lambda _value: None)
    panel.recorded_statuses = []
    panel._post_status = panel.recorded_statuses.append
    return panel


def _batch_snapshot(tmp_path):
    return {
        "kind": "FilmFoundrySession",
        "version": 1,
        "paths": {
            "input": str(tmp_path / "input"),
            "output": str(tmp_path / "output"),
            "developed_medium": str(tmp_path / "negative"),
            "negative": str(tmp_path / "negative"),
        },
    }


def test_full_gui_batch_persists_one_final_mixed_result_manifest(monkeypatch, tmp_path):
    inputs = [tmp_path / "a.jpg", tmp_path / "b.jpg"]
    for path in inputs:
        path.touch()
    output_root = tmp_path / "output"
    save_calls = []
    real_save = gui.save_session

    monkeypatch.setattr(gui, "iter_images", lambda _root: inputs)
    monkeypatch.setattr(gui, "run_timestamp", lambda _now=None: "2026-08-02_12-00-00")

    def fake_run(request):
        assert request.command == "full"
        assert request.scaled_override is False
        if request.input_path.name == "b.jpg":
            raise RuntimeError("scan failed")
        return SimpleNamespace(output_path=request.output_path, extra={})

    def counted_save(path, payload):
        save_calls.append(Path(path))
        real_save(path, payload)

    monkeypatch.setattr(gui, "_run_request", fake_run)
    monkeypatch.setattr(gui, "save_session", counted_save)
    panel = _batch_test_panel()

    panel._process(
        "full",
        DarkroomConfig(),
        tmp_path / "input",
        tmp_path / "negative",
        output_root,
        False,
        False,
        _batch_snapshot(tmp_path),
    )

    manifest = output_root / "2026-08-02_12-00-00_full" / "film_foundry_run.json"
    payload = json.loads(manifest.read_text(encoding="utf-8"))
    assert save_calls == [manifest, manifest]
    assert payload["run"]["status"] == "completed_with_errors"
    assert payload["batch"]["completed"] == 1
    assert payload["batch"]["failed"] == 1
    assert [item["status"] for item in payload["batch"]["items"]] == ["completed", "failed"]
    assert payload["batch"]["items"][1]["error"] == {
        "type": "RuntimeError",
        "message": "scan failed",
    }


def test_develop_and_scan_gui_batches_finalize_manifests(monkeypatch, tmp_path):
    config = DarkroomConfig()
    source = tmp_path / "source.jpg"
    source.touch()
    panel = _batch_test_panel()
    monkeypatch.setattr(gui, "run_timestamp", lambda _now=None: "2026-08-02_12-10-00")
    monkeypatch.setattr(gui, "iter_images", lambda _root: [source])

    def fake_run(request):
        if request.command == "develop":
            developed = Path(request.output_path) / f"{request.input_path.stem}.darkroom_negative.npz"
            return SimpleNamespace(output_path=developed, extra={})
        if request.command == "scan":
            return SimpleNamespace(
                output_path=None,
                source_kind="scanner_raw_tiff",
                source_path=request.input_path,
                extra={
                    "scanned": SimpleNamespace(
                        metadata={},
                        output_srgb=np.zeros((8, 12, 3), dtype=np.float32),
                    )
                },
            )
        raise AssertionError(request.command)

    monkeypatch.setattr(gui, "_run_request", fake_run)

    output_root = tmp_path / "develop-output"
    panel._process(
        "develop",
        config,
        tmp_path,
        tmp_path / "negative",
        output_root,
        False,
        False,
        _batch_snapshot(tmp_path),
    )
    develop_manifest = output_root / "2026-08-02_12-10-00_develop" / "film_foundry_run.json"
    develop_payload = json.loads(develop_manifest.read_text(encoding="utf-8"))
    assert develop_payload["batch"]["operation"] == "develop"
    assert develop_payload["batch"]["completed"] == 1

    medium = tmp_path / "negative" / "frame.darkroom_negative.npz"
    monkeypatch.setattr(gui, "iter_developed_medium_files", lambda _root: [medium])
    monkeypatch.setattr(gui, "unique_scan_run_id", lambda *_args, **_kwargs: "2026-08-02_12-20-00_scan")
    monkeypatch.setattr(gui, "final_positive_sidecar", lambda **_kwargs: None)
    monkeypatch.setattr(gui, "save_image_bundle", lambda *_args, **_kwargs: None)
    scan_root = tmp_path / "scan-output"
    panel._process(
        "scan",
        config,
        tmp_path,
        tmp_path / "negative",
        scan_root,
        False,
        False,
        _batch_snapshot(tmp_path),
    )
    scan_manifest = scan_root / "film_foundry_2026-08-02_12-20-00_scan.json"
    scan_payload = json.loads(scan_manifest.read_text(encoding="utf-8"))
    assert scan_payload["batch"]["operation"] == "scan"
    assert scan_payload["batch"]["completed"] == 1


def test_gui_batch_process_uses_run_request():
    import inspect
    from film_foundry.app import api as api

    source = inspect.getsource(gui.DarkroomPanel._process)
    assert gui._run_request is api.run_request
    assert "_run_request" in source
    assert "WorkflowRequest" in source
    assert "process_file(" not in source
    assert "develop_negative(" not in source
    assert "scan_from_file(" not in source


def test_gui_saves_film_and_scanner_presets_through_workflow():
    import inspect

    source = inspect.getsource(gui.DarkroomPanel._save_section_preset)
    assert "save_user_film_preset" in source
    assert "save_user_scanner_preset" in source
    assert gui.save_user_film_preset is not None
    assert gui.save_user_scanner_preset is not None


def test_scanner_editor_save_json_uses_utf8_without_bom(tmp_path):
    path = tmp_path / "scanner.json"
    scanner_editor.save_json(path, {"scanner": {"name": "中文扫描器"}})

    data = path.read_bytes()
    assert not data.startswith(b"\xef\xbb\xbf")
    assert "中文扫描器" in data.decode("utf-8")


def test_editor_payloads_preserve_owned_top_level_semantics():
    config = DarkroomConfig()
    config.medium = "film_reversal"
    config.enable_subtractive = False

    material_payload = material_editor.FilmMaterialEditor._film_payload(object(), config)
    scanner_payload = scanner_editor.ScannerRenderEditor._preset_payload(object(), config)

    assert DarkroomConfig.from_dict(material_payload).medium == "film_reversal"
    assert DarkroomConfig.from_dict(scanner_payload).enable_subtractive is False


def test_process_editor_exposes_every_formation_side_look_field():
    assert set(process_editor.DEVELOP_LOOK_CONTROL_DEFAULTS) == set(DEVELOP_LOOK_FIELDS)


def test_scanner_editor_sliders_keep_precise_numeric_entry():
    import inspect
    from film_foundry.ui.components import add_numeric_control

    assert "add_numeric_control" in inspect.getsource(scanner_editor.ScannerRenderEditor._slider)
    assert "ui.NumberInput" in inspect.getsource(add_numeric_control)


def test_standalone_editor_preset_labels_follow_gui_language():
    previous = current_language()
    try:
        set_language("zh_CN")
        _, film_zh = material_editor.film_preset_choices()
        _, develop_zh = process_editor.develop_preset_choices()
        _, scanner_zh = scanner_editor.scanner_preset_choices("positive")
        assert next(label for label, key in film_zh.items() if key == "clear_modern_negative") == "清晰现代"
        assert next(label for label, key in develop_zh.items() if key == "standard_color_reversal") == "彩色 · 正片（反转） · 标准流程"
        assert next(label for label, key in scanner_zh.items() if key == "positive_transparency_scan") == "正片 · 中性灯台"

        set_language("en_US")
        _, film_en = material_editor.film_preset_choices()
        _, develop_en = process_editor.develop_preset_choices()
        _, scanner_en = scanner_editor.scanner_preset_choices("positive")
        assert next(label for label, key in film_en.items() if key == "clear_modern_negative") == "Clear Modern"
        assert next(label for label, key in develop_en.items() if key == "standard_color_reversal") == "Color · Positive (Reversal) · Standard Process"
        assert next(label for label, key in scanner_en.items() if key == "positive_transparency_scan") == "Positive · Neutral Light Table"
    finally:
        set_language(previous)


def test_shared_scanner_editor_lists_all_transmission_presets():
    names = scanner_editor.scanner_preset_names()

    assert "neutral_scan" in names
    assert "experimental_light_table_scan" in names
    assert "positive_transparency_scan" in names


def test_legacy_catalog_filters_remain_available_for_grouped_display():
    names = scanner_editor.scanner_preset_names("positive")
    positive_records = gui.scanner_preset_catalog("positive")
    negative_records = gui.scanner_preset_catalog("negative")

    assert "positive_transparency_scan" in names
    assert "neutral_scan" in names
    assert all(record["polarity"] == "positive" for record in positive_records)
    assert all(record["polarity"] == "negative" for record in negative_records)


def test_launcher_uses_one_unified_transmission_scanner_editor_card():
    tool_ids = [tool[0] for tool in launcher.TOOLS]

    assert tool_ids.count("scanner") == 1
    assert "positive_scanner" not in tool_ids


def test_public_positive_material_and_light_table_presets_have_explicit_identity():
    positive_materials = gui.film_preset_catalog("positive")
    positive_scanners = gui.scanner_preset_catalog("positive")

    assert {record["key"] for record in positive_materials} >= {
        "color_reversal_transparency",
        "color_reversal_soft",
        "color_reversal_vivid",
        "monochrome_reversal_transparency",
    }
    assert {record["key"] for record in positive_scanners} >= {
        "positive_transparency_scan",
        "positive_transparency_bright",
        "positive_transparency_warm",
    }
    for record in positive_materials:
        config = DarkroomConfig.from_json(gui.film_preset_path(record["key"]))
        assert config.film.image_polarity == "positive"
    for record in positive_scanners:
        config = DarkroomConfig.from_json(gui.scanner_preset_path(record["key"]))
        assert config.scanner.interpreter_key == "positive_transparency_scan"
        assert config.scanner.light_table_ev > 0.0
        assert 1.0 <= config.scanner.scan_saturation <= 1.02
        assert config.scanner.positive_scan_color_control_strength <= 0.16


def test_public_negative_materials_keep_reduced_but_distinct_grain_baselines():
    records = gui.film_preset_catalog("negative")
    grain = {
        record["key"]: max(DarkroomConfig.from_json(gui.film_preset_path(record["key"])).film.granularity_sigma)
        for record in records
    }

    assert grain["clean_digital_like"] <= 0.011
    assert grain["clear_modern_negative"] <= 0.020
    assert grain["high_contrast_push"] > grain["clear_modern_negative"]


def _render_material_patches(material_key: str, patches: np.ndarray) -> np.ndarray:
    film = DarkroomConfig.from_json(gui.film_preset_path(material_key))
    develop = DarkroomConfig.from_json(gui.develop_preset_path("standard_color_negative"))
    scanner = DarkroomConfig.from_json(gui.scanner_preset_path("neutral_scan"))
    config = merge_config_presets(film, scanner, develop_config=develop)
    config.enable_grain = False
    config.enable_halation = False
    config.scanner.scan_normalize = False
    config.seed_strategy = "fixed"
    config.random_seed = 17
    return process_array(np.asarray(patches, dtype=np.float32), config)


def test_unconventional_negative_materials_have_declared_response_directions():
    records = {record["key"] for record in gui.film_preset_catalog("negative")}
    assert records >= {
        "blue_highlight_negative",
        "vivid_balanced_negative",
        "cyan_shadow_warm_highlight_negative",
    }

    levels = np.asarray([0.10, 0.25, 0.50, 0.75, 0.90, 0.98], dtype=np.float32)
    gray_ramp = np.repeat(levels[:, None, None], 3, axis=2)
    blue = _render_material_patches("blue_highlight_negative", gray_ramp)[:, 0]
    split = _render_material_patches("cyan_shadow_warm_highlight_negative", gray_ramp)[:, 0]

    assert blue[-2, 2] - blue[-2, 0] > 0.07
    assert blue[-2, 2] - blue[-2, 0] > blue[0, 2] - blue[0, 0] + 0.07
    assert split[0, 1] > split[0, 0]
    assert split[0, 2] - split[0, 0] > 0.06
    assert split[-2, 0] - split[-2, 2] > 0.12


def test_vivid_balanced_material_increases_color_separation_without_large_gray_bias():
    patches = np.asarray(
        [
            [0.8, 0.2, 0.2],
            [0.2, 0.8, 0.2],
            [0.2, 0.2, 0.8],
            [0.8, 0.8, 0.2],
            [0.2, 0.8, 0.8],
            [0.8, 0.2, 0.8],
            [0.5, 0.5, 0.5],
        ],
        dtype=np.float32,
    )[:, None, :]
    baseline = _render_material_patches("clear_modern_negative", patches)[:, 0]
    vivid = _render_material_patches("vivid_balanced_negative", patches)[:, 0]
    baseline_chroma = np.ptp(baseline[:6], axis=1).mean()
    vivid_chroma = np.ptp(vivid[:6], axis=1).mean()

    assert vivid_chroma > baseline_chroma * 1.45
    assert np.ptp(vivid[-1]) < 0.05


def test_color_restored_scan_explicitly_enables_bounded_material_compensation():
    config = DarkroomConfig.from_json(gui.scanner_preset_path("color_restored_scan"))

    assert config.scanner.interpreter_key == "negative_scan"
    assert config.scanner.negative_channel_compensation_enabled
    assert config.scanner.negative_channel_compensation_strength == 0.35


def test_scanner_capture_summary_reports_headroom_and_base_clipping():
    previous = current_language()
    try:
        set_language("en_US")
        summary = gui.scanner_capture_summary({
            "scanner_saturation_fraction": 0.125,
            "scanner_floor_fraction": 0.002,
            "base_saturated_channels": [True, False, True],
        })
        assert "12.5%" in summary
        assert "0.2%" in summary
        assert "RB" in summary
    finally:
        set_language(previous)


def test_builtin_preset_display_names_follow_gui_language():
    previous = current_language()
    try:
        set_language("zh_CN")
        assert gui.develop_preset_record("standard_color_reversal")["label"] == "彩色 · 正片（反转） · 标准流程"
        assert gui.scanner_preset_record("positive_transparency_scan")["label"] == "正片 · 中性灯台"
        assert gui.film_preset_record("blue_highlight_negative")["label"].endswith("冷蓝高光")
        assert gui.film_preset_record("vivid_balanced_negative")["label"].endswith("浓郁均衡")
        set_language("en_US")
        assert gui.develop_preset_record("standard_color_reversal")["label"] == "Color · Positive (Reversal) · Standard Process"
        assert gui.scanner_preset_record("positive_transparency_scan")["label"] == "Positive · Neutral Light Table"
        assert gui.film_preset_record("blue_highlight_negative")["label"].endswith("Blue Highlights")
        assert gui.film_preset_record("vivid_balanced_negative")["label"].endswith("Vivid Balanced")
    finally:
        set_language(previous)


def test_film_process_editor_accepts_silver_halide_programs_but_not_other_media():
    negative = DarkroomConfig()
    negative.chemistry.program_key = "color_negative"
    assert process_editor.is_supported_film_process(negative)

    reversal = DarkroomConfig()
    reversal.chemistry.medium_process = "reversal"
    reversal.chemistry.program_key = "color_reversal"
    assert process_editor.is_supported_film_process(reversal)

    instant = DarkroomConfig()
    instant.chemistry.medium_process = "instant"
    assert not process_editor.is_supported_film_process(instant)

    plate = DarkroomConfig()
    plate.chemistry.medium_process = "daguerreotype"
    assert not process_editor.is_supported_film_process(plate)


def test_film_process_program_keeps_material_response_mode_consistent():
    assert process_editor.mode_for_program("bw_reversal", "color_negative") == "bw_negative"
    assert process_editor.mode_for_program("color_reversal", "bw_negative") == "color_negative"
    assert process_editor.mode_for_program("auto", "bw_negative") == "bw_negative"
    assert process_editor.bleach_completion_for_program(
        "color_negative_bleach_bypass", 1.0, "color_negative"
    ) == 0.20
    assert process_editor.bleach_completion_for_program(
        "color_negative", 0.20, "color_negative_bleach_bypass"
    ) == 1.0


def test_gui_start_process_requests_cancel_when_already_running():
    from film_foundry.app.control import CancelFlag

    panel = gui.DarkroomPanel.__new__(gui.DarkroomPanel)
    flag = CancelFlag()
    panel._processing = True
    panel._cancel_flag = flag
    texts: list[str] = []
    states: list[object] = []
    statuses: list[str] = []
    panel.process_button = SimpleNamespace(
        configure=lambda **kwargs: texts.append(str(kwargs.get("text"))),
        state=lambda value: states.append(value),
        winfo_exists=lambda: True,
    )
    panel.status = SimpleNamespace(set=lambda value: statuses.append(str(value)))

    panel._start_process()

    assert flag.requested()
    assert tr("button.cancelling") in texts
    assert ["disabled"] in states
    assert statuses == [tr("status.cancelling")]


def test_gui_full_batch_stops_remaining_after_cancel(monkeypatch, tmp_path):
    from film_foundry.app.control import CancelFlag

    inputs = [tmp_path / "a.jpg", tmp_path / "b.jpg"]
    for path in inputs:
        path.touch()
    attempted: list[Path] = []
    flag = CancelFlag()
    flag.cancel()
    monkeypatch.setattr(gui, "iter_images", lambda _root: inputs)
    monkeypatch.setattr(gui, "run_timestamp", lambda _now=None: "2026-09-16_21-00-00")

    def fake_run(request):
        attempted.append(request.input_path)
        raise AssertionError("cancelled item should not start run_request")

    monkeypatch.setattr(gui, "_run_request", fake_run)
    panel = _batch_test_panel()
    output_root = tmp_path / "output"
    panel._process(
        "full",
        DarkroomConfig(),
        tmp_path / "input",
        tmp_path / "negative",
        output_root,
        False,
        False,
        _batch_snapshot(tmp_path),
        flag,
    )

    assert attempted == []
    manifest = output_root / "2026-09-16_21-00-00_full" / "film_foundry_run.json"
    payload = json.loads(manifest.read_text(encoding="utf-8"))
    assert payload["batch"]["completed"] == 0
    assert payload["batch"]["failed"] == 1
    assert payload["batch"]["items"][0]["status"] == "failed"
    assert "cancelled" in str(payload["batch"]["items"][0]["error"]["message"]).lower()
    joined = "\n".join(panel.recorded_statuses)
    assert "1" in joined
    assert "未开始" in joined or "not started" in joined


def test_gui_full_batch_cancel_after_first_success_skips_rest(monkeypatch, tmp_path):
    from film_foundry.app.control import CancelFlag

    inputs = [tmp_path / "a.jpg", tmp_path / "b.jpg"]
    for path in inputs:
        path.touch()
    attempted: list[Path] = []
    flag = CancelFlag()
    monkeypatch.setattr(gui, "iter_images", lambda _root: inputs)
    monkeypatch.setattr(gui, "run_timestamp", lambda _now=None: "2026-09-16_21-10-00")

    def fake_run(request):
        attempted.append(request.input_path)
        flag.cancel()
        return SimpleNamespace(output_path=request.output_path, extra={})

    monkeypatch.setattr(gui, "_run_request", fake_run)
    panel = _batch_test_panel()
    output_root = tmp_path / "output"
    panel._process(
        "full",
        DarkroomConfig(),
        tmp_path / "input",
        tmp_path / "negative",
        output_root,
        False,
        False,
        _batch_snapshot(tmp_path),
        flag,
    )

    assert attempted == [inputs[0]]
    manifest = output_root / "2026-09-16_21-10-00_full" / "film_foundry_run.json"
    payload = json.loads(manifest.read_text(encoding="utf-8"))
    assert payload["batch"]["completed"] == 1
    assert payload["batch"]["failed"] == 0
    assert len(payload["batch"]["items"]) == 1
