from film_foundry.core.development import build_effective_development
from film_foundry.model.config import ChemistryConfig, DarkroomConfig
from pathlib import Path
import math
import json
import numpy as np


def test_development_activity_responds_to_time_temperature_and_concentration():
    cold_short = ChemistryConfig(time_min=4.0, temperature_c=18.0, concentration=0.7)
    warm_long = ChemistryConfig(time_min=10.0, temperature_c=24.0, concentration=1.2)

    first = build_effective_development(cold_short)
    second = build_effective_development(warm_long)

    assert second.activity > first.activity
    assert second.progress > first.progress
    assert second.gamma_factor > first.gamma_factor


def test_developer_exhaustion_reduces_reagent_capacity_and_reaction_progress():
    fresh = build_effective_development(ChemistryConfig(developer_exhaustion=0.0))
    exhausted = build_effective_development(ChemistryConfig(developer_exhaustion=1.0))

    assert fresh.developer_capacity == 1.0
    assert exhausted.developer_capacity == 0.4
    assert exhausted.activity < fresh.activity
    assert exhausted.progress < fresh.progress
    assert exhausted.progress_ratio < fresh.progress_ratio


def test_developer_type_changes_derived_process_state():
    standard = build_effective_development(ChemistryConfig(developer_type="standard"))
    fine_grain = build_effective_development(ChemistryConfig(developer_type="fine_grain"))
    high_acutance = build_effective_development(
        ChemistryConfig(developer_type="high_acutance")
    )
    push = build_effective_development(ChemistryConfig(developer_type="push", push_stops=1.0))

    assert standard.grain_clump_factor == 1.0
    assert fine_grain.grain_factor < standard.grain_factor
    assert fine_grain.grain_clump_factor < standard.grain_clump_factor
    assert high_acutance.developer_profile == "high_acutance"
    assert high_acutance.grain_radius_factor < standard.grain_radius_factor
    assert high_acutance.grain_clump_factor > standard.grain_clump_factor
    assert push.gamma_factor > standard.gamma_factor
    assert push.grain_factor > standard.grain_factor
    assert push.grain_clump_factor > standard.grain_clump_factor


def test_high_acutance_bw_preset_uses_existing_first_development_adjacency():
    preset = DarkroomConfig.from_json(
        Path(__file__).resolve().parents[1]
        / "presets"
        / "develop"
        / "high_acutance_bw.json"
    )

    assert preset.mode == "bw_negative"
    assert preset.chemistry.program_key == "bw_negative"
    assert preset.chemistry.developer_type == "high_acutance"
    assert preset.chemistry.development_adjacency_strength > 0.0
    assert preset.chemistry.development_adjacency_radius > 0.0


def test_developer_and_fixer_profile_identity_is_auditable():
    standard = build_effective_development(
        ChemistryConfig(fixer_type="standard", fixer_exhaustion=0.60)
    )
    legacy_hardening_developer = build_effective_development(
        ChemistryConfig(developer_type="hardening")
    )
    rapid = build_effective_development(
        ChemistryConfig(fixer_type="rapid", fixer_exhaustion=0.60)
    )
    hardening = build_effective_development(
        ChemistryConfig(fixer_type="hardening", fixer_exhaustion=0.60)
    )

    assert standard.developer_profile == "standard"
    assert legacy_hardening_developer.developer_profile == "standard"
    assert rapid.fixer_profile == "rapid"
    assert hardening.fixer_profile == "hardening"
    assert rapid.clearing_failure < standard.clearing_failure < hardening.clearing_failure


def test_frame_size_changes_visible_grain_scale():
    half_frame = build_effective_development(ChemistryConfig(frame_size="half_frame"))
    medium_format = build_effective_development(ChemistryConfig(frame_size="6x6"))

    assert half_frame.grain_factor > medium_format.grain_factor
    assert half_frame.grain_radius_factor > medium_format.grain_radius_factor


