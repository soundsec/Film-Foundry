from half_frame_darkroom.core.development import build_effective_development
from half_frame_darkroom.model.config import ChemistryConfig, DarkroomConfig
from pathlib import Path
import math
import json


def test_development_activity_responds_to_time_temperature_and_concentration():
    cold_short = ChemistryConfig(time_min=4.0, temperature_c=18.0, concentration=0.7)
    warm_long = ChemistryConfig(time_min=10.0, temperature_c=24.0, concentration=1.2)

    first = build_effective_development(cold_short)
    second = build_effective_development(warm_long)

    assert second.activity > first.activity
    assert second.progress > first.progress
    assert second.gamma_factor > first.gamma_factor


def test_developer_type_changes_derived_process_state():
    standard = build_effective_development(ChemistryConfig(developer_type="standard"))
    fine_grain = build_effective_development(ChemistryConfig(developer_type="fine_grain"))
    push = build_effective_development(ChemistryConfig(developer_type="push", push_stops=1.0))

    assert fine_grain.grain_factor < standard.grain_factor
    assert push.gamma_factor > standard.gamma_factor
    assert push.grain_factor > standard.grain_factor


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


def test_develop_preset_overrides_film_chemistry_when_merged():
    film = DarkroomConfig.from_dict({"chemistry": {"developer_type": "standard", "time_min": 8.0}})
    develop = DarkroomConfig.from_dict({"develop": {"developer_type": "monobath", "time_min": 10.0}})

    merged = DarkroomConfig()
    from half_frame_darkroom.model.config import merge_config_presets

    merged = merge_config_presets(film, develop_config=develop)

    assert merged.chemistry.developer_type == "monobath"
    assert merged.chemistry.time_min == 10.0


def test_monobath_exhaustion_creates_residue_state():
    clean = build_effective_development(ChemistryConfig(developer_type="monobath", process_mode="monobath"))
    exhausted = build_effective_development(
        ChemistryConfig(
            developer_type="monobath",
            process_mode="monobath",
            fixer_exhaustion=0.8,
            silver_retention=0.5,
        )
    )

    assert exhausted.residue_factor > clean.residue_factor
    assert exhausted.silvering_factor > clean.silvering_factor
    assert exhausted.d_min_shift > clean.d_min_shift


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
            push_stops=3.0,
        )
    )

    assert under.gamma_factor < normal.gamma_factor * 0.75
    assert under.d_max_factor < normal.d_max_factor * 0.85
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
    assert accident.d_min_shift > clean.d_min_shift
    assert accident.d_max_factor < clean.d_max_factor
    assert accident.residue_factor > clean.residue_factor


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


def test_processing_config_loads_quality_controls():
    config = DarkroomConfig.from_dict(
        {
            "processing": {
                "quality_mode": "draft",
                "halation_work_long_edge": 900,
                "grain_work_long_edge": 1100,
            }
        }
    )

    assert config.processing.quality_mode == "draft"
    assert config.processing.halation_work_long_edge == 900
    assert config.processing.grain_work_long_edge == 1100