def test_develop_json_alias_loads_into_chemistry_field():
    config = DarkroomConfig.from_dict(
        {
            "develop": {
                "developer_type": "compensating",
                "time_min": 9.5,
                "temperature_c": 21.0,
                "concentration": 0.9,
            }
        }
    )

    assert config.develop is config.chemistry
    assert config.chemistry.developer_type == "compensating"
    assert config.chemistry.time_min == 9.5


def test_json_config_boundary_normalizes_vectors_matrices_and_layer_balance():
    config = DarkroomConfig.from_dict(
        {
            "film": {
                "density_min": [0.1, 0.2, 0.3],
                "dye_absorption_matrix": [
                    [1.0, 0.1, 0.0],
                    [0.0, 1.0, 0.1],
                    [0.1, 0.0, 1.0],
                ],
            },
            "develop": {"process_layer_balance": [1.0, 0.8, 0.6]},
            "scanner": {
                "print_color_shift": [0.1, 0.0, -0.1],
                "scanner_response_matrix": [
                    [1.0, 0.0, 0.0],
                    [0.0, 1.0, 0.0],
                    [0.0, 0.0, 1.0],
                ],
            },
        }
    )

    assert config.film.density_min == (0.1, 0.2, 0.3)
    assert config.film.dye_absorption_matrix[0] == (1.0, 0.1, 0.0)
    assert config.chemistry.process_layer_balance == (1.0, 0.8, 0.6)
    assert config.scanner.print_color_shift == (0.1, 0.0, -0.1)
    assert config.scanner.scanner_response_matrix[0] == (1.0, 0.0, 0.0)


def test_develop_preset_overrides_film_chemistry_when_merged():
    film = DarkroomConfig.from_dict({"chemistry": {"developer_type": "standard", "time_min": 8.0}})
    develop = DarkroomConfig.from_dict({"develop": {"developer_type": "monobath", "time_min": 10.0}})

    merged = DarkroomConfig()
    from film_foundry.model.config import merge_config_presets

    merged = merge_config_presets(film, develop_config=develop)

    assert merged.chemistry.developer_type == "monobath"
    assert merged.chemistry.time_min == 10.0


def test_split_preset_merge_enforces_stage_ownership():
    film = DarkroomConfig()
    film.film.name = "Owned Material"
    film.chemistry.developer_type = "must_not_leak_from_material"
    film.scanner.scan_saturation = 0.1
    film.look.exposure_ev = 1.5
    film.enable_grain = False

    develop = DarkroomConfig()
    develop.chemistry.developer_type = "owned_process"
    develop.look.exposure_ev = -0.4
    develop.scanner.scan_saturation = 0.2
    develop.film.name = "must_not_replace_material"

    scanner = DarkroomConfig()
    scanner.scanner.scan_saturation = 1.3
    scanner.look.print_contrast = 1.12
    scanner.look.exposure_ev = 2.0
    scanner.chemistry.developer_type = "must_not_replace_process"
    scanner.film.name = "must_not_replace_material_either"

    from film_foundry.model.config import merge_config_presets

    merged = merge_config_presets(film, scanner, develop_config=develop)

    assert merged.film.name == "Owned Material"
    assert merged.chemistry.developer_type == "owned_process"
    assert merged.scanner.scan_saturation == 1.3
    assert merged.look.exposure_ev == -0.4
    assert merged.look.print_contrast == 1.12
    assert merged.enable_grain is True


def test_monobath_exhaustion_creates_residue_state():
    clean = build_effective_development(ChemistryConfig(developer_type="monobath", process_mode="monobath"))
    exhausted = build_effective_development(
        ChemistryConfig(
            developer_type="monobath",
            process_mode="monobath",
            fixer_exhaustion=0.8,
            silver_plating=0.5,
        )
    )

    assert exhausted.residue_factor > clean.residue_factor
    assert exhausted.silvering_factor > clean.silvering_factor
    assert exhausted.d_min_shift == clean.d_min_shift


def test_fixer_exhaustion_changes_clearing_not_developer_formation():
    clean = build_effective_development(ChemistryConfig())
    exhausted = build_effective_development(ChemistryConfig(fixer_exhaustion=0.85))

    assert exhausted.activity == clean.activity
    assert exhausted.progress == clean.progress
    assert exhausted.gamma_factor == clean.gamma_factor
    assert exhausted.d_max_factor == clean.d_max_factor
    assert exhausted.developer_fog_shift == clean.developer_fog_shift
    assert exhausted.clearing_failure > clean.clearing_failure
    assert exhausted.d_min_shift == clean.d_min_shift


def test_extreme_development_settings_have_visible_negative_quality_effects():
    normal = build_effective_development(ChemistryConfig())
    under = build_effective_development(ChemistryConfig(time_min=2.0, temperature_c=16.0, concentration=0.4, agitation=0.2))
    over = build_effective_development(ChemistryConfig(time_min=24.0, temperature_c=30.0, concentration=2.5, agitation=3.0))
    ruined = build_effective_development(
        ChemistryConfig(
            developer_type="exhausted",
            fixer_type="monobath",
            time_min=20.0,
            temperature_c=30.0,
            concentration=2.0,
            developer_exhaustion=0.9,
            fixer_exhaustion=0.9,
            silver_retention=0.8,
            silver_plating=0.8,
            push_stops=3.0,
        )
    )

    assert under.gamma_factor < normal.gamma_factor * 0.75
    assert under.underdevelopment > normal.underdevelopment
    assert under.d_max_factor < normal.d_max_factor * 0.85
    assert over.overdevelopment > normal.overdevelopment
    assert over.d_min_shift > normal.d_min_shift + 0.02
    assert over.grain_factor > normal.grain_factor * 1.4
    assert ruined.d_min_shift > normal.d_min_shift + 0.15
    assert ruined.residue_factor > 1.0
    assert ruined.silvering_factor > 1.0


def test_out_of_range_development_settings_saturate_to_finite_ruined_state():
    extreme = build_effective_development(
        ChemistryConfig(
            time_min=100000.0,
            temperature_c=250.0,
            concentration=1000.0,
            agitation=100.0,
            push_stops=100.0,
            developer_exhaustion=50.0,
            fixer_exhaustion=50.0,
            compensation=50.0,
            silver_retention=50.0,
        )
    )

    for value in (
        extreme.activity,
        extreme.progress,
        extreme.progress_ratio,
        extreme.underdevelopment,
        extreme.overdevelopment,
        extreme.agitation_deficit,
        extreme.concentration_stress,
        extreme.temperature_stress,
        extreme.gamma_factor,
        extreme.d_min_shift,
        extreme.d_max_factor,
        extreme.toe_shift,
        extreme.shoulder_shift,
        extreme.grain_factor,
        extreme.grain_radius_factor,
        extreme.residue_factor,
        extreme.silvering_factor,
    ):
        assert math.isfinite(value)

    assert extreme.progress <= 1.0
    assert extreme.exhaustion == 1.0
    assert extreme.fixer_exhaustion == 1.0
    assert extreme.clearing_failure == 1.0
    assert extreme.d_max_factor >= 0.22
    assert extreme.residue_factor <= 3.0
    assert extreme.silvering_factor <= 3.0


def test_non_finite_development_settings_fall_back_or_saturate():
    state = build_effective_development(
        ChemistryConfig(
            time_min=float("inf"),
            temperature_c=float("nan"),
            concentration=float("-inf"),
            agitation=float("inf"),
            push_stops=float("-inf"),
            developer_exhaustion=float("nan"),
            fixer_exhaustion=float("inf"),
            silver_retention=float("-inf"),
        )
    )

    assert math.isfinite(state.activity)
    assert math.isfinite(state.gamma_factor)
    assert math.isfinite(state.d_min_shift)
    assert state.fixer_exhaustion == 1.0
    assert state.silvering_factor >= 0.0


def test_push_pull_changes_actual_activity_and_grain_directionally():
    pull = build_effective_development(ChemistryConfig(push_stops=-1.0))
    normal = build_effective_development(ChemistryConfig(push_stops=0.0))
    push = build_effective_development(ChemistryConfig(push_stops=1.0))

    assert pull.activity < normal.activity < push.activity
    assert pull.progress < normal.progress < push.progress
    assert pull.gamma_factor < normal.gamma_factor < push.gamma_factor
    assert pull.grain_factor < normal.grain_factor < push.grain_factor


def test_retained_silver_and_surface_plating_are_separate_controls():
    retained = build_effective_development(ChemistryConfig(silver_retention=0.8))
    plated = build_effective_development(ChemistryConfig(silver_plating=0.8))

    assert retained.silvering_factor == 0.0
    assert plated.silvering_factor > 0.0
    assert plated.residue_factor == 0.0


def test_darkroom_accident_settings_are_bounded_and_affect_state():
    clean = build_effective_development(ChemistryConfig())
    accident = build_effective_development(
        ChemistryConfig(
            light_leak_strength=2.0,
            chemical_stain=0.75,
            uneven_development=0.50,
        )
    )

    assert accident.light_leak_strength == 1.0
    assert accident.chemical_stain == 0.75
    assert accident.uneven_development == 0.50
    assert accident.d_min_shift == clean.d_min_shift
    assert accident.d_max_factor == clean.d_max_factor
    assert accident.developer_fog_shift == clean.developer_fog_shift
    assert accident.residue_factor > clean.residue_factor


def test_material_retained_halide_color_is_configurable():
    config = DarkroomConfig.from_dict(
        {"film": {"retained_halide_density_rgb": [1.0, 0.5, 0.25]}}
    )

    assert tuple(config.film.retained_halide_density_rgb) == (1.0, 0.5, 0.25)


def test_all_bundled_split_presets_load():
    preset_root = Path(__file__).resolve().parents[1] / "presets"
    preset_dirs = ("film", "develop", "scanner")

    for preset_kind in preset_dirs:
        presets = sorted((preset_root / preset_kind).glob("*.json"))

        assert presets, preset_kind
        for preset in presets:
            config = DarkroomConfig.from_json(preset)
            assert config.film.name
            assert config.chemistry.developer_type
            assert config.scanner.scan_method
            if preset_kind == "scanner":
                assert config.scanner.interpreter_key in {"negative_scan", "positive_transparency_scan"}
                assert config.scanner.output_polarity == "positive"
                if config.scanner.interpreter_key == "negative_scan":
                    assert config.scanner.target_medium_process == "negative"
                    assert config.scanner.input_polarity == "negative"
                else:
                    assert config.scanner.target_medium_process in {"slide", "reversal", "positive"}
                    assert config.scanner.input_polarity == "positive"


def test_accident_develop_preset_loads():
    preset = Path(__file__).resolve().parents[1] / "presets" / "develop" / "accident_kelp_light_leak.json"

    config = DarkroomConfig.from_json(preset)

    assert config.chemistry.light_leak_strength > 0.0
    assert config.chemistry.chemical_stain > 0.0
    assert config.chemistry.uneven_development > 0.0


def test_bundled_film_presets_only_define_material_state():
    preset_dir = Path(__file__).resolve().parents[1] / "presets" / "film"
    presets = sorted(preset_dir.glob("*.json"))

    assert presets
    for preset in presets:
        payload = json.loads(preset.read_text(encoding="utf-8"))
        assert set(payload) == {"film"}, preset.name
        assert "chemistry" not in payload
        assert "develop" not in payload
        assert "look" not in payload


def test_standard_develop_presets_use_authoritative_reduced_programs():
    preset_dir = Path(__file__).resolve().parents[1] / "presets" / "develop"
    color = DarkroomConfig.from_json(preset_dir / "standard_color_negative.json")
    bw = DarkroomConfig.from_json(preset_dir / "standard_bw_negative.json")

    assert color.chemistry.program_key == "color_negative"
    assert bw.chemistry.program_key == "bw_negative"


def test_all_bundled_develop_presets_route_through_unified_pool_model():
    from film_foundry.core.engine import develop_negative

    preset_dir = Path(__file__).resolve().parents[1] / "presets" / "develop"
    image = np.full((3, 4, 3), 0.4, dtype=np.float32)
    for preset in sorted(preset_dir.glob("*.json")):
        config = DarkroomConfig.from_json(preset)
        config.enable_grain = False
        config.enable_halation = False
        medium = develop_negative(image, config, rng=np.random.default_rng(7))
        representation = medium.metadata["film_process_model"]["representation"]
        assert representation != "legacy_density_adapter", preset.name


def test_negative_film_presets_declare_medium_identity():
    preset_dir = Path(__file__).resolve().parents[1] / "presets" / "film"
    for preset in sorted(preset_dir.glob("*.json")):
        config = DarkroomConfig.from_json(preset)
        if config.film.image_polarity == "positive":
            continue
        assert config.film.medium_family == "film", preset.name
        assert config.film.medium_process == "negative", preset.name
        assert config.film.image_polarity == "negative", preset.name
        assert config.film.color_process in {"color", "monochrome"}, preset.name


def test_clean_digital_like_is_high_latitude_negative_not_diagnostic_clear_base():
    preset = Path(__file__).resolve().parents[1] / "presets" / "film" / "clean_digital_like.json"
    config = DarkroomConfig.from_json(preset)

    assert config.film.medium_process == "negative"
    assert config.film.image_polarity == "negative"
    assert max(config.film.hd_gamma) < 0.60
    assert config.film.hd_toe_width >= 0.20
    assert config.film.hd_shoulder_width >= 0.30
    assert config.film.film_base_density_rgb[2] > config.film.film_base_density_rgb[1] > config.film.film_base_density_rgb[0]
    assert min(config.film.granularity_sigma) > 0.0
    assert max(config.film.granularity_sigma) < 0.016


def test_scanner_preset_roles_are_separated():
    preset_dir = Path(__file__).resolve().parents[1] / "presets" / "scanner"
    neutral = DarkroomConfig.from_json(preset_dir / "neutral_scan.json")
    rich = DarkroomConfig.from_json(preset_dir / "rich_color_scan.json")
    warm = DarkroomConfig.from_json(preset_dir / "warm_consumer_scan.json")
    dense = DarkroomConfig.from_json(preset_dir / "dense_shadow_scan.json")
    clean = DarkroomConfig.from_json(preset_dir / "clean_digital_scan.json")
    diagnostic = DarkroomConfig.from_json(preset_dir / "diagnostic_flat_scan.json")

    assert neutral.scanner.scan_saturation < rich.scanner.scan_saturation
    assert neutral.look.print_contrast < dense.look.print_contrast
    assert warm.scanner.scan_saturation < rich.scanner.scan_saturation
    assert neutral.scanner.scan_saturation >= 1.10
    assert clean.scanner.scan_saturation >= 1.08
    assert warm.scanner.print_color_shift[0] > neutral.scanner.print_color_shift[0]
    assert dense.scanner.scan_normalize_strength > neutral.scanner.scan_normalize_strength
    assert clean.scanner.scan_normalize_strength > neutral.scanner.scan_normalize_strength
    assert neutral.scanner.scan_normalize_strength >= 0.40
    assert clean.scanner.print_gamma < neutral.scanner.print_gamma
    assert diagnostic.scanner.scan_normalize is False


def test_processing_config_loads_quality_controls():
    config = DarkroomConfig.from_dict(
        {
            "processing": {
                "quality_mode": "draft",
                "halation_work_long_edge": 900,
                "emulsion_spread_work_long_edge": 1000,
                "grain_work_long_edge": 1100,
            }
        }
    )

    assert config.processing.quality_mode == "draft"
    assert config.processing.halation_work_long_edge == 900
    assert config.processing.emulsion_spread_work_long_edge == 1000
    assert config.processing.grain_work_long_edge == 1100
